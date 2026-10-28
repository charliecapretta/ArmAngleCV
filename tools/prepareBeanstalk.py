"""Build a Python 3.12 Elastic Beanstalk ZIP with no enclosing directory."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepareHosted import FILES as HOSTED_FILES

MAPPINGS = {
    '.platform/nginx/conf.d/00_rateLimits.conf': 'deploy/beanstalk/rateLimits.conf',
    'application.py': 'deploy/beanstalk/application.py',
    'Procfile': 'deploy/beanstalk/Procfile',
    'gunicorn.conf.py': 'deploy/beanstalk/gunicorn.conf.py',
    '.ebextensions/01_armangle.config': 'deploy/beanstalk/settings.config',
    '.platform/nginx/conf.d/elasticbeanstalk/01_uploads.conf': 'deploy/beanstalk/uploads.conf',
}
for stage in ('predeploy', 'postdeploy'):
    for hook_kind in ('hooks', 'confighooks'):
        MAPPINGS[f'.platform/{hook_kind}/{stage}/01_armangle.sh'] = f'deploy/beanstalk/{stage}.sh'


def build(out, include_examples=False, example_count=None, ffprobe=None, ffprobe_sha256=None):
    out = Path(out)
    if out.exists():
        raise ValueError('Choose a new archive filename.')
    if example_count is not None and not include_examples:
        raise ValueError('--example-count requires --include-examples.')
    # Exclude the Lightsail service, Caddy configuration and deployment-specific docs.
    names = [name for name in HOSTED_FILES if not name.startswith(('deploy/', 'docs/')) and name != 'requirements-hosted.txt']
    names += ['docs/AWS_BEANSTALK.md','docs/S3_SETUP.md','deploy/armangle-cleanup.timer',
              'deploy/beanstalk/manage.sh','deploy/beanstalk/cleanup.service','deploy/beanstalk/web-limits.conf',
              'deploy/beanstalk/backup.service','deploy/beanstalk/backup.timer','deploy/beanstalk/backup-prune.service','deploy/beanstalk/backup-prune.timer','docs/PUBLIC_RELEASE.md','docs/PRIVACY_STORAGE.md']
    mappings = {name:name for name in names}
    mappings.update(MAPPINGS)
    blobs = {}
    for destination, source in mappings.items():
        path = ROOT / source
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or unsafe source: ' + source)
        blobs[destination] = path.read_bytes()
    # EB installs only root requirements.txt. Headless OpenCV avoids X11/libGL packages.
    base = (ROOT / 'requirements.txt').read_text().replace('opencv-python==', 'opencv-python-headless==')
    extra = '\n'.join(line for line in (ROOT/'requirements-hosted.txt').read_text().splitlines() if not line.startswith('-r '))
    blobs['requirements.txt'] = (base+'\n'+extra+'\n').encode()
    if ffprobe is not None:
        # AL2023 ships no ffprobe. Bundle a static Linux build the operator vetted;
        # the predeploy hook re-checks this digest before installing it.
        ffprobe = Path(ffprobe)
        if ffprobe.is_symlink() or not ffprobe.is_file():
            raise ValueError('Missing or unsafe ffprobe binary: ' + str(ffprobe))
        data = ffprobe.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not ffprobe_sha256 or digest != ffprobe_sha256.strip().lower():
            raise ValueError(f'ffprobe SHA-256 {digest} does not match the published checksum.')
        if data[:4] != b'\x7fELF':
            raise ValueError('ffprobe must be a Linux (ELF) binary, not a macOS one.')
        blobs['deploy/bin/ffprobe'] = data
        blobs['deploy/bin/ffprobe.sha256'] = (digest + '  deploy/bin/ffprobe\n').encode()
    if include_examples:
        entries = []
        # Only this explicit MLB practice batch is authorized for inclusion.
        # Do not traverse uploads, research folders, or local user registrations.
        pitches = json.loads((ROOT / 'uiTestBatch.json').read_text())['pitches']
        if example_count is not None:
            if not 1 <= example_count <= len(pitches):
                raise ValueError(f'Choose between 1 and {len(pitches)} examples.')
            # Sample across arm angles instead of taking only the low-angle clips.
            pitches = sorted(pitches, key=lambda pitch: pitch['armAngle'])
            indices = ([len(pitches) // 2] if example_count == 1 else
                       [round(i * (len(pitches) - 1) / (example_count - 1))
                        for i in range(example_count)])
            pitches = [pitches[i] for i in indices]
        for pitch in pitches:
            from hosted.example import SLUG
            slug = f"mlb-{pitch['gamePk']}-{pitch['playId']}"
            if not SLUG.fullmatch(slug):
                raise ValueError('Invalid MLB example identifier.')
            source = ROOT / 'pitches' / f"{pitch['gamePk']}_{pitch['playId']}" / 'clip.mp4'
            if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to((ROOT / 'pitches').resolve()):
                raise ValueError(f'Missing or unsafe MLB example: {slug}')
            data = source.read_bytes()
            blobs[f'bundled-examples/{slug}.mp4'] = data
            entries.append(dict(slug=slug, sha256=hashlib.sha256(data).hexdigest(), details=dict(
                sessionName=f"MLB example · {pitch['pitcherName']}", sourceName=f'{slug}.mp4',
                pitcherName=pitch['pitcherName'], pThrows=pitch['pThrows'],
                park=pitch['homeTeam'], armAngle=pitch['armAngle'])))
        blobs['bundled-examples/manifest.json'] = json.dumps(entries, indent=2).encode()
    hashes = {name:hashlib.sha256(blob).hexdigest() for name,blob in blobs.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, blob in blobs.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith('.sh') or name == 'deploy/bin/ffprobe' else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, blob)
        archive.writestr('BEANSTALK_CONTENTS.json', json.dumps(hashes, indent=2))
    return len(blobs)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--include-examples', action='store_true', help='Bundle the 12 local MLB UI practice clips')
    parser.add_argument('--example-count', type=int, help='Bundle this many clips sampled across arm angles (default: all)')
    parser.add_argument('--ffprobe', type=Path, required=True, help='Static Linux ffprobe binary matching the instance architecture')
    parser.add_argument('--ffprobe-sha256', required=True, help="The binary's SHA-256 as published by its source")
    args=parser.parse_args()
    count = build(args.out, args.include_examples, args.example_count, args.ffprobe, args.ffprobe_sha256)
    print(f'Prepared {count} Elastic Beanstalk files in {args.out}. Nothing deployed.')
