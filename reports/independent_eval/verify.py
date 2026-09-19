"""
Draw a proposed 5-point annotation on its release frame so it can be checked and
corrected before being recorded.

The annotator's dominant error is not "which pixel is the wrist" -- it is arithmetic:
reading a coordinate off a grid and getting it wrong. Marking the proposed point back
onto the frame closes that loop, the way a labeler's cursor does for a human.

  verify.py <clipPath> <frameIndex> sx,sy ox,oy ex,ey wx,wy bx,by [tag]
"""
from pathlib import Path
import sys

import cv2

OUT = Path(__file__).resolve().parent / 'frames'
NAMES = ['shoulder', 'off_shoulder', 'elbow', 'wrist', 'ball']
COLOURS = {'shoulder': (0, 0, 255), 'off_shoulder': (255, 0, 255),
           'elbow': (0, 165, 255), 'wrist': (0, 255, 0), 'ball': (255, 255, 255)}


def main():
    clipPath, frameIndex = sys.argv[1], int(sys.argv[2])
    points = {}
    for name, token in zip(NAMES, sys.argv[3:8]):
        x, y = token.split(',')
        points[name] = (float(x), float(y))
    tag = sys.argv[8] if len(sys.argv) > 8 else 'v'

    capture = cv2.VideoCapture(clipPath)
    capture.set(cv2.CAP_PROP_POS_FRAMES, frameIndex)
    ok, frame = capture.read()
    capture.release()
    if not ok:
        raise SystemExit('unreadable frame')

    xs = [p[0] for p in points.values()]
    ys = [p[1] for p in points.values()]
    pad, scale = 55, 5
    x0, y0 = int(max(0, min(xs) - pad)), int(max(0, min(ys) - pad))
    x1, y1 = int(min(frame.shape[1], max(xs) + pad)), int(min(frame.shape[0], max(ys) + pad))
    patch = frame[y0:y1, x0:x1]
    big = cv2.resize(patch, (int(patch.shape[1] * scale), int(patch.shape[0] * scale)),
                     interpolation=cv2.INTER_LANCZOS4)

    for x in range(x0 - x0 % 10, x1 + 10, 10):
        px = int((x - x0) * scale)
        if 0 <= px < big.shape[1] and x % 50 == 0:
            cv2.line(big, (px, 0), (px, big.shape[0]), (0, 255, 255), 1)
            cv2.putText(big, str(x), (px + 3, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (0, 255, 255), 1, cv2.LINE_AA)
    for y in range(y0 - y0 % 10, y1 + 10, 10):
        py = int((y - y0) * scale)
        if 0 <= py < big.shape[0] and y % 50 == 0:
            cv2.line(big, (0, py), (big.shape[1], py), (0, 255, 255), 1)
            cv2.putText(big, str(y), (3, py - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (0, 255, 255), 1, cv2.LINE_AA)

    # shoulder -> ball is the measured line; draw it so a wrong point is obvious.
    sx, sy = points['shoulder']
    bx, by = points['ball']
    cv2.line(big, (int((sx - x0) * scale), int((sy - y0) * scale)),
             (int((bx - x0) * scale), int((by - y0) * scale)), (255, 255, 0), 2)
    for name, (x, y) in points.items():
        px, py = int((x - x0) * scale), int((y - y0) * scale)
        cv2.drawMarker(big, (px, py), COLOURS[name], cv2.MARKER_CROSS, 30, 2)
        cv2.circle(big, (px, py), 13, COLOURS[name], 2)
        cv2.putText(big, name[:4], (px + 16, py - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    COLOURS[name], 2, cv2.LINE_AA)

    path = OUT / f'{Path(clipPath).parent.name[:24]}_f{frameIndex}_{tag}.png'
    cv2.imwrite(str(path), big)
    print(path)


if __name__ == '__main__':
    main()
