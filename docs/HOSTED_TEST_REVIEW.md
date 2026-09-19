# Hosted server test-run review — September 19, 2026

## Later update: 1080p/60fps and optional S3 originals

The CloudFront/Beanstalk anonymous app was subsequently deployed and smoke-tested
live through upload, prediction, save, CSV export and deletion. Direct public HTTP
was blocked. The new 1080p/S3 version is prepared locally and is not deployed yet.

- Real 1920 × 1080 at 60fps is accepted without downscaling or dropping frames.
- Maximum clip remains 50 MB / 30 seconds; JPEG ceiling is 400 MB, with a 450 MB
  reservation before decoding. Browser/global quotas remain 500 MB / 5 GB.
- A 30-second synthetic 1080p60 clip produced 1,800 frames in 15.09 seconds on macOS,
  with 117.42 MiB peak decoder RSS, 210.27 MiB measurement-process RSS and 202.86 MiB
  total temporary video/frame storage. These are not AWS or worst-case measurements.
- Optional S3 originals use private encrypted PUTs through the instance role. Frames
  and SQLite stay local. Tests cover SDK request parameters, ownership isolation,
  backup references, expiry, lost PUT responses, registration failure, and persisted
  deletion retries. There is no live S3 test yet because no bucket has been created.
- Setup, IAM policy, required lifecycle rule and limitations are in `docs/S3_SETUP.md`.

The remainder records the earlier pre-deployment Lightsail review; its deployment
status, limits and account-only instructions are historical, not the current setup.

Verdict: the application package is ready for a controlled deployment test. It is not
live, and the real AWS instance/HTTPS checks below still need to pass before inviting
friends. Uploads currently use private disk storage, not S3. Public registration is off.

## Review fixes

- Increased the hosted browser upload timeout to 300 seconds. Its former 180-second
  timeout could expire before the proxy's allowed 120-second receive window plus the
  decoder's 100-second preparation window finished. Local upload timeout is unchanged.
- Reject startup if the model is missing, has the wrong checksum, has an incompatible
  feature schema, or the public HTTPS origin is invalid. The health route checks database
  access; prediction libraries/model load at startup instead of failing on the first pitch.
- Restore refuses archives missing the database, application tables, valid references,
  original video, metadata, or any prepared frame. Failed restore removes only the new
  recovery directory; it never replaces existing live data.
- Explicitly close SQLite connections used by initialization, cleanup and administration.
- Successful sign-ins no longer consume the failure budget, avoiding lockouts for friends
  sharing a network or signing in repeatedly. Failed-attempt limits remain active.
- Added a real HTTP integration test against Gunicorn, running the extracted deployment
  archive in a separate process with production settings and disposable test accounts.

## Verification completed

- 51 Python tests passed, including the unchanged local workflow and the hosted tests.
- 19 JavaScript tests passed, including hosted CSRF headers and the longer upload timeout.
- Frozen manual-v1 model hashes, refit identity, grouped score and resize invariance passed.
- Packaged Gunicorn test: unauthenticated requests blocked; correct Secure/HttpOnly cookies;
  unexpected hosts rejected; real MP4 uploaded and decoded; JPEG served; prediction matched
  saved CSV; second account denied access; saved CSV survived a full server process restart;
  session deletion removed frame access; sign-out invalidated access.
- Hosted regression tests: ownership on label/predict/import/frame/undo/export/delete paths;
  CSRF/origin checks; disabled training and filesystem routes; busy decoder, invalid upload,
  storage limit, expiry, stale undo, account revocation; valid backup/restore; incomplete
  backups rejected; repeated successful sign-ins behind one IP still work.
- Existing isolated browser check covered hosted sign-in, loading the workspace and opening
  the upload form without console errors. The full upload-to-CSV check above used real HTTP;
  it does not claim an end-to-end browser automation run or real TLS verification.

The earlier synthetic 900-frame 720p/30fps clip completed decoding in 4.16 seconds with
78.52 MiB peak decoder RSS on macOS. That result is a smoke measurement only, not the
capacity of a 1 GB Lightsail instance or a worst-case video. `tools/checkHostedCapacity.py`
is included in the deployment ZIP so this can be repeated on Linux.

## Remaining deployment checks

1. Provision the approved $7 instance, install the package, configure the hostname, private
   data directory and random secret, then validate Caddy and start the supplied systemd units.
2. Confirm HTTPS, cookie behavior, firewall isolation and the real hostname; the HTTP test
   emulates the loopback proxy headers and does not provision certificates.
3. Measure memory and preparation time on the instance, using a maximum supported clip
   while a second account browses frames. Check for service restarts, OOM events and errors.
4. Make and restore an off-instance backup, set its private destination and 7-day retention,
   and verify the hourly cleanup timer actually runs. Daily off-instance backups are not
   automatically configured by this package.
5. Create individual invited accounts and run one browser upload → release → five points →
   save → reload → CSV export trial before sending the link to friends.

Follow `docs/AWS_LIGHTSAIL.md`. Caddy, systemd and Linux resource controls cannot be executed
on this macOS development host; no AWS deployment or billing changes were made by this review.
