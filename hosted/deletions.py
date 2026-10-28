"""Deletion records kept independently of snapshots to prevent resurrection."""
from contextlib import closing
import io
import json
import re
import time

from hosted import storage

PREFIX = 'armangle/deletions/'


def configure(root, bucket, region):
    """Bind snapshots to their independent ledger; don't accept an empty/wrong bucket."""
    from hosted.app import connect
    storage.validate_config(bucket, region)
    location = json.dumps([bucket, region])
    with closing(connect(root)) as db, db:
        previous = db.execute("SELECT value FROM settings WHERE key='deletion_ledger'").fetchone()
        if previous and previous['value'] != location:
            raise RuntimeError('Keep the existing deletion ledger configuration; changing it needs a verified ledger migration.')
        if bucket:
            db.execute("INSERT OR IGNORE INTO settings VALUES ('deletion_ledger', ?)", (location,))


def record(root, identifiers, bucket='', region=''):
    from hosted.app import connect
    storage.validate_config(bucket, region)
    for identifier in identifiers:
        if not re.fullmatch(r'[0-9a-f]{32}', identifier):
            raise ValueError('Invalid video identifier.')
        # Publish BEFORE acknowledging deletion. A retry overwrites the same
        # marker; failure leaves the session available so the user can retry.
        # Markers contain only opaque IDs, no names, video or owner information.
        if bucket:
            storage.client(region).put_object(
                Bucket=bucket, Key=PREFIX + identifier, Body=io.BytesIO(b'1'),
                ContentLength=1, ContentType='application/octet-stream',
                ServerSideEncryption='AES256')
        with closing(connect(root)) as db, db:
            db.execute('INSERT OR IGNORE INTO deletions VALUES (?,?)', (identifier, time.time()))


def remote_ids(bucket, region):
    storage.validate_config(bucket, region)
    if not bucket:
        raise ValueError('An independent deletion bucket and region are required.')
    s3 = storage.client(region)
    result, token = set(), None
    while True:
        args = dict(Bucket=bucket, Prefix=PREFIX)
        if token:
            args['ContinuationToken'] = token
        page = s3.list_objects_v2(**args)
        for item in page.get('Contents', []):
            identifier = item['Key'][len(PREFIX):]
            if not re.fullmatch(r'[0-9a-f]{32}', identifier):
                raise ValueError('Unexpected deletion ledger key.')
            result.add(identifier)
        if not page.get('IsTruncated'):
            return result
        token = page.get('NextContinuationToken')
        if not token:
            raise ValueError('Incomplete deletion ledger listing.')


def reconcile(root, *, current_data=None, bucket='', region='', discard=False):
    """Only use a current independent ledger, never the restored snapshot's DB.

    The old service must be stopped throughout recovery so no deletion can race
    this read. Without trustworthy records, discard recovered sessions instead.
    """
    from pathlib import Path
    import sqlite3
    from hosted.app import cleanup, connect, media_lock
    root = Path(root)
    if not (root / 'restore.pending').exists():
        raise ValueError('This directory is not a quarantined restore.')
    if sum((bool(current_data), bool(bucket), bool(discard))) != 1:
        raise ValueError('Choose one current deletion ledger or discard all recovered sessions.')
    if current_data:
        source = Path(current_data).resolve()
        if source == root.resolve() or (source / 'restore.pending').exists():
            raise ValueError('Use the current live ledger, not a restored snapshot.')
        with closing(sqlite3.connect((source / 'app.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            identifiers = {row[0] for row in db.execute('SELECT video FROM deletions')}
    elif bucket:
        with closing(connect(root)) as db:
            expected = db.execute("SELECT value FROM settings WHERE key='deletion_ledger'").fetchone()
        if expected is None or json.loads(expected['value']) != [bucket, region]:
            raise ValueError('Use the deletion ledger bound to this snapshot. Older snapshots need the current live ledger or session discard.')
        identifiers = remote_ids(bucket, region)
    else:
        with closing(connect(root)) as db:
            identifiers = {row[0] for row in db.execute('SELECT id FROM videos')}
    with media_lock(root):
        with closing(connect(root)) as db, db:
            for identifier in identifiers:
                db.execute('INSERT OR IGNORE INTO deletions VALUES (?,?)', (identifier, time.time()))
                db.execute("UPDATE s3_objects SET state='delete' WHERE video=?", (identifier,))
                db.execute('DELETE FROM videos WHERE id=?', (identifier,))
        cleanup(root)
        (root / 'restore.pending').unlink()
