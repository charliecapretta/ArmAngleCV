"""Private S3 originals with a durable deletion queue; decoded frames stay local."""
from contextlib import closing
import hashlib
import logging
from pathlib import Path
import re

PREFIX = 'armangle/originals/'
logger = logging.getLogger(__name__)


def validate_config(bucket, region):
    if bool(bucket) != bool(region):
        raise RuntimeError('Set both ARMANGLE_S3_BUCKET and ARMANGLE_S3_REGION, or neither.')
    if bucket and (not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)
                   or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d', region)):
        raise RuntimeError('Use an S3 bucket name and AWS region, not a URL or credentials.')


def client(region):
    import boto3
    from botocore.config import Config
    # Use EC2's instance role. No access keys, bucket ACLs or public URLs.
    return boto3.client('s3', region_name=region,
                        config=Config(connect_timeout=3, read_timeout=8,
                                      retries={'total_max_attempts':1}))


def store_original(root, video, source, bucket, region):
    from hosted.app import connect
    validate_config(bucket, region)
    if not re.fullmatch(r'[0-9a-f]{32}', video):
        raise ValueError('Invalid video identifier.')
    s3 = client(region)
    # Dedicated non-versioned bucket: retrying a timed-out PUT cannot leave
    # hidden historical copies that the seven-day cleanup would miss.
    if s3.get_bucket_versioning(Bucket=bucket).get('Status'):
        raise ValueError('Use a bucket with versioning disabled, not suspended.')
    key = PREFIX + video + '/source.mp4'
    source = Path(source)
    with source.open('rb') as body:
        sha = hashlib.file_digest(body, 'sha256').hexdigest()
    with closing(connect(root)) as db, db:
        # Commit BEFORE PUT, so interruption or an ambiguous network result
        # leaves enough information for cleanup to delete the object later.
        db.execute('INSERT INTO s3_objects VALUES (?,?,?,?,NULL,?)',
                   (video, bucket, key, region, 'pending'))
    with source.open('rb') as body:
        result = s3.put_object(Bucket=bucket, Key=key, Body=body,
                               ContentLength=source.stat().st_size, ContentType='video/mp4',
                               ServerSideEncryption='AES256', Metadata={'sha256':sha})
    version = result.get('VersionId')
    with closing(connect(root)) as db, db:
        db.execute('UPDATE s3_objects SET version=? WHERE video=?', (version, video))
    return dict(kind='s3', bucket=bucket, key=key, region=region, version=version,
                bytes=source.stat().st_size, sha256=sha)


def drain_deletions(root, video=None, limit=20):
    """Called with media.lock held. Failures remain queued across restarts.

    The offline cleanup service obtains region/bucket from the database, so it
    does not need the web service's environment or session secret.
    """
    from hosted.app import connect
    with closing(connect(root)) as db:
        if video is None:
            rows = db.execute("SELECT * FROM s3_objects WHERE state='delete' LIMIT ?", (limit,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM s3_objects WHERE state='delete' AND video=?", (video,)).fetchall()
        for row in rows:
            try:
                args = dict(Bucket=row['bucket'], Key=row['key'])
                if row['version'] is not None:
                    args['VersionId'] = row['version']
                client(row['region']).delete_object(**args)
            except Exception:
                logger.warning('S3 deletion deferred for video %s', row['video'])
                continue
            with db:
                db.execute("DELETE FROM s3_objects WHERE video=? AND state='delete'", (row['video'],))
        if video is not None:
            return db.execute("SELECT COUNT(*) FROM s3_objects WHERE state='delete' AND video=?", (video,)).fetchone()[0]
        return db.execute("SELECT COUNT(*) FROM s3_objects WHERE state='delete'").fetchone()[0]
