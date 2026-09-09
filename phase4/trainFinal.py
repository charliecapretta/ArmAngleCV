"""
Fit the keypoint model on all 180 pitches and save it, plus a Ridge to match.

Separate from evaluate.py on purpose. evaluate.py answers "how good is this?" and must
never let a model see a pitcher it will be scored on. This answers "give me the model to
actually use", where holding data back would only make it worse.

The Ridge saved here is refit on the model's own out-of-fold keypoint predictions rather
than on the hand clicks. armAngleModel.joblib was fit on hand-clicked coordinates, and
predicted coordinates carry their own systematic bias; letting the Ridge see the
distribution it will actually face at inference is worth real degrees.

    ./.venv/bin/python phase4/trainFinal.py                  # -> phase4/keypoints.pt
    ./.venv/bin/python phase4/trainFinal.py --epochs 150
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import features
from phase4 import dataset, evaluate, keypointModel

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "keypoints.pt")
RIDGE_PATH = os.path.join(HERE, "armAngleFromPredicted.joblib")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=224)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--skip-ridge", action="store_true",
                        help="train only the keypoint model; reuse armAngleModel.joblib")
    parser.add_argument("--out", default=MODEL_PATH)
    parser.add_argument("--ridge", default=RIDGE_PATH)
    args = parser.parse_args()

    import torch
    from joblib import dump

    images, targets, transforms, index = dataset.load()
    groups = index[features.GROUP_COL].values

    # Out-of-fold keypoints first: the Ridge must be fitted on predictions from models
    # that never saw the pitcher, or it learns to trust keypoints that are unrealistically
    # good and is miscalibrated the moment it meets a new one. This costs a full
    # cross-validation, so --skip-ridge falls back to the hand-fit armAngleModel.joblib.
    if args.skip_ridge:
        print("skipping the Ridge refit -- armAngleModel.joblib will be used instead")
    print("out-of-fold keypoints for the Ridge...") if not args.skip_ridge else None
    if not args.skip_ridge:
        outOfFold = evaluate.outOfFoldKeypoints(
            images, targets, groups, args.folds, "resnet",
            {"size": args.size, "epochs": args.epochs, "seed": args.seed})
        frame = evaluate.featureFrame(dataset.toPixels(outOfFold, transforms), index)

        from AnglePredictor import make_model
        ridge = make_model().fit(frame[features.FEATURE_COLS].values,
                                 frame[features.TARGET_COL].values.astype(float))
        dump({"model": ridge, "features": features.FEATURE_COLS,
              "fittedOn": "out-of-fold predicted keypoints"}, args.ridge)
        print(f"  Ridge -> {args.ridge}")

    print(f"\nkeypoint model on all {len(images)} pitches...")
    model = keypointModel.ResnetKeypoints(size=args.size, epochs=args.epochs,
                                          seed=args.seed, verbose=True)
    model.fit(images, targets)

    torch.save({
        "stateDict": model.model.state_dict(),
        "size": args.size, "head": model.head, "upsample": model.upsample,
        "gridPad": model.GRID_PAD,
        "keypointNames": features.KEYPOINT_NAMES,
        "trainedOn": len(images),
    }, args.out)
    print(f"  keypoints -> {args.out} ({os.path.getsize(args.out) / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
