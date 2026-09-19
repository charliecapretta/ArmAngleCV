"""Isolated, time-limited decoder. Never accumulates decoded frames in memory."""
import json
import math
import os
from pathlib import Path
import sys
import time

MAX_BYTES = 50 * 1024 * 1024
MAX_SECONDS = 30
MAX_FRAMES = 1800
MAX_FRAME_BYTES = 400 * 1024 * 1024


def prepare(directory):
    import cv2
    cv2.setNumThreads(1)
    root = Path(directory)
    source = root / 'source.mp4'
    if not 0 < source.stat().st_size <= MAX_BYTES:
        raise ValueError('Choose an MP4 up to 50 MB.')
    with source.open('rb') as handle:
        if handle.read(12)[4:8] != b'ftyp':
            raise ValueError('Choose a valid MP4 video.')
    capture = cv2.VideoCapture(str(source))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        claimed = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        width = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if not 0 < width <= 1920 or not 0 < height <= 1080:
            raise ValueError('Choose a video no larger than 1920 × 1080.')
        if not capture.isOpened() or not math.isfinite(fps) or not 0 < fps <= 60.01:
            raise ValueError('Choose an MP4 at 60 fps or less.')
        if not math.isfinite(claimed) or claimed > MAX_FRAMES or claimed / fps > MAX_SECONDS + .01:
            raise ValueError('Choose a clip of 30 seconds or less.')
        times = []
        total = 0
        dimensions = None
        deadline = time.monotonic() + 90
        while True:
            if time.monotonic() > deadline:
                raise ValueError('Preparation took too long; try a shorter clip.')
            ok, frame = capture.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            if not 0 < w <= 1920 or not 0 < h <= 1080 or (dimensions and dimensions != (w, h)):
                raise ValueError('Choose a video no larger than 1920 × 1080 with fixed dimensions.')
            dimensions = (w, h)
            if len(times) >= MAX_FRAMES or (len(times) + 1) / fps > MAX_SECONDS + .01:
                raise ValueError('Choose a clip of 30 seconds or less.')
            ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                raise ValueError('Could not prepare this frame.')
            total += jpeg.nbytes
            if total > MAX_FRAME_BYTES:
                raise ValueError('This clip needs too much storage; choose a shorter clip.')
            (root / f'{len(times):04d}.jpg').write_bytes(jpeg.tobytes())
            times.append(capture.get(cv2.CAP_PROP_POS_MSEC) / 1000)
        if not times or (claimed > 0 and abs(len(times) - claimed) > 1):
            raise ValueError('The clip is empty or incomplete.')
        if not all(math.isfinite(t) and t >= 0 for t in times) or not all(b > a for a, b in zip(times, times[1:])):
            times = [i / fps for i in range(len(times))]
        if times[-1] > MAX_SECONDS + .1:
            raise ValueError('Choose a clip of 30 seconds or less.')
        result = dict(nFrames=len(times), fps=fps, imgW=dimensions[0], imgH=dimensions[1], frameTimes=times)
        (root / 'meta.json').write_text(json.dumps(result))
        return result
    finally:
        capture.release()


if __name__ == '__main__':
    try:
        prepare(sys.argv[1])
    except Exception as error:
        print(str(error) if isinstance(error, ValueError) else 'Video preparation failed.', file=sys.stderr)
        sys.exit(1)
