"""
Fetch 100 new training pitches, chosen to fill the coverage gaps rather than to
resample what the training set already has.

Three things make the sampling "smart" here, all of them measured rather than assumed:

1. **New pitchers only, and the eval set stays held out.** Excludes every pitcher in
   labels.csv (103) AND the 21 in reports/independent_eval (the blinded batch from
   2026-09-09). Reusing an eval pitcher for training would quietly destroy that set.

2. **One pitch per pitcher.** HANDOFF measures between-pitcher bias spread at 6.7 deg
   against 2.0 deg within-pitcher scatter, and GroupKFold splits on pitcher_id -- so a
   new pitcher is worth far more than a second pitch from a pitcher already present.

3. **Allocated against the gap, not against the pool.** labels.csv is 52% below +10 deg
   -- a band no unlabeled pitcher can supply, since every MLB pitcher under 0 deg is
   already labeled -- and thinnest at +30..+50, which is where the pool is deepest. The
   band quotas below take everything available in the scarce high band and taper where
   training is already covered.

Writes pitches/<game_pk>_<play_id>/{clip.mp4, meta.json}, which is what labelServer
scans. Nothing existing is touched: manifest.json, labels.csv and every current pitch
directory are left exactly as they are.

    ./.venv/bin/python fetchTrainingBatch.py --dryRun
    ./.venv/bin/python fetchTrainingBatch.py
"""
import argparse
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import savantUtils
from fetchPitchBatch import buildMeta
from labelServer import probeClip

ROOT = Path(__file__).resolve().parent
EVAL_SET = ROOT / 'reports' / 'independent_eval' / 'evalSet.json'
SEED = 20260910

# (low, high] -> how many pitchers to take. Sums to 100. Set from the coverage table:
# trained pitches per band vs pitchers available among unlabeled arms.
BAND_QUOTA = [
    ((10, 20), 8),    # pool has only 14; training already has 20 pitches here
    ((20, 30), 20),
    ((30, 40), 25),   # training thinnest (6.6%), pool deepest (93 pitchers)
    ((40, 50), 25),
    ((50, 60), 20),   # training 10 pitches, pool 32 pitchers
    ((60, 70), 2),    # every unlabeled pitcher this high in the data
]
RESERVE = 2.0  # queue this multiple of each quota; clips fail to resolve sometimes


def excludedPitchers():
    labeled = set(pd.read_csv(ROOT / 'labels.csv')['pitcher_id'].dropna().astype(int))
    held = set()
    if EVAL_SET.exists():
        held = {p['pitcherId'] for p in json.loads(EVAL_SET.read_text())['pitches']}
    return labeled, held


def choose(frame, labeled, held):
    """Pitcher per band, then one pitch per pitcher, spreading across games."""
    rng = np.random.default_rng(SEED)
    pool = frame[frame['arm_angle'].notna()].copy()
    pool['pitcher'] = pool['pitcher'].astype(int)
    pool = pool[~pool['pitcher'].isin(labeled | held)]

    byPitcher = pool.groupby('pitcher')['arm_angle'].mean()
    queued, gameUse = [], {}

    for (low, high), quota in BAND_QUOTA:
        inBand = byPitcher[(byPitcher > low) & (byPitcher <= high)].index.values
        rng.shuffle(inBand)
        want = min(int(quota * RESERVE), len(inBand))
        for pitcher in inBand[:want]:
            owned = pool[pool['pitcher'] == pitcher]
            # Prefer a game this batch has drawn from least: park variety is free here,
            # and per-park bias was measured at 1.22 deg SD, so spreading costs nothing.
            games = sorted(owned['game_pk'].unique(), key=lambda g: (gameUse.get(g, 0), rng.random()))
            game = games[0]
            gameUse[game] = gameUse.get(game, 0) + 1
            candidates = owned[owned['game_pk'] == game]
            row = candidates.iloc[int(rng.integers(len(candidates)))]
            queued.append({'row': row, 'band': (low, high), 'quota': quota})
    return queued


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dryRun', action='store_true')
    parser.add_argument('--target', type=int, default=100)
    args = parser.parse_args()

    frame = pd.read_csv(ROOT / 'savant_data (10).csv', low_memory=False)
    labeled, held = excludedPitchers()
    print(f'excluding {len(labeled)} labeled + {len(held)} eval pitchers')

    queued = choose(frame, labeled, held)
    print(f'queued {len(queued)} candidates across {len({q["row"]["pitcher"] for q in queued})} pitchers')
    for (low, high), quota in BAND_QUOTA:
        got = sum(1 for q in queued if q['band'] == (low, high))
        print(f'  ({low:>2},{high:>2}]  quota {quota:>2}   candidates {got:>2}')
    if args.dryRun:
        return 0

    session = savantUtils.makeSession()
    os.makedirs(savantUtils.PITCHES_DIR, exist_ok=True)

    # One /gf per game, cached by savantUtils.
    playIds, gameInfo = {}, {}
    for gamePk in sorted({int(q['row']['game_pk']) for q in queued}):
        try:
            table, info = savantUtils.playIdsForGame(gamePk, session)
        except Exception as error:  # noqa: BLE001
            print(f'  game {gamePk} feed FAILED: {error}')
            continue
        gameInfo[gamePk] = info
        for record in table.to_dict('records'):
            playIds[(gamePk, record['at_bat_number'], record['pitch_number'])] = record['play_id']
    print(f'resolved play_ids for {len(gameInfo)} games\n')

    filled = {band: 0 for band, _ in BAND_QUOTA}
    quotas = dict(BAND_QUOTA)
    written, failures = [], []

    for item in queued:
        row, band = item['row'], item['band']
        if filled[band] >= quotas[band] or sum(filled.values()) >= args.target:
            continue
        gamePk = int(row['game_pk'])
        playId = playIds.get((gamePk, int(row['at_bat_number']), int(row['pitch_number'])))
        if not playId:
            failures.append((int(row['pitcher']), 'no play_id in feed'))
            continue

        name = savantUtils.pitchDirName(gamePk, playId)
        directory = Path(savantUtils.PITCHES_DIR) / name
        clip = directory / 'clip.mp4'
        if directory.exists() and clip.exists():
            failures.append((int(row['pitcher']), 'already present'))
            continue

        try:
            directory.mkdir(parents=True, exist_ok=True)
            url = savantUtils.resolveClipUrl(playId, session)
            if not url:
                raise ValueError('no downloadable source on the video page')
            handle, temporary = tempfile.mkstemp(dir=directory, prefix='.dl-', suffix='.mp4')
            os.close(handle)
            if not savantUtils.downloadClip(url, temporary, session):
                os.unlink(temporary)
                raise ValueError('download failed')
            info = probeClip(temporary)
            if not info or info['nFrames'] < 1:
                os.unlink(temporary)
                raise ValueError('clip unreadable')
            os.replace(temporary, clip)
        except Exception as error:  # noqa: BLE001
            failures.append((int(row['pitcher']), str(error)))
            if directory.exists() and not clip.exists():
                for stray in directory.glob('.dl-*'):
                    stray.unlink()
                directory.rmdir()
            continue

        meta = buildMeta(row, playId, gameInfo.get(gamePk, {}))
        (directory / 'meta.json').write_text(json.dumps(meta, indent=2))
        filled[band] += 1
        written.append(meta)
        print(f'  [{sum(filled.values()):>3}/{args.target}] {meta["pitcherName"]:<24} '
              f'arm {meta["armAngle"]:>6.1f}  {meta["homeTeam"]}  {info["imgW"]}x{info["imgH"]}')

    (ROOT / 'trainingBatch20260910.json').write_text(json.dumps(written, indent=2))
    print(f'\nwrote {len(written)} pitches, {len({m["pitcherId"] for m in written})} pitchers')
    for (low, high), quota in BAND_QUOTA:
        print(f'  ({low:>2},{high:>2}]  {filled[(low, high)]:>2} / {quota}')
    if failures:
        print(f'failures: {len(failures)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
