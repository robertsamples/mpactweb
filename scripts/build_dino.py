"""Pack the dinosaur sprite frames into the sheets the Run terminal animates.

    python scripts/build_dino.py path/to/sprites [--variant fine|coarse]

Builds the standard set from ``<sprites>/manifest_<variant>.json`` and every
holiday set found as ``<sprites>/<theme>/manifest_<variant>.json``. Writes,
per theme, ``web/assets/dino/<theme>.png`` (every frame once, on a grid,
transparent background) and ``<theme>.json`` (clip -> frame indices, fps, and
the hand-offs the state machine needs). The page picks the theme by date and
only ever downloads that one sheet.

Frames are ASCII: '#' near side, '+' far side, '.' empty, and letters for
accessory colours. Clips can have different canvas heights (the crash grows
upward so the tail isn't clipped; hats need headroom), so every frame goes in
a cell as tall as the tallest, bottom-aligned on the shared ground line.
``ref_height`` is the standard idle frame's height: the page sizes the
dinosaur by it, so a taller canvas adds room above rather than shrinking it.

The body tones sit between the terminal's background (#141414) and its text
(#b9b9b9): visible, but dark enough that any text colour, red included, reads
on top. Accessory colours are the generator's palette dimmed by the same
amount the body was.
"""
import argparse
import json
import re
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
COLUMNS = 16
DIM = 0.85   # accessory colours relative to the generator's preview palette

BODY = {'#': (66, 66, 71), '+': (45, 45, 49)}
# Copy of the accessory palette in the generator's tools/render.py.
ACCESSORY = {
    'r': (112, 48, 46), 'w': (168, 163, 152), 'b': (58, 76, 120), 'v': (124, 120, 112),
    'p': (150, 104, 114), 'm': (60, 46, 78), 'o': (156, 96, 44), 'k': (58, 54, 64),
    'g': (166, 136, 64), 'q': (100, 110, 120), 'C': (172, 154, 104), 'y': (196, 180, 120),
    'Y': (226, 214, 170), 'F': (146, 42, 38), 'n': (100, 86, 70), 'l': (176, 154, 66),
    'G': (72, 112, 66), 'u': (106, 66, 128),
}
PALETTE = {**{c: tuple(round(v * DIM) for v in rgb) for c, rgb in ACCESSORY.items()}, **BODY}


def load(path):
    rows = [r.rstrip('\r\n') for r in path.read_text().splitlines() if r.strip()]
    width = max(len(r) for r in rows)
    return [r.ljust(width, '.') for r in rows]


def number_in(text, pattern):
    match = re.search(pattern, text)
    if not match:
        raise SystemExit(f'manifest connection text changed; could not find {pattern!r}')
    return int(match.group(1))


def build(root, variant, ref_height, out_dir, theme):
    manifest = json.loads((root / f'manifest_{variant}.json').read_text())
    frames, index = [], {}

    def add(rel):
        rel = rel.replace('\\', '/')
        if rel not in index:
            index[rel] = len(frames)
            frames.append(load(root / rel))
        return index[rel]

    clips = {}
    for name, clip in manifest['clips'].items():
        entry = {'fps': clip['fps'], 'loop': bool(clip.get('loop'))}
        if 'from_idle_frame' in clip:
            entry['from'] = {k: [add(f) for f in v] for k, v in clip['from_idle_frame'].items()}
        else:
            entry['frames'] = [add(f) for f in clip['frames']]
        clips[name] = entry

    height = max(len(f) for f in frames)
    width = max(len(f[0]) for f in frames)
    rows = -(-len(frames) // COLUMNS)
    sheet = Image.new('RGBA', (COLUMNS * width, rows * height), (0, 0, 0, 0))
    pixels = sheet.load()
    for n, frame in enumerate(frames):
        ox = (n % COLUMNS) * width
        oy = (n // COLUMNS) * height + height - len(frame)     # bottom-aligned
        for y, row in enumerate(frame):
            for x, c in enumerate(row):
                if c == '.':
                    continue
                if c not in PALETTE:
                    raise SystemExit(f'{theme}: unknown sprite character {c!r}; add it to PALETTE')
                pixels[ox + x, oy + y] = (*PALETTE[c], 255)

    links = manifest['connections']
    meta = {
        'width': width, 'height': height, 'ref_height': ref_height, 'columns': COLUMNS,
        'clips': clips,
        # Hand-offs, from the manifest's connection rules.
        'idle_exit_every': number_in(links['idle->start'], r'multiple of (\d+)'),
        'run_join': manifest['run_join_frame'],
        'stop_after': number_in(links['run->stop'], r'run frame (\d+)'),
        'crash_after': number_in(links['run->crash'], r'run frame (\d+)'),
    }
    png = out_dir / f'{theme}.png'
    sheet.save(png, optimize=True)
    (out_dir / f'{theme}.json').write_text(json.dumps(meta, separators=(',', ':')))
    print(f'{theme:10} {len(frames)} frames, cell {width}x{height}, sheet {sheet.size}, '
          f'{png.stat().st_size // 1024} KB')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('sprites', type=Path)
    parser.add_argument('--variant', default='fine', choices=('fine', 'coarse'))
    args = parser.parse_args()

    standard = json.loads((args.sprites / f'manifest_{args.variant}.json').read_text())
    ref_height = len(load(args.sprites / standard['clips']['idle']['frames'][0]))

    out_dir = REPO / 'web' / 'assets' / 'dino'
    out_dir.mkdir(parents=True, exist_ok=True)
    build(args.sprites, args.variant, ref_height, out_dir, 'standard')
    for sub in sorted(p for p in args.sprites.iterdir() if p.is_dir()):
        if (sub / f'manifest_{args.variant}.json').exists():
            build(sub, args.variant, ref_height, out_dir, sub.name)


if __name__ == '__main__':
    main()
