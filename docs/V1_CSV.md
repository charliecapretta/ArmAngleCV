# Manual V1 sessions and CSV exports

Open the app at `/` or `/analyze.html`. Upload an MP4, optionally name the session
and pitcher, and choose the throwing hand. Each upload starts one video session.
Use **Another pitch from this video** to add deliveries without uploading again.
The session list reopens saved releases and points after a browser reload.

Uploads accept up to 100 MiB (shown as 100 MB), 60 seconds, 1920 × 1080 pixels,
60 fps, and 3,600 decoded frames. The app validates the video before registering
it and removes failed upload copies. Unsupported/partially decoded clips are
rejected. Existing path-based registrations remain readable without rewriting
them; the new upload limits apply to newly uploaded files.

The current implementation still runs locally on port 8777. Uploaded copies are
stored in `uploads/`, session metadata in `localVideos.json`, and analysis labels
in `analysisLabels.csv`. These files are excluded from the public release. There
is no automatic retention/deletion policy yet: access control, durable cloud
storage, bounded cache capacity, and cleanup are later hosting checklist stages.
Do not expose this local server directly to the internet.

## Export behavior

Select **Export session CSV** after saving. Each saved pitch in the selected
session produces one row, sorted by release frame and pitch identifier. Unsaved
edits in that session disable export until saved. Completely untagged pitches
are not exported; a saved release checkpoint is included even without points.

The server reads saved annotations and recomputes predictions with the frozen
`manual-v1` model through `angleInputs.predict`. Browser preview values are never
used as CSV inputs. Editing points changes the next saved export; confirming a
different release clears its old saved points and prediction. The existing
`predictLabels.py` CLI keeps its older column names and complete-row-only export;
its predictions match the new session CSV for identical saved inputs.

| Column | Meaning |
| --- | --- |
| `session_id`, `session_name` | Stable video-session identifier and display name |
| `pitch_id` | Unique pitch record; several pitches may share a session |
| `pitcher_name`, `video_name` | Display metadata; video basename only, no server path |
| `release_frame` | Zero-based decoded frame index; frame 0 is the first frame |
| `release_time_sec` | Decoded video timestamp in seconds; frame/fps fallback if timestamps are unusable |
| `measured_angle_deg` | Throwing-shoulder-to-ball angle relative to image horizontal |
| `predicted_arm_angle_deg` | Frozen model estimate in degrees, recalculated from saved points |
| `status` | `labeled`, `release_selected`, `occluded`, `skipped`, or `incomplete` for invalid legacy labels |
| `release_quality`, `point_quality` | Human review notes, not calibrated confidence |
| `model_version` | `manual-v1`; identifies the model used by this export |
| `estimate_scope` | Explicit MLB-training / unverified-college statement |

Both angle fields are blank for incomplete, release-only, skipped, or occluded
rows. Missing ball coordinates are never invented. CSV uses UTF-8 with a byte
order mark and standard quoting for commas/quotes. User-entered text that could
be interpreted as a spreadsheet formula is prefixed with an apostrophe. Numeric
angles, including negative values, remain numeric.

## What the angles mean

Zero degrees is horizontal, positive is above the shoulder, and negative is below.
Left/right horizontal direction is folded using its absolute value. The image
measurement is affected by perspective and is not verified real-world ground
truth. Uniform resizing is normalized before model inference; cropping and camera
changes are not corrected. Model outputs are limited to −90° through +90° as a
range guard, not an accuracy guarantee.

Every export includes:

> Estimated arm angle from an MLB-trained model. Accuracy on college footage has not been established.

The 2.653° RMSE in `MODEL_CARD.md` describes MLB development cross-validation.
It is not college accuracy, a confidence interval, or an expected error for any
individual uploaded pitch. College verification is not a V1 launch requirement.
