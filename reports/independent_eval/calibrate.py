"""
Measure the annotator's own click error against the human labels the model was
trained on.

Why this exists: the evaluation below asks a NEW annotator to click keypoints on
unseen pitches. Whatever RMSE that produces is model error plus annotator error,
and the two are not separable from the eval set alone. So first re-click a handful
of TRAINING frames, where the human's clicks already exist, and measure the gap.

Uses the human's release frame_index on purpose: this isolates point placement from
release-frame choice, which is measured separately.

  targets   -> calibrationTargets.json  (clip + frame only, no coordinates)
  score     -> compares annotations.json against the sealed human clicks
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import features  # noqa: E402
import savantUtils as s  # noqa: E402

OUT = Path(__file__).resolve().parent
N = 6
SEED = 411


def targets():
    labels = pd.read_csv(ROOT / 'labels.csv')
    usable = labels.dropna(subset=features.COORD_COLS + ['frame_index', 'play_id'])
    picked = usable.sample(N, random_state=SEED)

    visible, sealed = [], []
    for _, row in picked.iterrows():
        dirName = s.pitchDirName(int(row['game_pk']), row['play_id'])
        clip = ROOT / 'pitches' / dirName / 'clip.mp4'
        if not clip.is_file():
            continue
        visible.append({'calId': f'cal{len(visible) + 1:02d}', 'dirName': dirName,
                        'clipPath': str(clip), 'frameIndex': int(row['frame_index']),
                        'pThrows': str(row['p_throws']),
                        'imgW': int(row['img_w']), 'imgH': int(row['img_h']),
                        'pitcherName': str(row['pitcher_name'])})
        sealed.append({'calId': visible[-1]['calId'],
                       **{c: float(row[c]) for c in features.COORD_COLS},
                       'true_arm_angle': float(row['true_arm_angle']),
                       'ball_angle': float(row['ball_angle'])})

    (OUT / 'calibrationTargets.json').write_text(json.dumps(visible, indent=2))
    (OUT / 'sealed' / 'calibrationTruth.json').write_text(json.dumps(sealed, indent=2))
    print(f'{len(visible)} calibration frames written; human clicks sealed')


def score():
    mine = {a['calId']: a for a in json.loads((OUT / 'calibrationAnnotations.json').read_text())}
    truth = {t['calId']: t for t in json.loads((OUT / 'sealed' / 'calibrationTruth.json').read_text())}

    rows = []
    for calId, human in truth.items():
        if calId not in mine:
            continue
        got = mine[calId]
        entry = {'calId': calId}
        for name in features.KEYPOINT_NAMES:
            dx = got[f'{name}_x'] - human[f'{name}_x']
            dy = got[f'{name}_y'] - human[f'{name}_y']
            entry[name] = float(np.hypot(dx, dy))
        entry['ball_angle_mine'] = features.pixelAngle(
            got['shoulder_x'], got['shoulder_y'], got['ball_x'], got['ball_y'])
        entry['ball_angle_human'] = human['ball_angle']
        entry['ball_angle_delta'] = entry['ball_angle_mine'] - human['ball_angle']
        rows.append(entry)

    frame = pd.DataFrame(rows)
    print(frame.round(2).to_string(index=False))
    print('\nmedian px error per keypoint:')
    for name in features.KEYPOINT_NAMES:
        print(f'  {name:<13} {frame[name].median():6.1f} px   (max {frame[name].max():.1f})')
    delta = frame['ball_angle_delta']
    print(f'\nball_angle vs human: bias {delta.mean():+.2f} deg, '
          f'sd {delta.std(ddof=1):.2f}, MAE {delta.abs().mean():.2f}, '
          f'max |err| {delta.abs().max():.2f}')
    (OUT / 'calibrationResult.json').write_text(frame.to_json(orient='records', indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['targets', 'score'])
    args = parser.parse_args()
    targets() if args.command == 'targets' else score()
