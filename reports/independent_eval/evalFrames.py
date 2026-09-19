"""
Viewing aids for hand-annotating the independent evaluation batch.

Nothing here produces a label. It decodes frames and draws grids so a human (or an
annotating agent) can read pixel coordinates off an enlarged crop instead of
eyeballing a 1280x720 still. Coordinates written by the annotator are always in
ORIGINAL frame pixels; the grid labels are what makes that possible.

  strip  <evalId> --start N --end N [--step N] [--box x0 y0 x1 y1]
  frame  <evalId> <frameIndex> [--grid]
  zoom   <evalId> <frameIndex> --box x0 y0 x1 y1 [--scale N]
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parent
WORK = OUT / 'frames'


def loadSet():
    return {p['evalId']: p for p in json.loads((OUT / 'evalSet.json').read_text())['pitches']}


def readFrames(clipPath, indices):
    capture = cv2.VideoCapture(str(clipPath))
    wanted, got = sorted(set(indices)), {}
    for index in wanted:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if ok:
            got[index] = frame
    capture.release()
    return got


def drawGrid(image, originX, originY, scale, minor=10, major=50):
    """Grid lines labelled in ORIGINAL frame coordinates."""
    height, width = image.shape[:2]
    startX = originX - (originX % minor)
    startY = originY - (originY % minor)
    for x in range(startX, originX + int(width / scale) + minor, minor):
        px = int((x - originX) * scale)
        if not 0 <= px < width:
            continue
        isMajor = x % major == 0
        cv2.line(image, (px, 0), (px, height),
                 (0, 255, 255) if isMajor else (60, 60, 60), 2 if isMajor else 1)
        if isMajor:
            cv2.putText(image, str(x), (px + 3, 18), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 255, 255), 1, cv2.LINE_AA)
    for y in range(startY, originY + int(height / scale) + minor, minor):
        py = int((y - originY) * scale)
        if not 0 <= py < height:
            continue
        isMajor = y % major == 0
        cv2.line(image, (0, py), (width, py),
                 (0, 255, 255) if isMajor else (60, 60, 60), 2 if isMajor else 1)
        if isMajor:
            cv2.putText(image, str(y), (3, py - 4), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (0, 255, 255), 1, cv2.LINE_AA)
    return image


def cropAndScale(frame, box, scale):
    x0, y0, x1, y1 = [int(v) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(frame.shape[1], x1), min(frame.shape[0], y1)
    patch = frame[y0:y1, x0:x1]
    big = cv2.resize(patch, (int((x1 - x0) * scale), int((y1 - y0) * scale)),
                     interpolation=cv2.INTER_LANCZOS4)
    return big, x0, y0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['strip', 'frame', 'zoom'])
    parser.add_argument('evalId')
    parser.add_argument('frameIndex', nargs='?', type=int)
    parser.add_argument('--start', type=int, default=165)
    parser.add_argument('--end', type=int, default=195)
    parser.add_argument('--step', type=int, default=2)
    parser.add_argument('--box', type=int, nargs=4)
    parser.add_argument('--scale', type=float, default=4.0)
    parser.add_argument('--grid', action='store_true')
    args = parser.parse_args()

    pitch = loadSet()[args.evalId]
    clip = OUT / pitch['clipPath']
    WORK.mkdir(parents=True, exist_ok=True)

    if args.command == 'strip':
        indices = list(range(args.start, args.end + 1, args.step))
        frames = readFrames(clip, indices)
        tiles = []
        for index in indices:
            if index not in frames:
                continue
            tile = frames[index]
            if args.box:
                tile, _, _ = cropAndScale(tile, args.box, 2.0)
            cv2.rectangle(tile, (0, 0), (tile.shape[1] - 1, 26), (0, 0, 0), -1)
            cv2.putText(tile, f'f{index}', (5, 19), cv2.FONT_HERSHEY_SIMPLEX,
                        0.65, (0, 255, 255), 2, cv2.LINE_AA)
            tiles.append(tile)
        perRow = 4
        rows = []
        for start in range(0, len(tiles), perRow):
            row = tiles[start:start + perRow]
            while len(row) < perRow:
                row.append(np.zeros_like(row[0]))
            rows.append(np.hstack(row))
        sheet = np.vstack(rows)
        path = WORK / f'{args.evalId}_strip_{args.start}-{args.end}.png'
        cv2.imwrite(str(path), sheet)
        print(path)
        return

    frames = readFrames(clip, [args.frameIndex])
    frame = frames.get(args.frameIndex)
    if frame is None:
        raise SystemExit(f'frame {args.frameIndex} unreadable')

    if args.command == 'frame':
        image = frame.copy()
        if args.grid:
            image = drawGrid(image, 0, 0, 1.0, minor=20, major=100)
        path = WORK / f'{args.evalId}_f{args.frameIndex}.png'
    else:
        image, x0, y0 = cropAndScale(frame, args.box, args.scale)
        image = drawGrid(image, x0, y0, args.scale)
        path = WORK / f'{args.evalId}_f{args.frameIndex}_zoom.png'
    cv2.imwrite(str(path), image)
    print(path)


if __name__ == '__main__':
    main()
