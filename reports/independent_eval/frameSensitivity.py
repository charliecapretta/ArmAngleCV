"""
What a one-frame disagreement about release actually costs.

Release selection is the manual step with the least ambiguity, so it is the one an
independent annotator can repeat reliably -- but "reliably" only matters if the
downstream angle is insensitive to it. This measures that directly on the evaluation
clips: re-run the pose seeder at the chosen release frame and at +/-1 and +/-2, and
report how far the body points and the shoulder->wrist angle move.

No annotation and no reference angles are used, so this is safe to run before the
batch is unsealed.
"""
import json
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import features  # noqa: E402
import poseSeed  # noqa: E402

OFFSETS = (-2, -1, 0, 1, 2)


def main():
    picks = json.loads((HERE / 'releaseFrames_pass1.json').read_text())['picks']
    pitches = {p['evalId']: p for p in json.loads((HERE / 'evalSet.json').read_text())['pitches']}

    rows = []
    for evalId, release in picks.items():
        pitch = pitches[evalId]
        clip = HERE / pitch['clipPath']
        angles, points = {}, {}
        for offset in OFFSETS:
            _, person = poseSeed.seed(clip, release + offset, pitch['pThrows'])
            if person is None:
                continue
            angles[offset] = features.pixelAngle(person['shoulder_x'], person['shoulder_y'],
                                                 person['wrist_x'], person['wrist_y'])
            points[offset] = person
        if 0 not in angles:
            continue
        base = points[0]
        for offset in OFFSETS:
            if offset == 0 or offset not in angles:
                continue
            shift = max(float(np.hypot(points[offset][f'{n}_x'] - base[f'{n}_x'],
                                       points[offset][f'{n}_y'] - base[f'{n}_y']))
                        for n in ['shoulder', 'off_shoulder', 'elbow', 'wrist'])
            rows.append({'evalId': evalId, 'offset': offset,
                         'dAngle': abs(angles[offset] - angles[0]), 'maxPointShift': shift})

    print(f'{"offset":>7}{"n":>4}{"median |d wrist_angle|":>24}{"p90":>8}{"median max point shift":>25}')
    for offset in OFFSETS:
        if offset == 0:
            continue
        subset = [r for r in rows if r['offset'] == offset]
        if not subset:
            continue
        d = np.array([r['dAngle'] for r in subset])
        s = np.array([r['maxPointShift'] for r in subset])
        print(f'{offset:>+7}{len(subset):>4}{np.median(d):>22.1f} deg{np.percentile(d, 90):>8.1f}'
              f'{np.median(s):>22.1f} px')
    (HERE / 'frameSensitivity.json').write_text(json.dumps(rows, indent=2))
    print(f'\nwrote {HERE / "frameSensitivity.json"}')


if __name__ == '__main__':
    main()
