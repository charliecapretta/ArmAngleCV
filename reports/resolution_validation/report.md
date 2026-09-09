# Image-size consistency and release-labeling update

The model now converts manually clicked coordinates to a common 720-pixel image height before predicting. Both coordinate axes use the same scale, preserving the image geometry. Original labels remain in their original image coordinates. This addresses resizing; it does not correct crop, zoom, camera perspective, or uncertain release timing.

## Validation uses MLB truth only

180 labeled pitches, 98 pitchers, 30 parks. All candidates use the same eight outer folds grouped by pitcher. A secondary eight-fold park split checks different parks but can share pitchers between train and test. These are development comparisons on the existing MLB dataset, not an untouched external benchmark. No college angle targets are assumed or used for training.

| Input method | Pitcher-held-out RMSE | Pitcher-held-out MAE | Park-held-out RMSE | Largest prediction change under resizing |
|---|---:|---:|---:|---:|
| Original pixels | 2.651° | 2.135° | 2.519° | 18.861° |
| Common image height — selected | 2.653° | 2.137° | 2.521° | 0.000° |
| Angles only | 3.234° | 2.551° | 3.038° | 0.000° |
| Shoulder-relative, scaled by shoulder-to-ball length | 3.907° | 2.754° | 2.714° | 0.000° |

The resize test scales each held-out annotation's coordinates and image dimensions by 0.5×, 1.5×, and 2× without changing the delivery. Original-pixel fold models changed by up to 18.861°. The selected approach changes by less than 0.000001° (floating-point rounding only). The 0.002° RMSE difference is negligible on this small dataset; the reason to adopt it is predictable resizing behavior, not improved reported accuracy.

The saved production model was also checked on uniformly resized copies of all nine college annotations and remained invariant. The college labels and original model were verified unchanged by hashes.

## Your existing hand-label predictions

| Time | Annotation | Previous model | Common-height model |
|---|---:|---:|---:|
| 2.267 s | 1 | 5.5° | 8.4° |
| 2.267 s | 2 | 6.4° | 9.5° |
| 5.567 s | 1 | 10.4° | 13.0° |
| 9.700 s | 1 | 6.1° | 9.1° |
| 13.433 s | 1 | 3.1° | 6.4° |
| 17.533 s | 1 | 8.9° | 10.7° |
| 21.033 s | 1 | 5.3° | 8.7° |
| 25.033 s | 1 | 5.0° | 7.9° |
| 25.033 s | 2 | 9.4° | 12.7° |

**Mean over seven unique release frames: 9.6° (previously 6.7°).** Repeated annotations are averaged within a frame before the overall average. These remain unverified college estimates, not measured true angles. The two deliveries at the end stay excluded.

## Labeling changes

- “Predict from these points” uses the new model on the currently displayed five points. It does not save or move any point. Changing the points clears the prior prediction; late responses cannot overwrite a changed frame's result.
- Release choice: not reviewed, closest available frame, or release timing unclear.
- Point clarity: not reviewed, points reviewed, or one or more points uncertain.
- Guidance accepts a blurred, approximate release. Every point belongs to the same selected frame. At 30 fps, neighboring samples are approximately 33.3 ms apart; no exact release-time or clean-image requirement is imposed.
- Shoulder guidance targets the joint where the upper arm meets the torso, rather than a sleeve edge. Unlocatable balls retain the existing hidden-ball workflow.
- Quality flags describe human review; they do not supply a probability or automatically change the numeric prediction. Older rows stay unreviewed. Flags append to the schema on future saves and are included in release exports.

## Use and reproduce

The live labeler serves the updated manual-point predictor. Batch prediction:

```bash
./.venv/bin/python predictLabels.py --labels localLabels.csv
```

Re-run the MLB input comparison without replacing model files:

```bash
./.venv/bin/python evaluateAngleInputs.py
```

To train another common-height model, add `--save` with a new filename. The evaluator refuses to replace existing model files. Models declare their input convention, and the shared prediction function checks that the feature schema matches. Legacy model files remain readable with their original input behavior.

Automatic keypoint labeling was not retrained or redesigned. Its existing scripts keep their prior model defaults; when explicitly given a new angle model, they honor its declared input convention.

[Detailed validation](/Users/charliecapretta/Desktop/ArmAngleAttempt/reports/resolution_validation/validation.json) · [Held-out predictions](/Users/charliecapretta/Desktop/ArmAngleAttempt/reports/resolution_validation/oof_predictions.csv) · [College predictions](/Users/charliecapretta/Desktop/ArmAngleAttempt/reports/hand_label_predictions/resolution_safe_predictions.csv)
