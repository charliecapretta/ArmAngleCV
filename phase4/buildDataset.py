"""
Phase 4, step 2: turn labels.csv + releaseFrames/ into crops and normalized targets.

The crop is derived from the DETECTOR's person box, never from the hand-clicked
keypoints. That distinction is the whole ballgame: deriving the crop from labels at
training time and from the detector at inference time is the classic top-down pose bug.
It produces excellent validation numbers and a system that does not work, because the
model quietly learns "the keypoints are near the middle of the crop" -- which is true
only when you built the crop from the keypoints.

Geometry, measured in diagnose.py over all 180 frames:
  - detect.choosePerson finds the pitcher on 99.4% of frames
  - a square crop centred on that box with side = 1.6 x boxHeight encloses all five
    clicked points on 99.4% of frames (median box height 237 px -> ~380 px crop)

Crops are written at 384 px, which is ~1:1 with that median crop, so no detail is
thrown away here. train.py can downscale to whatever input size it wants; making that
a training flag rather than a baked-in choice means trying 224 vs 320 does not require
rebuilding the dataset.

    ./.venv/bin/python phase4/buildDataset.py
    ./.venv/bin/python phase4/buildDataset.py --side 1.6 --out phase4/crops.npz
"""

import argparse
import os
import sys

import cv2
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import features
from phase4 import detect

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_NPZ = os.path.join(HERE, "crops.npz")
OUT_INDEX = os.path.join(HERE, "index.csv")


def cropSquare(image, centreX, centreY, side, outSize):
    """
    Square crop centred on (centreX, centreY), zero-padded where it leaves the frame.

    Padding rather than sliding the box back inside the frame: sliding would break the
    invariant that the pitcher sits at the crop centre, which is the one thing the model
    can rely on. Black borders are an honest "nothing here" signal instead.
    """
    x0, y0 = centreX - side / 2.0, centreY - side / 2.0
    side = int(round(side))
    x0i, y0i = int(round(x0)), int(round(y0))

    height, width = image.shape[:2]
    padLeft, padTop = max(0, -x0i), max(0, -y0i)
    padRight, padBottom = max(0, x0i + side - width), max(0, y0i + side - height)

    patch = image[max(0, y0i):min(height, y0i + side),
                  max(0, x0i):min(width, x0i + side)]
    if padLeft or padTop or padRight or padBottom:
        patch = cv2.copyMakeBorder(patch, padTop, padBottom, padLeft, padRight,
                                   cv2.BORDER_CONSTANT, value=(0, 0, 0))

    interpolation = cv2.INTER_AREA if side > outSize else cv2.INTER_LINEAR
    return cv2.resize(patch, (outSize, outSize), interpolation=interpolation), x0i, y0i, side


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--side", type=float, default=1.6,
                        help="crop side as a multiple of the person-box height")
    parser.add_argument("--size", type=int, default=384, help="stored crop resolution")
    parser.add_argument("--out", default=OUT_NPZ)
    parser.add_argument("--index", default=OUT_INDEX)
    args = parser.parse_args()

    cache = detect.load()
    labels = pd.read_csv(os.path.join(PROJECT_DIR, "data", "labels.csv"))
    labels = labels[labels["status"] == "labeled"]

    frames = pd.read_csv(os.path.join(PROJECT_DIR, "releaseFrames", "frames.csv"))
    frames = frames[frames["is_release"] == 1][["game_pk", "play_id", "file"]]
    labels = labels.merge(frames, on=["game_pk", "play_id"], how="inner")

    images, targets, transforms, index = [], [], [], []
    skipped, outside = 0, 0

    for _, row in labels.iterrows():
        entry = cache.get(row["play_id"])
        if not entry or not entry["people"]:
            skipped += 1
            continue

        path = os.path.join(PROJECT_DIR, "releaseFrames", row["file"])
        image = cv2.imread(path)
        if image is None:
            skipped += 1
            continue

        # Detector-only selection: exactly what inference will have. No ground truth.
        person = detect.choosePerson(entry["people"])
        box = person["box"]
        boxHeight = max(box[3] - box[1], 1.0)
        centreX, centreY = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0

        patch, x0, y0, side = cropSquare(image, centreX, centreY,
                                         args.side * boxHeight, args.size)

        clicked = np.array([[float(row[f"{name}_x"]), float(row[f"{name}_y"])]
                            for name in features.KEYPOINT_NAMES], dtype=np.float32)
        normalized = (clicked - np.array([x0, y0], dtype=np.float32)) / side

        allInside = bool(((normalized >= 0) & (normalized <= 1)).all())
        outside += (not allInside)

        # Ground truth is used HERE only to record whether Stage A found the pitcher,
        # for the diagnostics table. It never influences the crop.
        shoulderX, shoulderY = clicked[0]
        stageAOk = bool(box[0] <= shoulderX <= box[2] and box[1] <= shoulderY <= box[3])

        images.append(cv2.cvtColor(patch, cv2.COLOR_BGR2RGB))
        targets.append(normalized)
        transforms.append([x0, y0, side])
        index.append({
            "play_id": row["play_id"], "game_pk": row["game_pk"],
            "pitcher_id": row[features.GROUP_COL],
            "pitcher_name": row["pitcher_name"],
            "park": row["park"], "p_throws": row["p_throws"],
            features.TARGET_COL: row[features.TARGET_COL],
            "ball_angle_hand": row["ball_angle"],
            "stage_a_ok": int(stageAOk), "all_inside": int(allInside),
        })

    images = np.asarray(images, dtype=np.uint8)
    targets = np.asarray(targets, dtype=np.float32)
    transforms = np.asarray(transforms, dtype=np.float32)

    np.savez_compressed(args.out, images=images, targets=targets, transforms=transforms,
                        keypointNames=np.array(features.KEYPOINT_NAMES))
    pd.DataFrame(index).to_csv(args.index, index=False)

    frame = pd.DataFrame(index)
    print(f"{len(images)} crops at {args.size}px, side = {args.side} x boxHeight")
    print(f"  {frame['pitcher_id'].nunique()} pitchers, {frame['park'].nunique()} parks")
    print(f"  Stage A found the pitcher on {frame['stage_a_ok'].sum()}/{len(frame)}")
    print(f"  all 5 points inside the crop on {frame['all_inside'].sum()}/{len(frame)}")
    if skipped:
        print(f"  {skipped} skipped (no detection or unreadable frame)")
    print(f"  targets in [0,1]: {targets.min():.3f} .. {targets.max():.3f}")
    print(f"\nwrote {args.out} ({os.path.getsize(args.out) / 1e6:.0f} MB)")
    print(f"wrote {args.index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
