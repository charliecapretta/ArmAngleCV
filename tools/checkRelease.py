"""Check released data/model identity and reproduce the grouped manual score."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from joblib import load
from sklearn.model_selection import GroupKFold

from AnglePredictor import make_model
import angleInputs
import features


def main():
    manifest = json.loads((ROOT/'model'/'model-manifest.json').read_text())
    for path, expected in manifest['sha256'].items():
        actual = hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'Release identity changed: {path}. Version the artifact deliberately.')
    bundle = load(ROOT/manifest['model'])
    if bundle['training'] != manifest['training']:
        raise ValueError('Model training metadata differs from its release manifest')
    data = pd.read_csv(ROOT/manifest['training_labels'])
    data = data[data.status == 'labeled'].dropna(subset=features.COORD_COLS+[features.TARGET_COL,features.GROUP_COL])
    if (len(data), data.pitcher_id.nunique(), data.park.nunique()) != (180, 98, 30):
        raise ValueError('Unexpected training snapshot coverage')
    if data.duplicated(['game_pk','play_id']).any():
        raise ValueError('Duplicate training identifiers')
    rows = data.to_dict('records')
    x = np.array([angleInputs.vector(r, bundle['feature_mode']) for r in rows])
    y = data.true_arm_angle.to_numpy()
    predictions = np.zeros(len(y))
    for train, test in GroupKFold(8).split(x, y, data.pitcher_id):
        if set(data.iloc[train].pitcher_id) & set(data.iloc[test].pitcher_id):
            raise ValueError('Pitcher overlap in validation folds')
        predictions[test] = make_model().fit(x[train], y[train]).predict(x[test])
    rmse = float(np.sqrt(np.mean((predictions-y)**2)))
    expected = manifest['training']['results']['height720']['pitcher_rmse']
    np.testing.assert_allclose(rmse, expected, rtol=0, atol=1e-5)
    fitted = make_model().fit(x, y).predict(x)
    released = angleInputs.predict(bundle, rows)
    np.testing.assert_allclose(released, np.clip(fitted, -90, 90), rtol=0, atol=1e-5)
    resized = [{k:(float(v)*2 if k in features.COORD_COLS+['img_w','img_h'] else v)
                for k,v in r.items()} for r in rows]
    np.testing.assert_allclose(released, angleInputs.predict(bundle, resized), rtol=0, atol=1e-8)
    raw = data.ball_angle.to_numpy()
    print(f'manual-v1: 180 labels / 98 pitchers; baseline RMSE {np.sqrt(np.mean((raw-y)**2)):.3f}°; model RMSE {rmse:.3f}°')
    print('Artifact checksums, refit identity, grouped score, and resize invariance verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
