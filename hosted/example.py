"""Shared practice clip, cloned into a visitor's workspace by hard link.

One prepared clip lives outside the per-visitor `videos/` tree and is never
served directly. A visitor who asks for the example gets an ordinary video and
pitch of their own whose frames are hard links to the shared copy: every other
code path -- ownership, labels, undo, export, retention, backup and restore --
treats it exactly like an upload, at no extra disk cost and without touching the
storage budget. Retention expires the clone, not the shared original.
"""
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,62}')
# Statcast and presentation fields kept for a practice clip. `armAngle` is the
# reference the review step reveals once the five points are placed.
DETAILS = ('sessionName', 'sourceName', 'pitcherName', 'pThrows', 'park', 'armAngle')


def directory(root, slug=''):
    if slug and not SLUG.fullmatch(slug):
        raise ValueError('Use lowercase letters, numbers and hyphens for an example name.')
    return Path(root) / 'examples' / slug if slug else Path(root) / 'examples'


def available(root):
    """Installed example slugs, newest-looking name last. Never trusts the disk."""
    folder = directory(root)
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.iterdir()):
        if path.is_dir() and SLUG.fullmatch(path.name) and (path / 'pitch.json').is_file() and (path / 'meta.json').is_file():
            found.append(path.name)
    return found


def describe(root, slug):
    folder = directory(root, slug)
    pitch = json.loads((folder / 'pitch.json').read_text())
    meta = json.loads((folder / 'meta.json').read_text())
    if not isinstance(pitch, dict) or not isinstance(meta, dict) or not isinstance(meta.get('nFrames'), int) or meta['nFrames'] < 1:
        raise ValueError('The installed example is incomplete. Reinstall it.')
    return pitch, meta


def frameNames(meta):
    # Legacy JPEG examples/uploads remain readable until normal seven-day expiry.
    suffixes = ['.' + meta.get('frameFormat', 'jpg')]
    if meta.get('scrubWidth'):
        suffixes.append('.scrub.webp')
    return [f'{i:04d}{suffix}' for i in range(meta['nFrames']) for suffix in suffixes]


def install(root, source, details, slug='mlb-example', replace=False):
    """Prepare a clip once, for every visitor. Offline: never called by a request."""
    from hosted.video import prepare
    folder = directory(root, slug)
    if folder.exists():
        if not replace:
            raise ValueError(f'Example {slug!r} is already installed. Pass --replace to rebuild it.')
        shutil.rmtree(folder)
    folder.mkdir(parents=True, mode=0o700)
    try:
        shutil.copyfile(source, folder / 'source.mp4')
        os.chmod(folder / 'source.mp4', 0o600)
        meta = prepare(folder)
        pitch = {key: details.get(key, '') for key in DETAILS}
        pitch['pThrows'] = 'L' if str(pitch['pThrows']).upper().startswith('L') else 'R'
        pitch['armAngle'] = None if details.get('armAngle') in (None, '') else float(details['armAngle'])
        pitch['sessionName'] = str(pitch['sessionName'] or 'MLB example')[:160]
        pitch['sourceName'] = str(pitch['sourceName'] or f'{slug}.mp4')[:240]
        pitch['pitcherName'] = str(pitch['pitcherName'])[:160]
        pitch['park'] = str(pitch['park'])[:160]
        # Open near the middle, as an unlabelled MLB clip does: the visitor still
        # has to find the release themselves, which is the point of the exercise.
        pitch['startFrame'] = meta['nFrames'] // 2
        (folder / 'pitch.json').write_text(json.dumps(pitch, indent=1))
        os.chmod(folder / 'pitch.json', 0o600)
        return pitch, meta
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def remove(root, slug):
    folder = directory(root, slug)
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


def install_bundle(root, bundle):
    """Install explicitly bundled clips atomically; caller holds media.lock."""
    from hosted.video import MAX_FRAME_BYTES
    bundle = Path(bundle)
    entries = json.loads((bundle / 'manifest.json').read_text())
    installed = []
    for entry in entries:
        slug = entry['slug']
        folder = directory(root, slug)
        source = bundle / f'{slug}.mp4'
        if source.is_symlink() or not source.is_file():
            raise ValueError(f'Missing bundled example: {slug}')
        with source.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if digest != entry['sha256']:
            raise ValueError(f'Bundled example checksum mismatch: {slug}')
        identity = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        marker = folder / '.bundle-sha256'
        if folder.exists():
            if not marker.is_file() or marker.read_text() != identity:
                raise ValueError(f'Example {slug} already exists with different content.')
            _, meta = describe(root, slug)
            required = ['source.mp4'] + frameNames(meta)
            if any(not (folder / name).is_file() for name in required):
                raise ValueError(f'Installed example is incomplete: {slug}')
            if meta.get('frameFormat') == 'webp' and meta.get('scrubWidth'):
                continue
            # Upgrade only the shared template. Existing visitors keep immutable
            # JPEG hardlinks and expire normally; new visitors receive WebP.
        if shutil.disk_usage(root).free < source.stat().st_size + MAX_FRAME_BYTES + 1024**3:
            raise ValueError('Not enough disk space to install the bundled examples.')
        folder.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Stage on the data filesystem so publication is a single atomic rename.
        # A failed deployment can retry without exposing a partially decoded clip.
        with tempfile.TemporaryDirectory(prefix='.example-install-', dir=root) as temporary:
            install(temporary, source, entry['details'], slug)
            staged = directory(temporary, slug)
            (staged / '.bundle-sha256').write_text(identity)
            previous = Path(temporary) / 'previous'
            if folder.exists():
                folder.rename(previous)
            try:
                staged.rename(folder)
            except Exception:
                if previous.exists():
                    previous.rename(folder)
                raise
        installed.append(slug)
    return installed


def link(source, destination):
    """Hard link where the filesystem allows it; copy where it does not."""
    try:
        os.link(source, destination)
    except (OSError, NotImplementedError):
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o600)


def clone(db, root, owner, slug, guest=False):
    """Give `owner` their own pitch backed by the shared frames. Caller commits."""
    source = directory(root, slug)
    template, meta = describe(root, slug)
    video, identifier = uuid.uuid4().hex, uuid.uuid4().hex
    folder = Path(root) / 'videos' / video
    folder.mkdir(mode=0o700)
    try:
        for name in ['meta.json', 'source.mp4'] + frameNames(meta):
            path = source / name
            if not path.is_file():
                raise ValueError('The installed example is incomplete. Reinstall it.')
            link(path, folder / name)
        pitch = dict(gamePk='local', playId=identifier, dirName='local_' + identifier,
                     sessionId=video, isLocal=True, uiTestBatch=True, example=slug,
                     startFrame=template['startFrame'], armAngle=template['armAngle'],
                     **{key: template[key] for key in ('sessionName', 'sourceName', 'pitcherName', 'pThrows', 'park')},
                     **{key: meta[key] for key in ('nFrames', 'fps', 'imgW', 'imgH')})
        if guest:
            db.execute("INSERT OR IGNORE INTO users(id,username,password) VALUES (?,?,'!')", (owner, owner))
        # Shared inodes: charge no bytes against this visitor's storage budget.
        db.execute('INSERT INTO videos VALUES (?,?,?,?,0)', (video, owner, json.dumps(meta), time.time()))
        db.execute('INSERT INTO pitches VALUES (?,?,?)', (identifier, video, json.dumps(pitch)))
        return pitch
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
