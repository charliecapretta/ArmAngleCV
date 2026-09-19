"""Bounded MP4 upload validation for the manual workflow (no pose dependencies)."""
import math
import os
import time

MAX_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 60
MAX_FPS = 60
MAX_FRAMES = 3600
MAX_WIDTH = 1920
MAX_HEIGHT = 1080


def validateVideo(path):
    import cv2
    if not 0 < os.path.getsize(path) <= MAX_BYTES:
        raise ValueError('Choose an MP4 no larger than 100 MB.')
    with open(path, 'rb') as handle:
        header = handle.read(64)
    if header[4:8] != b'ftyp':
        raise ValueError('This file is not a supported MP4 video.')
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError('This MP4 could not be decoded. Try exporting it as H.264 MP4.')
        fps = capture.get(cv2.CAP_PROP_FPS)
        claimed = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not math.isfinite(fps) or not 0 < fps <= MAX_FPS + .01:
            raise ValueError('Choose a video with a valid frame rate of 60 fps or less.')
        if not math.isfinite(claimed) or claimed > MAX_FRAMES or claimed / fps > MAX_SECONDS + .001:
            raise ValueError('Choose a clip of 60 seconds or less (maximum 3,600 frames).')
        times, width, height = [], None, None
        deadline = time.monotonic() + 120
        while True:
            if time.monotonic() > deadline:
                raise ValueError('Video preparation took too long. Try a shorter clip.')
            ok, frame = capture.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            if not (0 < w <= MAX_WIDTH and 0 < h <= MAX_HEIGHT):
                raise ValueError('Choose a landscape video no larger than 1920 × 1080.')
            if width is not None and (w, h) != (width, height):
                raise ValueError('Video dimensions change within this clip. Re-export it as one MP4.')
            width, height = w, h
            times.append(capture.get(cv2.CAP_PROP_POS_MSEC) / 1000)
            if len(times) > MAX_FRAMES or len(times) / fps > MAX_SECONDS + .001:
                raise ValueError('Choose a clip of 60 seconds or less (maximum 3,600 frames).')
        if not times or (claimed > 0 and abs(len(times) - claimed) > 1):
            raise ValueError('The video is empty or could not be decoded completely.')
        validTimes = all(math.isfinite(t) and t >= 0 for t in times) and all(b > a for a, b in zip(times, times[1:]))
        if validTimes and times[-1] - times[0] > MAX_SECONDS:
            raise ValueError('Choose a clip of 60 seconds or less.')
        return dict(nFrames=len(times), fps=fps, imgW=width, imgH=height)
    finally:
        capture.release()
