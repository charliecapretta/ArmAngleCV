# S3 upload assessment

Update September 19, 2026: server-mediated private S3 originals are now implemented.
See [S3 setup](S3_SETUP.md) for the current behavior and deployment steps. No bucket
has been created or connected yet. Presigned browser uploads remain a future option.
The assessment below records the original inspection before that implementation.
The hosted preparation is separate from the currently running local app.

## Framework and current storage

- Browser: plain HTML/CSS and vanilla JavaScript, no React/Next.js. `web/labelTool.html`
  provides the file picker. `web/labelTool.js:sendVideo` sends the raw File through XHR
  to `POST /api/upload?filename=...&sessionName=...&pitcherName=...&pThrows=...`.
  Upload progress uses `xhr.upload.onprogress`; the response must be JSON `{ok,pitch}`.
- Local backend: Python standard-library `ThreadingHTTPServer` and
  `SimpleHTTPRequestHandler`, in `armangle/labelServer.py`. It streams chunks to
  `<project>/uploads/<uuid>.mp4`, validates with OpenCV, and registers the local path
  as `sourcePath` in `localVideos.json`. Analyses live in `analysisLabels.csv`.
- Hosted backend being prepared: Flask + Gunicorn, `hosted/app.py`. Originals live
  at `$ARMANGLE_DATA/videos/<uuid>/source.mp4`; JPEGs and `meta.json` share the same
  directory. Default data root: `/var/lib/armangle`. Accounts, ownership, videos,
  pitches, labels and undo live in `app.sqlite3`. Upload metadata is saved only after
  preparation succeeds. Cleanup and deletion currently handle disk files only.
- No AWS SDK or S3 client currently exists in either flow.

## Smallest storage change: server uploads to S3

Keep the browser request and response unchanged. Add `boto3`, private bucket/region
configuration, and narrowly scoped server-side AWS permissions. Continue receiving
and validating the file locally; upload the validated original to S3 under a
server-generated owner/video key, then record the bucket/key in storage metadata.
Only report success after both storage and registration succeed; clean up incomplete
objects on failure. Add S3 removal to expiry/early deletion and account for orphaned
uploads and object versions if versioning is enabled.

Keeping the local file would make S3 an additional copy. To make S3 the authoritative
original store, remove it after successful processing and teach future reprocessing
or cache recovery to download the object to a bounded temporary file. Do not replace
`sourcePath` with `s3://...`: `os.path.isfile`, `open` and the current OpenCV path flow
will not work unchanged. JPEG frame storage and SQLite remain local unless separately
migrated. This is the least frontend work but still sends every upload through Lightsail.

## Direct browser uploads: compatible, with a two-stage API

1. Browser asks authenticated `POST /api/uploads/init` for permission, including size
   and session details. Backend reserves quota, generates an unpredictable key tied
   to this user, records a pending upload, and returns short-lived presigned POST
   fields or a presigned PUT URL.
2. Browser uses XHR to send the file directly to private S3. Keep the existing file
   picker, metadata controls and progress indicator. No browser AWS keys or SDK needed.
3. Browser calls authenticated `POST /api/uploads/<id>/complete`. Server resolves the
   pending record from the signed-in user, verifies object size/checksum/type as
   applicable, downloads to a temporary file, validates and prepares frames with the
   bounded decoder, and returns the existing `{ok:true,pitch}` result. Reject arbitrary
   bucket/key claims from the client. Completion must be idempotent and handle busy
   preparation without uploading the file again. If preparation becomes asynchronous,
   add a persisted job/status endpoint and frontend polling.

Changes inside `sendVideo()`:
- Use separate init, S3 upload, and completion calls.
- PUT can send the same raw File; POST uses FormData containing the signed fields,
  with the file appended last. Let the browser set the FormData content type.
- Preserve `xhr.upload.onprogress`.
- Do not parse S3 success as the application's JSON; S3 can return an empty success
  response or XML. Resolve with the completion API's JSON instead.
- Send app cookies/CSRF headers only to the application, not S3. Match the signed S3
  headers exactly. Keep status messages separate for uploading and preparing frames.
- Configure bucket CORS for the actual app origin and chosen upload method/headers.
- Extend the hosted CSP `connect-src 'self'` to the exact bucket endpoint. CORS does
  not make the bucket public or replace authentication.

Presigned POST is preferable here for an enforced `content-length-range` policy
matching the hosted 50 MB cap. Still inspect the actual content with OpenCV: a MIME
header cannot establish that bytes are a valid supported video. Add rate limits,
pending-upload expiry and quota reservations before allowing users to request signatures.

Presigned authorizations are reusable until expiry; repeated writes can replace an
object at the same key. Finalization should pin a specific immutable object version or
copy a verified upload to a server-only final key, then store that identity. Do not
assume a browser 'complete' call makes a still-valid upload URL one-time-use.

## Recommendation for the $7 beta

Start with the smallest server-mediated S3 change only if the goal is durable original
storage immediately. Direct presigned uploads are a better follow-on when upload traffic
or worker occupancy matters. Both require local CPU and temporary disk for this decoder;
S3 alone does not solve video processing capacity. S3 usage adds charges beyond the
Lightsail instance price. Storing originals in S3 does not back up SQLite annotations.

References:
- https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html
- https://docs.aws.amazon.com/AmazonS3/latest/developerguide/sigv4-HTTPPOSTConstructPolicy.html
- https://docs.aws.amazon.com/AmazonS3/latest/userguide/ManageCorsUsing.html
- https://docs.aws.amazon.com/boto3/latest/guide/s3-presigned-urls.html
