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
    'application.py': 'deploy/beanstalk/application.py',
    'Procfile': 'deploy/beanstalk/Procfile',
    'gunicorn.conf.py': 'deploy/beanstalk/gunicorn.conf.py',
    '.ebextensions/01_armangle.config': 'deploy/beanstalk/settings.config',
    '.platform/nginx/conf.d/elasticbeanstalk/01_uploads.conf': 'deploy/beanstalk/uploads.conf',
}
for stage in ('predeploy', 'postdeploy'):
    for hook_kind in ('hooks', 'confighooks'):
        MAPPINGS[f'.platform/{hook_kind}/{stage}/01_armangle.sh'] = f'deploy/beanstalk/{stage}.sh'


def build(out):
    out = Path(out)
    if out.exists():
        raise ValueError('Choose a new archive filename.')
    # Exclude the Lightsail service, Caddy configuration and deployment-specific docs.
    names = [name for name in HOSTED_FILES if not name.startswith(('deploy/', 'docs/')) and name != 'requirements-hosted.txt']
    names += ['docs/AWS_BEANSTALK.md','docs/S3_SETUP.md','deploy/armangle-cleanup.timer',
              'deploy/beanstalk/manage.sh','deploy/beanstalk/cleanup.service','deploy/beanstalk/web-limits.conf']
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
    hashes = {name:hashlib.sha256(blob).hexdigest() for name,blob in blobs.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, blob in blobs.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, blob)
        archive.writestr('BEANSTALK_CONTENTS.json', json.dumps(hashes, indent=2))
    return len(blobs)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    print(f'Prepared {build(args.out)} Elastic Beanstalk files in {args.out}. Nothing deployed.')
