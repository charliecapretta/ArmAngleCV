"""
Unseal the reference angles and score the frozen model on the independent batch.

Run this ONLY after annotation is finished -- it prints Statcast arm_angle, which is
what the blinding protocol keeps hidden until then.

Annotations may come from either source:
  --annotations <file.json>   the JSON pass files in this directory
  --labels <file.csv>         a labels.csv-shaped export from the labeler, joined on
                              game_pk + play_id

Baselines are printed next to every metric, per the project's standing rule: the
trivial "predict the mean" floor, and raw `ball_angle` with no model at all.
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import joblib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
import angleInputs  # noqa: E402
import features  # noqa: E402

MODEL = ROOT / 'armAngleModelResolutionSafe.joblib'


def loadReference():
    sealed = json.loads((HERE / 'sealed' / 'reference.json').read_text())['pitches']
    return {entry['evalId']: entry for entry in sealed}


def loadAnnotations(args):
    pitches = {p['evalId']: p for p in json.loads((HERE / 'evalSet.json').read_text())['pitches']}
    if args.annotations:
        records = json.loads((HERE / args.annotations).read_text())
        for record in records:
            record.update({'img_w': pitches[record['evalId']]['imgW'],
                           'img_h': pitches[record['evalId']]['imgH']})
        return records
    frame = pd.read_csv(args.labels)
    byKey = {(int(p['gamePk']), p['playId']): p for p in pitches.values()}
    records = []
    for row in frame.to_dict('records'):
        key = (int(row['game_pk']), row['play_id'])
        if key not in byKey:
            continue
        record = {'evalId': byKey[key]['evalId'], 'frameIndex': row.get('frame_index'),
                  'img_w': row['img_w'], 'img_h': row['img_h']}
        record.update({c: row[c] for c in features.COORD_COLS})
        records.append(record)
    return records


def report(records, reference):
    bundle = joblib.load(MODEL)
    records = [r for r in records if r['evalId'] in reference]
    if not records:
        raise SystemExit('no annotated pitches match the sealed reference')

    truth = np.array([reference[r['evalId']]['armAngle'] for r in records], dtype=float)
    predicted = angleInputs.predict(bundle, records)
    raw = np.array([features.pixelAngle(r['shoulder_x'], r['shoulder_y'],
                                        r['ball_x'], r['ball_y']) for r in records], dtype=float)
    pitchers = np.array([reference[r['evalId']]['pitcherId'] for r in records])

    def line(name, values):
        error = values - truth
        print(f'{name:<34}RMSE {np.sqrt((error ** 2).mean()):7.3f}   '
              f'MAE {np.abs(error).mean():7.3f}   bias {error.mean():+7.3f}')

    print(f'independent evaluation -- n={len(records)} pitches, '
          f'{len(set(pitchers))} pitchers, none in the model\'s training set')
    print(f'model: {MODEL.name} (trained n={bundle["training"]["n"]}, '
          f'pitchers={bundle["training"]["pitchers"]})\n')
    line('predict the training mean', np.full_like(truth, 
         np.mean([reference[r['evalId']]['armAngle'] for r in records])))
    line('no model (raw ball_angle)', raw)
    line('frozen Ridge', predicted)
    print(f'\n{"evalId":<9}{"pitcher":>9}{"true":>8}{"pred":>8}{"err":>8}{"rawAng":>9}')
    order = np.argsort(truth)
    for i in order:
        r = records[i]
        print(f'{r["evalId"]:<9}{pitchers[i]:>9}{truth[i]:8.1f}{predicted[i]:8.1f}'
              f'{predicted[i] - truth[i]:+8.1f}{raw[i]:9.1f}')

    frame = pd.DataFrame({'pitcher': pitchers, 'err': predicted - truth})
    perPitcher = frame.groupby('pitcher')['err'].agg(['count', 'mean', 'std'])
    print(f'\nper-pitcher bias spread: sd {perPitcher["mean"].std(ddof=1):.2f} deg '
          f'over {len(perPitcher)} pitchers')
    out = HERE / 'evalResult.json'
    out.write_text(json.dumps({
        'n': len(records), 'pitchers': int(len(set(pitchers))),
        'rmse': float(np.sqrt(((predicted - truth) ** 2).mean())),
        'mae': float(np.abs(predicted - truth).mean()),
        'raw_rmse': float(np.sqrt(((raw - truth) ** 2).mean())),
        'per_pitch': [{'evalId': r['evalId'], 'true': float(t), 'pred': float(p)}
                      for r, t, p in zip(records, truth, predicted)]}, indent=2))
    print(f'\nwrote {out}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--annotations')
    parser.add_argument('--labels')
    args = parser.parse_args()
    if not (args.annotations or args.labels):
        parser.error('give --annotations or --labels')
    report(loadAnnotations(args), loadReference())


if __name__ == '__main__':
    main()
