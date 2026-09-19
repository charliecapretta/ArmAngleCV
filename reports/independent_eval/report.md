# Independent accuracy and consistency check — 2026-09-09

Frozen model `armAngleModelResolutionSafe.joblib` (Ridge, `height720`, trained n=180 /
98 pitchers, development held-out RMSE 2.651) evaluated on 30 new MLB pitches from
pitchers it has never seen, with Statcast reference angles sealed until every pitch was
annotated, and the full annotation repeated on 10 pitches.

**Headline: the run completed, and the result is annotator-limited. Held-out RMSE is
12.01°, but repeating the annotation on the same pitches moves the prediction by a
median 10.1°. The measurement noise is the same size as the measurement, so this run
does not resolve the model's accuracy on unseen pitchers — it bounds it.**

---

## 1. The batch

| | |
|---|---|
| pitches | **30** |
| distinct pitchers, none in `labels.csv` | **21** (target ≥15) |
| clips | 1280×720, 59.5–59.7 fps — same MLB centre-field distribution as all 180 training frames |
| Statcast angle range | +24.4° to +59.1° |
| blinding | references in `sealed/reference.json`, unopened until all 30 were annotated |

Selection stratified on each pitcher's mean angle across five strata, ≤2 pitches per
pitcher from different games, then shuffled. Seed 20260909, reproducible.

**Structural limit.** Every unseen-pitcher pitch in the source CSV lies between +6.5°
and +71.8°; the realised batch spans +24.4° to +59.1°. Every MLB pitcher below 0° —
Rogers, Hill, Thompson — is already in training, so this batch cannot test the sidearm
tail, which `HANDOFF.md` flags as the largest known bias band (−3.09°).

## 2. Accuracy — 30 pitches, blinded

```
predict the training mean       RMSE   9.063   MAE  7.535   bias  +0.000
no model (raw ball_angle)       RMSE  11.573   MAE  8.426   bias  -7.494
frozen Ridge                    RMSE  12.008   MAE  8.982   bias  -8.080
```

per-pitcher bias spread: sd 6.85° over 21 pitchers

The model scores **worse than predicting a constant**. That is not a verdict on the
model: the annotations feeding it are bad, in a specific and measurable way.

**The failure mode is a short shoulder→ball baseline.** Correlation between absolute
error and the annotated baseline length is **−0.503** — the shorter the arm I drew, the
worse the answer. Restricting to pitches where the annotated arm is a plausible length:

| annotated baseline | n | RMSE | MAE | bias |
|---|---|---|---|---|
| all | 30 | 12.01 | 8.98 | −8.08 |
| ≥ 60 px | 23 | 9.47 | 6.85 | −6.25 |
| ≥ 70 px | 16 | **7.80** | 5.28 | −4.42 |
| ≥ 80 px | 12 | 8.39 | 5.49 | −4.64 |

Typical hand-clicked baseline in training is ~104 px. When the release frame is blurred
or the arm foreshortened, I place the ball short of the hand, which drags the measured
angle toward horizontal — hence the uniform **−8° bias**. Filtering on baseline is not a
legitimate score (the failures are real), but it localises the defect.

## 3. Consistency — full annotation repeated on 10 pitches

Both passes were run blind. Pass 2 re-derived the release frame independently (from a
step-1 strip rather than pass 1's) and re-placed every point from a clean, unmarked
zoom.

```
mean |d prediction|   10.75 deg
median |d|            10.10 deg
sd of signed d        14.84 deg
max |d|               32.31 deg
release frame identical on 3/10
```

Neither pass is the good one:

```
pass 1 on these 10        RMSE 11.56   MAE  8.18   bias -7.63
pass 2 on these 10        RMSE 12.14   MAE 10.04   bias -7.59
average of both passes    RMSE  9.54   MAE  8.07
```

**Averaging two passes beats either one.** That is the signature of a noise-dominated
measurement: if the error were the model's, averaging my two readings would not help.

The two passes disagree by more than 19° on three pitches (eval07 +32.3, eval09 +19.2,
eval06 −13.6) and by under 5° on four. On eval07 and eval09, pass 2 was much closer to
Statcast (errors −22.8 → +9.5 and −24.5 → −5.3) — pass 1 had simply put the hand in the
wrong place.

## 4. A cleaner consistency estimate, free of any recall

Nine of the 21 pitchers contributed two pitches. A pitcher's arm angle is nearly
constant, so the two should agree; the natural-replicate spread needs no re-annotation
and cannot be contaminated by remembering the first pass:

```
mean |spread| between a pitcher's two pitches
   Statcast          1.3 deg
   my annotation    12.7 deg
```

Worst pairs: Petersen 25.5° apart (Statcast 0.8), Beeter 25.1° (0.8), Soriano 20.1°
(0.2), Suter 18.2° (1.5). Best: Pagán 1.6° (2.2), Faucher 3.8° (3.1).

**This is the cleanest number in the report.** Statcast says these pairs sit 1.3° apart;
my pipeline puts them 12.7° apart. Roughly 12° of the spread is annotation.

## 5. Why the annotator is the limit

Measured separately on six **training** frames where the human's clicks already exist
(the blinded 30 were never touched):

| keypoint | Claude | COCO pose |
|---|---|---|
| shoulder | 11.9 px | **5.6 px** |
| off_shoulder | 20.0 px | **8.1 px** |
| elbow | 17.3 px | 19.2 px |
| wrist | **23.1 px** | 50.4 px |
| ball | 35.2 px | *nothing finds it* |

Frozen model on those same six frames, varying only where the points came from:

```
hand-clicked by the human (ceiling)   RMSE  1.51
pose body + human ball                RMSE  6.64
pose body + Claude's ball             RMSE  9.90
```

The procedure used for the 30 was chosen on this calibration alone: pose for
shoulder / off_shoulder / elbow, my own click for wrist and ball, every proposal drawn
back onto its frame and corrected. That is the same division `labels.csv` provenance
shows the human used — pose-seeded body points, `ball:hand` on all 196 rows.

For contrast, the frozen model on human-clicked pitches from pitchers outside the older
pool: **RMSE 4.39 (n=4)**, and 3.74 across all 12 newest rows. Small n, wide interval,
but it is what competent annotation looks like on unseen pitchers.

## 6. Incidental finding — Stage A depends on the frame

Person selection (`phase4.detect.choosePerson`, measured 99.4% correct on the 180
training *release* frames) picked the wrong person on **7 of 30** clips when run at a
fixed probe frame of 176 — catcher, batter or a fielder. At the hand-picked release
frame it was correct on all 30. The rule is sound; it is only reliable once the pitcher
is actually in his delivery, which matters for any automatic pipeline that has to choose
a frame before it has a person.

## 7. What this does and does not establish

- The model was **not shown to be accurate or inaccurate** on unseen pitchers. The
  measurement is too noisy, by roughly the margin being tested.
- The upper bound stands: on 30 unseen pitchers, the pipeline end-to-end with a
  non-expert annotator produces RMSE 12.01° and is beaten by a constant.
- Annotation repeatability, three ways — test–retest 10.1° median, natural replicates
  12.7°, calibration vs the human 12.3° MAE in `ball_angle` — all agree at ~10–13°.
- To resolve the model's accuracy, the 30 clips need annotating by the person whose
  clicks trained it. Everything for that is in place:

```bash
./.venv/bin/python reports/independent_eval/scoreEval.py --labels labels.csv
```

`--labels` joins a `labels.csv`-shaped export on `game_pk` + `play_id`;
`--annotations <file.json>` takes the JSON pass format. Both unseal and print baselines
beside the result.

## 8. Files

```
selectBatch.py        blinded stratified selection; manifest + sealed reference
fetchEvalBatch.py     downloads clips, strips armAngle from written metadata
render.py             overview / zoom / strip views, gridded in original pixels
poseSeed.py           COCO body-point suggestion + overlay
ballStrip.py          release frame and the frames after it, centred on the wrist
verify.py             draws a proposed annotation back onto its frame
renderVerify.py       verification overlays for a whole pass file
annotateBatch.py      batches the two annotation stages
calibrate.py          annotator error vs the human's existing clicks
frameSensitivity.py   what +/-1 and +/-2 frames cost
consistency.py        release-frame test-retest
scoreEval.py          unseals and scores

annotations_pass1.json / annotations_pass2.json   the two annotation passes
evalResult.json  diagnostics.json  consistencyFull.json   results
```
