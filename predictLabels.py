"""Predict angles from saved hand labels without running automatic point detection.

    ./.venv/bin/python predictLabels.py --labels localLabels.csv
    ./.venv/bin/python predictLabels.py --model armAngleModel.joblib --out oldPredictions.csv
"""
import argparse
import csv
from pathlib import Path
from collections import defaultdict
import numpy as np
from joblib import load
import angleInputs
import features


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', default=str(root/'localLabels.csv'))
    parser.add_argument('--model', default=str(root/angleInputs.DEFAULT_MODEL))
    parser.add_argument('--out', default=str(root/'reports/hand_label_predictions/resolution_safe_predictions.csv'))
    args = parser.parse_args()
    labels, out, model = Path(args.labels).resolve(), Path(args.out).resolve(), Path(args.model).resolve()
    if out in (labels, model):
        parser.error('Output must be separate from labels and model files')
    with labels.open(newline='') as f:
        rows = [r for r in csv.DictReader(f) if r.get('status') == 'labeled']
    bundle = load(model)
    predictions = angleInputs.predict(bundle, rows)
    if not rows:
        print('No complete labeled rows to predict. No output written.')
        return 1
    output, groups = [], defaultdict(list)
    print('Time (s)    Frame    Prediction')
    for row, value in zip(rows, predictions):
        result = {k: row.get(k, '') for k in ('game_pk','play_id','frame_index','release_time_sec','source_video','release_quality','point_quality')}
        result.update(features.deriveAngles(row))
        result.update(predicted_arm_angle=float(value), feature_mode=bundle.get('feature_mode','raw'))
        output.append(result)
        source = row.get('source_video') or f"{row.get('game_pk')}_{row.get('play_id')}"
        groups[(source, row['frame_index'])].append(float(value))
        print(f"{row.get('release_time_sec',''):>10} {row['frame_index']:>7} {value:>11.2f}°")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(output[0])); writer.writeheader(); writer.writerows(output)
    print(f'\nMean over {len(groups)} unique frames: {np.mean([np.mean(v) for v in groups.values()]):.2f}°')
    print(f'MLB-trained estimate; college accuracy is unverified.\nSaved: {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
