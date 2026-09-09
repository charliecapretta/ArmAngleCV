"""
Stage A: run COCO pose over every release frame once and cache the result.

Both diagnose.py and buildDataset.py need the same person boxes, and yolo11n-pose on
this CPU is ~0.3 s/frame plus an 8 s cold load. Caching turns every downstream
experiment into a file read.

The cache stores EVERY detected person, not just the chosen one, so the box-picking
rule stays a decision made downstream rather than baked in here.

    ./.venv/bin/python phase4/detect.py            # writes phase4/detections.json
    ./.venv/bin/python phase4/detect.py --force    # re-detect even if cached
"""

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES_DIR = os.path.join(PROJECT_DIR, "releaseFrames")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "detections.json")

# COCO-17 indices, same as labelServer.KEYPOINT_INDEX.
COCO = {
    "leftShoulder": 5, "rightShoulder": 6,
    "leftElbow": 7, "rightElbow": 8,
    "leftWrist": 9, "rightWrist": 10,
}

BODY_POINTS = ["shoulder", "off_shoulder", "elbow", "wrist"]


def loadRows(limit=None):
    """Labeled pitches joined to their extracted release frame."""
    labels = pd.read_csv(os.path.join(PROJECT_DIR, "labels.csv"))
    labels = labels[labels["status"] == "labeled"].copy()

    frames = pd.read_csv(os.path.join(FRAMES_DIR, "frames.csv"))
    frames = frames[frames["is_release"] == 1][["game_pk", "play_id", "file"]]

    merged = labels.merge(frames, on=["game_pk", "play_id"], how="inner")
    merged["path"] = merged["file"].apply(lambda f: os.path.join(FRAMES_DIR, f))
    merged = merged[merged["path"].apply(os.path.exists)].reset_index(drop=True)
    return merged.head(limit) if limit else merged


def peopleOn(model, path, pThrows):
    """
    Every detected person, keypoints renamed into our five-point scheme.

    Same convention as labelServer.posePoints: the throwing side comes from p_throws,
    so `shoulder` is always the throwing shoulder and `off_shoulder` the glove side,
    matching how the hand labels are defined. Sorted largest box first -- on a
    centre-field shot the pitcher is nearest the camera.
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
        box = [round(float(v), 2) for v in boxes[index].tolist()]
        person = {"box": box,
                  "area": round((box[2] - box[0]) * (box[3] - box[1]), 1),
                  "points": {}}
        for name, cocoName in (("shoulder", f"{side}Shoulder"),
                               ("off_shoulder", f"{otherSide}Shoulder"),
                               ("elbow", f"{side}Elbow"),
                               ("wrist", f"{side}Wrist")):
            x, y, confidence = keypoints[COCO[cocoName]]
            person["points"][name] = [round(float(x), 2), round(float(y), 2),
                                      round(float(confidence), 3)]
        people.append(person)

    people.sort(key=lambda p: -p["area"])
    return people


AREA_FRACTION = 0.8


def choosePerson(people):
    """
    Pick the pitcher out of the detected people.

    "Largest box" gets it right on 97.8% of frames, and the 4 it misses are not near
    misses: feeding the wrong person's crop to the keypoint model produced a mean angle
    error of 55 degrees, and those 4 pitches alone contributed over half of the total
    squared error in the first full evaluation.

    Looking at those 4, the pitcher was the SECOND largest box every time, within 10% of
    the largest -- and always lower in the frame. That is geometry, not luck: on a
    centre-field shot the pitcher is nearest the camera, so his feet sit lower in the
    image than those of the fielders behind him, while an outfielder can still cover a
    similar number of pixels.

    So: among boxes within AREA_FRACTION of the largest, take the bottom-most. Measured
    over all 180 labeled frames, this picks the right person on 179 (99.4%). Bottom-most
    alone, ignoring area, drops to 86% -- it starts choosing the catcher and umpire, who
    are lower still. Both halves are needed.
    """
    if not people:
        return None
    biggest = max(person["area"] for person in people)
    candidates = [person for person in people
                  if person["area"] >= AREA_FRACTION * biggest]
    return max(candidates, key=lambda person: person["box"][3])


def load():
    """The cache, keyed by play_id. Raises if detect.py has not been run."""
    if not os.path.exists(CACHE_PATH):
        raise SystemExit("no phase4/detections.json -- run phase4/detect.py first")
    with open(CACHE_PATH) as handle:
        return json.load(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if os.path.exists(CACHE_PATH) and not args.force:
        print(f"{CACHE_PATH} already exists -- pass --force to redo")
        return 0

    rows = loadRows(args.limit)
    print(f"{len(rows)} release frames to detect")

    from ultralytics import YOLO
    print("loading yolo11n-pose.pt ...", flush=True)
    model = YOLO(os.path.join(PROJECT_DIR, "yolo11n-pose.pt"))

    cache = {}
    for count, (_, row) in enumerate(rows.iterrows(), start=1):
        cache[row["play_id"]] = {
            "game_pk": int(row["game_pk"]),
            "file": row["file"],
            "p_throws": row["p_throws"],
            "people": peopleOn(model, row["path"], row["p_throws"]),
        }
        if count % 40 == 0:
            print(f"  {count}/{len(rows)} frames...", flush=True)

    with open(CACHE_PATH, "w") as handle:
        json.dump(cache, handle)
    empty = sum(1 for v in cache.values() if not v["people"])
    print(f"\nwrote {CACHE_PATH}  ({len(cache)} frames, {empty} with no person)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
