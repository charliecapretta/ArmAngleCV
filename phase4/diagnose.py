"""
Phase 4, step 0: measure what off-the-shelf pose already gives you, before training.

Three questions, all answerable without fitting anything, and the answers decide how
much of Phase 4 is actually needed:

  1. Does Stage A find the pitcher? The label tool picks the largest COCO person box on
     the reasoning that the pitcher is nearest the centre-field camera. If that rule
     misses, no amount of keypoint training helps -- the crop would be wrong.

  2. How far off is COCO pose on the four body points? This is the bar a fine-tune has
     to beat. If COCO is already within ~2 px, the only thing left to learn is the ball.

  3. What does the error budget look like split by keypoint? Feed COCO body points plus
     the hand-clicked ball through the existing Ridge. The gap between that and 2.651
     is what the body points cost you; whatever remains is the ball's problem.

Context for reading the numbers: median shoulder->ball baseline is 105 px, so
1 px of keypoint error is worth about 0.54 degrees.

    ./.venv/bin/python phase4/diagnose.py
    ./.venv/bin/python phase4/diagnose.py --limit 20     # quick smoke test
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from armangle import features
from armangle.AnglePredictor import cross_val_predictions, make_model, score  # noqa: F401

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES_DIR = os.path.join(PROJECT_DIR, "releaseFrames")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diagnostics.json")

# COCO-17 indices, same as labelServer.KEYPOINT_INDEX.
COCO = {
    "leftShoulder": 5, "rightShoulder": 6,
    "leftElbow": 7, "rightElbow": 8,
    "leftWrist": 9, "rightWrist": 10,
}

BODY_POINTS = ["shoulder", "off_shoulder", "elbow", "wrist"]


def loadRows(limit=None):
    """Labeled pitches joined to their extracted release frame."""
    labels = pd.read_csv(os.path.join(PROJECT_DIR, "data", "labels.csv"))
    labels = labels[labels["status"] == "labeled"].copy()

    frames = pd.read_csv(os.path.join(FRAMES_DIR, "frames.csv"))
    frames = frames[frames["is_release"] == 1][["game_pk", "play_id", "file"]]

    merged = labels.merge(frames, on=["game_pk", "play_id"], how="inner")
    merged["path"] = merged["file"].apply(lambda f: os.path.join(FRAMES_DIR, f))
    merged = merged[merged["path"].apply(os.path.exists)]
    return merged.head(limit) if limit else merged


def cocoPeople(model, path, pThrows):
    """
    Every detected person on the frame, keypoints renamed into our five-point scheme.

    Same selection logic as labelServer.posePoints: the throwing side comes from
    p_throws, so `shoulder` is always the throwing shoulder and `off_shoulder` the
    glove side, exactly as the hand labels are defined.
    """
    result = model.predict([path], conf=0.25, verbose=False)[0]
    if result.keypoints is None or len(result.keypoints) == 0:
        return []

    keypointData = result.keypoints.data.cpu().numpy()
    boxes = result.boxes.xyxy.cpu().numpy()

    side = "right" if (pThrows or "R").upper().startswith("R") else "left"
    otherSide = "left" if side == "right" else "right"

    people = []
    for index in range(len(keypointData)):
        keypoints = keypointData[index]
        box = boxes[index].tolist()
        person = {"box": box, "area": (box[2] - box[0]) * (box[3] - box[1]), "points": {}}
        for name, cocoName in (("shoulder", f"{side}Shoulder"),
                               ("off_shoulder", f"{otherSide}Shoulder"),
                               ("elbow", f"{side}Elbow"),
                               ("wrist", f"{side}Wrist")):
            x, y, confidence = keypoints[COCO[cocoName]]
            person["points"][name] = (float(x), float(y), float(confidence))
        people.append(person)

    people.sort(key=lambda p: -p["area"])
    return people


def boxContains(box, x, y, pad=0.0):
    width, height = box[2] - box[0], box[3] - box[1]
    return (box[0] - pad * width <= x <= box[2] + pad * width
            and box[1] - pad * height <= y <= box[3] + pad * height)


def summarise(name, errors):
    errors = np.asarray(errors, dtype=float)
    if not len(errors):
        print(f"  {name:<16} no detections")
        return None
    median, p90 = float(np.median(errors)), float(np.percentile(errors, 90))
    print(f"  {name:<16} n={len(errors):<4} median {median:6.1f} px "
          f"({median * 0.54:5.2f} deg)   p90 {p90:6.1f} px")
    return {"n": len(errors), "median_px": median, "p90_px": p90}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    rows = loadRows(args.limit)
    print(f"{len(rows)} labeled pitches with an extracted release frame\n")

    from ultralytics import YOLO
    print("loading yolo11n-pose.pt ...", flush=True)
    model = YOLO(os.path.join(PROJECT_DIR, "yolo11n-pose.pt"))

    report = {}
    detected, largestOk, anyBoxOk, largestIsBest = 0, 0, 0, 0
    misses = []
    pointErrors = {name: [] for name in BODY_POINTS}
    padNeeded = []
    cocoRows = []

    for count, (_, row) in enumerate(rows.iterrows(), start=1):
        people = cocoPeople(model, row["path"], row["p_throws"])
        if not people:
            misses.append({"play_id": row["play_id"], "why": "no person detected"})
            continue
        detected += 1

        clicked = {name: (float(row[f"{name}_x"]), float(row[f"{name}_y"]))
                   for name in BODY_POINTS + ["ball"]}

        # Q1: is the largest box the PITCHER? Judged on the shoulder alone, because
        # that is the only point guaranteed to be on the torso. At release the throwing
        # arm is fully extended and YOLO's person box clips it, so the wrist and ball
        # routinely fall outside a box that has correctly found the pitcher. Scoring
        # those as misses confuses "wrong person" with "box too tight" -- one needs a
        # better selection rule, the other just needs a bigger crop, measured below.
        needed = [clicked["shoulder"]]
        largest = people[0]
        if all(boxContains(largest["box"], x, y) for x, y in needed):
            largestOk += 1
        else:
            misses.append({"play_id": row["play_id"],
                           "why": "largest box is not the pitcher",
                           "box": [round(v, 1) for v in largest["box"]],
                           "clicked": {k: [round(v, 1) for v in xy]
                                       for k, xy in clicked.items()}})

        containing = [p for p in people
                      if all(boxContains(p["box"], x, y) for x, y in needed)]
        if containing:
            anyBoxOk += 1
            if containing[0] is largest:
                largestIsBest += 1

        # The person we would actually crop from: the largest box when it is right,
        # otherwise the one that is. Using this for Q2 separates COCO's keypoint error
        # from Stage A's selection error -- two different problems with two different fixes.
        chosen = containing[0] if containing else largest

        # How much must that box grow to hold all five clicked points? This sets the
        # crop padding in buildDataset.py.
        box = chosen["box"]
        width, height = max(box[2] - box[0], 1.0), max(box[3] - box[1], 1.0)
        # Expressed the way buildDataset.py will parameterise it: a SQUARE crop centred
        # on the person box, with side = k * boxHeight. Report the smallest k that holds
        # all five points.
        centreX, centreY = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        reach = max(max(abs(x - centreX), abs(y - centreY)) for x, y in clicked.values())
        padNeeded.append(2 * reach / height)

        # Q2: how far off is COCO on the body points?
        for name in BODY_POINTS:
            x, y, confidence = chosen["points"][name]
            if confidence <= 0.0:
                continue
            clickedX, clickedY = clicked[name]
            pointErrors[name].append(float(np.hypot(x - clickedX, y - clickedY)))

        # Q3: COCO body points + hand-clicked ball, as a feature row.
        coords = {"ball_x": clicked["ball"][0], "ball_y": clicked["ball"][1]}
        usable = True
        for name in BODY_POINTS:
            x, y, confidence = chosen["points"][name]
            if confidence <= 0.0:
                usable = False
                break
            coords[f"{name}_x"], coords[f"{name}_y"] = x, y
        if usable:
            coords.update(features.deriveAngles(coords))
            coords[features.TARGET_COL] = row[features.TARGET_COL]
            coords[features.GROUP_COL] = row[features.GROUP_COL]
            coords["park"] = row["park"]
            cocoRows.append(coords)

        if count % 40 == 0:
            print(f"  {count}/{len(rows)} frames...", flush=True)

    total = len(rows)
    print("\n=== Q1: DOES STAGE A FIND THE PITCHER? ===")
    print(f"  a person was detected at all        {detected}/{total} "
          f"({100 * detected / total:.1f}%)")
    print(f"  largest box holds the shoulder      {largestOk}/{total} "
          f"({100 * largestOk / total:.1f}%)   <- Stage A hit rate, target >=95%")
    print(f"  SOME box holds them                 {anyBoxOk}/{total} "
          f"({100 * anyBoxOk / total:.1f}%)")
    if anyBoxOk:
        print(f"  ...and it was the largest           {largestIsBest}/{anyBoxOk}"
              f"   <- shortfall here is what a better box-picking rule could recover")
    if padNeeded:
        pads = np.asarray(padNeeded)
        print("\n  square crop side (as a multiple of person-box height) needed to "
              "enclose all 5 points:")
        print(f"    median {np.median(pads):.2f}   p90 {np.percentile(pads, 90):.2f}   "
              f"max {pads.max():.2f}")
        for factor in (0.8, 1.0, 1.2, 1.4, 1.6):
            print(f"    side = {factor:.1f} x boxHeight encloses all 5 on "
                  f"{100 * (pads <= factor).mean():5.1f}% of pitches")
    report["stageA"] = {"total": total, "detected": detected, "largest_ok": largestOk,
                        "any_box_ok": anyBoxOk, "largest_is_best": largestIsBest,
                        "pad_needed_p90": float(np.percentile(padNeeded, 90)) if padNeeded else None}

    print("\n=== Q2: COCO POSE ERROR vs HAND CLICKS (largest box) ===")
    print("  1 px ~ 0.54 deg at the median 105 px shoulder->ball baseline")
    report["cocoError"] = {name: summarise(name, errs) for name, errs in pointErrors.items()}

    print("\n=== Q3: COCO BODY + HAND BALL, THROUGH THE RIDGE ===")
    if len(cocoRows) < 30:
        print(f"  only {len(cocoRows)} usable rows -- not enough to score")
    else:
        frame = pd.DataFrame(cocoRows).dropna(subset=features.FEATURE_COLS)
        frame = frame[frame[features.FEATURE_COLS].apply(
            lambda column: column != "").all(axis=1)]
        for column in features.FEATURE_COLS:
            frame[column] = frame[column].astype(float)
        nPitchers = frame[features.GROUP_COL].nunique()
        splits = min(8, nPitchers)
        predictions = cross_val_predictions(frame, splits)
        y = frame[features.TARGET_COL].values
        print(f"  {len(frame)} pitches, {nPitchers} pitchers, GroupKFold {splits} folds")
        score("COCO body + hand ball -> Ridge", y, predictions)
        score("  same rows, raw ball_angle", y, frame["ball_angle"].values)
        print("  compare: hand-clicked everything -> Ridge   RMSE  2.651   MAE  2.135")
        report["cocoRidge"] = {"n": len(frame), "pitchers": int(nPitchers)}

    if misses:
        print(f"\n=== {len(misses)} STAGE A FAILURES (first 10) ===")
        for miss in misses[:10]:
            print(f"  {miss['play_id'][:8]}  {miss['why']}")
    report["misses"] = misses

    with open(OUT_PATH, "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"\nwrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
