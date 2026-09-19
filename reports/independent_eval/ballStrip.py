"""
Ball-search strip: the release frame and the frames just after it, centred on the
pose wrist.

At release the ball sits against a motion-blurred hand and is often invisible. A few
frames later the hand has followed through downward while the ball -- travelling
almost straight away from a centre-field camera -- has barely moved in the image.
That makes it an isolated bright dot near where it was released, which is far easier
to place than anything visible in the release frame itself.

  ballStrip.py <clipPath> <releaseFrame> <wristX> <wristY> [span] [step] [zoom]
"""
from pathlib import Path
import sys

import cv2
import numpy as np

OUT = Path(__file__).resolve().parent / 'frames'


def main():
    clipPath, release = sys.argv[1], int(sys.argv[2])
    wx, wy = float(sys.argv[3]), float(sys.argv[4])
    span = int(sys.argv[5]) if len(sys.argv) > 5 else 10
    step = int(sys.argv[6]) if len(sys.argv) > 6 else 2
    zoom = float(sys.argv[7]) if len(sys.argv) > 7 else 4.0

    x0, x1 = int(wx - 110), int(wx + 110)
    y0, y1 = int(wy - 90), int(wy + 90)

    capture = cv2.VideoCapture(str(clipPath))
    tiles = []
    for index in range(release - 2, release + span + 1, step):
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        cx0, cy0 = max(0, x0), max(0, y0)
        patch = frame[cy0:min(frame.shape[0], y1), cx0:min(frame.shape[1], x1)].copy()
        tile = cv2.resize(patch, (int(patch.shape[1] * zoom), int(patch.shape[0] * zoom)),
                          interpolation=cv2.INTER_LANCZOS4)
        for x in range(cx0 - cx0 % 10, x1 + 10, 10):
            px = int((x - cx0) * zoom)
            if 0 <= px < tile.shape[1]:
                major = x % 50 == 0
                cv2.line(tile, (px, 0), (px, tile.shape[0]),
                         (0, 255, 255) if major else (60, 60, 60), 2 if major else 1)
                if major:
                    cv2.putText(tile, str(x), (px + 2, 30), cv2.FONT_HERSHEY_SIMPLEX,
                                0.45, (0, 255, 255), 1, cv2.LINE_AA)
        for y in range(cy0 - cy0 % 10, y1 + 10, 10):
            py = int((y - cy0) * zoom)
            if 0 <= py < tile.shape[0]:
                major = y % 50 == 0
                cv2.line(tile, (0, py), (tile.shape[1], py),
                         (0, 255, 255) if major else (60, 60, 60), 2 if major else 1)
                if major:
                    cv2.putText(tile, str(y), (2, py - 4), cv2.FONT_HERSHEY_SIMPLEX,
                                0.45, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.rectangle(tile, (0, 0), (tile.shape[1] - 1, 20), (0, 0, 0), -1)
        cv2.putText(tile, f'f{index}{"  <-RELEASE" if index == release else ""}',
                    (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2, cv2.LINE_AA)
        tiles.append(tile)
    capture.release()

    perRow = 3
    rows = []
    for begin in range(0, len(tiles), perRow):
        row = tiles[begin:begin + perRow]
        while len(row) < perRow:
            row.append(np.zeros_like(row[0]))
        rows.append(np.hstack(row))
    path = OUT / f'{Path(clipPath).parent.name[:24]}_ball{release}.png'
    cv2.imwrite(str(path), np.vstack(rows))
    print(path)


if __name__ == '__main__':
    main()
