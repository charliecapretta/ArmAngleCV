"""Render verification overlays for every proposed annotation in a pass file."""
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NAMES = ['shoulder', 'off_shoulder', 'elbow', 'wrist', 'ball']


def main():
    passFile = HERE / sys.argv[1]
    only = set(sys.argv[2:])
    pitches = {p['evalId']: p for p in json.loads((HERE / 'evalSet.json').read_text())['pitches']}
    for record in json.loads(passFile.read_text()):
        if only and record['evalId'] not in only:
            continue
        clip = HERE / pitches[record['evalId']]['clipPath']
        args = [f'{record[f"{n}_x"]},{record[f"{n}_y"]}' for n in NAMES]
        subprocess.run(['./.venv/bin/python', str(HERE / 'verify.py'), str(clip),
                        str(record['frameIndex']), *args, record['evalId']],
                       cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
