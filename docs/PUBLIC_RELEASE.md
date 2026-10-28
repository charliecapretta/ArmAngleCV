# Public release gates and operations

Prepared September 19, 2026. This is the current public-launch checklist; older
Azure/private-beta checklists are historical. A prepared ZIP is not launch approval.

Current privacy/storage behavior and recovery commands: [Privacy and storage](PRIVACY_STORAGE.md).
That guide supersedes older cache, original-retention and restore instructions.

## Owner inputs and live blockers

- Confirm the current public HTTPS URL and provide access through a signed-in AWS
  console or a locally configured AWS profile. Do not send access keys in chat.
- Choose a maximum monthly operating budget and a public support/privacy email.
  Set `ARMANGLE_SUPPORT_EMAIL` to that address before launch.
- Confirm permission to serve the MLB footage publicly. Otherwise set
  `ARMANGLE_EXAMPLES=off` in Beanstalk: new practice sessions and existing practice
  frame access are disabled, while users can still export saved annotations.
  Build without `--include-examples`. A code-only deployment does not delete clips
  previously installed on the server. The environment switch is required too.
- Configure durable external data storage, or explicitly launch a limited beta
  with tested off-instance snapshots and a documented recovery point. Daily
  snapshots can lose up to roughly 24 hours of work, and failed jobs extend that.
  They are not a substitute for a durable database in an unrestricted release.
- Measure AWS capacity at the real upload limit, exercise restoration to a fresh
  instance, and prove the CloudFront origin remains private after replacement.

## Release verification

Use Python 3.12 and the pinned hosted requirements, then run:

```sh
python -m unittest discover -s tests -v
node --test tests/labelTool.test.cjs
python tools/checkRelease.py
python tools/checkHostedCapacity.py
python tools/prepareBeanstalk.py --out release/NEW-RELEASE.zip
```

The capacity command now generates 60 seconds at 1080p/60fps. It is synthetic,
not a worst-case clip or a concurrent-user benchmark. Keep several GB of disk
free. Production admission reserves up to 1,000 MiB for a new upload plus 1 GiB
free disk. Unit fixtures model capacity explicitly; packaged HTTP tests keep
the real production disk checks. All must pass before public launch.

Audit the generated ZIP's requirements (including headless OpenCV) using
`pip-audit -r requirements.txt`. Record the resolved versions and audit date.
Build a clean Python environment; optional research packages in a developer
environment are not part of the hosted release.

## Emergency upload pause

On the running Beanstalk instance:

```sh
sudo -u webapp /usr/local/libexec/armangle-manage uploads pause
sudo -u webapp /usr/local/libexec/armangle-manage uploads status
sudo -u webapp /usr/local/libexec/armangle-manage uploads resume
```

The private marker persists through process restarts and in-place updates.
New upload/preparation requests and practice sessions receive 503. Existing
work can still be reviewed, saved, exported and deleted. An upload already
in progress may finish. This does not block incoming bandwidth or all request
costs: edge rate limits and traffic monitoring remain necessary. The marker is
not included in data backups; reapply a pause after restoring if needed.

## Off-instance backups

The `backup-s3` command creates a consistent database/media snapshot, uploads it
with SSE-S3 encryption through the instance role, and verifies remote size and
SHA-256 metadata. It uses a unique key and removes its temporary local archive.
This verifies successful storage metadata, not a downloaded restore. Test the
download and restore independently before relying on it.

After a snapshot verifies, the same command deletes snapshots older than
`--keep-days` (seven by default) and re-reads each key to confirm it no longer
resolves. A key that still resolves, a failed delete, and an object whose age
cannot be read are all reported as failures and left for the lifecycle rule;
only a confirmed 404 counts as deleted. Pruning runs only after a verified
snapshot, so a failed backup never also removes the older ones, and the command
exits non-zero when pruning does not complete, which is what the timer reports.
Run `backup-prune` with the same bucket and region to prune without taking a new
snapshot, including after a backup failure.

Before enabling it, create a dedicated private, non-versioned S3 bucket. Enable
Block Public Access, restrict access to the instance/operator roles, and enforce
HTTPS. Configure lifecycle expiry for `armangle/backups/` after seven days and
abort incomplete multipart uploads after one day. Lifecycle deletion is
asynchronous; verify actual retention and align the public notice with it.
Do not enable Object Lock or retain historical versions for this seven-day policy.
The application's own pruning is what evidences the published retention window;
the lifecycle rule is the backstop for a lost instance or a lost cleanup queue,
and its asynchronous timing is therefore no longer what the notice depends on.

The instance role needs `s3:GetBucketVersioning` on the bucket and
`s3:PutObject`, `s3:GetObject`, `s3:AbortMultipartUpload` and `s3:DeleteObject`
on its `armangle/backups/*` prefix. Pruning also needs `s3:ListBucket` on the
bucket itself, restricted to that prefix, because listing is a bucket-level
action:

```json
{
  "Sid": "ListArmAngleBackupsOnly",
  "Effect": "Allow",
  "Action": "s3:ListBucket",
  "Resource": "arn:aws:s3:::YOUR_BACKUP_BUCKET",
  "Condition": {"StringLike": {"s3:prefix": "armangle/backups/*"}}
}
```

Recovery operators additionally need listing and download access. Do not grant
public reads or use static access keys.

```sh
sudo -u webapp /usr/local/libexec/armangle-manage backup-s3 \
  --bucket YOUR_PRIVATE_BACKUP_BUCKET --region YOUR_REGION
```

```sh
sudo -u webapp /usr/local/libexec/armangle-manage backup-prune \
  --bucket YOUR_PRIVATE_BACKUP_BUCKET --region YOUR_REGION
```

Keep the returned key and SHA-256 in private operational records. Preserve the
current `ARMANGLE_SECRET` securely outside the instance; it is intentionally
absent from archives and is required to preserve browser session ownership.
S3 original videos are references in the snapshot, not embedded copies; those
objects must still exist for original-video recovery. Prepared frames and saved
annotations are embedded. Practice hard links are expanded into regular files
so a fresh restore is self-contained. This can increase snapshot size and cost.

After the first remote backup and downloaded restore pass, install the supplied
timers (deployment enables them only when `/etc/armangle-backup.env` exists;
include the independent prune units described in the privacy/storage guide):

```sh
sudo install -m 644 /var/app/current/deploy/beanstalk/backup.service /etc/systemd/system/armangle-backup.service
sudo install -m 644 /var/app/current/deploy/beanstalk/backup.timer /etc/systemd/system/armangle-backup.timer
```

Create root-owned `/etc/armangle-backup.env`, mode 600, containing
`ARMANGLE_BACKUP_BUCKET=...` and `ARMANGLE_BACKUP_REGION=...`, then:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now armangle-backup.timer
sudo systemctl start armangle-backup.service
sudo systemctl status armangle-backup.service armangle-backup.timer
```

Monitor failed jobs and the age of the latest successful snapshot. A timer on a
lost instance cannot back up data; recreate these settings on replacement.
The snapshot holds the media lock while archiving, so large backups may briefly
reject uploads. Schedule during low traffic and measure duration and free space.

For a recovery drill, download one snapshot privately, verify its SHA-256, and
restore to a new directory on a fresh instance:

```sh
python -m hosted.manage --data /NEW/RECOVERY/DIRECTORY restore /PRIVATE/snapshot.tar.gz
```

Keep the old service stopped. Run `restore-reconcile` against the independent
deletion ledger as described in the privacy/storage guide. Restore the same
session secret and origin settings, then verify
saved labels, frame access, CSV parity, ownership isolation, and deletion. Do
not overwrite the live directory. Do not announce a recovery guarantee until
this drill has passed using a downloaded remote snapshot.

## Live acceptance and rollback

- Verify HTTPS, private origin ingress, trusted proxy mode and disabled caching
  for private requests. Recheck after each infrastructure update.
- Test two browsers through upload, labeling, reload, CSV export and deletion.
  Test invalid clips, busy preparation, expiry and unavailable remote storage.
- Test simultaneous visitors and actual 60-second 1080p/60fps clips. Record
  preparation latency, memory, disk, request errors, and the point of saturation.
- Configure edge rate limits, health/error/disk alerts, a backup-failure alert,
  budget alerts and a documented response owner. Alerts are not a spending cap.
- Record the release ZIP SHA-256, application version, environment settings and
  model version. Preserve the previous ZIP and a verified data snapshot.
- Exercise rollback on a staging instance. Do not assume in-place deploy settings
  prevent Beanstalk from replacing an unhealthy instance. The current VPC origin
  also targets a particular EC2 instance and must be updated after replacement.
- Review the privacy page against actual hosting logs, backup retention and
  contact information. The page describes implementation, not a complete legal
  review of every jurisdiction or audience.

References: [Beanstalk storage](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/concepts.concepts.design.html),
[WAF rate limits](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based.html),
[S3 lifecycle expiry](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html).
