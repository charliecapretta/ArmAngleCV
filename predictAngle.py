"""
Arm angle for one pitch, end to end, no clicking.

    play_id -> clip -> release frame -> Stage A person box -> crop
            -> keypoint model -> features.py -> Ridge -> degrees

This is the whole pipeline as a single command. The one thing it does not do is find
the release frame: that is still chosen by hand (`--frame`), and automating it is a
separate piece of work. Without `--frame` it falls back to the frame recorded in
labels.csv, which makes this runnable on any already-labeled pitch.

    ./.venv/bin/python predictAngle.py --play-id 021a4434-6043-3435-9718-c6b92b0bf8fc
    ./.venv/bin/python predictAngle.py --play-id <id> --frame 181
    ./.venv/bin/python predictAngle.py --image someFrame.jpg --p-throws R

Every coordinate goes through features.py, the same module labelServer.py uses to
derive the angles it stores, so a predicted row and a hand-clicked row cannot be
computed differently.
"""

import argparse
import csv
import os
import sys

import cv2
import numpy as np

import features
import angleInputs
import savantUtils
from phase4 import buildDataset, dataset, detect

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(PROJECT_DIR, "phase4", "keypoints.pt")
RIDGE_PATH = os.path.join(PROJECT_DIR, "phase4", "armAngleFromPredicted.joblib")
CROP_SIDE = 1.6      # multiples of person-box height; see buildDataset.py


def loadKeypointModel(path):
    import torch
    from phase4 import keypointModel

    if not os.path.exists(path):
        raise SystemExit(f"no keypoint model at {path} -- run phase4/trainFinal.py first")

    saved = torch.load(path, map_location="cpu", weights_only=False)
    model = keypointModel.ResnetKeypoints(size=saved["size"], head=saved["head"],
                                          upsample=saved["upsample"])
    model.model = model._build()
    model.model.load_state_dict(saved["stateDict"])
    model.model.eval()
    return model


def labelRow(playId):
    """The labels.csv row for this pitch, if it has one -- for --frame and p_throws."""
    path = os.path.join(PROJECT_DIR, "labels.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            if row["play_id"] == playId:
                return row
    return None


def frameFromClip(gamePk, playId, frameIndex):
    """Decode one frame out of the stored clip, reading forward to it."""
    clipPath = os.path.join(savantUtils.PITCHES_DIR, f"{gamePk}_{playId}", "clip.mp4")
    if not os.path.exists(clipPath):
        raise SystemExit(f"no clip at {clipPath}")

    capture = cv2.VideoCapture(clipPath)
    image, index = None, 0
    while index <= frameIndex:
        ok, current = capture.read()
        if not ok:
            break
        if index == frameIndex:
            image = current
        index += 1
    capture.release()
    if image is None:
        raise SystemExit(f"frame {frameIndex} not in clip")
    return image


def keypointsFor(image, pThrows, model, poseModel):
    """Stage A box -> crop -> keypoints, returned in full-frame pixels."""
    people = detect.peopleOn(poseModel, image, pThrows)
    if not people:
        raise SystemExit("no person detected in the frame")

    box = detect.choosePerson(people)["box"]    # right on 99.4% of frames
    boxHeight = max(box[3] - box[1], 1.0)
    centreX, centreY = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0

    patch, x0, y0, side = buildDataset.cropSquare(
        image, centreX, centreY, CROP_SIDE * boxHeight, 384)

    crop = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)[None]
    normalized = model.predict(crop)
    pixels = dataset.toPixels(normalized, np.array([[x0, y0, side]], dtype=np.float32))
    return pixels[0], box


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--play-id")
    parser.add_argument("--frame", type=int)
    parser.add_argument("--image", help="predict on a jpg directly instead of a clip")
    parser.add_argument("--p-throws", default=None)
    parser.add_argument("--model", default=MODEL_PATH)
    parser.add_argument("--ridge", default=RIDGE_PATH)
    args = parser.parse_args()

    from joblib import load
    from ultralytics import YOLO

    model = loadKeypointModel(args.model)
    poseModel = YOLO(os.path.join(PROJECT_DIR, "yolo11n-pose.pt"))

    row = labelRow(args.play_id) if args.play_id else None
    pThrows = args.p_throws or (row["p_throws"] if row else "R")

    if args.image:
        image = cv2.imread(args.image)
        if image is None:
            raise SystemExit(f"could not read {args.image}")
    else:
        if not args.play_id:
            raise SystemExit("need --play-id or --image")
        frameIndex = args.frame if args.frame is not None else (
            int(row["frame_index"]) if row and row.get("frame_index") else None)
        if frameIndex is None:
            raise SystemExit("no --frame given and none recorded in labels.csv")
        if not row:
            raise SystemExit("play_id is not in labels.csv, so the clip's game_pk is "
                             "unknown -- use --image on an extracted frame instead")
        image = frameFromClip(row["game_pk"], args.play_id, frameIndex)

    pixels, box = keypointsFor(image, pThrows, model, poseModel)
    coords = dataset.coordDict(pixels)
    coords.update(features.deriveAngles(coords))

    saved = load(args.ridge) if os.path.exists(args.ridge) else None
    coords.update(img_w=image.shape[1], img_h=image.shape[0])
    angle = float(angleInputs.predict(saved, [coords])[0]) if saved else None

    print(f"\nperson box      {[round(v, 1) for v in box]}")
    print("keypoints (full-frame px):")
    for name in features.KEYPOINT_NAMES:
        print(f"  {name:<14}{coords[f'{name}_x']:>8.1f}{coords[f'{name}_y']:>8.1f}")
    print(f"\nball_angle (raw geometry)   {coords['ball_angle']:+7.2f} deg")
    if angle is not None:
        print(f"arm_angle  (model)          {angle:+7.2f} deg")
    else:
        print("no Ridge found -- run phase4/trainFinal.py for a corrected angle")
    if row and row.get("true_arm_angle"):
        truth = float(row["true_arm_angle"])
        shown = angle if angle is not None else coords["ball_angle"]
        print(f"Statcast arm_angle          {truth:+7.2f} deg    "
              f"error {shown - truth:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
