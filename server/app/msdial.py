"""MS-DIAL alignment exports, read however MS-DIAL wrote them.

The vendored ``mzmineimport.reformat_msdial`` reads exactly one layout: the
tab-separated ``.txt`` of the MS-DIAL version MPACT was written against,
with 35 annotation columns before the first sample and four header-block
rows (Class, File type, Injection order, Batch ID) above the column names.
Two things in newer exports break that, and the vendored reader can't be
edited, so uploads are rewritten here instead:

- **More annotation columns.** MS-DIAL 5.5 adds five (Measured terms,
  Evidence source, Candidates found / above threshold / reference matched)
  before the samples. The fixed offset then reads annotation text as
  abundances and loses the first samples.
- **Half-quoted CSV.** The ``.csv`` export quotes a field that contains a
  comma, but a compound name MS-DIAL has already wrapped in quotes comes
  out as ``""2,8-Dimethyl-5,7-dimethoxychromone""`` -- escaped but never
  enclosed -- so a standard CSV reader splits the name at each comma and
  shifts every later cell along the row.

:func:`normalize` reads either file type, finds the layout from the header
block rather than from fixed positions, and writes the legacy ``.txt``:
Alignment ID, Average Rt(min) and Average Mz first, annotation columns
trimmed or padded to 35, then the sample columns and the per-class
Average/Stdev columns. Only the annotation block changes shape; every
alignment ID, retention time, m/z, sample name and abundance is carried
over exactly as written.
"""

import os
import re
from pathlib import Path

LEGACY_META = 35          # columns before the first sample in the legacy layout
BLOCK_LABELS = ('Class', 'File type', 'Injection order', 'Batch ID')
SUMMARY = ('Average', 'Stdev')
KEY_COLUMNS = ('Alignment ID', 'Average Rt(min)', 'Average Mz')
HEAD_ROWS = 12            # the column-name row is within this many lines

_NUMBER = re.compile(r'^-?\d+(\.\d+)?([eE][-+]?\d+)?$')


class MsdialError(ValueError):
    """An MS-DIAL export whose layout can't be worked out."""


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def split_csv_line(line):
    """Cells of one line of an MS-DIAL ``.csv`` export.

    Standard CSV quoting is honoured (``"1,2-diols"``, and ``""`` for a
    quote inside a quoted field), plus MS-DIAL's escaped-but-unenclosed
    field ``""text""``, which is read as ``"text"`` -- the cell exactly as
    the ``.txt`` export writes it.
    """
    if '"' not in line:
        return line.split(',')
    cells = []
    i, n = 0, len(line)
    while True:
        if line.startswith('""', i) and i + 2 < n and line[i + 2] not in ',"':
            # ""text"": up to the next "" that ends the field.
            j, end = i + 2, -1
            while True:
                close = line.find('""', j)
                if close < 0:
                    break
                if close + 2 == n or line[close + 2] == ',':
                    end = close
                    break
                j = close + 2
            if end < 0:
                cells.append(line[i:].replace('""', '"'))
                return cells
            cells.append('"' + line[i + 2:end].replace('""', '"') + '"')
            i = end + 2
        elif i < n and line[i] == '"':
            # A properly quoted field.
            j, parts = i + 1, []
            while True:
                close = line.find('"', j)
                if close < 0:
                    parts.append(line[j:])
                    j = n
                    break
                parts.append(line[j:close])
                if line.startswith('""', close):
                    parts.append('"')
                    j = close + 2
                    continue
                j = close + 1
                break
            # Anything between the closing quote and the comma stays with it.
            comma = line.find(',', j)
            stop = n if comma < 0 else comma
            parts.append(line[j:stop])
            cells.append(''.join(parts))
            i = stop
        else:
            comma = line.find(',', i)
            stop = n if comma < 0 else comma
            cells.append(line[i:stop])
            i = stop
        # i is now on the comma after the field, or past the end.
        if i >= n:
            return cells
        i += 1
        if i == n:
            cells.append('')
            return cells


def _rows(path):
    """The cells of every line, for either export type."""
    tab = Path(path).suffix.lower() == '.txt'
    with open(path, encoding='utf-8-sig', errors='replace', newline='') as handle:
        for line in handle:
            line = line.rstrip('\r\n')
            yield line.split('\t') if tab else split_csv_line(line)


def is_msdial(path):
    """True for an MS-DIAL alignment export, in either file type."""
    try:
        for index, row in enumerate(_rows(path)):
            if any(cell.strip() == 'Alignment ID' for cell in row):
                return True
            if index >= HEAD_ROWS:
                return False
    except OSError:
        return False
    return False


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

class Layout:
    """Where everything is in one export, worked out from its header.

    ``label`` is the column holding the header-block labels (Class, File
    type, ...): it is the last annotation column, and the samples start
    right after it. The per-class Average/Stdev columns follow the samples
    and are marked in the header block's Batch ID row.
    """

    def __init__(self, block, header):
        self.header = [cell.strip() for cell in header]
        self.width = len(header)
        named = {}
        for index, cell in enumerate(header):
            named.setdefault(cell.strip(), index)
        missing = [name for name in KEY_COLUMNS if name not in named]
        if missing:
            raise MsdialError(f'MS-DIAL export without the {", ".join(missing)} column(s)')
        self.keys = [named[name] for name in KEY_COLUMNS]

        label, labels = None, {}
        for row in block:
            first = next((i for i, cell in enumerate(row) if cell.strip()), None)
            if first is None:
                continue
            if label is None:
                label = first
            if first == label:
                labels[row[first].strip()] = (row + [''] * self.width)[:self.width]
        if label is None or label < max(self.keys) or label >= self.width - 1:
            raise MsdialError('could not find the Class / File type rows above the samples')
        self.label = label
        self.labels = labels
        self.block_rows = len(block)

        summary = [i for i in range(label + 1, self.width)
                   if any(row[i].strip() in SUMMARY for row in labels.values())]
        start = summary[0] if summary else self.width
        if summary != list(range(start, self.width)):
            raise MsdialError('Average/Stdev columns are mixed in with the samples')
        self.samples = range(label + 1, start)
        self.summary = range(start, self.width)
        if not len(self.samples):
            raise MsdialError('no sample columns found')

        # The legacy reader cuts 2 x (Stdev cells in the Batch ID row) columns
        # off the end, so keep the originals only when that count is right;
        # otherwise they are replaced by one Average/Stdev pair.
        batch = labels.get('Batch ID', [''] * self.width)
        marks = [batch[i].strip() for i in self.summary]
        self.keep_summary = (bool(marks) and marks.count('Stdev') * 2 == len(marks)
                             and marks.count('Average') == marks.count('Stdev'))

        others = [i for i in range(label + 1) if i not in self.keys]
        self.meta = (self.keys + others)[:LEGACY_META]

    def is_legacy(self):
        return (self.label == LEGACY_META - 1 and self.keys == [0, 1, 2]
                and self.keep_summary and self.block_rows == len(BLOCK_LABELS)
                and list(self.labels) == list(BLOCK_LABELS))

    def repair(self, row):
        """Bring a row to the header's width, or ``None`` if it can't be.

        Only annotation text can hold the delimiter, so the key columns are
        taken from the front, the sample and summary block from the back, and
        any surplus in between is folded back into the first free-text
        annotation (Metabolite name). A short row is a truncated line: it is
        padded only if nothing but Average/Stdev cells is missing.
        """
        if len(row) == self.width:
            return row
        if len(row) < self.width:
            return row + [''] * (self.width - len(row)) if len(row) >= self.samples.stop else None
        tail = self.width - (self.label + 1)
        head = max(self.keys) + 1
        between = self.label + 1 - head
        middle = row[head:len(row) - tail]
        excess = len(middle) - between
        middle = [','.join(middle[:excess + 1])] + middle[excess + 1:]
        return row[:head] + middle + row[len(row) - tail:]

    def legacy(self, row, summary_fill):
        """One data or column-name row in the legacy layout."""
        meta = [row[i] for i in self.meta]
        meta += [''] * (LEGACY_META - len(meta))
        samples = [row[i] for i in self.samples]
        summary = [row[i] for i in self.summary] if self.keep_summary else summary_fill
        return meta + samples + summary


def read_layout(path):
    """``(Layout, index of the column-name line)`` from an export's head."""
    block = []
    for index, row in enumerate(_rows(path)):
        if any(cell.strip() == 'Alignment ID' for cell in row):
            return Layout(block, row), index
        block.append(row)
        if index >= HEAD_ROWS:
            break
    raise MsdialError('no "Alignment ID" column header in the first lines')


# --------------------------------------------------------------------------
# Writing the legacy layout
# --------------------------------------------------------------------------

def _clean(cell):
    # Tabs and line breaks would split the cell; a double quote at the start
    # of a cell would open a quoted field in pandas' reader and swallow the
    # lines after it.
    return cell.replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').replace('"', '')


def _already_clean(path, layout, header_at):
    """A legacy .txt with every line as wide as the header and no quotes."""
    with open(path, encoding='utf-8-sig', errors='replace', newline='') as handle:
        for index, line in enumerate(handle):
            if index <= header_at:
                continue
            line = line.rstrip('\r\n')
            if line and ('"' in line or line.count('\t') != layout.width - 1):
                return False
    return True


def _block_row(layout, label):
    """A header-block row (Class, File type, ...) in the legacy layout."""
    source = layout.labels.get(label)
    n = len(layout.samples)
    if source is not None:
        samples = [source[i] for i in layout.samples]
        summary = [source[i] for i in layout.summary] if layout.keep_summary else None
    else:
        # Not in this export: something the legacy reader can index.
        samples = {'File type': ['Sample'] * n,
                   'Injection order': [str(i + 1) for i in range(n)],
                   'Batch ID': ['1'] * n}.get(label, ['NA'] * n)
        summary = None
    if summary is None:
        summary = list(SUMMARY) if label == 'Batch ID' else ['NA', 'NA']
    return [''] * (LEGACY_META - 1) + [label] + samples + summary


def normalize(path):
    """Rewrite an MS-DIAL export as the legacy tab-separated layout.

    Returns the path of the rewritten file (a ``.csv`` is replaced by a
    ``.txt`` of the same stem), or ``None`` when the file is not an MS-DIAL
    export or is already a clean legacy ``.txt``. Raises
    :class:`MsdialError` when the layout can't be worked out.
    """
    path = Path(path)
    if path.suffix.lower() not in ('.csv', '.txt') or not is_msdial(path):
        return None
    layout, header_at = read_layout(path)
    if (path.suffix.lower() == '.txt' and layout.is_legacy()
            and _already_clean(path, layout, header_at)):
        return None

    target = path.with_suffix('.txt')
    scratch = target.with_name(target.name + '.part')
    fill = ['0', '0']
    written = 0
    try:
        with open(scratch, 'w', encoding='utf-8', newline='') as out:
            def emit(cells):
                out.write('\t'.join(_clean(cell) for cell in cells))
                out.write('\n')

            for label in BLOCK_LABELS:
                emit(_block_row(layout, label))
            emit(layout.legacy(layout.header, list(SUMMARY)))

            for index, row in enumerate(_rows(path)):
                if index <= header_at or not any(cell.strip() for cell in row):
                    continue
                repaired = layout.repair(row)
                if repaired is None:
                    raise MsdialError(f'line {index + 1} has {len(row)} cells where the header '
                                      f'has {layout.width}: the file looks cut short')
                row = repaired
                rt, mz = row[layout.keys[1]].strip(), row[layout.keys[2]].strip()
                if not (row[layout.keys[0]].strip() and _NUMBER.match(rt) and _NUMBER.match(mz)):
                    raise MsdialError(f'line {index + 1}: no alignment ID, retention time '
                                      f'and m/z where the header puts them')
                emit(layout.legacy(row, fill))
                written += 1
        if not written:
            raise MsdialError('the export has no features')
        os.replace(scratch, target)
    except BaseException:
        scratch.unlink(missing_ok=True)
        raise
    if target != path:
        path.unlink()
    return target

