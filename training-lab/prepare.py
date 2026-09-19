"""Prepare unmarked fixed frames; never copy annotations or reference angles."""
import hashlib
import argparse
import json
from pathlib import Path
import cv2
ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / 'reports' / 'independent_eval'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offset', type=int, default=0)
    args = parser.parse_args()
    pitches = json.loads((SOURCE / 'evalSet.json').read_text())['pitches'][args.offset:args.offset+5]
    if len(pitches) != 5:
        parser.error('Need five available pitches')
    frames = {r['evalId']: r['frameIndex'] for r in json.loads((SOURCE / 'annotations_pass1.json').read_text())}
    output = ROOT / 'assets'
    output.mkdir(exist_ok=True)
    items = []
    for pitch in pitches:
        frame = frames[pitch['evalId']]
        cap = cv2.VideoCapture(str(SOURCE / pitch['clipPath']))
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
            ok, image = cap.read()
        finally:
            cap.release()
        if not ok:
            raise RuntimeError(f"Could not decode {pitch['evalId']} frame {frame}")
        dest = output / f"{pitch['evalId']}.png"
        if not cv2.imwrite(str(dest), image):
            raise RuntimeError(f'Could not write {dest}')
        items.append(dict(id=pitch['evalId'], image=f'assets/{dest.name}', frameIndex=frame,
                          width=image.shape[1], height=image.shape[0], throws=pitch['pThrows'],
                          gamePk=pitch['gamePk'], playId=pitch['playId'], pitcherId=pitch['pitcherId'],
                          imageSha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
    # Keep the original frame-set ID stable so browser progress survives upgrades.
    stable_id = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:16]
    for item, pitch in zip(items, pitches):
        item.update(nFrames=pitch['nFrames'], fps=pitch['fps'])
    manifest = dict(version=1, protocol='fixed-frame-human-repeat', items=items)
    manifest['id'] = stable_id
    (output / 'session.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Prepared five unmarked frames.')

if __name__ == '__main__':
    main()
