"""
Test-retest of the release-frame step, in frames and in degrees.

Release selection is annotated twice, independently of the reference angles, and the
disagreement is converted into what it actually costs: the shoulder->wrist angle at
each of the two chosen frames, using the same pose seeder both times so the only
thing varying is the frame.
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


def main():
    one = json.loads((HERE / 'releaseFrames_pass1.json').read_text())['picks']
    two = json.loads((HERE / 'releaseFrames_pass2.json').read_text())['picks']
    pitches = {p['evalId']: p for p in json.loads((HERE / 'evalSet.json').read_text())['pitches']}

    rows = []
    for evalId in sorted(one):
        pitch = pitches[evalId]
        clip = HERE / pitch['clipPath']
        angles = {}
        for label, frame in (('p1', one[evalId]), ('p2', two[evalId])):
            _, person = poseSeed.seed(clip, frame, pitch['pThrows'])
            angles[label] = None if person is None else features.pixelAngle(
                person['shoulder_x'], person['shoulder_y'], person['wrist_x'], person['wrist_y'])
        if None in angles.values():
            continue
        rows.append({'evalId': evalId, 'pitcher': pitch['pitcherName'],
                     'f1': one[evalId], 'f2': two[evalId],
                     'dFrames': two[evalId] - one[evalId],
                     'a1': angles['p1'], 'a2': angles['p2'],
                     'dAngle': angles['p2'] - angles['p1']})

    print(f'{"evalId":<9}{"pitcher":<20}{"pass1":>7}{"pass2":>7}{"d frames":>10}'
          f'{"wrist ang 1":>13}{"wrist ang 2":>13}{"d deg":>9}')
    for r in rows:
        print(f'{r["evalId"]:<9}{r["pitcher"][:19]:<20}{r["f1"]:>7}{r["f2"]:>7}'
              f'{r["dFrames"]:>+10}{r["a1"]:>13.1f}{r["a2"]:>13.1f}{r["dAngle"]:>+9.1f}')

    df = np.array([r['dFrames'] for r in rows], dtype=float)
    da = np.array([r['dAngle'] for r in rows], dtype=float)
    print(f'\nrelease frame: exact agreement {int((df == 0).sum())}/{len(df)}, '
          f'within +/-1 {int((np.abs(df) <= 1).sum())}/{len(df)}, '
          f'mean |d| {np.abs(df).mean():.2f} frames')
    print(f'shoulder->wrist angle: mean |d| {np.abs(da).mean():.2f} deg, '
          f'median |d| {np.median(np.abs(da)):.2f}, max |d| {np.abs(da).max():.2f}, '
          f'sd of signed d {da.std(ddof=1):.2f}')
    (HERE / 'consistencyResult.json').write_text(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
