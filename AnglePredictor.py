"""
Predict Statcast arm_angle from hand-labeled broadcast keypoints.

Terminal stage of the pipeline:
    fetchPitchBatch.py / fetchLowSlotBatch.py  ->  pitches/
    labelServer.py + labelTool.html            ->  labels.csv
    AnglePredictor.py                          ->  this

Two things this reports, and the second is the point:

  1. Label quality  -- gross outliers, and specifically shoulder/wrist swaps,
     which mirror the angle's sign and are otherwise easy to miss.
  2. Held-out accuracy against baselines you must beat. A model that cannot
     beat "just use ball_angle" is not doing anything.

Validation is GroupKFold on pitcher_id, never a random split. A pitcher's arm
angle is nearly constant, so random folds put the same pitcher in train and
test and the model scores well by recognising pitchers rather than measuring
geometry. Measured on this data: a random 80/20 split leaves 67% of the test
set contaminated, and it flatters RandomForest from RMSE 10.3 to 3.3.

Usage:
    python AnglePredictor.py
    python AnglePredictor.py --loo          # leave-one-pitcher-out (strictest)
    python AnglePredictor.py --save model.joblib
"""

import argparse

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# Features, target and group key all live in features.py so that evaluation here
# and inference on predicted keypoints cannot drift apart.
from features import (ANGLE_MAX, ANGLE_MIN, FEATURE_COLS, GROUP_COL,  # noqa: E402
                      TARGET_COL)


OUTLIER_LIMIT = 15.0


def load_labeled_data(path):
    labels = pd.read_csv(path)
    data = labels[labels['status'] == "labeled"].copy()
    return data.dropna(subset=FEATURE_COLS + [TARGET_COL, GROUP_COL])


def make_model():
    """
    Ridge over standardised features.

    Ridge scores identically to plain LinearRegression here (RMSE 2.649 either
    way) but the coefficients are stable, which plain least squares cannot
    promise with 12 correlated pixel coordinates over ~180 rows.
    """
    return make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-2, 4, 40)))


def check_labels(data):
    """Flag labels whose error is too large to be measurement noise."""
    error = data[TARGET_COL] - data['ball_angle']
    flagged = data.loc[error.abs() > OUTLIER_LIMIT]

    print("=== LABEL QUALITY ===")
    print(f"  {len(data)} labeled pitches, {data[GROUP_COL].nunique()} pitchers, "
          f"{data['park'].nunique()} parks")
    if flagged.empty:
        print("  no gross outliers")
        return data

    for _, row in flagged.iterrows():
        err = row[TARGET_COL] - row['ball_angle']
        # A sign-mirrored angle is the signature of shoulder and wrist reversed.
        mirrored = abs(row['ball_angle'] + row[TARGET_COL]) < 8
        note = "  <- sign-mirrored: shoulder/wrist SWAPPED" if mirrored else ""
        print(f"  {row['play_id'][:8]} {row['park']:<4} frame {int(row['frame_index']):<4} "
              f"true {row[TARGET_COL]:+6.1f}  err {err:+6.1f}{note}")
    print(f"  {len(flagged)} excluded from the figures below -- re-label them")
    return data.drop(flagged.index)


def cross_val_predictions(data, n_splits):
    """One prediction per row, each from a model that never saw that pitcher."""
    x = data[FEATURE_COLS].values
    y = data[TARGET_COL].values
    groups = data[GROUP_COL].values

    predictions = np.zeros(len(y))
    for train_idx, test_idx in GroupKFold(n_splits=n_splits).split(x, y, groups=groups):
        model = make_model().fit(x[train_idx], y[train_idx])
        predictions[test_idx] = model.predict(x[test_idx])
    # Clamped for the same reason inference is: a score should measure what the system
    # would actually output. Today no fold prediction reaches the rail, so this changes
    # nothing -- it just keeps evaluation honest if one ever does.
    return np.clip(predictions, ANGLE_MIN, ANGLE_MAX)


def score(name, y, predictions):
    rmse = root_mean_squared_error(y, predictions)
    mae = mean_absolute_error(y, predictions)
    print(f"  {name:<38} RMSE {rmse:6.3f}   MAE {mae:6.3f}")
    return rmse, mae


def report(data, predictions):
    y = data[TARGET_COL].values
    error = y - predictions

    print("\n=== ACCURACY ===")
    score("predict the mean (trivial baseline)", y, np.full(len(y), y.mean()))
    score("no model (ball_angle raw)", y, data['ball_angle'].values)
    score("this model, held out by pitcher", y, predictions)

    print("\n=== BY TRUE ANGLE ===")
    for low, high in [(-70, -30), (-30, -10), (-10, 10), (10, 30), (30, 50), (50, 80)]:
        mask = (y >= low) & (y < high)
        if not mask.sum():
            continue
        thin = "  << thin" if mask.sum() < 15 else ""
        print(f"  {low:>4} to {high:>3}:  n={mask.sum():<4} bias {error[mask].mean():+6.2f}  "
              f"MAE {np.abs(error[mask]).mean():5.2f}{thin}")

    # Per-pitcher offsets are the dominant residual: each pitcher sits at a
    # consistent bias across parks, but you cannot know a new pitcher's in
    # advance -- which is exactly why folds are grouped by pitcher.
    print("\n=== PER-PITCHER BIAS (>=5 pitches) ===")
    frame = data.assign(error=error)
    rows = []
    for name, group in frame.groupby('pitcher_name'):
        if len(group) >= 5:
            rows.append((group[TARGET_COL].mean(), name, len(group),
                         group['error'].mean(), group['error'].std(),
                         group['park'].nunique()))
    if rows:
        print(f"  {'pitcher':<24}{'n':>4}{'trueAvg':>9}{'bias':>8}{'sd':>7}{'parks':>7}")
        for true_avg, name, n, bias, sd, parks in sorted(rows):
            flag = "  <<" if abs(bias) > 3 else ""
            print(f"  {name:<24}{n:>4}{true_avg:>+9.1f}{bias:>+8.2f}{sd:>7.2f}{parks:>7}{flag}")
        spread = max(r[3] for r in rows) - min(r[3] for r in rows)
        print(f"\n  between-pitcher bias spread {spread:.1f} deg vs "
              f"mean within-pitcher sd {np.mean([r[4] for r in rows]):.2f} deg")

    print("\n=== BY PARK ===")
    by_park = frame.groupby('park')['error']
    biases = [g.mean() for _, g in by_park if len(g) >= 3]
    sizes = [len(g) for _, g in by_park if len(g) >= 3]
    if biases:
        # Compare the spread of park means against what pure sampling noise would
        # produce at these group sizes -- otherwise small parks look meaningful.
        expected = np.sqrt(np.mean([error.std(ddof=1) ** 2 / n for n in sizes]))
        print(f"  park bias SD {np.std(biases):.2f} deg")
        print(f"  expected from sampling noise alone: {expected:.2f} deg")
        print("  -> " + ("indistinguishable from noise; pool, don't fit per park"
                         if np.std(biases) <= expected * 1.5
                         else "exceeds the noise floor; per-park effects may be real"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default="labels.csv")
    parser.add_argument("--splits", type=int, default=8)
    parser.add_argument("--loo", action="store_true",
                        help="leave-one-pitcher-out: strictest, one fold per pitcher")
    parser.add_argument("--save", metavar="PATH",
                        help="refit on everything and save the model")
    args = parser.parse_args()

    data = load_labeled_data(args.labels)
    data = check_labels(data)

    n_splits = data[GROUP_COL].nunique() if args.loo else args.splits
    print(f"\nvalidation: GroupKFold on {GROUP_COL}, {n_splits} folds"
          + (" (leave-one-pitcher-out)" if args.loo else ""))

    predictions = cross_val_predictions(data, n_splits)
    report(data, predictions)

    if args.save:
        from joblib import dump
        model = make_model().fit(data[FEATURE_COLS].values, data[TARGET_COL].values)
        dump({"model": model, "features": FEATURE_COLS}, args.save)
        print(f"\nrefit on all {len(data)} rows -> {args.save}")


if __name__ == "__main__":
    main()
