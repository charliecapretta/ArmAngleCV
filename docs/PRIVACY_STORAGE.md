# Privacy and storage changes — September 23, 2026

This describes the current source, not a verified AWS deployment. It supersedes
older instructions about original retention, frame caching, and restore startup.

## Application behavior

- The hosted file picker uses multipart form fields. Names and filenames are no
  longer included in its URL. Legacy raw-video API requests remain compatible;
  their query strings must not be logged. The local desktop workflow is unchanged.
- Frames use `Cache-Control: no-store, private`, without conditional cache
  responses. A new frame URL version avoids reusing older seven-day cache entries.
  Previously downloaded files, open tabs and old cache entries cannot be remotely
  erased. The new frontend asset has a new version URL.
- `ARMANGLE_RETAIN_ORIGINALS=off` is the default: new original MP4s are discarded
  after decoding. Prepared frames and annotations remain. `on` explicitly retains
  the MP4 (including its audio/metadata), locally or in optional S3 storage.
  Previously retained originals still follow their existing deletion/expiry rules.
  Shared practice templates are unaffected. Defaults do not migrate old uploads.
- Delete session records an opaque tombstone before deleting live records. Physical
  deletion failures stay eligible for cleanup; the UI distinguishes pending removal.
  Delete all affects only the current owner, ends the cookie session, and requests
  browser cache/cookie/storage clearing. Already-open tabs can still hold images.
- Seven-day session expiry is unchanged. Cleanup runs hourly and reports a failed
  service run while remote objects remain pending, rather than claiming success.
- Backups are scheduled for removal after seven days; this is not a hard physical
  deletion deadline. An hourly prune timer runs independently of successful backups.
  The backup runtime refuses retention parameters outside 1–7 days.
- Restored snapshots are quarantined. The app refuses startup until independent
  deletion records have been reconciled. A snapshot cannot supply its own ledger.

## Configuration before accepting public uploads

Set a real `ARMANGLE_SUPPORT_EMAIL`. Configure a private S3 deletion ledger using
`ARMANGLE_DELETION_BUCKET` and `ARMANGLE_DELETION_REGION`, then set
`ARMANGLE_PUBLIC_RELEASE=on`. Public mode refuses startup without these settings;
it does not certify the remaining infrastructure checks below. Keep the same
ledger settings across restarts: the database binds snapshots to that location
and rejects accidental changes. A deliberate bucket migration requires copying
and verifying all markers before changing the recorded binding offline.

Deletion markers use `armangle/deletions/<random-video-id>` with a one-byte body,
SSE-S3 encryption and no names, footage or owner details. They are written before
deletion is acknowledged. A failed write returns 503 so the user can retry.
Keep the ledger independent of snapshot rollback. Use a dedicated private,
non-versioned bucket with Block Public Access, HTTPS-only access and no Object
Lock. Do not apply the seven-day backup rule to deletion markers. Keep them for
at least 30 days; seven-day video expiry is still enforced during recovery.
Local tombstones also survive process restarts and remain in the live database.

The application role needs `s3:PutObject` only on
`arn:aws:s3:::DELETION_BUCKET/armangle/deletions/*`. Recovery operators need
`s3:ListBucket` on that bucket restricted with `s3:prefix` to
`armangle/deletions/`. Do not give the application permission to delete markers.
The recovery tool verifies that the selected bucket/region match the snapshot's
binding; it refuses unbound older snapshots for remote reconciliation.

For the separate backup bucket, the application needs:

- `s3:GetBucketVersioning` on the bucket;
- `s3:ListBucket` on the bucket, restricted to prefix `armangle/backups/`;
- `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` and
  `s3:AbortMultipartUpload` on `armangle/backups/*`.

Use a private non-versioned backup bucket and a seven-day lifecycle rule on
`armangle/backups/` as an additional deletion mechanism. Create root-owned
`/etc/armangle-backup.env` with mode 600 and only `ARMANGLE_BACKUP_BUCKET` and
`ARMANGLE_BACKUP_REGION`. Deployment installs all units; it enables backup and
independent prune timers only when this environment file exists. Recreate this
configuration after instance replacement. First test a backup and downloaded
restore, then enable the timers on an already deployed instance:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now armangle-backup.timer armangle-backup-prune.timer
sudo systemctl start armangle-backup.service armangle-backup-prune.service
sudo systemctl status armangle-cleanup.timer armangle-backup.timer armangle-backup-prune.timer
```

Alert on failed cleanup, failed backup/prune jobs, old successful snapshots,
disk pressure and instance replacement. Scheduled jobs without alerting are
not a reliable retention or recovery guarantee.

## Restore without undoing deletions

Stop the old application before reconciling, and keep it stopped until cutover.
Otherwise a deletion can race the final ledger read. Preserve the session secret
and the configured ledger outside the failed instance. Restore to a new directory:

```sh
python -m hosted.manage --data /NEW/RECOVERY restore /PRIVATE/snapshot.tar.gz
python -m hosted.manage --data /NEW/RECOVERY restore-reconcile --bucket DELETION_BUCKET --region AWS_REGION
```

If the stopped, current live disk survives, its deletion records may be used:

```sh
python -m hosted.manage --data /NEW/RECOVERY restore-reconcile --current-data /CURRENT/LIVE/DATA
```

Never pass another snapshot as `--current-data`. If no trustworthy independent
records survive (including deletions made before remote recording was enabled),
leave recovery offline or deliberately discard recovered sessions:

```sh
python -m hosted.manage --data /NEW/RECOVERY restore-reconcile --discard-sessions
```

The last command deletes recovered sessions and their referenced originals; use
only for a real recovery, not a drill sharing production object references.
Perform drills with isolated buckets and test uploads. Reconciliation removes
deleted/expired sessions and their files before removing the quarantine marker.
Test a snapshot taken before a deletion and confirm that session cannot reopen.
Never remove `restore.pending` manually to bypass reconciliation.

## Still requires live infrastructure work

SQLite and frames remain on the instance. This patch does not migrate them to a
durable shared database/object store. Keep a limited beta with a tested recovery
point until that migration is done. Daily snapshots can lose roughly a day of
work, and failures can extend that. Do not claim lossless recovery.

Verify the actual deployed version, HTTPS-only access, private origin ingress,
least-privilege IAM, bucket public-access blocks, EBS encryption, provider log
retention and backup lifecycle rules. Disable edge caching for private responses
(minimum TTL zero); forward cookies and all required request parameters. Exclude
query strings/cookies/authorization from request logs and disable request-body
logging. Audit existing logs for the old upload names and set their expiry.
Provider logs and downloaded snapshots have their own retention boundaries.

Nginx/Caddy allow 200 MiB plus 64 KiB multipart overhead. Flask independently
bounds the video itself to 200 MiB and limits fields/parts. Multipart parsing and
nginx may spool temporary copies: monitor both temp and application volumes.
The conservative 1 GiB per-owner and 5 GiB total quotas remain. Global exhaustion
now returns a server-capacity message; it no longer tells an empty workspace to
delete its own videos. The single decoder still rejects concurrent preparation.
Measure real 1080p/60 clips and simultaneous visitors on the actual instance;
raising quotas without durable capacity and monitoring is not a fix.

AWS references: [S3 lifecycle removal is asynchronous](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html)
and [bucket versus object permissions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-policy-language-overview.html).
