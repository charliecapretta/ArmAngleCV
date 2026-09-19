"""
Download the independent evaluation clips.

Isolated from the training pipeline on purpose: clips land in
reports/independent_eval/clips/, and nothing here touches pitches/, labels.csv or
the labeler manifest -- the user may be labeling in Chrome while this runs.

The meta.json written per clip carries NO armAngle. The annotator reads these files.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import savantUtils as s  # noqa: E402
from labelServer import probeClip  # noqa: E402

OUT = Path(__file__).resolve().parent
CLIPS = OUT / 'clips'
TARGET_USABLE = 30


def usableClip(path):
    if not Path(path).is_file():
        return False
    info = probeClip(str(path))
    return bool(info and info['nFrames'] > 0 and info['imgW'] > 0 and info['imgH'] > 0)


def main():
    manifest = json.loads((OUT / 'manifest_blind.json').read_text())['pitches']
    CLIPS.mkdir(parents=True, exist_ok=True)
    session = s.makeSession()

    # One /gf call per game, cached by savantUtils.
    playIdByKey, venueByGame = {}, {}
    for gamePk in sorted({p['gamePk'] for p in manifest}):
        try:
            frame, info = s.playIdsForGame(int(gamePk), session)
        except Exception as error:  # noqa: BLE001
            print(f'  game {gamePk} feed FAILED: {error}')
            continue
        venueByGame[gamePk] = info.get('venue_id')
        for row in frame.to_dict('records'):
            playIdByKey[(gamePk, row['at_bat_number'], row['pitch_number'])] = row['play_id']
    print(f'resolved play_ids for {len(venueByGame)} games')

    fetched, failures = [], []
    for pitch in manifest:
        if len(fetched) >= TARGET_USABLE:
            break
        key = (pitch['gamePk'], pitch['atBatNumber'], pitch['pitchNumber'])
        playId = playIdByKey.get(key)
        if not playId:
            failures.append((pitch['evalId'], 'no play_id in feed'))
            continue

        directory = CLIPS / s.pitchDirName(pitch['gamePk'], playId)
        directory.mkdir(parents=True, exist_ok=True)
        clip = directory / 'clip.mp4'

        if not usableClip(clip):
            try:
                url = s.resolveClipUrl(playId, session)
                if not url:
                    raise ValueError('no downloadable source on the video page')
                fd, temporary = tempfile.mkstemp(dir=directory, prefix='.dl-', suffix='.mp4')
                os.close(fd)
                if not s.downloadClip(url, temporary, session) or not usableClip(temporary):
                    os.unlink(temporary)
                    raise ValueError('download failed or produced an unreadable clip')
                os.replace(temporary, clip)
            except Exception as error:  # noqa: BLE001
                failures.append((pitch['evalId'], str(error)))
                print(f'  {pitch["evalId"]} FAILED: {error}')
                continue

        info = probeClip(str(clip))
        meta = dict(pitch)
        meta.update({'playId': playId, 'venueId': venueByGame.get(pitch['gamePk']),
                     'clipPath': str(clip.relative_to(OUT)),
                     'nFrames': info['nFrames'], 'imgW': info['imgW'],
                     'imgH': info['imgH'], 'fps': info.get('fps')})
        (directory / 'meta.json').write_text(json.dumps(meta, indent=2))
        fetched.append(meta)
        print(f'  {pitch["evalId"]} ok  {pitch["pitcherName"]:<24} '
              f'{info["imgW"]}x{info["imgH"]} {info["nFrames"]}f')

    (OUT / 'evalSet.json').write_text(json.dumps(
        {'pitches': fetched, 'failures': failures}, indent=2))
    pitchers = {p['pitcherId'] for p in fetched}
    print(f'\nusable clips: {len(fetched)}   distinct unseen pitchers: {len(pitchers)}')
    if failures:
        print(f'failures: {len(failures)}')


if __name__ == '__main__':
    main()
