"""
Run the Phase 4 pipeline over an arbitrary video and draw what it saw.

predictAngle.py is the pipeline for MLB clips shot from the centre-field camera, where
detect.choosePerson reliably finds the pitcher. On any other footage that rule is not
safe -- it is built around "on a centre-field shot the pitcher is nearest the camera" --
so this script lets you say which person is the pitcher with --anchor, and always writes
an annotated frame so the keypoints can be judged by eye rather than trusted.

    ./.venv/bin/python analyzeVideo.py --video reel.mp4 --frames 402,403,404 \
        --anchor 1000,600 --p-throws R --out overlays

The angle it prints is only meaningful if the camera looks along the pitcher->home axis.
Statcast's arm_angle lives in the x-z plane (across-field x height); a centre-field
camera images that plane, which is why pixel geometry works there at all. Footage shot
from the side images the y-z plane instead and measures something else entirely.
"""

import argparse
import os
import sys

import cv2
import numpy as np

import features
import angleInputs
from phase4 import buildDataset, dataset, detect

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CROP_SIDE = 1.6

COLORS = {"shoulder": (255, 80, 80), "off_shoulder": (80, 160, 255),
          "elbow": (80, 255, 120), "wrist": (255, 220, 60), "ball": (255, 60, 255)}


def pickPerson(people, anchor):
    """Nearest box centre to the anchor, or the project's default rule when none given."""
    if anchor is None:
        return detect.choosePerson(people)
    ax, ay = anchor
    return min(people, key=lambda p: (((p["box"][0] + p["box"][2]) / 2 - ax) ** 2
                                      + ((p["box"][1] + p["box"][3]) / 2 - ay) ** 2))


def analyseFrame(image, pThrows, model, poseModel, anchor):
    people = detect.peopleOn(poseModel, image, pThrows)
    if not people:
        return None
    person = pickPerson(people, anchor)
    box = person["box"]
    boxHeight = max(box[3] - box[1], 1.0)
    centreX, centreY = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0

    patch, x0, y0, side = buildDataset.cropSquare(
        image, centreX, centreY, CROP_SIDE * boxHeight, 384)
    crop = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)[None]
    pixels = dataset.toPixels(model.predict(crop),
                              np.array([[x0, y0, side]], dtype=np.float32))[0]

    coords = dataset.coordDict(pixels)
    coords.update(features.deriveAngles(coords))
    return {"box": box, "pixels": pixels, "coords": coords,
            "crop": patch, "origin": (x0, y0, side)}


def draw(image, result, label):
    out = image.copy()
    b = [int(v) for v in result["box"]]
    cv2.rectangle(out, (b[0], b[1]), (b[2], b[3]), (200, 200, 200), 2)

    points = result["pixels"]
    shoulder = tuple(int(v) for v in points[0])
    ball = tuple(int(v) for v in points[4])
    cv2.line(out, shoulder, ball, (255, 255, 255), 3)
    for index, name in enumerate(features.KEYPOINT_NAMES):
        p = tuple(int(v) for v in points[index])
        cv2.circle(out, p, 9, COLORS[name], -1)
        cv2.circle(out, p, 9, (0, 0, 0), 2)

    for offset, (text, color) in enumerate([(label, (255, 255, 255))]):
        cv2.putText(out, text, (20, 50 + offset * 46), cv2.FONT_HERSHEY_SIMPLEX,
                    1.3, (0, 0, 0), 7)
        cv2.putText(out, text, (20, 50 + offset * 46), cv2.FONT_HERSHEY_SIMPLEX,
                    1.3, color, 2)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--frames", required=True, help="comma-separated frame indices")
    parser.add_argument("--anchor", help="x,y of the pitcher, to override Stage A")
    parser.add_argument("--p-throws", default="R")
    parser.add_argument("--model", default=os.path.join(PROJECT_DIR, "phase4", "keypoints.pt"))
    parser.add_argument("--ridge", default=os.path.join(PROJECT_DIR, "armAngleModel.joblib"))
    parser.add_argument("--out", default="overlays")
    args = parser.parse_args()

    from joblib import load
    from ultralytics import YOLO
    from predictAngle import loadKeypointModel

    wanted = [int(v) for v in args.frames.split(",")]
    anchor = tuple(float(v) for v in args.anchor.split(",")) if args.anchor else None

    model = loadKeypointModel(args.model)
    poseModel = YOLO(os.path.join(PROJECT_DIR, "yolo11n-pose.pt"))
    ridge = load(args.ridge) if os.path.exists(args.ridge) else None

    capture = cv2.VideoCapture(args.video)
    frames, index = {}, 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index in wanted:
            frames[index] = frame
        index += 1
    capture.release()

    os.makedirs(args.out, exist_ok=True)
    print(f"{'frame':>7}{'ball_angle':>12}{'ridge':>10}   shoulder->ball baseline")
    angles = []
    for number in wanted:
        if number not in frames:
            print(f"{number:>7}   not in video")
            continue
        result = analyseFrame(frames[number], args.p_throws, model, poseModel, anchor)
        if result is None:
            print(f"{number:>7}   no person detected")
            continue

        raw = float(result["coords"]["ball_angle"])
        corrected = None
        if ridge is not None:
            inputs = dict(result["coords"], img_w=frames[number].shape[1], img_h=frames[number].shape[0])
            corrected = float(angleInputs.predict(ridge, [inputs])[0])
            angles.append(corrected)

        baseline = float(np.hypot(*(result["pixels"][4] - result["pixels"][0])))
        shown = f"{corrected:>10.1f}" if corrected is not None else f"{'--':>10}"
        print(f"{number:>7}{raw:>12.1f}{shown}   {baseline:.0f} px")

        path = os.path.join(args.out, f"frame{number:05d}.jpg")
        cv2.imwrite(path, draw(frames[number], result,
                               f"f{number}  ball_angle {raw:+.1f}"
                               + (f"   ridge {corrected:+.1f}" if corrected else "")))

    if angles:
        print(f"\nmean over {len(angles)} frames: {np.mean(angles):+.1f} deg "
              f"(spread {np.std(angles):.1f})")
    print(f"overlays -> {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
