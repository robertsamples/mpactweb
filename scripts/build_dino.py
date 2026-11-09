"""Pack the dinosaur sprite frames into the sheet the Run terminal animates.

    python scripts/build_dino.py path/to/sprites [--variant fine|coarse]

Reads ``manifest_<variant>.json`` and its ASCII frames ('#' near side, '+'
far side, '.' empty) and writes ``web/assets/dino.png`` (every frame once, on
a grid, transparent background) and ``web/assets/dino.json`` (clip -> frame
indices, fps, and the hand-off frames the state machine needs).

The two tones are baked into the PNG. They sit between the terminal's
background (#141414) and its text (#b9b9b9): visible, but dark enough that
any text colour, red included, reads on top.
"""
import argparse
import json
import re
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
NEAR = (66, 66, 71, 255)
FAR = (45, 45, 49, 255)
COLUMNS = 16


def load(path):
    rows = [r.rstrip('\r\n') for r in path.read_text().splitlines() if r.strip()]
    width = max(len(r) for r in rows)
    return [r.ljust(width, '.') for r in rows]


def number_in(text, pattern):
    match = re.search(pattern, text)
    if not match:
        raise SystemExit(f'manifest connection text changed; could not find {pattern!r}')
    return int(match.group(1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('sprites', type=Path)
    parser.add_argument('--variant', default='fine', choices=('fine', 'coarse'))
    args = parser.parse_args()

    manifest = json.loads((args.sprites / f'manifest_{args.variant}.json').read_text())
    clips = manifest['clips']
    frames, index = [], {}

    def add(rel):
        rel = rel.replace('\\', '/')
        if rel not in index:
            index[rel] = len(frames)
            frames.append(load(args.sprites / rel))
        return index[rel]

    out = {}
    for name, clip in clips.items():
        entry = {'fps': clip['fps'], 'loop': bool(clip.get('loop'))}
        if 'from_idle_frame' in clip:
            entry['from'] = {k: [add(f) for f in v] for k, v in clip['from_idle_frame'].items()}
        else:
            entry['frames'] = [add(f) for f in clip['frames']]
        out[name] = entry

    height, width = len(frames[0]), len(frames[0][0])
    rows = -(-len(frames) // COLUMNS)
    sheet = Image.new('RGBA', (COLUMNS * width, rows * height), (0, 0, 0, 0))
    pixels = sheet.load()
    for n, frame in enumerate(frames):
        ox, oy = (n % COLUMNS) * width, (n // COLUMNS) * height
        for y, row in enumerate(frame):
            for x, c in enumerate(row):
                if c == '#':
                    pixels[ox + x, oy + y] = NEAR
                elif c == '+':
                    pixels[ox + x, oy + y] = FAR

    links = manifest['connections']
    meta = {
        'width': width, 'height': height, 'columns': COLUMNS,
        'clips': out,
        # Hand-offs, from the manifest's connection rules.
        'idle_exit_every': number_in(links['idle->start'], r'multiple of (\d+)'),
        'run_join': manifest['run_join_frame'],
        'stop_after': number_in(links['run->stop'], r'run frame (\d+)'),
        'crash_after': number_in(links['run->crash'], r'run frame (\d+)'),
    }
    assets = REPO / 'web' / 'assets'
    sheet.save(assets / 'dino.png', optimize=True)
    (assets / 'dino.json').write_text(json.dumps(meta, separators=(',', ':')))
    print(f'{len(frames)} frames, {width}x{height}, sheet {sheet.size}, '
          f'{(assets / "dino.png").stat().st_size // 1024} KB')


if __name__ == '__main__':
    main()
