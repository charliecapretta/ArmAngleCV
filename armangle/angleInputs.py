"""Versioned inputs for models trained from manually labeled points.

The stored annotations always remain in original image pixels. Height normalization
is uniform, so it preserves geometry; it fixes resizing, not crop/zoom/view changes.
"""
import math
import sys

import numpy as np

from armangle import features
MODES = ('raw', 'height720', 'angles_only', 'body_relative')
DEFAULT_MODE = 'height720'
DEFAULT_MODEL = 'model/armAngleModelResolutionSafe.joblib'


def columns(mode):
    if mode in ('raw', 'height720'):
        return list(features.FEATURE_COLS)
    if mode == 'angles_only':
        return ['ball_angle', 'wrist_angle']
    if mode == 'body_relative':
        return ['ball_angle', 'wrist_angle'] + [f'{p}_{a}_relative' for p in features.KEYPOINT_NAMES[1:] for a in ('x', 'y')]
    raise ValueError(f'Unknown feature mode: {mode}')


def vector(row, mode=DEFAULT_MODE):
    columns(mode)  # Reject unknown modes before constructing an input.
    coords = {}
    for key in features.COORD_COLS:
        try:
            value = float(row[key])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f'Missing or invalid point: {key}') from None
        if not math.isfinite(value):
            raise ValueError(f'Non-finite point: {key}')
        coords[key] = value
    if mode == 'height720':
        try:
            height, width = float(row['img_h']), float(row['img_w'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Original image width and height are required') from None
        if not all(math.isfinite(v) and v > 0 for v in (height, width)):
            raise ValueError('Original image dimensions must be positive and finite')
        coords = {key: value * 720.0 / height for key, value in coords.items()}
    if mode in ('raw', 'height720'):
        return features.featureVector(coords)
    angles = features.deriveAngles(coords)
    result = [angles['ball_angle'], angles['wrist_angle']]
    if mode == 'angles_only':
        return result
    pts = np.array([[coords[p+'_x'], coords[p+'_y']] for p in features.KEYPOINT_NAMES])
    delta = pts - pts[0]
    baseline = float(np.linalg.norm(delta[4]))
    if baseline <= 1e-8:
        raise ValueError('Shoulder and ball must be separate for relative inputs')
    delta[:, 0] *= 1 if delta[4, 0] >= 0 else -1
    return result + (delta[1:] / baseline).ravel().tolist()


def predict(bundle, rows):
    mode = bundle.get('feature_mode', 'raw')  # Existing saved models remain readable.
    if bundle.get('features') != columns(mode):
        raise ValueError('Model feature schema does not match its declared input mode')
    if not rows:
        return np.array([], dtype=float)
    x = np.array([vector(row, mode) for row in rows], dtype=float)
    result = np.asarray(bundle['model'].predict(x), dtype=float)
    if not np.isfinite(result).all():
        raise ValueError('Model returned a non-finite prediction')
    # Guard rail, not a correction: the Ridge is linear and unbounded, so bad keypoints
    # can push it outside the physically possible range. Clamping keeps a nonsense
    # number from reaching a caller, but a clamped prediction is still a wrong one --
    # so say so rather than hiding it.
    outside = int(((result < features.ANGLE_MIN) | (result > features.ANGLE_MAX)).sum())
    if outside:
        print(f'angleInputs.predict: clamped {outside} of {len(result)} prediction(s) '
              f'to [{features.ANGLE_MIN:g}, {features.ANGLE_MAX:g}] -- the keypoints '
              f'behind them are probably wrong', file=sys.stderr)
    return np.clip(result, features.ANGLE_MIN, features.ANGLE_MAX)
