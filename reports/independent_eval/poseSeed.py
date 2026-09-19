"""
Pose-seeded body points for one frame, plus a verification overlay.

This mirrors what the labeler already does: `labels.csv` provenance shows the human
took COCO pose suggestions for off_shoulder / elbow / wrist on a large share of the
training rows and corrected them by hand. The ball is `ball:hand` on every single
row -- pose never supplies it, and neither does this file.

Person choice follows phase4.detect.choosePerson exactly (bottom-most among boxes
within 80% of the largest area), which measured 99.4% correct on the 180 training
frames.

  poseSeed.py <clipPath> <frameIndex> <pThrows> [zoomPad]
"""
import json
from pathlib import Path
import sys

import cv2
import numpy as np
from ultralytics import YOLO

OUT = Path(__file__).resolve().parent / 'frames'
KEYPOINT_INDEX = {'leftShoulder': 5, 'rightShoulder': 6, 'leftElbow': 7,
                  'rightElbow': 8, 'leftWrist': 9, 'rightWrist': 10}
AREA_FRACTION = 0.8
MODEL = None


def model():
    global MODEL
    if MODEL is None:
        MODEL = YOLO('yolo11n-pose.pt')
    return MODEL


def seed(clipPath, frameIndex, pThrows):
    capture = cv2.VideoCapture(str(clipPath))
    capture.set(cv2.CAP_PROP_POS_FRAMES, int(frameIndex))
    ok, image = capture.read()
    capture.release()
    if not ok:
        raise SystemExit('unreadable frame')

    result = model().predict([image], conf=0.25, verbose=False)[0]
    if result.keypoints is None or len(result.keypoints) == 0:
        return image, None

    data = result.keypoints.data.cpu().numpy()
    boxes = result.boxes.xyxy.cpu().numpy()
    side = 'right' if (pThrows or 'R').upper().startswith('R') else 'left'
    other = 'left' if side == 'right' else 'right'

    people = []
    for i in range(len(data)):
        keypoints, box = data[i], [float(v) for v in boxes[i].tolist()]
        person = {'box': [round(v, 1) for v in box],
                  'area': round((box[2] - box[0]) * (box[3] - box[1]), 1)}
        for label, key in (('shoulder', f'{side}Shoulder'), ('elbow', f'{side}Elbow'),
                           ('wrist', f'{side}Wrist'), ('off_shoulder', f'{other}Shoulder')):
            x, y, confidence = keypoints[KEYPOINT_INDEX[key]]
            person[f'{label}_x'] = round(float(x), 2)
            person[f'{label}_y'] = round(float(y), 2)
            person[f'{label}_conf'] = round(float(confidence), 3)
        people.append(person)

    biggest = max(p['area'] for p in people)
    candidates = [p for p in people if p['area'] >= AREA_FRACTION * biggest]
    return image, max(candidates, key=lambda p: p['box'][3])


def overlay(image, person, frameIndex, stem, pad=70, scale=5):
    x0, y0, x1, y1 = person['box']
    cx0, cy0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
    cx1, cy1 = int(min(image.shape[1], x1 + pad)), int(min(image.shape[0], y1 + pad))
    patch = image[cy0:cy1, cx0:cx1].copy()
    big = cv2.resize(patch, (int(patch.shape[1] * scale), int(patch.shape[0] * scale)),
                     interpolation=cv2.INTER_LANCZOS4)

    for x in range(cx0 - cx0 % 10, cx1 + 10, 10):
        px = int((x - cx0) * scale)
        if 0 <= px < big.shape[1]:
            major = x % 50 == 0
            cv2.line(big, (px, 0), (px, big.shape[0]),
                     (0, 255, 255) if major else (70, 70, 70), 2 if major else 1)
            if major:
                cv2.putText(big, str(x), (px + 3, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                            (0, 255, 255), 1, cv2.LINE_AA)
    for y in range(cy0 - cy0 % 10, cy1 + 10, 10):
        py = int((y - cy0) * scale)
        if 0 <= py < big.shape[0]:
            major = y % 50 == 0
            cv2.line(big, (0, py), (big.shape[1], py),
                     (0, 255, 255) if major else (70, 70, 70), 2 if major else 1)
            if major:
                cv2.putText(big, str(y), (3, py - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                            (0, 255, 255), 1, cv2.LINE_AA)

    colours = {'shoulder': (0, 0, 255), 'off_shoulder': (255, 0, 255),
               'elbow': (0, 165, 255), 'wrist': (255, 255, 255)}
    for label, colour in colours.items():
        px = int((person[f'{label}_x'] - cx0) * scale)
        py = int((person[f'{label}_y'] - cy0) * scale)
        cv2.drawMarker(big, (px, py), colour, cv2.MARKER_CROSS, 34, 3)
        cv2.circle(big, (px, py), 16, colour, 2)
        cv2.putText(big, f'{label[:4]} {person[f"{label}_conf"]:.2f}', (px + 20, py - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2, cv2.LINE_AA)

    path = OUT / f'{stem}_f{frameIndex}_pose.png'
    cv2.imwrite(str(path), big)
    return path


def main():
    clipPath, frameIndex, pThrows = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    pad = int(sys.argv[4]) if len(sys.argv) > 4 else 70
    image, person = seed(clipPath, frameIndex, pThrows)
    if person is None:
        print(json.dumps({'error': 'no person detected'}))
        return
    stem = Path(clipPath).parent.name[:24]
    path = overlay(image, person, frameIndex, stem, pad=pad)
    print(json.dumps({'person': person, 'overlay': str(path)}, indent=2))


if __name__ == '__main__':
    main()
