# Capacity measurement and retention promises

Prepared September 23, 2026. Two launch gates from
[the public release checklist](PUBLIC_RELEASE.md) are planned here: an AWS
capacity test using real clips and simultaneous visitors, and a retention
claim on the privacy page that the implementation cannot currently keep.
The capacity plan has not been executed; it is a plan, not evidence. Retention
step 2 was implemented on September 23, 2026 and is marked below. Its
verification against a real bucket is still outstanding.

## 1. Capacity with real clips and concurrent users

### Why the synthetic measurement is not enough

`tools/checkHostedCapacity.py` times one decode of one generated clip in
isolation and says so in its own `limitation` field. The live constraints are
different in kind, not degree: preparation is serialized by a lock, the web
service and its decoder share a 700 MB memory ceiling, and each clip costs
close to a gigabyte of disk.

The preparation lock decides what concurrency means here. `media_lock` in
`hosted/app.py` takes `LOCK_EX | LOCK_NB` and aborts with 429 rather than
waiting, so a second uploader is refused immediately instead of queued.
nginx adds `limit_req zone=uploads burst=2`. This is therefore a queueing and
failure-mode test, not a throughput benchmark. The questions are what the
second and third visitor see, for how long, and what is left behind.

### The three ceilings to instrument

**Memory.** `deploy/beanstalk/web-limits.conf` sets `MemoryHigh=550M` and
`MemoryMax=700M`. The decoder runs as a subprocess of gunicorn and shares that
cgroup, so frame serving during a preparation is the pressure point. Sample
`memory.current`, `memory.peak` and `memory.events`, and check
`journalctl -k | grep -i oom` after every scenario.

**Disk.** A 200 MB source plus up to 800 MB of prepared JPEGs is roughly one
gigabyte per clip, against a 1 GB free reserve and a 5 GiB global stored-video
quota. Four clips approach the instance limit. Drive the test into rejection
deliberately and confirm the rejection is a typed 413, not the `ENOSPC`
traceback the local suite produced on September 20.

**Timeout ordering.** The configured chain is CloudFront 120 s
([Beanstalk guide](AWS_BEANSTALK.md)), nginx `proxy_read_timeout 150s`,
gunicorn `timeout 150`, a 90 s decode deadline and a 15 minute browser
allowance. CloudFront cuts first. On a real 200 MB upload over a real uplink,
establish whether the visitor receives 504 while the server completes anyway
and registers a session the browser was told had failed.

### Clip corpus

Use footage there is clear permission to upload; personal 1080p/60 fps phone
video is sufficient and avoids the MLB redistribution question entirely.

| Clip | Purpose |
|---|---|
| ~200 MB, 1080p/60, near 60 s | The real worst case the limits advertise |
| ~50 MB, 1080p/60 | Ordinary upload for concurrency scenarios |
| 201 MB | Must be refused by nginx as 413 |
| 61 seconds | Must be refused by validation |
| 30 fps, and one variable-frame-rate file | Frame-count and rate handling |
| Truncated file | Decoder failure path |

### Harness

Add `tools/loadTest.py`. It drives N independent sessions against a base URL
with separate cookie jars and performs only what the browser performs: upload,
poll frames, place points, predict, export. It records per-request status,
latency, and counts of 429, 413 and 503. It must not call any endpoint the UI
does not call, or it stops measuring the real application.

Alongside it, sample the instance once per second during each run: free disk,
the cgroup memory counters above, CPU steal and burstable credit balance,
nginx status-code counts, and the gunicorn error log.

### Scenarios, in order

1. Single upload from cold. Baseline preparation time, peak memory, disk cost.
2. Two simultaneous uploads. Confirm the second receives 429 with the retry
   message, and that retrying after the first completes succeeds.
3. One upload with three visitors scrubbing frames, which exercises
   `limit_req zone=frames burst=90` while the decoder holds memory.
4. Fill to the global quota. Confirm clean refusal and an accurate message.
5. Upload attempted during `backup-s3`, which holds the media lock while
   archiving.
6. Cleanup and expiry afterwards. Confirm disk is reclaimed and saved work
   that should survive does.

### Exit criteria

No OOM kill. No `ENOSPC` traceback. Every rejection is a typed JSON error with
the intended status. The second visitor always receives an honest retry
message rather than a timeout or a failure. Saved annotations survive the full
run and export correctly. Cleanup reclaims the disk it should.

### Outputs

Write raw samples and a short report to `reports/hosted_capacity/<date>/`.
Reconcile the README and the in-app banner with the measured limits, set the
maximum number of simultaneous beta invitees from the result, and decide
whether 200 MB and 5 GiB should be reduced. Replace the self-disclaiming
`limitation` string in `tools/checkHostedCapacity.py` with a pointer to the
measurement.

## 2. Retention: make the promise match the implementation

### The actual gap

Original videos already have an explicit deletion path: `storage.drain_deletions`
plus the hourly cleanup, with the S3 lifecycle rule as a backstop for instance
loss. Backups do not. `armangle/backups/` is covered by lifecycle expiry alone,
and lifecycle deletion is asynchronous, so the privacy page's "Private backup
copies may remain for up to seven additional days" is the one retention claim
with no mechanism that can be verified or monitored.

Two separate deletion claims depend on scheduled jobs, and both should be
checked on the live instance before either is published:
`armangle-cleanup.timer` for the seven-day session expiry, and the backup
prune below. A timer that is not enabled, or that fails silently, makes the
published sentence false without any visible symptom.

### Step 1: correct the wording now

In `hosted/templates/privacy.html`:

- Say backup copies are scheduled for deletion after seven days and that
  removal can take somewhat longer, rather than stating a hard bound.
- Say plainly that deleting a session removes it from the live service
  immediately but does not rewrite snapshots already taken; those age out on
  the schedule above.
- Make the backup sentence conditional on backups being configured. No backup
  timer is installed today, so the page currently describes a process that
  does not exist on the running instance.

This is publishable on its own and does not depend on step 2.

### Step 2: add explicit pruning (implemented September 23, 2026)

Mirror what originals already do, so the claim is enforced by the application
and the lifecycle rule becomes a backstop rather than the mechanism.

- `hosted/backups.py`: add `prune_backups(bucket, region, keep_days=7)`. List
  the `armangle/backups/` prefix, explicitly `delete_object` everything past
  the cutoff, then `head_object` each deleted key and require a 404. Return
  the deleted keys and any that failed.
- Call it at the end of `upload_backup`, and add a `backup-prune` command in
  `hosted/manage.py` beside `backup-s3` so the daily timer prunes even when a
  snapshot upload fails.
- IAM: the instance role currently has `PutObject`, `GetObject` and
  `AbortMultipartUpload` on `armangle/backups/*`. Add `s3:DeleteObject` on
  that prefix and `s3:ListBucket` on the bucket restricted by a prefix
  condition. Update the policy text in [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md)
  and [S3_SETUP.md](S3_SETUP.md) together with the code.
- Tests: extend the `BackupS3` fake in `tests/test_hosted.py` with
  `list_objects_v2` and `delete_object`. Cover an old object deleted, a recent
  one kept, a failed verification surfaced rather than swallowed, and pruning
  still running after a failed snapshot upload.
- Keep the lifecycle rule for the case the application cannot cover: loss of
  the instance and its queue.

As built: `prune_backups(bucket, region, keep_days=7)` in `hosted/backups.py`
lists the prefix with pagination, deletes expired keys, and re-reads each one,
counting only a confirmed 404 as deleted. A failed delete, a key that still
resolves, a non-404 error on the confirming read, and an object whose age
cannot be read are all reported as failures and left to the lifecycle rule.
`upload_backup` prunes only after a snapshot verifies, and never discards the
new key if pruning faults. `hosted/manage.py` gains `backup-prune` and a
`--keep-days` option on both commands, and exits non-zero when pruning does
not complete so the systemd timer reports it. Covered by two tests in
`tests/test_hosted.py`; the hosted suite is 37/37.

### Verification before publishing the wording

Take one real snapshot, place a backdated object under the backups prefix, run
`backup-prune`, and confirm the deleted key returns 404. Separately confirm
the lifecycle rule is enabled and has taken effect. Check that
`armangle-cleanup.timer` is enabled and that its last run succeeded. Record
all of this in the release review before the privacy page goes public.

## Sequencing

The wording correction ships with the public source release. The capacity
measurement and explicit pruning gate opening the hosted site to visitors.

References:
- https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html
- https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-rules.html
