# Predictions from your hand-labeled points

Used the existing `armAngleModel.joblib` with your original full-resolution point coordinates. No automatic labeling or retraining was run. The two unlabeled deliveries at the end remain excluded.

| Video time | Frame | Annotation | Predicted arm angle |
|---|---:|---:|---:|
| 2.267 s | 68 | 1 | 5.5° |
| 2.267 s | 68 | 2 | 6.4° |
| 5.567 s | 167 | 1 | 10.4° |
| 9.700 s | 291 | 1 | 6.1° |
| 13.433 s | 403 | 1 | 3.1° |
| 17.533 s | 526 | 1 | 8.9° |
| 21.033 s | 631 | 1 | 5.3° |
| 25.033 s | 751 | 1 | 5.0° |
| 25.033 s | 751 | 2 | 9.4° |

**Mean across seven unique frames: 6.7°.** Predictions for repeated annotations were averaged within each frame before calculating this mean. All nine individual outputs are retained above.

These are predictions from the MLB-trained angle model; this college footage has no independent true-angle measurements to validate them. Original pixel coordinates were used exactly as supplied to the current inference pipeline.

[Download full prediction data](/Users/charliecapretta/Desktop/ArmAngleAttempt/reports/hand_label_predictions/predictions.csv)

Model limitation: 179 of the 180 labeled MLB training examples are 1280 × 720; this reel is 1920 × 1080. The model uses absolute pixel coordinates, so resolution and framing differences can influence its output. This run deliberately follows the existing pipeline with no rescaling or retraining.
