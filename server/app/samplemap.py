"""A sample list and metadata sheet, narrowed to one peak table.

The pipeline joins the metadata to the sample list on Sample_Code, indexes
the result by Injection and looks each of the peak table's injections up in
it. Rows for injections the peak table doesn't have are never looked at, so
a class can keep one composite sample list and metadata sheet covering
everybody's samples and use it with any one person's peak table.

What a composite sheet does tend to pick up is repeats: two people's lists
pasted together list a shared blank twice, or the same Sample_Code appears
on two metadata rows. A repeated Sample_Code makes the join repeat that
sample's injections; the lookup then finds two rows where the pipeline
expects one, and the run stops part-way with an error that doesn't say why.

:func:`fit` is applied to a run's private copies of the two sheets (and to
the copies the group preview reads): it keeps only the rows this peak table
uses, collapses repeats -- the first one wins, and a repeat that disagrees
with it is reported -- and trims stray spaces from the key cells, which
would otherwise make a name fail to match. The library files themselves are
never changed.
"""

import csv
from pathlib import Path

SAMPLE_KEYS = ('Injection', 'Sample_Code')
META_KEYS = ('Sample_Code', 'Biological_Group')


def _read(path):
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as handle:
        rows = [row for row in csv.reader(handle)]
    if not rows:
        return [], []
    columns = [cell.strip() for cell in rows[0]]
    width = len(columns)
    body = [(row + [''] * width)[:width] for row in rows[1:] if any(c.strip() for c in row)]
    return columns, body


def _write(path, columns, rows):
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(columns)
        writer.writerows(rows)


def _same(name):
    """A name as the pipeline's lookups compare it: pandas reads a purely
    numeric injection name as a number on one side (``1.0``) and the other
    (``1``, ``001``), and those still match there, so they must here."""
    name = str(name).strip()
    try:
        number = float(name)
    except ValueError:
        return name
    return str(int(number)) if number.is_integer() else repr(number)


def _pick(rows, key, value, keep):
    """Rows whose ``key`` cell is in ``keep``, the first occurrence of each
    key winning. Returns ``(rows, conflicts, {key: value})``; a conflict is a
    repeated key whose ``value`` cell differs from the one kept."""
    chosen, first, conflicts = [], {}, []
    for row in rows:
        row[key] = row[key].strip()
        row[value] = row[value].strip()
        if _same(row[key]) not in keep:
            continue
        if row[key] in first:
            if row[value] != first[row[key]] and row[key] not in conflicts:
                conflicts.append(row[key])
            continue
        first[row[key]] = row[value]
        chosen.append(row)
    return chosen, conflicts, first


def fit(injections, samplelist, metadata, out_samplelist=None, out_metadata=None):
    """Write the two sheets cut down to ``injections`` (the peak table's
    sample columns). By default the files are rewritten in place.

    Returns a list of warnings. Sheets missing a key column are left as they
    are: the pipeline's own check reports that.
    """
    out_samplelist = Path(out_samplelist or samplelist)
    out_metadata = Path(out_metadata or metadata)
    s_cols, s_rows = _read(samplelist)
    m_cols, m_rows = _read(metadata)
    if not all(c in s_cols for c in SAMPLE_KEYS) or not all(c in m_cols for c in META_KEYS):
        return []

    inj, code = (s_cols.index(c) for c in SAMPLE_KEYS)
    s_keep, s_conflicts, code_of = _pick(s_rows, inj, code, {_same(n) for n in injections})
    m_code, group = (m_cols.index(c) for c in META_KEYS)
    m_keep, m_conflicts, _ = _pick(m_rows, m_code, group, {_same(c) for c in code_of.values()})

    warnings = []
    if s_conflicts:
        warnings.append(
            f'{len(s_conflicts)} injection(s) are listed more than once in the sample list '
            f'with different Sample_Codes; the first row was used for '
            f'{", ".join(s_conflicts[:5])}' + (' ...' if len(s_conflicts) > 5 else ''))
    if m_conflicts:
        warnings.append(
            f'{len(m_conflicts)} Sample_Code(s) appear more than once in the metadata with '
            f'different Biological_Groups; the first row was used for '
            f'{", ".join(m_conflicts[:5])}' + (' ...' if len(m_conflicts) > 5 else ''))

    _write(out_samplelist, s_cols, s_keep)
    _write(out_metadata, m_cols, m_keep)
    return warnings

