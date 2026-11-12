"""Validate numbered JPEG exports; visual fidelity requires a separate review."""
import argparse
import json
from pathlib import Path
from PIL import Image


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('folder', type=Path)
    p.add_argument('--count', type=int, default=8)
    p.add_argument('--width', type=int, default=900)
    p.add_argument('--height', type=int, default=1200)
    p.add_argument('--max-bytes', type=int, default=10_000_000)
    args = p.parse_args()
    errors, rows = [], []
    if not args.folder.is_dir():
        p.error('Export folder does not exist')
    for number in range(1, args.count + 1):
        files = sorted(args.folder.glob(f'{number:02d}_*.jpg'))
        if len(files) != 1:
            errors.append(f'{number:02d}: expected one JPEG, found {len(files)}')
            continue
        f = files[0]
        try:
            with Image.open(f) as im:
                im.load()
                rows.append(dict(file=f.name, size=im.size, mode=im.mode,
                                 bytes=f.stat().st_size,
                                 embedded_icc=bool(im.info.get('icc_profile'))))
                if im.size != (args.width, args.height):
                    errors.append(f'{f.name}: wrong dimensions {im.size}')
                if im.mode != 'RGB':
                    errors.append(f'{f.name}: expected RGB, found {im.mode}')
                if im.format != 'JPEG':
                    errors.append(f'{f.name}: content is not JPEG')
                if f.stat().st_size > args.max_bytes:
                    errors.append(f'{f.name}: exceeds size limit')
        except Exception as exc:
            errors.append(f'{f.name}: cannot decode ({exc})')
    expected = {f'{n:02d}' for n in range(1, args.count + 1)}
    for f in args.folder.glob('[0-9][0-9]_*.jpg'):
        if f.name[:2] not in expected:
            errors.append(f'{f.name}: unexpected numbered export')
    print(json.dumps(dict(files=rows, errors=errors, visual_qa='required separately'),
                     ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == '__main__':
    main()
