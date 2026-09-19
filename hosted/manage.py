"""Offline administration: accounts, cleanup, consistent backups and restore."""
import argparse
from contextlib import closing
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import uuid

from werkzeug.security import generate_password_hash
from hosted.app import cleanup, connect, initialize, media_lock
from hosted import example
from hosted.video import MAX_FRAMES


def backup(root, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Backup destination already exists.')
    with media_lock(root), tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / 'app.sqlite3'
        with closing(connect(root)) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        # Freeze video membership from the same database snapshot.
        with closing(sqlite3.connect(snapshot)) as target:
            videos = [row[0] for row in target.execute('SELECT id FROM videos')]
        try:
            with destination.open('xb') as output:
                os.chmod(destination, 0o600)
                with tarfile.open(fileobj=output, mode='w:gz') as archive:
                    archive.add(snapshot, arcname='app.sqlite3')
                    for video in videos:
                        archive.add(Path(root) / 'videos' / video, arcname='videos/' + video)
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def restore(archive_path, destination):
    root = Path(destination)
    if root.exists():
        raise ValueError('Restore requires a new, nonexistent data directory. Stop the server first.')
    root.mkdir(mode=0o700)
    try:
        with tarfile.open(archive_path, 'r:gz') as archive:
            for member in archive.getmembers():
                path = Path(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()) or path.parts[0] not in ('app.sqlite3', 'videos'):
                    raise ValueError('Invalid backup contents.')
            archive.extractall(root, filter='data')
        if not (root / 'app.sqlite3').is_file():
            raise ValueError('Backup is missing its database.')
        with closing(connect(root)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Database integrity check failed.')
            tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'users', 'videos', 'pitches', 'labels', 'undo', 'attempts'} <= tables:
                raise ValueError('Backup is not a complete Arm Angle database.')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Backup contains broken database references.')
            for video in db.execute('SELECT id,metadata FROM videos'):
                if not re.fullmatch('[0-9a-f]{32}', video['id']):
                    raise ValueError('Backup has an invalid video identifier.')
                directory = root / 'videos' / video['id']
                metadata = json.loads(video['metadata'])
                count = metadata.get('nFrames')
                if not isinstance(count, int) or not 0 < count <= MAX_FRAMES:
                    raise ValueError('Backup has invalid frame metadata.')
                required = [directory / 'meta.json']
                original = metadata.get('original')
                if original is None:
                    required.append(directory / 'source.mp4')
                else:
                    if not isinstance(original, dict) or original.get('kind') != 's3' or 's3_objects' not in tables:
                        raise ValueError('Backup has invalid S3 original metadata.')
                    obj = db.execute("SELECT * FROM s3_objects WHERE video=? AND state='active'", (video['id'],)).fetchone()
                    if obj is None or any(original.get(k) != obj[k] for k in ('bucket','key','region','version')):
                        raise ValueError('Backup is missing its S3 original reference.')
                required += [directory / f'{i:04d}.jpg' for i in range(count)]
                if any(not path.is_file() or path.stat().st_size == 0 for path in required):
                    raise ValueError('Backup is missing video or frame files.')
        initialize(root)
    except Exception:
        shutil.rmtree(root)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default=os.environ.get('ARMANGLE_DATA', '/var/lib/armangle'))
    commands = parser.add_subparsers(dest='command', required=True)
    user = commands.add_parser('user', help='Create or reset an invited account; invalidates old sessions')
    user.add_argument('username')
    revoke = commands.add_parser('revoke', help='Disable sign-in and invalidate sessions')
    revoke.add_argument('username')
    commands.add_parser('cleanup')
    practice = commands.add_parser('example', help='Install, list or remove the shared practice clip')
    practice.add_argument('action', choices=('install', 'list', 'remove'))
    practice.add_argument('video', nargs='?', help='MP4 to install')
    practice.add_argument('--slug', default='mlb-example')
    practice.add_argument('--meta', help='JSON file of Statcast fields for this pitch')
    practice.add_argument('--pitcher', default='')
    practice.add_argument('--throws', default='R', choices=('R', 'L'))
    practice.add_argument('--angle', help='Statcast arm angle in degrees, revealed at review')
    practice.add_argument('--park', default='')
    practice.add_argument('--session-name', default='MLB example')
    practice.add_argument('--replace', action='store_true')
    save = commands.add_parser('backup')
    save.add_argument('destination')
    load = commands.add_parser('restore')
    load.add_argument('archive')
    args = parser.parse_args()
    if args.command == 'restore':
        restore(args.archive, args.data)
        print('Restored. Keep the same ARMANGLE_SECRET, then start the server with this data directory.')
        return
    initialize(args.data)
    if args.command == 'user':
        username = args.username.strip().lower()
        if not re.fullmatch(r'[a-z0-9._-]{3,64}', username):
            raise SystemExit('Use 3–64 letters, numbers, periods, underscores or hyphens.')
        password = getpass.getpass('Password (at least 12 characters): ')
        if len(password) < 12 or len(password) > 256 or password != getpass.getpass('Repeat password: '):
            raise SystemExit('Passwords must match and be 12–256 characters.')
        with closing(connect(args.data)) as db, db:
            db.execute('INSERT INTO users(id,username,password) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET password=excluded.password, version=version+1',
                       (uuid.uuid4().hex, username, generate_password_hash(password, method='pbkdf2:sha256:600000')))
        print('Account ready. Share the username and password privately with this tester.')
    elif args.command == 'revoke':
        with closing(connect(args.data)) as db, db:
            result = db.execute('UPDATE users SET password=?, version=version+1 WHERE username=?',
                               (generate_password_hash(secrets.token_urlsafe(48), method='pbkdf2:sha256:600000'), args.username.lower()))
        print('Account revoked.' if result.rowcount else 'Account not found.')
    elif args.command == 'example':
        if args.action == 'list':
            for slug in example.available(args.data):
                pitch, meta = example.describe(args.data, slug)
                print(f"{slug}\t{pitch['pitcherName'] or 'unnamed'}\t{pitch['armAngle']}\u00b0\t{meta['nFrames']} frames")
            return
        if args.action == 'remove':
            print('Removed.' if example.remove(args.data, args.slug) else 'No such example.')
            return
        if not args.video:
            raise SystemExit('Pass the MP4 to install.')
        # The exported CSV names the clip after the example, not the staged file.
        details = dict(sessionName=args.session_name, sourceName=args.slug + '.mp4',
                       pitcherName=args.pitcher, pThrows=args.throws, park=args.park, armAngle=args.angle)
        if args.meta:
            # A pitch entry from uiTestBatch.json supplies the same fields.
            source = json.loads(Path(args.meta).read_text())
            details.update(pitcherName=source.get('pitcherName', args.pitcher),
                           pThrows=source.get('pThrows', args.throws),
                           park=source.get('homeTeam', args.park),
                           armAngle=source.get('armAngle', args.angle))
        with media_lock(args.data):
            pitch, meta = example.install(args.data, args.video, details, args.slug, args.replace)
        print(f"Installed {args.slug}: {pitch['pitcherName'] or 'unnamed'}, {pitch['armAngle']}\u00b0, {meta['nFrames']} frames.")
        print('Confirm you may redistribute this footage before serving it publicly.')
    elif args.command == 'cleanup':
        with media_lock(args.data):
            cleanup(args.data)
        print('Expired and orphaned data removed.')
    elif args.command == 'backup':
        backup(args.data, args.destination)
        print('Backup created. Copy it off the instance privately; remove backups after 7 days.')


if __name__ == '__main__':
    main()
