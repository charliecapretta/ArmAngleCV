"""
Phase 4, step 4: what does the keypoint model cost you, in degrees?

Pixel error is a means, not the point. The question this project has always asked is
how close you land to Statcast's arm_angle, so every keypoint model is scored by
pushing its predictions through the same Ridge the hand labels use and reporting the
same three baselines the rest of the pipeline reports.

Validation is GroupKFold on pitcher_id, for the reason it always is here: a pitcher's
arm angle is nearly constant, so a random split leaves 67% of the test set contaminated
and flattered RandomForest from RMSE 10.3 to 3.3. Every keypoint prediction below comes
from a model that never saw that pitcher.

    ./.venv/bin/python phase4/evaluate.py                      # mean baseline, ~instant
    ./.venv/bin/python phase4/evaluate.py --model resnet --size 224
    ./.venv/bin/python phase4/evaluate.py --model resnet --size 320 --folds 5
"""

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import features
from AnglePredictor import cross_val_predictions, score
from phase4 import dataset, keypointModel

HAND_CLICKED_RMSE = 2.651   # hand-clicked everything -> Ridge, the ceiling
HAND_CLICKED_MAE = 2.135


TRAINABLE_RANGE = (-0.5, 1.5)


def outOfFoldKeypoints(images, targets, groups, folds, modelName, modelArgs):
    """One keypoint prediction per pitch, each from a model that never saw that pitcher."""
    predictions = np.zeros_like(targets)
    splitter = GroupKFold(n_splits=folds)

    # A crop where Stage A picked the wrong person puts its targets far outside the
    # frame -- one row sits at -1.04. Such a row is unlearnable and only adds noise, so
    # it is dropped from TRAINING. It stays in every test fold: you would never train on
    # a crop you know is wrong, but you must still be scored on it.
    low, high = TRAINABLE_RANGE
    trainable = ((targets >= low) & (targets <= high)).all(axis=(1, 2))
    if not trainable.all():
        print(f"  excluding {int((~trainable).sum())} unlearnable crop(s) from training "
              f"(kept in test)")

    for foldIndex, (trainIdx, testIdx) in enumerate(
            splitter.split(images, targets, groups=groups), start=1):
        started = time.time()
        trainIdx = trainIdx[trainable[trainIdx]]
        model = keypointModel.MODELS[modelName](**modelArgs)
        model.fit(images[trainIdx], targets[trainIdx])
        predictions[testIdx] = model.predict(images[testIdx])
        print(f"  fold {foldIndex}/{folds}: trained on {len(trainIdx)}, "
              f"predicted {len(testIdx)}  ({time.time() - started:.0f}s)", flush=True)
    return predictions


def featureFrame(pixels, index, swapFrom=None, swapNames=()):
    """
    Build a Ridge-ready frame from predicted pixel coordinates.

    swapFrom/swapNames drive the ablations: substitute a subset of the keypoints from
    another source (the hand labels) to see which ones actually cost you degrees.
    """
    rows = []
    for position in range(len(pixels)):
        coords = dataset.coordDict(pixels[position])
        if swapFrom is not None:
            other = dataset.coordDict(swapFrom[position])
            for name in swapNames:
                coords[f"{name}_x"] = other[f"{name}_x"]
                coords[f"{name}_y"] = other[f"{name}_y"]
        coords.update(features.deriveAngles(coords))
        coords[features.TARGET_COL] = index.iloc[position][features.TARGET_COL]
        coords[features.GROUP_COL] = index.iloc[position][features.GROUP_COL]
        coords["park"] = index.iloc[position]["park"]
        rows.append(coords)

    frame = pd.DataFrame(rows)
    for column in features.FEATURE_COLS:
        frame[column] = frame[column].astype(float)
    return frame


def throughRidge(frame, folds):
    """
    Ridge over predicted coordinates, itself held out by pitcher.

    Refit rather than reusing armAngleModel.joblib because that Ridge was fit on
    hand-clicked coordinates, and predicted coordinates carry their own systematic
    bias -- letting the Ridge see the distribution it will actually face is worth
    real degrees. Both are reported so the difference is visible.
    """
    splits = min(folds, frame[features.GROUP_COL].nunique())
    return cross_val_predictions(frame, splits)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="mean", choices=sorted(keypointModel.MODELS))
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--ridge-folds", type=int, default=8)
    parser.add_argument("--size", type=int, default=224)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--verbose", action="store_true",
                        help="print per-epoch training loss")
    args = parser.parse_args()

    images, targets, transforms, index = dataset.load()
    groups = index[features.GROUP_COL].values
    truth = index[features.TARGET_COL].values.astype(float)

    print(f"{len(images)} pitches, {index[features.GROUP_COL].nunique()} pitchers, "
          f"{index['park'].nunique()} parks")
    print(f"model '{args.model}', GroupKFold on {features.GROUP_COL}, "
          f"{args.folds} folds\n")

    modelArgs = {}
    if args.model != "mean":
        modelArgs = {"size": args.size, "epochs": args.epochs,
                     "seed": args.seed, "verbose": args.verbose}

    predicted = outOfFoldKeypoints(images, targets, groups, args.folds,
                                   args.model, modelArgs)

    predictedPixels = dataset.toPixels(predicted, transforms)
    handPixels = dataset.toPixels(targets, transforms)
    errors = np.linalg.norm(predictedPixels - handPixels, axis=2)

    print("\n=== KEYPOINT ERROR (held out by pitcher, full-frame pixels) ===")
    print(f"  {'keypoint':<16}{'median':>9}{'p90':>9}{'~deg':>8}   {'COCO':>7}")
    cocoMedians = {"shoulder": 10.7, "off_shoulder": 4.8, "elbow": 7.0, "wrist": 15.2,
                   "ball": None}
    for position, name in enumerate(features.KEYPOINT_NAMES):
        column = errors[:, position]
        coco = cocoMedians[name]
        cocoText = f"{coco:>7.1f}" if coco else f"{'--':>7}"
        print(f"  {name:<16}{np.median(column):>9.1f}{np.percentile(column, 90):>9.1f}"
              f"{np.median(column) * 0.54:>8.2f}   {cocoText}")
    print("  median px, p90 px, median converted to degrees, and COCO's median for "
          "comparison\n  (nothing off the shelf finds the ball at all)")

    print("\n=== END TO END: PREDICTED KEYPOINTS -> RIDGE -> arm_angle ===")
    frame = featureFrame(predictedPixels, index)
    ridgePredictions = throughRidge(frame, args.ridge_folds)

    score("predict the mean (trivial baseline)", truth, np.full(len(truth), truth.mean()))
    score("hand-clicked ball_angle, no model", truth,
          index["ball_angle_hand"].astype(float).values)
    score("predicted ball_angle, no model", truth, frame["ball_angle"].values)
    score("predicted keypoints -> Ridge (refit)", truth, ridgePredictions)

    # The Ridge that already exists was fitted on hand-clicked coordinates. Applying it
    # unchanged to predicted ones shows whether refitting is actually helping or whether
    # it is chasing noise in the predicted features.
    handFit = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "model", "armAngleModel.joblib")
    if os.path.exists(handFit):
        from joblib import load
        saved = load(handFit)
        score("predicted keypoints -> Ridge (hand-fit)", truth,
              np.clip(saved["model"].predict(frame[features.FEATURE_COLS].values),
                      features.ANGLE_MIN, features.ANGLE_MAX))
    print(f"  {'hand-clicked keypoints -> Ridge (ceiling)':<38} "
          f"RMSE {HAND_CLICKED_RMSE:6.3f}   MAE {HAND_CLICKED_MAE:6.3f}")

    rmse = float(np.sqrt(np.mean((truth - ridgePredictions) ** 2)))
    verdict = ("strong -- near the hand-label ceiling" if rmse < 4 else
               "useful -- a real automatic measurement" if rmse < 6 else
               "marginal -- worse than raw hand-clicked ball_angle at 3.23" if rmse < 8 else
               "under-powered at n=180; the fix is more labels, not more architecture")
    print(f"\n  verdict: {rmse:.2f} deg -> {verdict}")

    print("\n=== ABLATION: WHICH KEYPOINT COSTS YOU? ===")
    print("  (predicted keypoints, except the named ones taken from the hand labels)")
    for label, swap in (("hand ball only", ["ball"]),
                        ("hand shoulder only", ["shoulder"]),
                        ("hand body, predicted ball", ["shoulder", "off_shoulder",
                                                       "elbow", "wrist"]),
                        ("hand everything", list(features.KEYPOINT_NAMES))):
        ablated = featureFrame(predictedPixels, index, swapFrom=handPixels, swapNames=swap)
        score(label, truth, throughRidge(ablated, args.ridge_folds))

    print("\n=== BY TRUE ANGLE ===")
    residual = truth - ridgePredictions
    for low, high in [(-70, -30), (-30, -10), (-10, 10), (10, 30), (30, 50), (50, 80)]:
        mask = (truth >= low) & (truth < high)
        if not mask.sum():
            continue
        thin = "  << thin" if mask.sum() < 15 else ""
        print(f"  {low:>4} to {high:>3}:  n={mask.sum():<4} bias {residual[mask].mean():+6.2f}  "
              f"MAE {np.abs(residual[mask]).mean():5.2f}{thin}")

    outPath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           f"predictions-{args.model}.csv")
    pd.DataFrame({
        "play_id": index["play_id"], "pitcher_name": index["pitcher_name"],
        "park": index["park"], "true_arm_angle": truth,
        "predicted": ridgePredictions, "error": residual,
    }).to_csv(outPath, index=False)
    print(f"\nwrote {outPath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
