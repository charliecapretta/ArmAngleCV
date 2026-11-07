# Release verification — September 9, 2026

Scope: the manual-workflow release milestone. Independent validation and new
human annotation work are deferred at the owner's request.

## Environment and automated checks

A new Python 3.12.2 virtual environment was created on the development Mac and
populated from `requirements.txt`, without using the existing project's installed
packages. Package installation and `pip check` succeeded. Optional fetch packages
were installed separately for the live download check.

- 37 Python tests passed, including the original 29, five downloader regressions,
  a complete HTTP startup/save/export/undo check, and two public-file export checks.
- All 13 existing JavaScript workflow tests passed.
- The isolated HTTP test explicitly blocks download and pose imports and uses a
  generated temporary video and temporary label files.
- The frozen model matches a refit on its exact original training snapshot and
  reproduces 2.653° RMSE, versus 3.227° for the direct angle baseline.
- Uniform resizing leaves the released model's predictions unchanged.
- Model and source-label checksums match `model/model-manifest.json`.

The GitHub workflow is configured but has not run on GitHub, because nothing has
been published. These results are local checks, not a claim of completed hosted CI.

## Clean-copy download and browser check

One fixed MLB example was downloaded into a prepared public copy with no existing
video folder using `python scripts/fetchUiTestBatch.py --limit 1`. The downloader reported
one verified clip ready and preserved the manifest and labels.

In the isolated copy's browser UI, the example opened, release frame 176 was
selected, five points were placed, the angle estimate appeared, and the analysis
was saved. The browser reported one of one analyses saved. The downloaded text
file was inspected and contained the release time, estimated angle, quality
flags, and scope note. Reloading restored the saved release frame and all five
points. Test points were copied from an already labeled example
solely to check the workflow; this is not a new accuracy evaluation.

All browser test writes were made in the temporary copy. The source project's
MLB and personal labels, registered videos, and model files were preserved.

## Publication state

The owner selected the MIT code license. The generated public folder and ZIP
contain only `release-files.txt` entries plus their checksum manifest. They
exclude personal registrations, annotations, prediction reports, editor files,
large weights, downloaded footage, and Git history. Nothing has been pushed,
and the original repository history has not been rewritten.
