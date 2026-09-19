# Arm Angle

A local tool for estimating a pitcher's release arm angle from manually marked
video frames. Choose the release frame, click five points, then review, save,
and export the result.

**Validated scope: MLB center-field broadcast footage.** The shipped model's
pitcher-grouped development validation is **2.653° RMSE / 2.137° MAE** on 180
pitches, versus **3.227° / 2.592°** for the raw shoulder-to-ball angle. College,
high-school, and other camera views remain unverified. See the
[model card](MODEL_CARD.md) for the exact dataset and limitations.

## Quickstart

Use **Python 3.12**. Run these commands from this project's folder:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\Activate.ps1` instead. Release preparation
is checked on macOS; the included GitHub workflow runs the same core checks on
Linux. No GPU, neural-network weights, or optional pose packages are needed.

You can use your own short clip immediately. To fetch one fixed MLB example first:

```bash
python -m pip install -r requirements-fetch.txt
python fetchUiTestBatch.py --limit 1
```

Omit `--limit 1` to restore all 12 examples. Existing clips and labels are preserved.
Downloads depend on the source's availability; your own video works offline.

Start the app:

```bash
python labelServer.py
```

Open [Pitch analysis](http://127.0.0.1:8777/index.html) and choose **Upload a video**
or **Try an MLB example**. Use the file picker for an MP4 up to 100 MB, 60 seconds,
1920 × 1080, 60 fps, and 3,600 frames. Optionally name the session and pitcher.
The local app saves an uploaded copy on your computer; no cloud deployment is active.

1. **Release:** use the timeline, arrow keys, and neighboring frames. Select
   **Use this frame** to save a release checkpoint.
2. **Points:** click throwing shoulder, opposite shoulder, elbow, wrist, then
   ball. Drag a point to adjust it. If the ball is hidden, mark it unavailable.
3. **Review:** see the measured image angle and model estimate. Select **Save
   analysis**, then add another pitch or **Export session CSV**.

A saved five-point analysis produces a row in `analysisLabels.csv` and an angle
in degrees. A hidden ball can be saved as an occluded annotation but does not
produce a model estimate. Quality notes record uncertainty; they are not a
confidence score. Use short clips or reels: frames are decoded into memory.

## Your data

| File | Purpose |
|---|---|
| `analysisLabels.csv` | Your saved analyses; excluded from the public release |
| `localVideos.json` | Video registrations and named session metadata |
| `uploads/` | Copies of videos selected with the file picker |
| `localLabels.csv` | Older personal annotations; analysis reads them without rewriting them |
| `labels.csv` | Current MLB development labels; training workspace writes here |
| `data/manual-v1/labels.csv` | Frozen source snapshot for the shipped model; do not edit |

**Training labeler** is for dataset construction and reveals Statcast after point
placement. **Pitch analysis** stores edits separately, including practice edits
on MLB examples. Undo restores the previous saved row during the current session.
Keep registered source videos in place so saved annotations can reopen them.

The session list reopens saved work after a reload. **Export session CSV** includes
one row per saved pitch; unfinished pitches have a status and blank prediction.
Exports use saved points and are disabled while the session has unsaved edits.
See [the V1 CSV contract](docs/V1_CSV.md) for fields, conventions, and limitations.

For the older command-line CSV export of complete saved estimates:

```bash
python predictLabels.py --labels analysisLabels.csv --out predictions.csv
```

## Development and verification

With the manual environment active, and Node.js 22 installed for the JavaScript tests:

```bash
python -m unittest discover -s tests -v
node --test tests/labelTool.test.cjs
python tools/checkRelease.py
```

Tests use temporary files. The first-run test starts a real local server, opens
an MP4, saves release and point annotations, predicts, exports, and checks undo
while blocking optional download and pose packages. The release check verifies
model/data checksums, refit identity, pitcher-grouped accuracy, and resizing.

The public file set is built separately from personal workspace files and Git
history:

```bash
python tools/prepareRelease.py --out release/public
```

Use a new output folder for each build. See [release preparation](docs/RELEASE.md)
for the public-file list and publication boundary.

## Project status

- Supported: manual release selection, five-point labeling, normalized angle
  prediction, separate analysis storage, and exports.
- Experimental: automatic keypoints and release detection.
- Deferred: a new independent validation batch and validation on college footage.

The root dataset now has 192 complete labels from 103 pitchers. The shipped
`manual-v1` model remains trained on its frozen 180-label snapshot. These are
explicitly separate versions; [MODEL_CARD.md](MODEL_CARD.md) explains the results.

[Research workflows](docs/RESEARCH.md) · [Data attribution](DATA_SOURCES.md) ·
[MIT license](LICENSE)

## Private hosted beta

A separate Flask/Gunicorn runtime is prepared for a small, invite-only AWS Lightsail
beta. It adds isolated accounts, SQLite saves, private disk-backed video frames,
upload/storage limits, expiry and deletion. The local quickstart above is unchanged.
See [the AWS setup guide](docs/AWS_LIGHTSAIL.md) and [S3 upload assessment](docs/S3_UPLOAD_ASSESSMENT.md).
No cloud resources have been deployed. Public registration remains disabled.

For **Elastic Beanstalk with Python 3.12**, use the separate
[Beanstalk setup guide](docs/AWS_BEANSTALK.md) and package generated by
`tools/prepareBeanstalk.py`. The Lightsail ZIP is not a Beanstalk source bundle.

Hosted uploads support 1080p/60fps MP4 clips up to 50 MB and 30 seconds.
[Optional private S3 original storage](docs/S3_SETUP.md) uses the EC2 instance role;
frames and SQLite annotations remain on the instance. Direct browser-to-S3 uploads
are not implemented.
