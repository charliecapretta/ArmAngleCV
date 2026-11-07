"""Restore the fixed MLB example clips without changing the manifest or labels."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

import savantUtils as s
from labelServer import probeClip

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / 'uiTestBatch.json'


def manifestPitches(path):
    records = json.loads(Path(path).read_text())['pitches']
    if not isinstance(records, list) or not records:
        raise ValueError('The manifest must contain a non-empty pitches list')
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Every manifest pitch must be an object')
        game, play = str(record.get('gamePk', '')), str(record.get('playId', ''))
        if not game.isdigit() or not re.fullmatch(r'[a-zA-Z0-9-]+', play):
            raise ValueError('Invalid pitch identifier in manifest')
        key = (game, play)
        if key in seen:
            raise ValueError('Duplicate pitch in manifest')
        seen.add(key)
    return records


def usableClip(path):
    if not Path(path).is_file():
        return False
    info = probeClip(str(path))
    return bool(info and info['nFrames'] > 0 and info['imgW'] > 0 and info['imgH'] > 0)


def restorePitches(records, pitchesDir, session=None):
    """Keep healthy clips, restore missing metadata, and replace only verified downloads."""
    failures = []
    ownsSession = False
    try:
        for record in records:
            name = s.pitchDirName(record['gamePk'], record['playId'])
            directory = Path(pitchesDir) / name
            clip, metaPath = directory / 'clip.mp4', directory / 'meta.json'
            temporary = None
            try:
                directory.mkdir(parents=True, exist_ok=True)
                if not usableClip(clip):
                    if session is None:
                        session = s.makeSession()
                        ownsSession = True
                    url = s.resolveClipUrl(record['playId'], session)
                    if not url:
                        raise ValueError('The source page has no downloadable clip')
                    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.download-', suffix='.mp4')
                    os.close(fd)
                    if not s.downloadClip(url, temporary, session) or not usableClip(temporary):
                        raise ValueError('Downloaded clip could not be decoded')
                    os.replace(temporary, clip)
                if not metaPath.exists():
                    # Preserve existing metadata and annotations. Restore a missing
                    # metadata file from the same committed source record.
                    metaPath.write_text(json.dumps(record, indent=2) + '\n')
                print(f'Ready: {name}', flush=True)
            except (OSError, ValueError, RuntimeError, ModuleNotFoundError) as error:
                failures.append(name)
                print(f'Unavailable: {name}: {error}', flush=True)
                if isinstance(error, ModuleNotFoundError):
                    print('Install download support: python -m pip install -r requirements-fetch.txt')
            finally:
                if temporary:
                    Path(temporary).unlink(missing_ok=True)
                    Path(temporary + '.part').unlink(missing_ok=True)
    finally:
        if ownsSession:
            session.close()
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=BATCH)
    parser.add_argument('--out-dir', type=Path, default=Path(s.PITCHES_DIR))
    parser.add_argument('--limit', type=int, help='Restore only the first N examples')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    try:
        records = manifestPitches(args.manifest)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(f'Could not read manifest: {error}')
    records = records[:args.limit]
    failures = restorePitches(records, args.out_dir)
    print(f'{len(records)-len(failures)}/{len(records)} examples ready. Manifest and labels unchanged.')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
