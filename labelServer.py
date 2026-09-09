"""
Local server behind labelTool.html.

Decodes clip frames on demand, runs pose on whichever single frame the labeler
has landed on, and saves release checkpoints and point labels.

Release selections and points are saved atomically. Local-video labels live in
localLabels.csv, separate from the MLB labels.csv. Local videos are registered
by path in localVideos.json without copying the footage. Extracting frames as JPEGs cost
~90 MB per pitch (22 GB for a 250-pitch batch) against ~5 MB for the clip
itself, so clips are decoded into RAM on first touch and the least recently
used is evicted. Pose runs lazily, one frame at a time, only when asked.

    python labelServer.py
    then open http://127.0.0.1:8777/labelTool.html

Endpoints:
    GET  /api/pitches                  every pitch that has a clip
    GET  /api/labels                   MLB and local rows (for resume)
    GET  /api/clip?dir=..              frame count, FPS, and decoded timestamps
    GET  /api/frame?dir=..&frame=N     one frame as JPEG
    GET  /api/prefetch?dir=..          warm a clip in the background
    GET  /api/pose?dir=..&frame=N      body keypoints (both shoulders, elbow, wrist)
    GET  /api/ball?dir=..&frame=N      baseball / glove boxes for one frame
    POST /api/label                    upsert one row, returning an undo token
    POST /api/import                   register a local video/pitch by path
    POST /api/undo                     restore the row preceding a save
"""

import csv
import json
import math
import shutil
import tempfile
import uuid
import os
import sys
import threading
import urllib.parse
from collections import OrderedDict
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import savantUtils
from features import deriveAngles

LABELS_PATH = os.path.join(savantUtils.PROJECT_DIR, "labels.csv")
LOCAL_LABELS_PATH = os.path.join(savantUtils.PROJECT_DIR, "localLabels.csv")
LOCAL_VIDEOS_PATH = os.path.join(savantUtils.PROJECT_DIR, "localVideos.json")
ANALYSIS_LABELS_PATH = os.path.join(savantUtils.PROJECT_DIR, "analysisLabels.csv")

# Column order is append-only: the first 17 are the original schema, so rows
# written before the 5-point expansion stay readable and simply carry blanks.
LABEL_COLUMNS = [
    "game_pk", "play_id", "frame_index", "img_w", "img_h",
    "shoulder_x", "shoulder_y", "wrist_x", "wrist_y",
    "park", "true_arm_angle",
    "elbow_x", "elbow_y", "p_throws", "seeded", "venue_id", "status",
    # 5-point expansion. shoulder_* remains the THROWING shoulder.
    "off_shoulder_x", "off_shoulder_y",   # glove-side shoulder -> torso rotation
    "ball_x", "ball_y",                   # ball at release -- Statcast's own basis
    "provenance",                         # per-point origin, e.g. "shoulder:hand,ball:det"
    # Derived on write (see deriveColumns) -- never sent by the browser, so they
    # cannot drift out of step with the coordinates they come from.
    "pitcher_id", "pitcher_name",         # group key: fold splits MUST be by pitcher
    "ball_angle", "wrist_angle",          # shoulder->ball / shoulder->wrist, degrees
    "fps", "release_time_sec", "ball_visibility", "source_video",
    "release_quality", "point_quality",
]

# COCO-17 indices, same ordering as the V-columns in Pitcher_Motion_Data.csv.
KEYPOINT_INDEX = {
    "leftShoulder": 5, "rightShoulder": 6,
    "leftElbow": 7, "rightElbow": 8,
    "leftWrist": 9, "rightWrist": 10,
    "leftHip": 11, "rightHip": 12,
}

poseModel = None
poseLock = threading.Lock()
writeLock = threading.Lock()
undoRecords = {}

# Frames are decoded from the mp4 on demand and held as JPEG bytes in RAM rather
# than written out. Extracting them cost ~90 MB per pitch on disk (22 GB for a
# 250-pitch batch); the clips themselves are ~5 MB. A decoded clip is ~90 MB of
# RAM, so a few are kept and the least recently used is dropped.
MAX_CACHED_CLIPS = 3
JPEG_QUALITY = 90

clipCache = OrderedDict()          # dirName -> {"jpegs": [bytes], "fps", "imgW", "imgH"}
cacheLock = threading.Lock()       # guards clipCache membership/ordering only
clipLocks = {}                     # dirName -> lock, so one clip decodes once
clipLocksLock = threading.Lock()


def clipLockFor(dirName):
    with clipLocksLock:
        if dirName not in clipLocks:
            clipLocks[dirName] = threading.Lock()
        return clipLocks[dirName]


def probeClip(clipPath):
    """Frame count / fps / size without decoding the whole clip."""
    import cv2
    capture = cv2.VideoCapture(clipPath)
    if not capture.isOpened():
        return None
    info = {
        "nFrames": int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
        "fps": capture.get(cv2.CAP_PROP_FPS) or 0.0,
        "imgW": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "imgH": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    }
    capture.release()
    return info


def decodeClip(dirName):
    """Decode every frame of one clip to JPEG bytes. ~2-6s, done once per clip."""
    import cv2
    clipPath = clipPathFor(dirName)
    capture = cv2.VideoCapture(clipPath)
    if not capture.isOpened():
        return None

    params = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
    jpegs, frameTimes, width, height = [], [], 0, 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        height, width = frame.shape[:2]
        ok, buffer = cv2.imencode(".jpg", frame, params)
        if not ok:
            capture.release()
            raise ValueError("Could not encode a frame; frame numbering was preserved.")
        jpegs.append(buffer.tobytes())
        frameTimes.append(capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    capture.release()

    if not jpegs:
        return None
    # Fall back only when the decoder cannot supply a monotonic timeline.
    if any(b <= a for a, b in zip(frameTimes, frameTimes[1:])):
        frameTimes = [i / fps if fps > 0 else None for i in range(len(jpegs))]
    return {"jpegs": jpegs, "fps": fps, "imgW": width, "imgH": height,
            "frameTimes": frameTimes}


def getClip(dirName):
    # Multiple labeled pitches in one local reel share a single decoded cache.
    cacheKey = clipPathFor(dirName)
    with cacheLock:
        entry = clipCache.get(cacheKey)
        if entry is not None:
            clipCache.move_to_end(cacheKey)
            return entry
    with clipLockFor(cacheKey):
        with cacheLock:
            entry = clipCache.get(cacheKey)
            if entry is not None:
                return entry
        entry = decodeClip(dirName)
        if entry is None:
            return None
        with cacheLock:
            clipCache[cacheKey] = entry
            clipCache.move_to_end(cacheKey)
            while len(clipCache) > MAX_CACHED_CLIPS:
                clipCache.popitem(last=False)
        return entry


def prefetchClip(dirName):
    threading.Thread(target=getClip, args=(dirName,), daemon=True).start()


detectModel = None
detectLock = threading.Lock()


def getDetectModel():
    """
    glove_tracking (glove / homeplate / baseball / rubber), loaded lazily.

    Measured at ~1210 ms/frame on this machine -- and worse batched (1452 ms at
    batch 16, memory pressure) -- against ~100 ms for pose. So this is only ever
    run on a single frame the labeler is actually looking at, never while
    scrubbing.
    """
    global detectModel
    with detectLock:
        if detectModel is None:
            from baseballcv.functions import LoadTools
            from ultralytics import YOLO
            print("Loading glove_tracking ...", flush=True)
            detectModel = YOLO(LoadTools().load_model(model_alias="glove_tracking"))
        return detectModel


def ballPoints(dirName, frameIndex):
    """
    Baseball / glove boxes for one frame.

    Detections are plausibility-gated. An earlier version of this pipeline
    locked onto a white shirt in the crowd and reported it as the ball, so
    anything high in the frame or too large to be a baseball is dropped.
    """
    import cv2
    import numpy as np

    entry = getClip(dirName)
    if entry is None:
        return {"error": "could not decode clip"}
    frameIndex = int(frameIndex)
    if not (0 <= frameIndex < len(entry["jpegs"])):
        return {"error": f"frame {frameIndex} out of range"}

    image = cv2.imdecode(
        np.frombuffer(entry["jpegs"][frameIndex], dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        return {"error": "unreadable frame"}

    model = getDetectModel()
    result = model.predict([image], conf=0.20, verbose=False)[0]
    names = model.names

    height, width = image.shape[:2]
    balls, gloves = [], []
    for box in result.boxes:
        className = names[int(box.cls[0])]
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
        entryBox = {
            "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "cx": round((x1 + x2) / 2, 2), "cy": round((y1 + y2) / 2, 2),
            "conf": round(float(box.conf[0]), 3),
        }
        if className == "baseball":
            boxW, boxH = x2 - x1, y2 - y1
            # Reject the crowd/stands band and anything too big to be a ball.
            if entryBox["cy"] < height * 0.20:
                continue
            if boxW > width * 0.05 or boxH > height * 0.08:
                continue
            balls.append(entryBox)
        elif className == "glove":
            gloves.append(entryBox)

    balls.sort(key=lambda b: -b["conf"])
    gloves.sort(key=lambda b: -b["conf"])
    return {"balls": balls, "gloves": gloves}


def getPoseModel():
    """Loaded lazily so the server starts instantly and pose costs nothing until used."""
    global poseModel
    with poseLock:
        if poseModel is None:
            from ultralytics import YOLO
            print("Loading yolo11n-pose.pt ...", flush=True)
            poseModel = YOLO("yolo11n-pose.pt")
        return poseModel


def readLocalVideos():
    if not os.path.exists(LOCAL_VIDEOS_PATH):
        return []
    with open(LOCAL_VIDEOS_PATH) as handle:
        return json.load(handle)


def relativeSourceVideo(path):
    """Project-relative path when the clip is inside the project, else unchanged."""
    if not path:
        return path
    root = os.path.join(savantUtils.PROJECT_DIR, "")
    return path[len(root):] if os.path.abspath(path).startswith(root) else path


def clipPathFor(dirName):
    if not dirName or ".." in dirName or "/" in dirName or "\\" in dirName:
        raise ValueError("Invalid clip identifier")
    if dirName.startswith("local_"):
        for pitch in readLocalVideos():
            if pitch["dirName"] == dirName:
                return pitch["sourcePath"]
        raise ValueError("Local video is not registered")
    return os.path.join(savantUtils.PITCHES_DIR, dirName, "clip.mp4")


def atomicWrite(path, write):
    """Keep the old file intact if serialization or replacement fails."""
    if os.path.exists(path) and not os.path.exists(path + ".before-streamlined"):
        shutil.copy2(path, path + ".before-streamlined")
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".label-")
    try:
        with os.fdopen(fd, "w", newline="") as handle:
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def registerLocalVideo(payload):
    path = os.path.abspath(os.path.expanduser(str(payload.get("path", ""))))
    if not os.path.isfile(path):
        raise ValueError("Video file not found. Paste its full local path.")
    info = probeClip(path)
    if not info or info["nFrames"] < 1:
        raise ValueError("This file could not be opened as a video.")
    throws = str(payload.get("pThrows", "R")).upper()
    if throws not in ("R", "L"):
        raise ValueError("Choose R or L for the throwing hand.")
    startFrame = int(payload.get("startFrame", 0))
    if not 0 <= startFrame < info["nFrames"]:
        raise ValueError("Starting frame is outside this video.")
    playId = uuid.uuid4().hex
    pitch = {"gamePk": "local", "playId": playId, "dirName": "local_" + playId,
             "sourcePath": path, "sourceName": os.path.basename(path),
             "pitcherName": str(payload.get("pitcherName", "")), "pThrows": throws,
             "park": str(payload.get("park", "")), "armAngle": None,
             "startFrame": startFrame, "isLocal": True, **info}
    with writeLock:
        videos = readLocalVideos()
        videos.append(pitch)
        atomicWrite(LOCAL_VIDEOS_PATH, lambda handle: json.dump(videos, handle, indent=2))
    return pitch


def listPitches():
    """Every pitch that has a clip. Frame counts come from a cheap header probe."""
    pitches = []
    root = savantUtils.PITCHES_DIR
    if not os.path.isdir(root):
        return readLocalVideos()
    for name in sorted(os.listdir(root)):
        pitchDir = os.path.join(root, name)
        clipPath = os.path.join(pitchDir, "clip.mp4")
        metaPath = os.path.join(pitchDir, "meta.json")
        if not (os.path.exists(clipPath) and os.path.exists(metaPath)):
            continue
        with open(metaPath, "r") as handle:
            meta = json.load(handle)
        info = probeClip(clipPath)
        if not info or info["nFrames"] < 1:
            continue
        pitches.append({
            "dirName": name,
            "playId": meta.get("playId"), "gamePk": meta.get("gamePk"),
            "pitcherName": meta.get("pitcherName"), "pThrows": meta.get("pThrows"),
            "pitchType": meta.get("pitchType"), "releaseSpeed": meta.get("releaseSpeed"),
            "armAngle": meta.get("armAngle"), "park": meta.get("homeTeam"),
            "venueId": meta.get("venueId"), "gameDate": meta.get("gameDate"),
            "uiTestBatch": bool(meta.get("uiTestBatch")),
            **info,
        })
    return readLocalVideos() + pitches


_pitcherCache = {}


def pitcherFor(gamePk, playId):
    """(pitcherId, pitcherName) from the pitch's meta.json, memoised."""
    key = f"{gamePk}_{playId}"
    if str(gamePk) == "local":
        pitch = next((p for p in readLocalVideos() if p["playId"] == playId), {})
        return "", pitch.get("pitcherName", "")
    if key not in _pitcherCache:
        metaPath = os.path.join(savantUtils.PITCHES_DIR, key, "meta.json")
        meta = {}
        if os.path.exists(metaPath):
            try:
                with open(metaPath) as handle:
                    meta = json.load(handle)
            except (json.JSONDecodeError, OSError):
                meta = {}
        _pitcherCache[key] = (meta.get("pitcherId", ""), meta.get("pitcherName", ""))
    return _pitcherCache[key]


def deriveColumns(row):
    """
    Fill the computed columns from the stored coordinates.

    Done here rather than in the browser on purpose: writeLabel rewrites every
    row on every save, so these are recomputed from the coordinates each time and
    can never disagree with them -- including for rows edited outside the tool.
    """
    row = dict(row)
    pitcherId, pitcherName = pitcherFor(row.get("game_pk"), row.get("play_id"))
    row["pitcher_id"] = pitcherId
    row["pitcher_name"] = pitcherName

    row.update(deriveAngles(row))
    return row


def readLabelFile(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def readLabels(workspace=None):
    if workspace == "training":
        return readLabelFile(LABELS_PATH)
    if workspace == "analysis":
        # Existing local annotations are a read-only starting point. Analysis edits
        # always upsert into analysisLabels.csv, leaving curated labels untouched.
        rows = readLabelFile(LOCAL_LABELS_PATH) + readLabelFile(ANALYSIS_LABELS_PATH)
        merged = {(str(r["game_pk"]), str(r["play_id"])): r for r in rows}
        return list(merged.values())
    return readLabelFile(LABELS_PATH) + readLabelFile(LOCAL_LABELS_PATH)


def labelPathFor(row):
    if row.get("workspace") == "analysis":
        return ANALYSIS_LABELS_PATH
    return LOCAL_LABELS_PATH if str(row.get("game_pk")) == "local" else LABELS_PATH


def validateLabel(row):
    row = dict(row)
    if row.get("workspace") not in (None, "training", "analysis"):
        raise ValueError("Unknown workspace")
    if row.get("workspace") == "training" and str(row.get("game_pk")) == "local":
        raise ValueError("Use Pitch analysis for local footage")
    status = row.get("status")
    if status not in ("release_selected", "labeled", "occluded", "skipped"):
        raise ValueError("Unknown label status")
    dirName = f"{row.get('game_pk')}_{row.get('play_id')}"
    entry = getClip(dirName)
    if entry is None:
        raise ValueError("Could not read this video")
    rawFrame = row.get("frame_index")
    frameIndex = int(rawFrame)
    if float(rawFrame) != frameIndex or not 0 <= frameIndex < len(entry["jpegs"]):
        raise ValueError("Frame is outside this video")
    # Stored relative to the project when the clip lives inside it, so labels.csv
    # stays portable: a labels file is shared and read on machines where this
    # project sits at a different absolute path. Footage registered from elsewhere
    # (a user's own reel in ~/Downloads) has no relative form and is kept as is.
    row.update(frame_index=frameIndex, img_w=entry["imgW"], img_h=entry["imgH"],
               fps=entry["fps"], release_time_sec=entry["frameTimes"][frameIndex],
               source_video=relativeSourceVideo(clipPathFor(dirName)))
    row["ball_visibility"] = "occluded" if status == "occluded" else (
        "visible" if status == "labeled" else "")
    for field, allowed in (("release_quality", ("unreviewed", "closest_available", "unclear")),
                           ("point_quality", ("unreviewed", "reviewed", "uncertain"))):
        value = row.get(field) or "unreviewed"
        if value not in allowed:
            raise ValueError(f"Invalid {field}")
        row[field] = value
    if status in ("release_selected", "skipped"):
        row["point_quality"] = "unreviewed"
    names = ["shoulder", "off_shoulder", "elbow", "wrist", "ball"]
    for name in names:
        required = status == "labeled" or (status == "occluded" and name != "ball")
        for axis, bound in (("x", entry["imgW"]), ("y", entry["imgH"])):
            key = f"{name}_{axis}"
            if status in ("release_selected", "skipped") or (status == "occluded" and name == "ball"):
                row[key] = ""
            elif required:
                try:
                    value = float(row.get(key, ""))
                except (TypeError, ValueError):
                    raise ValueError(f"Missing {name.replace('_', ' ')} point") from None
                if not math.isfinite(value) or not 0 <= value < bound:
                    raise ValueError(f"Invalid {name.replace('_', ' ')} point")
                row[key] = value
    if status in ("release_selected", "skipped"):
        row["provenance"] = row["seeded"] = ""
    return deriveColumns(row)


def writeRows(path, rows):
    def serialize(handle):
        writer = csv.DictWriter(handle, fieldnames=LABEL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    atomicWrite(path, serialize)


def writeLabel(row, withUndo=False):
    row = validateLabel(row)
    path = labelPathFor(row)
    with writeLock:
        rows = readLabelFile(path)
        key = (str(row["game_pk"]), str(row["play_id"]))
        previous = next((r for r in rows if (str(r["game_pk"]), str(r["play_id"])) == key), None)
        rows = [r for r in rows if (str(r["game_pk"]), str(r["play_id"])) != key]
        rows.append(row)
        writeRows(path, rows)
        token = uuid.uuid4().hex
        # Store the actual prior row, including legacy incomplete annotations.
        stored = next(r for r in readLabelFile(path) if (str(r["game_pk"]), str(r["play_id"])) == key)
        undoRecords[token] = (path, key, previous, stored)
    return (len(rows), token) if withUndo else len(rows)


def undoLabel(token):
    with writeLock:
        if token not in undoRecords:
            raise ValueError("This undo is no longer available. Reopen the pitch to review it.")
        path, key, previous, expected = undoRecords[token]
        rows = readLabelFile(path)
        current = next((r for r in rows if (str(r["game_pk"]), str(r["play_id"])) == key), None)
        if current != expected:
            raise ValueError("This pitch changed after the save. Reopen it before editing.")
        rows = [r for r in rows if (str(r["game_pk"]), str(r["play_id"])) != key]
        if previous is not None:
            rows.append(previous)
        writeRows(path, rows)
        del undoRecords[token]
    return len(rows)


def deleteLabel(row):
    path = labelPathFor(row)
    with writeLock:
        rows = readLabelFile(path)
        key = (str(row.get("game_pk")), str(row.get("play_id")))
        rows = [r for r in rows if (str(r["game_pk"]), str(r["play_id"])) != key]
        writeRows(path, rows)
    return len(rows)


def posePoints(dirName, frameIndex, pThrows):
    import cv2
    import numpy as np

    entry = getClip(dirName)
    if entry is None:
        return {"error": "could not decode clip"}
    frameIndex = int(frameIndex)
    if not (0 <= frameIndex < len(entry["jpegs"])):
        return {"error": f"frame {frameIndex} out of range"}

    image = cv2.imdecode(
        np.frombuffer(entry["jpegs"][frameIndex], dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
        return {"error": "unreadable frame"}

    model = getPoseModel()
    with poseLock:
        result = model.predict([image], conf=0.25, verbose=False)[0]
    if result.keypoints is None or len(result.keypoints) == 0:
        return {"error": "no person detected"}

    keypointData = result.keypoints.data.cpu().numpy()
    boxes = result.boxes.xyxy.cpu().numpy()

    side = "right" if (pThrows or "R").upper().startswith("R") else "left"

    people = []
    for personIndex in range(len(keypointData)):
        keypoints = keypointData[personIndex]
        entry = {
            "box": [round(float(v), 1) for v in boxes[personIndex].tolist()],
            "side": side,
        }
        for jointName in ("Shoulder", "Elbow", "Wrist", "Hip"):
            x, y, confidence = keypoints[KEYPOINT_INDEX[f"{side}{jointName}"]]
            entry[jointName.lower()] = {
                "x": round(float(x), 2), "y": round(float(y), 2),
                "conf": round(float(confidence), 3),
            }
        # Glove-side shoulder too: the shoulder line gives torso rotation, which
        # is the candidate explanation for the small L/R bias in the labels.
        otherSide = "left" if side == "right" else "right"
        x, y, confidence = keypoints[KEYPOINT_INDEX[f"{otherSide}Shoulder"]]
        entry["offShoulder"] = {
            "x": round(float(x), 2), "y": round(float(y), 2),
            "conf": round(float(confidence), 3),
        }
        entry["area"] = round(
            (entry["box"][2] - entry["box"][0]) * (entry["box"][3] - entry["box"][1]), 1
        )
        people.append(entry)

    # Biggest person first: on a center-field shot the pitcher is nearest the
    # camera and therefore the largest figure on the mound side of the frame.
    people.sort(key=lambda p: -p["area"])
    return {"people": people}



manualAngleBundle = None
manualAngleLock = threading.Lock()


def predictManualAngle(payload):
    """Read-only prediction using the versioned model for manually clicked points."""
    import angleInputs
    from joblib import load
    if payload.get("status") != "labeled":
        raise ValueError("Five visible points are required for this angle model")
    row = validateLabel(payload)  # Source dimensions come from the decoded clip.
    global manualAngleBundle
    with manualAngleLock:
        if manualAngleBundle is None:
            path = os.path.join(savantUtils.PROJECT_DIR, angleInputs.DEFAULT_MODEL)
            if not os.path.exists(path):
                raise ValueError("The resolution-normalized angle model is not available")
            manualAngleBundle = load(path)
        value = float(angleInputs.predict(manualAngleBundle, [row])[0])
    return {"ok": True, "predicted_arm_angle": value,
            "feature_mode": manualAngleBundle["feature_mode"],
            "note": "MLB-trained estimate; college accuracy is unverified."}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=savantUtils.PROJECT_DIR, **kwargs)

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stderr.write("  %s\n" % (fmt % args))

    def end_headers(self):
        # Development workspaces must load matching HTML, styles, and behavior.
        if urllib.parse.urlparse(self.path).path in ("/", "/index.html", "/labelTool.html", "/labelTool.js", "/workspace.css"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def sendJson(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        workspace = (query.get("workspace") or [None])[0]
        if workspace not in (None, "training", "analysis"):
            return self.sendJson({"error": "Unknown workspace"}, 400)
        if parsed.path == "/analyze.html":
            with open(os.path.join(savantUtils.PROJECT_DIR, "labelTool.html"), "r") as handle:
                html = handle.read().replace('data-workspace="training"', 'data-workspace="analysis"')
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/api/pitches":
            pitches = listPitches()
            if workspace == "training":
                pitches = [p for p in pitches if not p.get("isLocal")]
            elif workspace == "analysis":
                pitches = [p for p in pitches if p.get("isLocal") or p.get("uiTestBatch")]
            return self.sendJson({"pitches": pitches})

        if parsed.path == "/api/labels":
            return self.sendJson({"labels": readLabels(workspace)})

        if parsed.path == "/api/clip":
            try:
                entry = getClip((query.get("dir") or [""])[0])
                if entry is None:
                    raise ValueError("Could not decode this video")
                return self.sendJson({"nFrames": len(entry["jpegs"]), "fps": entry["fps"],
                                      "frameTimes": entry["frameTimes"]})
            except (ValueError, OSError) as error:
                return self.sendJson({"error": str(error)}, 400)

        if parsed.path == "/api/frame":
            dirName = (query.get("dir") or [""])[0]
            if not dirName or ".." in dirName or "/" in dirName:
                return self.sendJson({"error": "bad dir"}, 400)
            entry = getClip(dirName)
            if entry is None:
                return self.sendJson({"error": "could not decode clip"}, 404)
            try:
                frameIndex = int((query.get("frame") or ["0"])[0])
            except ValueError:
                return self.sendJson({"error": "bad frame"}, 400)
            if not (0 <= frameIndex < len(entry["jpegs"])):
                return self.sendJson({"error": "frame out of range"}, 404)
            body = entry["jpegs"][frameIndex]
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            if query.get("download"):
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{dirName}_f{frameIndex:04d}.jpg"')
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return None

        if parsed.path == "/api/prefetch":
            dirName = (query.get("dir") or [""])[0]
            if dirName and ".." not in dirName and "/" not in dirName:
                prefetchClip(dirName)
            return self.sendJson({"ok": True})

        if parsed.path == "/api/ball":
            dirName = (query.get("dir") or [""])[0]
            if not dirName or ".." in dirName or "/" in dirName:
                return self.sendJson({"error": "bad dir"}, 400)
            try:
                return self.sendJson(ballPoints(dirName, (query.get("frame") or ["0"])[0]))
            except Exception as error:  # noqa: BLE001
                return self.sendJson({"error": f"{type(error).__name__}: {error}"}, 500)

        if parsed.path == "/api/pose":
            dirName = (query.get("dir") or [""])[0]
            frameIndex = (query.get("frame") or ["0"])[0]
            pThrows = (query.get("throws") or ["R"])[0]
            if not dirName or ".." in dirName or "/" in dirName:
                return self.sendJson({"error": "bad dir"}, 400)
            try:
                return self.sendJson(posePoints(dirName, frameIndex, pThrows))
            except Exception as error:  # noqa: BLE001
                return self.sendJson({"error": f"{type(error).__name__}: {error}"}, 500)

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/api/label", "/api/import", "/api/delete-label", "/api/undo", "/api/predict-angle"):
            return self.sendJson({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 65536:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            if parsed.path == "/api/predict-angle":
                return self.sendJson(predictManualAngle(payload))
            if parsed.path == "/api/import":
                return self.sendJson({"ok": True, "pitch": registerLocalVideo(payload)})
            if parsed.path == "/api/undo":
                return self.sendJson({"ok": True, "total": undoLabel(payload.get("undoToken"))})
            if not payload.get("play_id") or payload.get("game_pk") in (None, ""):
                raise ValueError("Missing pitch identifier")
            if parsed.path == "/api/delete-label":
                return self.sendJson({"ok": True, "total": deleteLabel(payload)})
            total, token = writeLabel(payload, withUndo=True)
            return self.sendJson({"ok": True, "total": total, "undoToken": token, "path": labelPathFor(payload)})
        except (ValueError, TypeError, KeyError) as error:
            return self.sendJson({"error": str(error)}, 400)
        except OSError:
            return self.sendJson({"error": "Could not save to disk. Your previous labels are intact."}, 500)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    pitches = listPitches()
    print(f"Serving {savantUtils.PROJECT_DIR}")
    print(f"{len(pitches)} pitches with clips (frames decoded on demand)")
    if not pitches:
        print("  (none yet -- open a local video in the labeler, or run fetchPitchBatch.py)")
    print(f"Labels -> {LABELS_PATH}")
    print(f"\n  open http://127.0.0.1:{port}/labelTool.html\n", flush=True)

    # Warm the pose model off the request path -- cold load is ~8s, which would
    # otherwise land on the first seed of the session.
    threading.Thread(target=getPoseModel, daemon=True).start()

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
