"""
Pull out just the release frames you already identified.

labels.csv records confirmed releases, including those awaiting point labels, so nothing
needs detecting -- this seeks straight to that frame and writes it out.

Why bother: the 184 clips take ~887 MB. One frame each is ~42 MB, a 20x saving.
And an image set is what CVAT actually wants: 180 images is one task, whereas
184 videos would be 184 tasks.

    python scripts/extractReleaseFrames.py                # release frame only
    python scripts/extractReleaseFrames.py --window 2     # release frame +/- 2, for margin

Produces:
    releaseFrames/<game_pk>_<play_id>_f<frame>.jpg
    releaseFrames/frames.csv     one row per image, with pitcher/park/true angle
"""

import argparse
import csv
import json
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

import savantUtils
from labelServer import clipPathFor

OUT_DIR = os.path.join(savantUtils.PROJECT_DIR, "releaseFrames")


def readLabels(path):
    with open(path, newline="") as handle:
        return [r for r in csv.DictReader(handle)
                if r.get("status") in ("labeled", "release_selected", "occluded")
                and r.get("frame_index") not in (None, "")]


def grabFrames(clipPath, wanted):
    """
    Read the clip once and keep only the frames asked for.

    Sequential reading beats seeking per frame: CAP_PROP_POS_FRAMES on H.264 has
    to fall back to the nearest keyframe anyway, and these clips are short.
    """
    capture = cv2.VideoCapture(clipPath)
    if not capture.isOpened():
        return {}

    grabbed, index, highest = {}, 0, max(wanted)
    while index <= highest:
        ok, frame = capture.read()
        if not ok:
            break
        if index in wanted:
            grabbed[index] = frame
        index += 1
    capture.release()
    return grabbed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default=os.path.join(savantUtils.PROJECT_DIR, "data", "labels.csv"))
    parser.add_argument("--window", type=int, default=0,
                        help="also keep N frames either side of release")
    parser.add_argument("--quality", type=int, default=92)
    parser.add_argument("--outDir", default=OUT_DIR)
    args = parser.parse_args()

    rows = readLabels(args.labels)
    if not rows:
        print("No labeled rows with a frame_index.")
        return 1

    os.makedirs(args.outDir, exist_ok=True)
    jpegParams = [int(cv2.IMWRITE_JPEG_QUALITY), args.quality]

    written, missing, bytesOut = [], 0, 0
    for count, row in enumerate(rows, start=1):
        dirName = f"{row['game_pk']}_{row['play_id']}"
        clipPath = clipPathFor(dirName)
        metaPath = os.path.join(savantUtils.PITCHES_DIR, dirName, "meta.json")
        if not os.path.exists(clipPath):
            missing += 1
            continue

        meta = {}
        if os.path.exists(metaPath):
            with open(metaPath) as handle:
                meta = json.load(handle)

        release = int(row["frame_index"])
        wanted = {release + offset for offset in range(-args.window, args.window + 1)}
        wanted = {f for f in wanted if f >= 0}

        for frameIndex, image in sorted(grabFrames(clipPath, wanted).items()):
            fileName = f"{dirName}_f{frameIndex:04d}.jpg"
            path = os.path.join(args.outDir, fileName)
            cv2.imwrite(path, image, jpegParams)
            bytesOut += os.path.getsize(path)
            written.append({
                "file": fileName,
                "game_pk": row["game_pk"], "play_id": row["play_id"],
                "frame_index": frameIndex,
                "label_status": row.get("status", ""),
                "ball_visibility": row.get("ball_visibility", ""),
                "release_quality": row.get("release_quality") or "unreviewed",
                "point_quality": row.get("point_quality") or "unreviewed",
                "is_release": int(frameIndex == release),
                "pitcher_id": row.get("pitcher_id", ""),
                "pitcher_name": row.get("pitcher_name", ""),
                "p_throws": row.get("p_throws", ""),
                "park": row.get("park", ""), "venue_id": row.get("venue_id", ""),
                "true_arm_angle": row.get("true_arm_angle", ""),
                "img_w": row.get("img_w", ""), "img_h": row.get("img_h", ""),
            })

        if count % 40 == 0:
            print(f"  {count}/{len(rows)} pitches...", flush=True)

    if not written:
        print("No readable release frames. Existing export was left intact.")
        return 1
    indexPath = os.path.join(args.outDir, "frames.csv")
    with open(indexPath, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(written[0].keys()))
        writer.writeheader()
        writer.writerows(written)

    print(f"\nwrote {len(written)} images from {len(rows) - missing} pitches "
          f"({bytesOut / 1e6:.0f} MB)")
    if missing:
        print(f"  {missing} pitches had no clip.mp4")
    print(f"index: {indexPath}")
    clipBytes = sum(
        os.path.getsize(os.path.join(savantUtils.PITCHES_DIR, d, "clip.mp4"))
        for d in os.listdir(savantUtils.PITCHES_DIR)
        if os.path.exists(os.path.join(savantUtils.PITCHES_DIR, d, "clip.mp4")))
    print(f"clips currently on disk: {clipBytes / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
