"""Build a reviewed public file set without copying personal files or Git history."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def releaseFiles(root):
    names = [line.strip() for line in (root/'tools'/'release-files.txt').read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate path in release-files.txt')
    files = {}
    for name in names:
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError(f'Unsafe release path: {name}')
        if set(relative.parts) & {'.git', '.idea', '.venv', '__pycache__', 'pitches', 'cache'}:
            raise ValueError(f'Private or generated directory in release list: {name}')
        source = root/name
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Missing or unsafe source file: {name}')
        content = source.read_bytes()
        if len(content) > 25_000_000:
            raise ValueError(f'Large artifact requires an explicit distribution decision: {name}')
        patterns = [rb'/Users/[A-Za-z0-9_.-]+', rb'/home/[A-Za-z0-9_.-]+',
                    rb'[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+',
                    rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                    rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b']
        if any(re.search(pattern, content) for pattern in patterns):
            raise ValueError(f'Personal path or credential pattern in public file: {name}')
        files[name] = content
    return files


def build(root, out):
    root, out = Path(root).resolve(), Path(out).resolve()
    archive = out.with_name(out.name+'.zip')
    if out.exists() or archive.exists():
        raise ValueError('Choose a new output folder; existing release files are never overwritten')
    files = releaseFiles(root)  # Validate everything before creating any output.
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    files['RELEASE_CONTENTS.json'] = (json.dumps({'files_sha256': hashes}, indent=2)+'\n').encode()
    out.mkdir(parents=True)
    for name, content in files.items():
        target = out/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 9, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, content)
    return len(files), archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'release/public')
    args = parser.parse_args()
    try:
        count, archive = build(ROOT, args.out)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f'Prepared {count} public files in {args.out.resolve()}')
    print(f'Archive: {archive}')
    print('No Git history was copied or rewritten. Nothing was published.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
