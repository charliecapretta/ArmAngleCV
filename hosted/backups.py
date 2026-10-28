"""Create an encrypted off-instance snapshot using the EC2 instance role."""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import shutil
import tempfile
import uuid

from hosted import storage

PREFIX = 'armangle/backups/'
KEEP_DAYS = 7


def _connect(bucket, region):
    storage.validate_config(bucket, region)
    if not bucket:
        raise ValueError('A private backup bucket and region are required.')
    s3 = storage.client(region)
    if s3.get_bucket_versioning(Bucket=bucket).get('Status'):
        raise ValueError('Use a dedicated backup bucket with versioning disabled, not suspended.')
    return s3


def _absent(error):
    """True only for a definite 404. A timeout is not evidence of deletion."""
    response = getattr(error, 'response', None) or {}
    return (response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound')
            or response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 404)


def prune_backups(bucket, region, keep_days=KEEP_DAYS):
    """Delete snapshots past the published retention window and confirm it.

    The bucket's lifecycle rule remains the backstop for a lost instance, but
    its deletion is asynchronous, so it cannot evidence the seven-day statement
    on the privacy page. Each key is deleted explicitly and then re-read: a key
    that still resolves is reported as failed rather than counted as removed.
    """
    if not isinstance(keep_days, int) or not 1 <= keep_days <= KEEP_DAYS:
        raise ValueError('Backup retention must be between 1 and 7 days.')
    s3 = _connect(bucket, region)
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    deleted, failed, kept = [], [], 0
    token = None
    expired = []
    while True:
        args = dict(Bucket=bucket, Prefix=PREFIX)
        if token:
            args['ContinuationToken'] = token
        page = s3.list_objects_v2(**args)
        for item in page.get('Contents', []):
            modified = item.get('LastModified')
            if modified is None:
                # Without an age the object cannot be judged; leave it to the
                # lifecycle rule rather than deleting something unidentified.
                failed.append(item['Key'])
            elif modified < cutoff:
                expired.append(item['Key'])
            else:
                kept += 1
        token = page.get('NextContinuationToken')
        if not (page.get('IsTruncated') and token):
            break
    for key in expired:
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception:
            failed.append(key)
            continue
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except Exception as error:
            if _absent(error):
                deleted.append(key)
            else:
                failed.append(key)
        else:
            failed.append(key)
    return dict(bucket=bucket, prefix=PREFIX, keep_days=keep_days,
                cutoff=cutoff.strftime('%Y-%m-%dT%H:%M:%SZ'),
                deleted=deleted, failed=failed, kept=kept)


def upload_backup(root, bucket, region, keep_days=KEEP_DAYS):
    from hosted.manage import backup
    if not isinstance(keep_days, int) or not 1 <= keep_days <= KEEP_DAYS:
        raise ValueError('Backup retention must be between 1 and 7 days.')
    s3 = _connect(bucket, region)
    # Backup archives dereference shared examples. Budget their logical size,
    # not merely their physical disk usage, and leave the runtime's reserve.
    root = Path(root)
    size = sum(p.stat().st_size for p in (root / 'videos').rglob('*') if p.is_file())
    size += (root / 'app.sqlite3').stat().st_size
    if shutil.disk_usage(tempfile.gettempdir()).free < size + 1024**3:
        raise ValueError('Not enough temporary disk space for a backup plus the 1 GB reserve.')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    key = f'{PREFIX}{timestamp}-{uuid.uuid4().hex}.tar.gz'
    with tempfile.TemporaryDirectory(prefix='armangle-backup-') as tmp:
        archive = Path(tmp) / 'snapshot.tar.gz'
        backup(root, archive)
        with archive.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        s3.upload_file(str(archive), bucket, key, ExtraArgs={
            'ServerSideEncryption':'AES256', 'ContentType':'application/gzip',
            'Metadata':{'sha256':digest},
        })
        remote = s3.head_object(Bucket=bucket, Key=key)
        if (remote.get('ContentLength') != archive.stat().st_size or
                remote.get('Metadata', {}).get('sha256') != digest or
                remote.get('ServerSideEncryption') != 'AES256'):
            raise RuntimeError('Remote backup verification failed; do not rely on this snapshot.')
    result = dict(bucket=bucket, key=key, sha256=digest)
    # Prune only after a verified snapshot, so a failed backup never also
    # deletes the older ones. A pruning fault must not discard the new key:
    # report it alongside the snapshot the caller has to record.
    try:
        result['pruned'] = prune_backups(bucket, region, keep_days)
    except Exception as error:
        result['pruned'] = dict(error=str(error), deleted=[], failed=[], kept=None)
    return result
