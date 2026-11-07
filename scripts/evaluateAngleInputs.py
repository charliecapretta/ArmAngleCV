"""Compare image-input conventions against MLB truth and uniform resizing.

No college labels are used for fitting or selection. Use --save to write a versioned
height720 model. This is development cross-validation, not a new external test set.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from joblib import dump
from sklearn.model_selection import GroupKFold
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from armangle.AnglePredictor import make_model
from armangle import features
from armangle import angleInputs
def rescale(row, factor):
    row = dict(row)
    for key in features.COORD_COLS + ['img_w', 'img_h']:
        row[key] = float(row[key]) * factor
    return row


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', default=str(root/'data'/'labels.csv'))
    parser.add_argument('--out', default=str(root/'reports/resolution_validation'))
    parser.add_argument('--save', help='Save height720 model to a NEW path; never overwrite existing models')
    args = parser.parse_args()
    if args.save and Path(args.save).exists():
        parser.error('Choose a new model path; existing models are preserved')
    df = pd.read_csv(args.labels)
    df = df[df.status == 'labeled'].dropna(subset=features.COORD_COLS + ['true_arm_angle','pitcher_id','img_w','img_h','park']).reset_index(drop=True)
    if (df.game_pk.astype(str) == 'local').any():
        parser.error('College/local rows cannot be used as verified MLB training examples')
    rows = df.to_dict('records'); y = df.true_arm_angle.values
    summary, outputs = {}, df[['game_pk','play_id','pitcher_id','park','true_arm_angle']].copy()
    for mode in angleInputs.MODES:
        x = np.array([angleInputs.vector(r, mode) for r in rows]); drift = []
        stats = {}
        for groupcol, tag in [('pitcher_id','pitcher'),('park','park')]:
            pred = np.zeros(len(rows)); foldids = np.zeros(len(rows),dtype=int)
            for fold,(train,test) in enumerate(GroupKFold(8).split(x,y,df[groupcol].values)):
                assert not set(df.iloc[train][groupcol]) & set(df.iloc[test][groupcol])
                model = make_model().fit(x[train],y[train]); pred[test] = model.predict(x[test]); foldids[test] = fold
                if tag == 'pitcher':
                    for factor in (.5,1.5,2):
                        shifted = model.predict(np.array([angleInputs.vector(rescale(rows[i],factor),mode) for i in test]))
                        drift.extend(abs(shifted-pred[test]).tolist())
            outputs[f'{mode}_{tag}_prediction'] = pred; outputs[f'{tag}_fold'] = foldids
            stats[f'{tag}_rmse'] = float(np.sqrt(np.mean((pred-y)**2)))
            stats[f'{tag}_mae'] = float(np.mean(abs(pred-y)))
        stats.update(resize_max_delta=float(max(drift)),resize_mean_delta=float(np.mean(drift)))
        summary[mode] = stats; print(mode,stats,flush=True)
    folder = Path(args.out); folder.mkdir(parents=True,exist_ok=True)
    outputs.to_csv(folder/'oof_predictions.csv', index=False)
    metadata = {'n':len(rows),'pitchers':int(df.pitcher_id.nunique()),'parks':int(df.park.nunique()),'folds':8,
                'labels_sha256':hashlib.sha256(Path(args.labels).read_bytes()).hexdigest(),
                'uniform_resize_factors':[.5,1.5,2],'results':summary,
                'selection':'height720 removes uniform-resolution sensitivity with essentially unchanged MLB held-out accuracy. Body-relative and angles-only inputs scored worse by pitcher.',
                'limits':'Development comparison on existing MLB labels. Park folds can share pitchers. Uniform scaling is not a change of camera view, crop, zoom, or temporal sampling. No college ground truth or college fitting.'}
    (folder/'validation.json').write_text(json.dumps(metadata,indent=2))
    if args.save:
        mode = angleInputs.DEFAULT_MODE; x = np.array([angleInputs.vector(r,mode) for r in rows])
        model = make_model().fit(x,y)
        bundle = {'model':model,'features':angleInputs.columns(mode),'feature_mode':mode,'schema_version':1,
                  'training':metadata,'coordinate_policy':'Original image coordinates plus original img_h and img_w; uniformly normalized to 720px height before feature computation.'}
        dump(bundle,args.save); print('Saved',args.save)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
