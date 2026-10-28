# Hosted server test-run review — September 19, 2026

## Latest update: one-minute uploads and five accessible practice pitches

`armangle-beanstalk-60s-5pitches-20260919.zip` contains five MLB clips and supports
200 MiB / 60-second / 1080p / 60 fps uploads, up to 3,600 frames. The JPEG ceiling
is 800 MiB, with upload reservations of original size plus that ceiling. The
90-second decoder deadline and 1 GiB per-visitor / 5 GiB global quotas remain.

The practice endpoint now creates or reuses five distinct private pitches, rather
than reopening just one. Named buttons and the pitch selector expose all five;
saved labels and existing single-example work survive reopening. Clone failures
roll back the new set. Each practice clip retains its own CSV export.

Verification: all 29 hosted tests and 35 JavaScript tests passed. Coverage includes
a real 60-second, 60-fps MP4 through the upload endpoint (64 × 48 test frames),
rejection at 3,601 frames, five-pitch navigation, reuse, old-user upgrades, owner
isolation, and partial-clone cleanup. This is local validation, not an AWS performance
benchmark. Deployment and live AWS checks remain to be performed.

The sections below record earlier releases and their previous limits.

## MLB examples included in the deployment bundle

`armangle-beanstalk-200mb-s3-examples-20260919.zip` includes the 12 explicitly selected
`uiTestBatch.json` MLB clips and presentation/Statcast metadata. The archive is about
60 MiB and contains 52 checksummed runtime/media files plus its checksum manifest.
No user uploads, annotations or research datasets are included. The post-deploy hook
installs examples as `webapp`; unchanged examples are skipped on subsequent runs.

Verification from an extracted copy of the actual ZIP: all checksums passed; all 12
real clips decoded successfully; a second installation installed zero clips; each
example had its complete frame set. Installed originals and JPEGs used 918.8 MiB
locally. These shared examples are outside the 5 GiB upload quota, remain on instance
disk, and do not expire. Budget roughly 1 GiB extra runtime disk plus the compressed
source bundle retained by Beanstalk. All 26 hosted tests passed, including idempotent
installation, private example access, failed decoding and checksum rejection.
Linux deployment-hook execution and actual AWS performance still need live verification.

## Latest update: 200 MB upload preparation

The current hosted code and new `armangle-beanstalk-200mb-s3-20260919.zip` accept
200 MiB originals (displayed as 200 MB). This update is local, not deployed. Duration,
dimensions, frame rate, frame count, decoder memory strategy and JPEG ceiling remain
30 seconds / 1080p / 60 fps / 1,800 frames / sequential decoding / 400 MiB of JPEGs.
Per-browser/account quota is now 1 GiB; global quota remains 5 GiB. Reservations use
the declared original size plus 400 MiB of frames, up to 600 MiB. S3 originals still
count toward quotas. nginx/Caddy body limits and hosted browser timeout were updated.

The local 200 MiB boundary test streams a valid short MP4 padded with a trailing MP4
`free` box through the application and real decoder, then verifies rejection at one
byte over the limit. It verifies request size, staging and decoding compatibility;
it is not a benchmark of a high-bitrate 200 MiB clip. Live AWS upload, proxy-buffer
disk use, preparation latency and memory still require measurement. Direct browser
uploads to S3 and a processing queue remain future work.

Verification: 24 hosted tests, four packaged Gunicorn HTTP workflow tests (including
Beanstalk anonymous and CloudFront proxy modes), and 34 JavaScript tests passed.
`git diff --check` passed. The deployment ZIP contains 39 allowlisted runtime files.

Pricing checked against AWS's Ohio S3 price list published September 18, 2026:
S3 Standard's first tier is $0.023/GiB-month. One 200 MiB original retained for
seven days costs approximately $0.00105 in storage, using a 30-day month. Requests,
delivery, compute, backups and taxes are additional; this is not a total server bill.
Changing the byte cap does not change the configured EC2 instance or its base price.
Frame extraction and angle prediction run on the server, not in the user's browser.
Source: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-east-2/index.json

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
