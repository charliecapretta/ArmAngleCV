"""
Generic frame renderer for hand annotation. Produces coordinate-gridded views of
any clip, in ORIGINAL frame pixels regardless of zoom.

  render.py overview <clipPath> <frameIndex>                 half-size, 100px grid
  render.py zoom     <clipPath> <frameIndex> x0 y0 x1 y1 [scale]
  render.py strip    <clipPath> <start> <end> <step> x0 y0 x1 y1
"""
import sys
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parent / 'frames'
OUT.mkdir(parents=True, exist_ok=True)


def readFrame(clipPath, index):
    capture = cv2.VideoCapture(str(clipPath))
    capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise SystemExit(f'frame {index} unreadable in {clipPath}')
    return frame


def grid(image, originX, originY, scale, minor, major):
    height, width = image.shape[:2]
    for x in range(originX - originX % minor, originX + int(width / scale) + minor, minor):
        px = int(round((x - originX) * scale))
        if not 0 <= px < width:
            continue
        big = x % major == 0
        cv2.line(image, (px, 0), (px, height), (0, 255, 255) if big else (70, 70, 70), 2 if big else 1)
        if big:
            cv2.putText(image, str(x), (px + 3, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (0, 255, 255), 1, cv2.LINE_AA)
    for y in range(originY - originY % minor, originY + int(height / scale) + minor, minor):
        py = int(round((y - originY) * scale))
        if not 0 <= py < height:
            continue
        big = y % major == 0
        cv2.line(image, (0, py), (width, py), (0, 255, 255) if big else (70, 70, 70), 2 if big else 1)
        if big:
            cv2.putText(image, str(y), (3, py - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (0, 255, 255), 1, cv2.LINE_AA)
    return image


def main():
    mode, clipPath = sys.argv[1], sys.argv[2]
    stem = Path(clipPath).parent.name[:24]

    if mode == 'overview':
        index = int(sys.argv[3])
        frame = readFrame(clipPath, index)
        small = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2))
        small = grid(small, 0, 0, 0.5, 50, 200)
        path = OUT / f'{stem}_f{index}_overview.png'

    elif mode == 'zoom':
        index = int(sys.argv[3])
        x0, y0, x1, y1 = (int(v) for v in sys.argv[4:8])
        scale = float(sys.argv[8]) if len(sys.argv) > 8 else 5.0
        frame = readFrame(clipPath, index)
        patch = frame[max(0, y0):y1, max(0, x0):x1]
        big = cv2.resize(patch, (int(patch.shape[1] * scale), int(patch.shape[0] * scale)),
                         interpolation=cv2.INTER_LANCZOS4)
        big = grid(big, x0, y0, scale, 10, 50)
        path, small = OUT / f'{stem}_f{index}_zoom.png', big

    elif mode == 'strip':
        start, end, step = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
        x0, y0, x1, y1 = (int(v) for v in sys.argv[6:10])
        zoom = float(sys.argv[10]) if len(sys.argv) > 10 else 2.0
        tiles = []
        for index in range(start, end + 1, step):
            frame = readFrame(clipPath, index)
            tile = frame[max(0, y0):y1, max(0, x0):x1].copy()
            tile = cv2.resize(tile, (int(tile.shape[1] * zoom), int(tile.shape[0] * zoom)),
                              interpolation=cv2.INTER_LANCZOS4)
            cv2.rectangle(tile, (0, 0), (tile.shape[1] - 1, 24), (0, 0, 0), -1)
            cv2.putText(tile, f'f{index}', (5, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 255, 255), 2, cv2.LINE_AA)
            tiles.append(tile)
        perRow = 4
        rows = []
        for begin in range(0, len(tiles), perRow):
            row = tiles[begin:begin + perRow]
            while len(row) < perRow:
                row.append(np.zeros_like(row[0]))
            rows.append(np.hstack(row))
        small = np.vstack(rows)
        path = OUT / f'{stem}_strip{start}-{end}.png'
    else:
        raise SystemExit(f'unknown mode {mode}')

    cv2.imwrite(str(path), small)
    print(path)


if __name__ == '__main__':
    main()
