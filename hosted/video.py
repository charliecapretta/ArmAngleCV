"""Isolated, time-limited decoder. Never accumulates decoded frames in memory."""
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

MAX_BYTES = 200 * 1024 * 1024
MAX_SECONDS = 60
MAX_FRAMES = 3600
MAX_FRAME_BYTES = 800 * 1024 * 1024


def probeDuration(source):
    """Reject unknown, non-finite or excessive duration before decoding any frame."""
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                 '-show_entries', 'format=duration:stream=duration',
                                 '-of', 'json', str(source)], capture_output=True, text=True,
                                timeout=10, check=True)
        probe = json.loads(result.stdout)
        durations = [float(probe['format']['duration'])]
        streams = probe.get('streams', [])
        if not streams:
            raise ValueError('No video stream.')
        durations += [float(stream['duration']) for stream in streams if 'duration' in stream]
        if any(not math.isfinite(duration) or duration <= 0 for duration in durations):
            raise ValueError('Invalid duration.')
    except FileNotFoundError as error:
        raise ValueError('Video preparation is unavailable: ffprobe is not installed.') from error
    except (subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        raise ValueError('Could not verify clip duration. Choose a valid MP4 of 60 seconds or less.') from error
    if max(durations) > MAX_SECONDS:
        raise ValueError('Choose a clip of 60 seconds or less.')
    return max(durations)


def prepare(directory):
    from PIL import Image
    import cv2
    cv2.setNumThreads(1)
    root = Path(directory)
    source = root / 'source.mp4'
    if not 0 < source.stat().st_size <= MAX_BYTES:
        raise ValueError('Choose an MP4 up to 200 MB.')
    with source.open('rb') as handle:
        if handle.read(12)[4:8] != b'ftyp':
            raise ValueError('Choose a valid MP4 video.')
    probeDuration(source)
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
            raise ValueError('Choose a clip of 60 seconds or less.')
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
                raise ValueError('Choose a clip of 60 seconds or less.')
            # Preserve original pixels for precise joint placement. Scrubbing is
            # 640px/q60; selecting a release always loads the full q78 version.
            scrubWidth = min(640, w)
            scrub = cv2.resize(frame, (scrubWidth, max(1, round(h * scrubWidth / w))),
                               interpolation=cv2.INTER_AREA)
            for suffix, pixels, quality in [('.webp', frame, 78), ('.scrub.webp', scrub, 60)]:
                encoded = io.BytesIO()
                # Method 1 bounds encoder CPU while retaining q78 joint detail.
                Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)).save(
                    encoded, format='WEBP', quality=quality, method=1 if suffix == '.webp' else 2)
                frameBytes = encoded.getvalue()
                total += len(frameBytes)
                if total > MAX_FRAME_BYTES:
                    raise ValueError('This clip needs too much storage; choose a shorter clip.')
                (root / f'{len(times):04d}{suffix}').write_bytes(frameBytes)
            times.append(capture.get(cv2.CAP_PROP_POS_MSEC) / 1000)
        if not times or (claimed > 0 and abs(len(times) - claimed) > 1):
            raise ValueError('The clip is empty or incomplete.')
        if not all(math.isfinite(t) and t >= 0 for t in times) or not all(b > a for a, b in zip(times, times[1:])):
            times = [i / fps for i in range(len(times))]
        if times[-1] > MAX_SECONDS + .1:
            raise ValueError('Choose a clip of 60 seconds or less.')
        result = dict(nFrames=len(times), fps=fps, imgW=dimensions[0], imgH=dimensions[1], frameTimes=times,
                      frameFormat='webp', scrubWidth=min(640, dimensions[0]))
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
