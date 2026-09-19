# Arm Angle Practice Lab

A separate local site for a human five-pitch, two-pass repeatability exercise. It serves only this folder and writes exports only to its own `exports/` folder. It has no API for writing training labels or modifying the model.

From the project root:

```sh
.venv/bin/python training-lab/prepare.py --offset 5
.venv/bin/python training-lab/serve.py
```

Open http://127.0.0.1:8790. The preparation step uses OpenCV and the existing local independent-evaluation clips. The current fresh round uses evaluation pitches 06–10, distinct from the original five. The offset selects which five local clips to use. Earlier suggested frames are only starting points: the local server decodes any chosen frame from the original clip. No old points, model predictions, or reference angles are copied into the site. Generated broadcast frames are ignored by Git and are not included in the public release.

For each pitch, browse the clip using the slider, frame number, or ±1/±10 buttons. Choose the first frame with the ball visibly separated from the hand and click **Lock frame & label**. Place five points, take a break, then complete the shuffled repeat pass. The second pass uses the exact locked frame and offers no frame changes. Before saving a first-pass label, **Change frame** lets you revise the choice and clears that frame’s draft points. Earlier points stay hidden during the repeat. Select a point to correct it, zoom for detail, and skip rather than guess if a landmark cannot be located. Drafts and completed labels persist in a dedicated browser-storage key tied to the frame set. Use the same browser and address to resume. Download results when finished; a separate JSON copy is saved in `training-lab/exports/` before the download starts, never as rows in labels.csv. Exports are ignored by Git.

Results report shoulder-to-ball geometric angle differences and original-pixel point displacement. They measure consistency, not model accuracy. Exported results include timestamps, image hashes, frame indices, pitch identifiers, skips, notes, and both sets of points for later independent scoring. Exports contain the selected frame index and image hash for each record. Comparisons require matching frame indices. Frame choice is performed once, so this measures point-placement repeatability rather than repeatability of release selection. Five pitches do not establish general accuracy. A new frame set uses a separate browser-storage key; earlier practice data is retained.

This lab is intentionally separate from the main app and the existing public release package. It is local only; no site has been published.

Checks: `node --test training-lab/core.test.cjs`.
