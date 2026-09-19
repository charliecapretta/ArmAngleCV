"""Prepare only hosted runtime files; never include research data, secrets or history."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['requirements.txt', 'requirements-hosted.txt', 'labelServer.py', 'savantUtils.py',
         'features.py', 'angleInputs.py', 'analysisExport.py', 'videoUploads.py',
         'armAngleModelResolutionSafe.joblib', 'labelTool.html', 'labelTool.js', 'workspace.css',
         'LICENSE', 'MODEL_CARD.md', 'model-manifest.json', 'docs/AWS_LIGHTSAIL.md',
         'tools/checkHostedCapacity.py', 'docs/HOSTED_TEST_REVIEW.md', 'docs/S3_SETUP.md']
FILES += ['hosted/' + p for p in ['__init__.py','app.py','video.py','storage.py','example.py','manage.py','account.js','templates/login.html','templates/account.html']]
FILES += ['deploy/' + p for p in ['gunicorn.conf.py','armangle.service','armangle-cleanup.service','armangle-cleanup.timer','Caddyfile','armangle.env.example']]


def build(out):
    out = Path(out)
    if out.exists():
        raise ValueError('Choose a new archive filename.')
    blobs = {}
    for name in FILES:
        source = ROOT / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('Missing or unsafe file: ' + name)
        blobs[name] = source.read_bytes()
    manifest = {name:hashlib.sha256(blob).hexdigest() for name,blob in blobs.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, blob in blobs.items():
            archive.writestr(name,blob)
        archive.writestr('HOSTED_CONTENTS.json',json.dumps(manifest,indent=2))
    return len(blobs)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(f'Prepared {build(args.out)} runtime files in {args.out}. Nothing deployed.')
