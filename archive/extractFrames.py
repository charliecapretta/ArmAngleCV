"""
Extract every frame of each clip into its own folder, so the release frame can
be picked by eye instead of guessed at by a detector.

Automatic release detection was tried twice (ball-leaves-hand, then arm
kinematics + ball-at-plate physics) and neither was dependable enough to hand a
labeler a 6-frame filmstrip -- both drifted into the follow-through. Dumping
the frames is cheap, exact, and puts the call where it belongs.

Layout produced:

    pitches/<game_pk>_<play_id>/
        clip.mp4
        meta.json
        frames/
            frame_0000.jpg
            frame_0001.jpg
            ...
        frames/index.json     # frame count, fps, dimensions, meta echo

Clips run ~400 frames at ~60 fps (about 7 seconds). At full quality that is
roughly 40-60 MB of JPEGs per pitch, so use --every to thin the output or
--quality to shrink it if disk becomes a problem.

Usage:
    python extractFrames.py                 # every pitch, every frame
    python extractFrames.py --limit 5
    python extractFrames.py --every 2 --quality 85
"""

import argparse
import json
import os
import shutil
import sys

import cv2

import savantUtils


def extractOne(pitchDir, args):
    clipPath = os.path.join(pitchDir, "clip.mp4")
    metaPath = os.path.join(pitchDir, "meta.json")
    framesDir = os.path.join(pitchDir, "frames")
    indexPath = os.path.join(framesDir, "index.json")

    if not os.path.exists(clipPath):
        return "no clip.mp4"
    if os.path.exists(indexPath) and not args.force:
        return "cached"

    meta = {}
    if os.path.exists(metaPath):
        with open(metaPath, "r") as handle:
            meta = json.load(handle)

    # Clear any leftovers from the earlier candidate-filmstrip approach.
    if os.path.isdir(framesDir):
        shutil.rmtree(framesDir)
    os.makedirs(framesDir, exist_ok=True)

    capture = cv2.VideoCapture(clipPath)
    if not capture.isOpened():
        return "could not open clip"

    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

    jpegParams = [int(cv2.IMWRITE_JPEG_QUALITY), args.quality]
    written = []
    frameIndex = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frameIndex % args.every == 0:
            fileName = f"frame_{frameIndex:04d}.jpg"
            cv2.imwrite(os.path.join(framesDir, fileName), frame, jpegParams)
            written.append({"frameIndex": frameIndex, "file": fileName})
        frameIndex += 1
    capture.release()

    if not written:
        return "decoded 0 frames"

    index = {
        "playId": meta.get("playId"),
        "gamePk": meta.get("gamePk"),
        "pitcherName": meta.get("pitcherName"),
        "pThrows": meta.get("pThrows"),
        "pitchType": meta.get("pitchType"),
        "releaseSpeed": meta.get("releaseSpeed"),
        "armAngle": meta.get("armAngle"),
        "park": meta.get("homeTeam"),
        "venueId": meta.get("venueId"),
        "gameDate": meta.get("gameDate"),
        "imgW": width,
        "imgH": height,
        "fps": fps,
        "nFramesTotal": frameIndex,
        "every": args.every,
        "frames": written,
    }
    with open(indexPath, "w") as handle:
        json.dump(index, handle, indent=2)

    megabytes = sum(
        os.path.getsize(os.path.join(framesDir, f["file"])) for f in written
    ) / 1e6
    return (f"ok ({len(written)} frames of {frameIndex}, "
            f"{width}x{height} @ {fps:.1f}fps, {megabytes:.0f}MB)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pitchesDir", default=savantUtils.PITCHES_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--every", type=int, default=1,
                        help="keep every Nth frame (1 = all frames)")
    parser.add_argument("--quality", type=int, default=90)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    pitchDirs = sorted(
        os.path.join(args.pitchesDir, name)
        for name in os.listdir(args.pitchesDir)
        if os.path.isdir(os.path.join(args.pitchesDir, name))
    )
    if args.limit:
        pitchDirs = pitchDirs[:args.limit]
    if not pitchDirs:
        print(f"No pitch directories in {args.pitchesDir}")
        return 1

    print(f"Extracting frames for {len(pitchDirs)} pitches", flush=True)
    counts = {"ok": 0, "cached": 0, "failed": 0}
    for index, pitchDir in enumerate(pitchDirs, start=1):
        try:
            status = extractOne(pitchDir, args)
        except Exception as error:  # noqa: BLE001
            status = f"ERROR: {type(error).__name__}: {error}"
        bucket = "ok" if status.startswith("ok") else ("cached" if status == "cached" else "failed")
        counts[bucket] += 1
        print(f"  [{index}/{len(pitchDirs)}] {os.path.basename(pitchDir)[:26]}: {status}",
              flush=True)

    print(f"\nDone. ok={counts['ok']} cached={counts['cached']} failed={counts['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
