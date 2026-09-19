# Research and experimental workflows

The supported release is the manual workflow described in the [README](../README.md).
Automatic release detection and keypoint labeling remain experimental.

## Data and model versions

- `manual-v1`: the unchanged shipped normalized Ridge model, trained on 180
  complete labels from 98 pitchers. Its exact source file is
  `data/manual-v1/labels.csv` (184 rows including four skipped pitches).
- `development-2026-09-09`: root `labels.csv`, containing 192 complete labels
  from 103 pitchers, plus four skipped pitches. The 12 additional labels are
  not in the shipped normalized model's training snapshot.
- See [MODEL_CARD.md](../MODEL_CARD.md) for evaluation boundaries and checksums.

## Reproduce the shipped model's development validation

Run from the project root with the README environment activated:

```bash
python evaluateAngleInputs.py --labels data/manual-v1/labels.csv --out reports/reproduced-manual-v1
```

This evaluates input conventions with eight folds grouped by pitcher. The
expected `height720` RMSE is approximately 2.653 degrees. It does not overwrite
the model. Use a new `--save` path for a deliberate refit; do not silently replace
the released model or label snapshot.

The current development dataset can be evaluated separately:

```bash
python AnglePredictor.py
python evaluateAngleInputs.py --out reports/reproduced-development
```

The current original-input RMSE is approximately 2.665 degrees and normalized
input RMSE approximately 2.676 degrees. These are refitted cross-validation
models, not new independent measurements of the shipped artifact.

## Downloading training material

Install `requirements-fetch.txt` first. `fetchPitchBatch.py` samples the included
Statcast export, while `fetchLowSlotBatch.py` queries additional low-slot pitchers.
Network availability and upstream footage retention can affect these scripts.
`fetchUiTestBatch.py` restores the fixed public example manifest; it does not
select new examples or change the training labels.

## Optional pose and keypoint experiments

The previous local experiment used these additional packages:

```text
torch==2.2.2
torchvision==0.17.2
ultralytics==8.4.124
```

These are historical experiment versions, not a supported cross-platform
installation recipe. Install experiments in a separate environment. The old
ball-detector experiments additionally use BaseballCV and some scripts use PyAV;
neither is required by the manual workflow. Model weights, cached video, and
derived arrays are not distributed in the public release.

`phase4/` contains detector-derived crops, a keypoint model, and pitcher-grouped
evaluation. Its recorded five-fold result on 180 pitches is RMSE 13.579 degrees,
MAE 6.864 degrees, compared with 2.651 / 2.135 for manually marked keypoints.
Large person-selection failures and extreme arm-slot errors dominate this result.
It is not a ready automatic measurement tool.

Release selection remains manual. Existing experiments did not establish an
automatic method that reliably improves on the consistent timing of Savant-cut
clips. That timing shortcut does not apply to a user's own video.

The [historical notes](RESEARCH_HISTORY.md) retain earlier experiments and their
dated claims. Internal handoff notes and personal-video predictions stay outside
the public release. A new independent validation study is deferred; it is not
part of this release milestone.
