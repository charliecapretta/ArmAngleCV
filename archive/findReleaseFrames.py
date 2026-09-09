"""
Phase 1 -- narrow each clip down to a handful of release-frame candidates and
pre-seed shoulder/elbow/wrist keypoints on each one.

Approach (arrived at by testing against real clips -- two earlier versions failed):

  1. Ball-leaves-hand detection does not work. Broadcast baseballs beside the
     pitcher are small and motion-blurred, and the detector happily fires on
     white objects in the crowd; the first version locked onto a shirt in the
     stands and returned a follow-through frame.

  2. Arm kinematics alone lands late. Peak wrist speed refined toward maximum
     arm extension drifts into the follow-through, where the arm extends again
     on the way down across the body.

  So release is estimated two ways and the physical one wins:

    poseEstimate    -- peak wrist speed of the throwing arm, used to locate the
                       delivery and as the fallback.
    physicsEstimate -- find the frame where the ball crosses the strike zone
                       above home plate (an easy, reliable detection: the plate
                       is static and known, and a tight zone rejects the crowd
                       false positives), then subtract the flight time implied
                       by Statcast's own release_speed and release_extension
                       for this exact pitch.

  Both are recorded in candidates.json along with their disagreement, so the
  estimate can be audited rather than trusted blindly.

Cost control (this machine is CPU-only -- no CUDA, and MPS is unavailable on
Intel): a coarse strided detection pass locates the delivery window and the
rubber, pose runs strided inside that window, and only the final candidates get
dense per-frame inference.

Writes per pitch:
    pitches/<dir>/frames/cand_<frameIndex>.jpg
    pitches/<dir>/candidates.json

Usage:
    python findReleaseFrames.py --limit 5
    python findReleaseFrames.py
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

import savantUtils

# COCO-17 keypoint indices, same ordering as the V-columns in Pitcher_Motion_Data.csv.
KEYPOINT_INDEX = {
    "leftShoulder": 5, "rightShoulder": 6,
    "leftElbow": 7, "rightElbow": 8,
    "leftWrist": 9, "rightWrist": 10,
    "leftHip": 11, "rightHip": 12,
}

CANDIDATES_BEFORE = 2
CANDIDATES_AFTER = 3
MIN_KEYPOINT_CONF = 0.30


def loadModels(poseWeights):
    from baseballcv.functions import LoadTools
    from ultralytics import YOLO

    loadTools = LoadTools()
    detectModel = YOLO(loadTools.load_model(model_alias="glove_tracking"))
    poseModel = YOLO(poseWeights)
    return detectModel, poseModel


def readFrames(clipPath, maxFrames):
    capture = cv2.VideoCapture(clipPath)
    if not capture.isOpened():
        return [], (None, None, None)

    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

    frames = []
    while len(frames) < maxFrames:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    return frames, (fps, width, height)


def boxCenter(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def bestBox(detections, className):
    entries = detections.get(className) or []
    if not entries:
        return None
    return max(entries, key=lambda entry: entry[1])[0]


def detectFrames(detectModel, frames, indices, confidence, imageSize, batchSize=16):
    """Run glove_tracking on selected frame indices. Returns {index: {class: [(box, conf)]}}."""
    names = detectModel.names
    output = {}
    for start in range(0, len(indices), batchSize):
        batchIndices = indices[start:start + batchSize]
        results = detectModel.predict(
            [frames[i] for i in batchIndices],
            conf=confidence, imgsz=imageSize, verbose=False,
        )
        for frameIndex, result in zip(batchIndices, results):
            detections = {}
            for box in result.boxes:
                className = names[int(box.cls[0])]
                detections.setdefault(className, []).append(
                    (box.xyxy[0].tolist(), float(box.conf[0]))
                )
            output[frameIndex] = detections
    return output


def posePitcher(poseModel, frames, indices, anchorPoint, pThrows,
                confidence, imageSize, batchSize=16):
    """Run pose on selected frames and keep only the pitcher's arm keypoints."""
    output = {}
    for start in range(0, len(indices), batchSize):
        batchIndices = indices[start:start + batchSize]
        results = poseModel.predict(
            [frames[i] for i in batchIndices],
            conf=confidence, imgsz=imageSize, verbose=False,
        )
        for frameIndex, result in zip(batchIndices, results):
            keypoints = pickPitcherPerson(result, anchorPoint, frames[frameIndex].shape)
            output[frameIndex] = extractArmPoints(keypoints, pThrows)
    return output


def pickPitcherPerson(poseResult, anchorPoint, frameShape):
    """
    Choose the pose instance standing closest to the rubber.

    The rubber is static, so a single median rubber position over the clip is a
    reliable anchor -- and it cleanly separates the pitcher from the batter,
    catcher, umpire, and crowd.
    """
    if poseResult.keypoints is None or len(poseResult.keypoints) == 0:
        return None
    if poseResult.boxes is None or len(poseResult.boxes) == 0:
        return None

    keypointData = poseResult.keypoints.data.cpu().numpy()  # (n, 17, 3)
    boxes = poseResult.boxes.xyxy.cpu().numpy()

    height, width = frameShape[:2]
    if anchorPoint is None:
        anchorPoint = (width / 2.0, height * 0.62)

    bestIndex, bestScore = None, None
    for index, box in enumerate(boxes):
        centerX = (box[0] + box[2]) / 2.0
        # Feet, not centroid: the pitcher's feet are on the rubber.
        feetY = box[3]
        distance = float(np.hypot(centerX - anchorPoint[0], feetY - anchorPoint[1]))
        if bestScore is None or distance < bestScore:
            bestScore, bestIndex = distance, index

    return keypointData[bestIndex]


def extractArmPoints(keypoints, pThrows):
    if keypoints is None:
        return None

    side = "right" if (pThrows or "R").upper().startswith("R") else "left"
    result = {"side": side}

    for jointName in ("Shoulder", "Elbow", "Wrist", "Hip"):
        x, y, confidence = keypoints[KEYPOINT_INDEX[f"{side}{jointName}"]]
        result[jointName.lower()] = {
            "x": round(float(x), 2), "y": round(float(y), 2),
            "conf": round(float(confidence), 3),
        }

    otherSide = "left" if side == "right" else "right"
    x, y, confidence = keypoints[KEYPOINT_INDEX[f"{otherSide}Shoulder"]]
    result["offShoulder"] = {
        "x": round(float(x), 2), "y": round(float(y), 2), "conf": round(float(confidence), 3)
    }
    return result


def medianBoxCenter(detectionsByFrame, className):
    centers = [
        boxCenter(bestBox(detections, className))
        for detections in detectionsByFrame.values()
        if bestBox(detections, className) is not None
    ]
    if not centers:
        return None
    return (
        float(np.median([c[0] for c in centers])),
        float(np.median([c[1] for c in centers])),
    )


def findDeliveryWindow(detectionsByFrame, indices, nFrames):
    """Frames where the rubber or plate is visible = center-field camera is live."""
    visible = [
        i for i in indices
        if detectionsByFrame.get(i, {}).get("rubber")
        or detectionsByFrame.get(i, {}).get("homeplate")
    ]
    if not visible:
        return 0, nFrames - 1

    runs, runStart, previous = [], visible[0], visible[0]
    for index in visible[1:]:
        if index - previous > 30:
            runs.append((runStart, previous))
            runStart = index
        previous = index
    runs.append((runStart, previous))
    return max(runs, key=lambda run: run[1] - run[0])


def releaseFromKinematics(poseByFrame, indices):
    """
    Release estimate from the throwing arm.

    1. Wrist speed profile over the strided pose samples.
    2. Take the peak-speed frame.
    3. Within a neighborhood of it, choose maximum arm extension
       (wrist-to-shoulder distance) with the wrist still above the hip --
       through follow-through the elbow re-bends and the hand drops.
    """
    usable = [
        i for i in indices
        if poseByFrame.get(i)
        and poseByFrame[i]["wrist"]["conf"] >= MIN_KEYPOINT_CONF
        and poseByFrame[i]["shoulder"]["conf"] >= MIN_KEYPOINT_CONF
    ]
    if len(usable) < 4:
        return None, {"reason": f"only {len(usable)} confident pose frames"}

    speeds = []
    for position in range(1, len(usable)):
        previousIndex, currentIndex = usable[position - 1], usable[position]
        gap = currentIndex - previousIndex
        if gap <= 0 or gap > 6:
            continue
        previous = poseByFrame[previousIndex]["wrist"]
        current = poseByFrame[currentIndex]["wrist"]
        speed = float(np.hypot(current["x"] - previous["x"], current["y"] - previous["y"])) / gap
        speeds.append((currentIndex, speed))

    if not speeds:
        return None, {"reason": "no wrist speed samples"}

    peakIndex, peakSpeed = max(speeds, key=lambda entry: entry[1])

    neighborhood = [i for i in usable if abs(i - peakIndex) <= 10]
    bestIndex, bestExtension = None, -1.0
    for index in neighborhood:
        pose = poseByFrame[index]
        wrist, shoulder, hip = pose["wrist"], pose["shoulder"], pose["hip"]
        # Wrist must still be above the hip: excludes the follow-through downswing.
        if hip["conf"] >= MIN_KEYPOINT_CONF and wrist["y"] > hip["y"]:
            continue
        extension = float(np.hypot(wrist["x"] - shoulder["x"], wrist["y"] - shoulder["y"]))
        if extension > bestExtension:
            bestExtension, bestIndex = extension, index

    if bestIndex is None:
        bestIndex, bestExtension = peakIndex, -1.0

    return bestIndex, {
        "method": "wristKinematics",
        "peakSpeedFrame": int(peakIndex),
        "peakWristSpeedPx": round(peakSpeed, 1),
        "armExtensionPx": round(bestExtension, 1),
    }


def flightFrames(meta, fps):
    """
    How many frames the pitch spends in the air, from Statcast's own numbers.

    release_speed is out-of-hand velocity and the ball bleeds roughly 8% of it
    before the plate, so the average speed over the flight is a bit lower.
    Distance travelled is 60.5 ft from the rubber to the plate, less however far
    in front of the rubber the pitcher actually let go (release_extension).
    """
    speed = meta.get("releaseSpeed")
    if not speed or not fps:
        return None
    extension = meta.get("releaseExtension") or 6.0
    distanceFeet = 60.5 - float(extension)
    averageFeetPerSecond = float(speed) * 0.92 * 1.46667
    if averageFeetPerSecond <= 0:
        return None
    return (distanceFeet / averageFeetPerSecond) * fps


def findPlateCross(detectionsByFrame, plateCenter, frameShape, searchFrom):
    """
    First frame where a ball is detected in the strike-zone box above home plate.

    This is a far easier detection than catching the ball beside the pitcher's
    hand -- the plate is static and known, the ball is larger there, and a
    tight zone around it rejects the crowd false positives that derailed the
    earlier ball-separation approach.
    """
    if plateCenter is None:
        return None, None
    height, width = frameShape[:2]
    zoneHalfWidth = width * 0.10
    zoneAbove = height * 0.28
    zoneBelow = height * 0.04

    for index in sorted(i for i in detectionsByFrame if i >= searchFrom):
        ballBox = bestBox(detectionsByFrame[index], "baseball")
        if ballBox is None:
            continue
        centerX, centerY = boxCenter(ballBox)
        if abs(centerX - plateCenter[0]) > zoneHalfWidth:
            continue
        if not (plateCenter[1] - zoneAbove <= centerY <= plateCenter[1] + zoneBelow):
            continue
        boxWidth, boxHeight = ballBox[2] - ballBox[0], ballBox[3] - ballBox[1]
        if boxWidth > width * 0.05 or boxHeight > height * 0.08:
            continue
        return index, (round(centerX, 1), round(centerY, 1))
    return None, None


def plausibleBall(ballBox, anchorPoint, plateCenter, frameShape):
    """
    Reject ball detections that cannot be a pitch in flight.

    Guards against the failure seen in testing: a white object in the crowd,
    high in the frame, read as a baseball.
    """
    if ballBox is None or anchorPoint is None:
        return False
    height, width = frameShape[:2]
    center = boxCenter(ballBox)

    # Must be below the top of the playing area, not in the stands.
    if center[1] < height * 0.20:
        return False
    # Must sit roughly between the rubber and the plate, vertically.
    topY = min(anchorPoint[1], plateCenter[1] if plateCenter else anchorPoint[1]) - height * 0.30
    if center[1] < topY:
        return False
    # Ball boxes are small.
    boxWidth, boxHeight = ballBox[2] - ballBox[0], ballBox[3] - ballBox[1]
    if boxWidth > width * 0.06 or boxHeight > height * 0.10:
        return False
    return True


def refineWithBall(detectionsByFrame, estimate, anchorPoint, plateCenter, frameShape, window=8):
    """If a plausible ball first appears near the estimate, prefer that frame."""
    for index in sorted(detectionsByFrame):
        if abs(index - estimate) > window:
            continue
        ballBox = bestBox(detectionsByFrame[index], "baseball")
        if plausibleBall(ballBox, anchorPoint, plateCenter, frameShape):
            return index, boxCenter(ballBox)
    return None, None


def processPitch(pitchDir, detectModel, poseModel, args):
    clipPath = os.path.join(pitchDir, "clip.mp4")
    metaPath = os.path.join(pitchDir, "meta.json")
    outPath = os.path.join(pitchDir, "candidates.json")

    if not (os.path.exists(clipPath) and os.path.exists(metaPath)):
        return "missing clip or meta"
    if os.path.exists(outPath) and not args.force:
        return "cached"

    with open(metaPath, "r") as handle:
        meta = json.load(handle)

    frames, (fps, width, height) = readFrames(clipPath, args.maxFrames)
    if len(frames) < 20:
        return f"only {len(frames)} frames decoded"

    # --- Pass A: coarse detection to find the delivery window and the rubber ---
    coarseIndices = list(range(0, len(frames), args.detectStride))
    coarse = detectFrames(
        detectModel, frames, coarseIndices, args.detectConf, args.detectSize
    )
    windowStart, windowEnd = findDeliveryWindow(coarse, coarseIndices, len(frames))
    rubberCenter = medianBoxCenter(coarse, "rubber")
    plateCenter = medianBoxCenter(coarse, "homeplate")

    # --- Pass B: strided pose across the delivery window ---
    poseIndices = list(range(windowStart, min(windowEnd, len(frames) - 1) + 1, args.poseStride))
    if len(poseIndices) < 4:
        return "delivery window too short"
    poseByFrame = posePitcher(
        poseModel, frames, poseIndices, rubberCenter, meta.get("pThrows"),
        args.poseConf, args.poseSize,
    )

    estimate, detail = releaseFromKinematics(poseByFrame, poseIndices)
    if estimate is None:
        return f"no release found ({detail.get('reason')})"

    # --- Pass C: dense pose in a tight band, to place the estimate exactly ---
    bandStart = max(windowStart, estimate - args.poseStride * 2)
    bandEnd = min(windowEnd, len(frames) - 1, estimate + args.poseStride * 2)
    bandIndices = [i for i in range(bandStart, bandEnd + 1) if i not in poseByFrame]
    if bandIndices:
        poseByFrame.update(posePitcher(
            poseModel, frames, bandIndices, rubberCenter, meta.get("pThrows"),
            args.poseConf, args.poseSize,
        ))
    denseIndices = sorted(i for i in poseByFrame if bandStart <= i <= bandEnd)
    refined, refinedDetail = releaseFromKinematics(poseByFrame, denseIndices)
    if refined is not None:
        estimate = refined
        detail.update({"refined": refinedDetail})

    # --- Pass D: physics cross-check via the ball crossing the plate ---
    # Release is a hard instant to see; the ball arriving over the plate is not.
    # Detect that, then subtract the flight time implied by Statcast's own
    # release_speed / release_extension for this exact pitch.
    poseEstimate = estimate
    scanStart = max(0, poseEstimate - 12)
    scanEnd = min(len(frames), poseEstimate + 55)
    ballDetections = detectFrames(
        detectModel, frames, list(range(scanStart, scanEnd)),
        args.detectConf, args.detectSize,
    )
    crossFrame, crossCenter = findPlateCross(
        ballDetections, plateCenter, frames[0].shape, searchFrom=poseEstimate - 5
    )
    flight = flightFrames(meta, fps)

    detail["poseEstimate"] = int(poseEstimate)
    if flight is not None:
        detail["flightFrames"] = round(flight, 1)

    if crossFrame is not None and flight is not None:
        physicsEstimate = int(round(crossFrame - flight))
        detail["plateCrossFrame"] = int(crossFrame)
        detail["plateCrossCenter"] = list(crossCenter)
        detail["physicsEstimate"] = physicsEstimate
        detail["poseVsPhysicsFrames"] = int(abs(physicsEstimate - poseEstimate))
        if 0 <= physicsEstimate < len(frames):
            estimate = physicsEstimate
            detail["method"] = "plateCrossPhysics"

    candidateIndices = [
        i for i in range(
            estimate - args.candidatesBefore * args.candidateStride,
            estimate + args.candidatesAfter * args.candidateStride + 1,
            args.candidateStride,
        )
        if 0 <= i < len(frames)
    ]

    framesDir = os.path.join(pitchDir, "frames")
    os.makedirs(framesDir, exist_ok=True)
    for stale in os.listdir(framesDir):
        if stale.startswith("cand_"):
            os.remove(os.path.join(framesDir, stale))

    missing = [i for i in candidateIndices if i not in poseByFrame]
    if missing:
        poseByFrame.update(posePitcher(
            poseModel, frames, missing, rubberCenter, meta.get("pThrows"),
            args.poseConf, args.poseSize,
        ))

    candidates = []
    for frameIndex in candidateIndices:
        fileName = f"cand_{frameIndex:04d}.jpg"
        cv2.imwrite(
            os.path.join(framesDir, fileName), frames[frameIndex],
            [int(cv2.IMWRITE_JPEG_QUALITY), 95],
        )
        candidates.append({
            "frameIndex": frameIndex,
            "file": f"frames/{fileName}",
            "isReleaseGuess": frameIndex == estimate,
            "seed": poseByFrame.get(frameIndex),
            "ballBox": bestBox(ballDetections.get(frameIndex, {}), "baseball"),
            "gloveBox": bestBox(ballDetections.get(frameIndex, {}), "glove"),
        })

    payload = {
        "playId": meta["playId"],
        "gamePk": meta["gamePk"],
        "imgW": width, "imgH": height, "fps": fps,
        "nFramesDecoded": len(frames),
        "deliveryWindow": [int(windowStart), int(windowEnd)],
        "rubberCenter": [round(v, 1) for v in rubberCenter] if rubberCenter else None,
        "plateCenter": [round(v, 1) for v in plateCenter] if plateCenter else None,
        "releaseFrameGuess": int(estimate),
        "releaseDetail": detail,
        "pThrows": meta.get("pThrows"),
        "armAngle": meta.get("armAngle"),
        "candidates": candidates,
    }
    with open(outPath, "w") as handle:
        json.dump(payload, handle, indent=2)

    seeded = sum(1 for c in candidates if c["seed"])
    agreement = detail.get("poseVsPhysicsFrames")
    agreeText = "" if agreement is None else f", pose-physics Δ{agreement}f"
    return (f"ok (release @ {estimate}, {len(candidates)} cands, "
            f"{seeded} seeded, {detail.get('method')}{agreeText})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pitchesDir", default=savantUtils.PITCHES_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--poseWeights", default="yolo11n-pose.pt")
    parser.add_argument("--detectConf", type=float, default=0.25)
    parser.add_argument("--poseConf", type=float, default=0.25)
    parser.add_argument("--detectStride", type=int, default=6,
                        help="coarse detection stride for the delivery window")
    parser.add_argument("--poseStride", type=int, default=3,
                        help="pose stride inside the delivery window")
    parser.add_argument("--detectSize", type=int, default=640)
    parser.add_argument("--poseSize", type=int, default=640)
    parser.add_argument("--maxFrames", type=int, default=460)
    parser.add_argument("--candidatesBefore", type=int, default=4)
    parser.add_argument("--candidatesAfter", type=int, default=4)
    parser.add_argument("--candidateStride", type=int, default=1)
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

    print(f"Loading models (glove_tracking + {args.poseWeights})", flush=True)
    detectModel, poseModel = loadModels(args.poseWeights)

    print(f"\nProcessing {len(pitchDirs)} pitches", flush=True)
    counts = {"ok": 0, "cached": 0, "failed": 0}
    for index, pitchDir in enumerate(pitchDirs, start=1):
        try:
            status = processPitch(pitchDir, detectModel, poseModel, args)
        except Exception as error:  # noqa: BLE001
            status = f"ERROR: {type(error).__name__}: {error}"

        if status.startswith("ok"):
            counts["ok"] += 1
        elif status == "cached":
            counts["cached"] += 1
        else:
            counts["failed"] += 1
        print(f"  [{index}/{len(pitchDirs)}] {os.path.basename(pitchDir)[:24]}: {status}",
              flush=True)

    print(f"\nDone. ok={counts['ok']} cached={counts['cached']} failed={counts['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
