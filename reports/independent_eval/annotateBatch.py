"""
Per-pitch annotation aids for the independent evaluation, batched.

Two stages, matching how the labeler works:

  find <evalId...>    delivery strip around the usual release window, cropped to the
                      detected pitcher, so the annotator can pick the release frame.
  points <evalId:frame ...>
                      pose overlay (body-point suggestion, to verify/correct) plus a
                      ball-search strip (release frame and the frames after it).

Pose supplies only a SUGGESTION for the four body points, which is what `labels.csv`
provenance shows the human did on much of the training set. The ball is never
suggested: it is placed by the annotator, on every row, exactly as in training.
"""
from pathlib import Path
import subprocess
import sys
import json

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import poseSeed  # noqa: E402

PROBE_FRAME = 176   # median release over 196 training labels is 178; probe just before
WINDOW = (164, 200)


def evalSet():
    return {p['evalId']: p for p in json.loads((HERE / 'evalSet.json').read_text())['pitches']}


def deliveryStrip(pitch, window=WINDOW, step=2, zoom='2.4'):
    clip = HERE / pitch['clipPath']
    _, person = poseSeed.seed(clip, PROBE_FRAME, pitch['pThrows'])
    if person is None:
        box = [520, 340, 700, 600]
    else:
        box = person['box']
    pad = 60
    x0, y0 = int(max(0, box[0] - pad)), int(max(0, box[1] - pad))
    x1, y1 = int(min(1280, box[2] + pad)), int(min(720, box[3] + pad))
    subprocess.run(['./.venv/bin/python', str(HERE / 'render.py'), 'strip', str(clip),
                    str(window[0]), str(window[1]), str(step), str(x0), str(y0), str(x1), str(y1), zoom],
                   cwd=ROOT, check=True)


def pointsFor(pitch, frameIndex):
    clip = HERE / pitch['clipPath']
    image, person = poseSeed.seed(clip, frameIndex, pitch['pThrows'])
    if person is None:
        print(f'{pitch["evalId"]}: NO PERSON at f{frameIndex}')
        return None
    stem = Path(clip).parent.name[:24]
    poseSeed.overlay(image, person, frameIndex, stem, pad=60, scale=5)
    subprocess.run(['./.venv/bin/python', str(HERE / 'ballStrip.py'), str(clip),
                    str(frameIndex), str(person['wrist_x']), str(person['wrist_y']),
                    '10', '2', '4'], cwd=ROOT, check=True)
    person['evalId'] = pitch['evalId']
    person['frameIndex'] = frameIndex
    return person


def main():
    mode, args = sys.argv[1], sys.argv[2:]
    pitches = evalSet()
    if mode == 'find':
        for evalId in args:
            deliveryStrip(pitches[evalId])
    elif mode == 'find1':
        start, end = int(args[0]), int(args[1])
        for evalId in args[2:]:
            deliveryStrip(pitches[evalId], window=(start, end), step=1, zoom='2.0')
    else:
        seeded = {}
        for token in args:
            evalId, frame = token.split(':')
            person = pointsFor(pitches[evalId], int(frame))
            if person:
                seeded[evalId] = person
        path = HERE / 'poseSeeds.json'
        existing = json.loads(path.read_text()) if path.exists() else {}
        existing.update(seeded)
        path.write_text(json.dumps(existing, indent=2))
        for evalId, p in seeded.items():
            print(f'{evalId} f{p["frameIndex"]}  sh({p["shoulder_x"]:.0f},{p["shoulder_y"]:.0f}) '
                  f'off({p["off_shoulder_x"]:.0f},{p["off_shoulder_y"]:.0f}) '
                  f'el({p["elbow_x"]:.0f},{p["elbow_y"]:.0f}) '
                  f'wr({p["wrist_x"]:.0f},{p["wrist_y"]:.0f}) conf {p["shoulder_conf"]:.2f}')


if __name__ == '__main__':
    main()
