# Manual arm-angle model: manual-v1

## Intended use

Estimate Statcast-style release arm angle from five manually clicked points in
MLB center-field broadcast footage. A human selects the release frame and marks
throwing shoulder, opposite shoulder, elbow, wrist, and ball. The UI also reports
the directly measured shoulder-to-ball angle relative to image horizontal.

This is a research estimate, not a calibrated measurement for arbitrary cameras.
College, high-school, side-view, and recruiting-reel accuracy is unverified.

## Released artifact and exact training snapshot

| Item | Value |
|---|---|
| Release identifier | `manual-v1` |
| Default model | `model/armAngleModelResolutionSafe.joblib` |
| Training snapshot | `data/manual-v1/labels.csv` |
| Snapshot size | 184 rows: 180 complete labels, 4 skipped |
| Coverage | 98 pitchers, 30 parks |
| Input mode | `height720`, schema version 1 |
| Estimator | StandardScaler followed by RidgeCV |
| Supported runtime | Python 3.12 and `requirements.txt` |

The training snapshot is an exact byte-for-byte copy of the original source
file identified by the shipped model's embedded training checksum. Model files
have not been retrained or replaced during release preparation. Checksums and
evaluation metadata are in [model-manifest.json](../model/model-manifest.json).

`data/labels.csv` is the separate `development-2026-09-09` snapshot: 196 rows,
192 complete labels, 103 pitchers, 30 parks. It contains 12 later complete labels.
Its current cross-validation result must not be presented as the released model's
training-set size or as an independent test of that model. The original
`model/armAngleModel.joblib` remains available for legacy raw-pixel inference; it is
not the manual UI's default.

## Development validation

Eight outer folds grouped by pitcher, using the 180-label training snapshot:

| Method | RMSE | MAE |
|---|---:|---:|
| Direct shoulder-to-ball angle, no fitted model | 3.227° | 2.592° |
| Normalized manual model, held out by pitcher | **2.653°** | **2.137°** |

RMSE emphasizes large misses; MAE is the average absolute error. These are
development cross-validation results, not an untouched external benchmark.
The shipped estimator was subsequently fit on all 180 complete labels.
Input conventions were compared on this same dataset, so the result does not
measure an independent model-selection process. Park-grouped results in the
underlying report can share pitchers and should not replace the pitcher split.

The 192-label development dataset separately yields 2.665° RMSE for original
pixels and 2.676° for normalized inputs. Small differences between these numbers
reflect different datasets and input conventions, not a new model release.

Reproduce the release evaluation:

```bash
python scripts/evaluateAngleInputs.py --labels data/manual-v1/labels.csv --out reports/reproduced-manual-v1
python tools/checkRelease.py
```

## Input and output conventions

Coordinates and original image width/height are required. Both coordinate axes
are multiplied by `720 / img_h` before constructing the 12 model features.
Uniform resizing therefore leaves predictions unchanged. Cropping, zoom changes,
perspective, and release-frame ambiguity are not corrected.

Angles are in degrees: zero is horizontal, positive is above the shoulder and
negative is below it. Left/right horizontal direction is folded using its
absolute value. The model output is limited to [-90, 90]; clipping is a range
guard, not evidence that a bad prediction is correct. Missing-ball labels can
be saved as occluded, but cannot produce a five-point model estimate.

## Limits

- The model is specific to the camera geometry represented in MLB training data.
- The extreme low-slot tail has only 12 pitches below -30°, all from one pitcher.
- A new pitcher's systematic offset is unknown. The displayed number has no
  calibrated per-pitch confidence interval.
- Uncertain release timing and ambiguous points affect the result. Quality flags
  preserve human review notes; they do not calibrate or filter predictions.
- Automatic keypoints and release detection are experimental. Their results are
  separate from this manual model's accuracy.

See [DATA_SOURCES.md](DATA_SOURCES.md) for attribution and
[docs/RESEARCH.md](RESEARCH.md) for experimental workflows.
