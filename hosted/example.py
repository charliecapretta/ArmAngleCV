"""Shared practice clip, cloned into a visitor's workspace by hard link.

One prepared clip lives outside the per-visitor `videos/` tree and is never
served directly. A visitor who asks for the example gets an ordinary video and
pitch of their own whose frames are hard links to the shared copy: every other
code path -- ownership, labels, undo, export, retention, backup and restore --
treats it exactly like an upload, at no extra disk cost and without touching the
storage budget. Retention expires the clone, not the shared original.
"""
import json
import os
from pathlib import Path
import re
import shutil
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


def link(source, destination):
    """Hard link where the filesystem allows it; copy where it does not."""
    try:
        os.link(source, destination)
    except (OSError, NotImplementedError):
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o600)


def existing(pitches, slug):
    """The visitor's own live clone of this example, if retention has kept it."""
    return next((p for p in pitches if p.get('example') == slug), None)


def clone(db, root, owner, slug, guest=False):
    """Give `owner` their own pitch backed by the shared frames. Caller commits."""
    source = directory(root, slug)
    template, meta = describe(root, slug)
    video, identifier = uuid.uuid4().hex, uuid.uuid4().hex
    folder = Path(root) / 'videos' / video
    folder.mkdir(mode=0o700)
    try:
        for name in ['meta.json', 'source.mp4'] + [f'{i:04d}.jpg' for i in range(meta['nFrames'])]:
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
