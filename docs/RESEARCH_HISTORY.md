# Historical research notes

Archived from the pre-release README on September 9, 2026. Counts and commands
below describe earlier development snapshots. For supported setup and matching
model/dataset versions, use the root README and MODEL_CARD.md.

# Arm angle from broadcast video

Measure pitcher arm angle at release from MLB broadcast clips, calibrated
against Statcast's `arm_angle`.

Everything runs out of `.venv` (kept separate from anaconda base because
importing `baseballcv` self-installs `yolov9` and upgrades `protobuf`/`pydantic`,
which breaks `mediapipe` and `streamlit` where it lands).

## Measure your own video

You do not need any MLB footage to use this. The model ships trained; you supply
a clip and five clicks.

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python labelServer.py
```

Open **[Pitch analysis](http://127.0.0.1:8777/analyze.html)**, choose "Open a
video", and give it the full path to your file. Nothing is copied or uploaded --
the server is local and reads the footage where it sits. Then:

1. **Release** -- step through frames, click "Use this frame". Closest available
   is fine; a clean, exact release frame is not required.
2. **Points** -- click throwing shoulder, off shoulder, elbow, wrist, ball.
3. **Review** -- read the estimated arm angle, adjust, save, download.

Your annotations go to `analysisLabels.csv`, which is yours alone and is not
tracked by git. The MLB training labels are never touched.

### How accurate is this on *your* video?

Honest answer: **unknown, and probably worse than the headline number.**

The 2.665° figure below is measured on MLB centre-field broadcast, held out by
pitcher. Every one of the 192 training pitches comes from that one camera
position. Footage from another angle breaks the assumption the method rests on --
that the camera images the across-field x height plane that Statcast measures in.

Measured out-of-distribution behaviour, on a Division II reel shot from behind
and beside the pitcher at field level: the automatic pipeline returned roughly
-60 deg for a three-quarters arm slot, which is plainly wrong. Hand-clicked
points on that same reel gave about 9.6 deg, still unverified against any truth.

What this means in practice:

- **Centre-field camera, pitcher roughly facing away from you: usable.**
- **Side-on, behind, or high angles: expect a wrong answer**, delivered with no
  warning. Predictions are clamped to the physically possible [-90, 90], which
  catches nonsense but not confident errors.
- There is no per-pitcher calibration for someone the model has never seen. The
  measured between-pitcher bias spread is 5.0 deg.

Treat an estimate on non-broadcast footage as a rough reading, not a measurement.


## Workflow

```bash
./.venv/bin/python fetchPitchBatch.py --nPitches 250   # clips + Statcast truth
./.venv/bin/python fetchLowSlotBatch.py                # top up the sidearm end
./.venv/bin/python labelServer.py                      # label — open the URL it prints
./.venv/bin/python AnglePredictor.py                   # label QA + held-out accuracy
```

**Current result** (192 labeled pitches, 103 pitchers, 30 parks), validated with
`GroupKFold` on `pitcher_id` so no pitcher appears in both training and test:

```
predict the mean (trivial baseline)    RMSE 31.495   MAE 24.432
no model (ball_angle raw)              RMSE  3.290   MAE  2.660
model, held out by pitcher             RMSE  2.665   MAE  2.158
```

Reproduce with `./.venv/bin/python AnglePredictor.py`.

### 1. `fetchPitchBatch.py` — pitches + ground truth

Reads the local `savant_data (10).csv` (25,000 pitches, 2026-08-10→16, 412
pitchers, 93 games, 22 parks). No remote Statcast pull needed.

Selection is stratified across `arm_angle` bin × park, capped per pitcher. At
250 pitches that yields ~173 pitchers across all 22 parks, spanning −65°→76°.

`play_id` comes from the `/gf` game feed, joined on `at_bat_number` +
`pitch_number` (exact — ~98.5% match, no duplicate keys). The mp4 is scraped
from the `sporty-videos` page. Produces:

```
pitches/<game_pk>_<play_id>/clip.mp4
pitches/<game_pk>_<play_id>/meta.json
```

Notes: `/gf` returns 403 without a browser User-Agent (handled in
`savantUtils.py`). Feed responses are cached under `cache/gf/`. All pitches on
**2026-08-16 have null `arm_angle`** — not yet published — and are dropped.

### 2. `labelServer.py` + `labelTool.html` + `labelTool.js` — labeling

Start `./.venv/bin/python labelServer.py`, then open
[Choose your workspace](http://127.0.0.1:8777/index.html).

- **[Training labeler](http://127.0.0.1:8777/labelTool.html)** builds the MLB dataset.
  It starts with the new 12-pitch trial batch, covering Statcast angles from
  −59.9° to +75.1°. The other queues expose all MLB clips or unfinished stages.
- **[Pitch analysis](http://127.0.0.1:8777/analyze.html)** opens your own footage,
  resumes existing local annotations, or lets you practice with those MLB clips.
  Its estimates use `armAngleModelResolutionSafe.joblib`; college accuracy remains
  unverified. There is no automatic promotion from analysis to training data.

Both workspaces follow three steps:

1. **Release:** compare neighboring frames and click “Use this frame.” This saves
   a checkpoint and locks the frame. Choose the closest available release;
   a clean, exact frame is not required. Training has a release-only batch action
   under More tools.
2. **Points:** manually place throwing shoulder, off shoulder, elbow, wrist, and
   ball. Drag to adjust; numbered buttons replace individual points. Review
   becomes available when all required points are placed. Body suggestions are
   optional training tools and never run automatically.
3. **Review:** both workspaces prominently show the measured shoulder-to-ball
   angle from your clicked points, relative to the image horizontal. Training
   reveals the Statcast reference, then “Save & next pitch”
   advances after a successful save. Analysis calculates the estimate, allows
   adjustments, saves the annotation, and offers a text download of the result.

Quality notes, additional tools, and image measurements are collapsed by default.
“Need points” resumes release checkpoints and older incomplete labels. Changing
release unlocks the timeline; moving to another frame clears coordinates.

**Local clips and reels.** In Pitch analysis, “Open a video” accepts a full file
path, pitcher, park/session, and throwing hand. No footage is copied. After saving
an analysis, use “Another pitch from this video” for a new delivery. Keep the
source file in place; leave park blank for mixed-venue reels.

**Separate storage.** Training writes `labels.csv`. Analysis writes
`analysisLabels.csv` and reads existing `localLabels.csv` annotations as a starting
point, with analysis edits taking precedence. It never rewrites those original
local labels or MLB training labels. Registrations remain in `localVideos.json`.
Saved analysis coordinates are used to recalculate the estimate when reviewed.
Legacy clients without a workspace parameter retain their original storage routes.

`fetchUiTestBatch.py` adds the 12 verified MLB clips listed in `uiTestBatch.json`,
without replacing existing clips or labels. There are now 196 MLB clips available.
The new batch has not been used to retrain the model.

If Chrome and the preview differ after an update, use the same URL and refresh
with Cmd+Shift+R. UI files now request fresh loads to avoid mixing cached versions.

Playback uses decoded frame timestamps where available, with source FPS as a
fallback. The UI shows FPS, release time, and neighboring-frame offsets in
milliseconds. Frame indices start at zero. It does not invent intermediate
frames or automatically detect releases.

| Key | Action |
|---|---|
| `←` / `→` | Step one frame while choosing release |
| `Shift+←` / `Shift+→` | Step ten frames |
| `Space` | Play / pause |
| `Enter` | Confirm release / review points / save |
| `P` / `Shift+P` | Training only: suggest missing body points / replace suggestions |
| `1`–`5` | Place that point again |
| `U` / `S` | Undo last save / skip pitch |
| `Z` / `C` | Zoom / clear points |
| `X` | Swap throwing shoulder and wrist |

**Hidden ball.** Mark “Ball cannot be located” to save the four
body points without inventing a ball position. These rows have status
`occluded` and are excluded from existing five-point training. Other statuses
are `release_selected`, `labeled`, and `skipped`. Review pose suggestions before
saving; they remain guesses, shown with rings. The ball is hand-clicked.

Saves replace files atomically and keep a `.before-streamlined` backup before
the first change to an existing file. Undo restores the previous saved row and
is available during the current server/browser session. Unsaved changes survive
switching pitches in the same page; a reload/close warning protects those
in-memory drafts. Confirmed releases and completed labels survive restarting.

The first 26 CSV columns retain their order. New columns are `fps`,
`release_time_sec`, `ball_visibility`, and `source_video`. Coordinates remain
in the original full-resolution image even when zoomed. `provenance` records
point origins (`hand`, `pose`, or `saved`); angles are derived by the server.

Frames are decoded to a shared RAM cache on first use, with three source videos
retained at most. Long videos require more memory and time than short clips;
this workflow is intended for pitch clips and short reels.

To export releases even before point labeling is finished:

```bash
./.venv/bin/python extractReleaseFrames.py
./.venv/bin/python extractReleaseFrames.py --labels localLabels.csv --outDir localReleaseFrames
```


### Manual angle predictions with consistent image sizing

The labeler's **Predict from these points** button estimates angle from your five
current clicks using `armAngleModelResolutionSafe.joblib`. It does not run point
detection or save labels. The model scales coordinates uniformly to a common
720-pixel image height; labels themselves stay in original pixels. Uniform resizing
now leaves predictions unchanged. Crop/zoom/view changes are not corrected by this.

```bash
./.venv/bin/python predictLabels.py --labels localLabels.csv
./.venv/bin/python evaluateAngleInputs.py
# Optional refit: add --save with a new model filename (existing files are preserved).
```

MLB validation: 180 pitches, 98 pitchers; eight folds held out by pitcher.
Original pixels: RMSE 2.651°, MAE 2.135°. Common height: RMSE 2.653°, MAE 2.137°.
MLB is the verified accuracy reference; college labels are not true angle targets.
The old `armAngleModel.joblib` and the automatic keypoint pipeline are preserved.
See `reports/resolution_validation/report.md` for the input comparison and limits.

The labeler accepts the closest available release frame, including blurred 30 fps
footage. Optional **Release choice** and **Point clarity** fields persist uncertainty
without claiming an exact release. Existing rows default to unreviewed. The new
`release_quality` and `point_quality` fields append after the first 30 CSV columns.
Quality flags are review metadata, not calibrated confidence or automatic exclusions.

### 3. `fetchLowSlotBatch.py` — closing the sidearm gap

Targeted pull for low arm slots, which the one-week export barely contained.
Reads the season arm-angle leaderboard, keeps pitchers at or below
`--maxArmAngle`, pulls their whole season from Statcast search, and samples
across as many distinct parks and games as possible.

```bash
./.venv/bin/python fetchLowSlotBatch.py --dryRun
./.venv/bin/python fetchLowSlotBatch.py
```

**The binding constraint is that these pitchers barely exist.** On the 2026
leaderboard (min 50 pitches) only **3 of 720** sit below −10°
(Rogers −60.8, Hill −21.9, Thompson −12.2) and 17 below +10°. The low end
therefore cannot be made into a continuum — it is a few tight clusters, and
each pitcher's own arm angle is very stable (Rogers: SD 1.97° over 827
pitches).

So the quota is weighted: `--perPitcherLow` (default 10) for pitchers below
−10°, `--perPitcher` (default 4) for the rest. And park variety is the point —
since each pitcher sits at one angle, the useful variation left is the camera.
A default run selects 84 pitches over 29 parks and 82 games.

What this buys is a test of whether the identity relationship holds at extreme
angles *across cameras*. It is not a test of generalization across many
different low-slot pitchers, because there aren't many.

### 4. `AnglePredictor.py` — label QA + the model

Terminal stage. Reports label quality (gross outliers, and specifically
shoulder/wrist swaps, which mirror the angle's sign), then held-out accuracy
against the baselines a model has to beat, plus breakdowns by angle band,
pitcher and park.

```bash
./.venv/bin/python AnglePredictor.py
./.venv/bin/python AnglePredictor.py --loo               # leave-one-pitcher-out
./.venv/bin/python AnglePredictor.py --save model.joblib
```

**Validation is `GroupKFold` on `pitcher_id`, never a random split.** A pitcher's
arm angle is nearly constant, so random folds put the same pitcher in train and
test. Measured on this data: a random 80/20 split leaves **67% of the test set
contaminated**, and flatters RandomForest from RMSE 10.3 to 3.3.

**Features.** `ball_angle` carries the nonlinear part (the arctangent); the raw
pixel coordinates then act as linear corrections on the residual — chiefly torso
geometry, via `shoulder_y` and `off_shoulder_y`, whose coefficients come out near
equal and opposite. Neither half works alone:

| features | RMSE |
|---|---|
| raw pixels only | 5.04 |
| `ball_angle` only | 3.23 |
| `ball_angle` + raw pixels | **2.65** |

Ridge scores identically to plain least squares (2.649 either way) but keeps the
coefficients stable across 12 correlated coordinates on ~180 rows.

**What does not help:** RandomForest (RMSE 10.3 grouped — trees cannot
extrapolate beyond their training range, and cannot subtract two coordinates);
per-park intercepts (park bias SD 1.22° against a 1.08° sampling-noise floor);
`frame_index`, `img_w`, `img_h`, `venue_id`.

**The dominant residual is a per-pitcher offset** — spread 6.7° between pitchers
against 2.04° scatter within one, stable across up to 10 parks each. Likely the
gap between the *visible* shoulder you click and the modeled shoulder joint
Statcast uses. You cannot know a new pitcher's offset in advance, which is the
whole reason folds are grouped by pitcher. For a pitcher you *have* labeled,
subtracting their measured offset gets you to their within-pitcher scatter of
~2°.

**Coverage caveat:** only 12 of 180 pitches sit below −30°, all from Tyler Rogers,
and only 3 pitchers in MLB are below −10°. The extreme submarine end is thin by
necessity, and its residual bias (−3.09°) is the largest of any band.

## Not in the pipeline

`extractFrames.py` writes every frame to disk. Superseded by on-demand decoding
above; kept only for the case where you want frames as files.

### `findReleaseFrames.py`

Automatic release detection, kept for reference but **not reliable enough to
use**. Two approaches were tried and both failed on real clips:

1. **Ball leaving the hand.** Broadcast baseballs near the pitcher are small and
   motion-blurred; the detector fired on a white shirt in the crowd and returned
   a follow-through frame.
2. **Arm kinematics + plate-crossing physics.** Peak wrist speed refined toward
   maximum arm extension lands late, because the arm extends again on the way
   down. Backing out release from the ball crossing the plate (using
   `release_speed` / `release_extension`) agreed to within a frame on the pitch
   it was checked against, but was not validated broadly.

Release frames are picked by hand instead. Worth revisiting once `labels.csv`
provides ground-truth release frames to validate against.

## Data notes

`Pitcher_Motion_Data.csv` (530 MB) is a **COCO-17 pose dataset**, not raw video:
`V1`–`V51` are (x, y, confidence) triplets — `V16`–`V21` shoulders, `V28`–`V33`
wrists, matching the ordering the pose model emits. 462,746 frames, 100
pitchers, 1,345 pitches keyed by `(pitcher, pitch_type, pitch_id)`.

It has **no `game_pk`, date, `play_id`, or venue**, so it cannot be joined to
per-pitch `arm_angle` — only to a pitcher-season average. The season is **2021**
(100/100 of its pitchers appear on the 2021 arm-angle leaderboard vs 86/100 for
2022), spanning −5.6° to 67.1°. Currently set aside; a candidate pretraining set
for Phase 4.
