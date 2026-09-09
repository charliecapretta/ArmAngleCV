# UI workflow update — September 8, 2026

Two workspaces now share Release → Points → Review. Training starts with the
12 new MLB trial clips, with verified angles spanning −59.9° to +75.1°.
Analysis reuses saved local points or the trial clips, and writes separate
analysis annotations. Review prominently shows the measured shoulder-to-ball
image angle; analysis also displays the normalized-model estimate.

## Verification

- 19 Python tests and 13 JavaScript tests passed.
- Browser-tested manual placement, review enablement, hidden reference until
  review, save and advance, and empty manual points after release confirmation.
- Browser-tested resuming the college reel, prediction, separate analysis save,
  and reloading. Frame 68 displayed 12.7° measured and 8.4° predicted.
- All browser test writes used disposable label/registration copies on port 8778.
  The original local labels, registrations, and model files remained unchanged.
  Production MLB labels were left available for the user's ongoing edits.
- Live port 8777 serves no-store UI files, 196 training clips, and 21 analysis
  records (9 local registrations plus 12 MLB examples).

The new clips are for labeling and UI trials; the model was not retrained.
