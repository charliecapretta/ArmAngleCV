"""
Select an independent evaluation batch: MLB pitches from pitchers the frozen model
has never seen, with the Statcast reference angle SEALED away from the annotator.

Blinding is the point of this file. Stratification needs arm_angle, so the script
reads it -- but it writes two artifacts:

    manifest_blind.json   what the annotator works from. No arm_angle, no bucket
                          label, order shuffled so position leaks nothing.
    sealed/reference.json arm_angle per pitch. Not opened until labeling is done.

Run once. Re-running with the same seed reproduces the same batch.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SEED = 20260909

# Oversample: some play_ids will not resolve to a downloadable clip. We need 30
# usable pitches from >=15 pitchers, so queue well past that and stop when full.
N_PITCHERS = 26
PER_PITCHER = 2
N_STRATA = 5


def main():
    pitches = pd.read_csv(ROOT / 'savant_data (10).csv', low_memory=False)
    trained = set(pd.read_csv(ROOT / 'labels.csv')['pitcher_id'].dropna().astype(int))

    pool = pitches[pitches['arm_angle'].notna()].copy()
    pool['pitcher'] = pool['pitcher'].astype(int)
    pool = pool[~pool['pitcher'].isin(trained)]
    print(f'unseen-pitcher pool: {len(pool)} pitches, {pool["pitcher"].nunique()} pitchers')

    rng = np.random.default_rng(SEED)

    # Stratify on the PITCHER's mean angle, not the pitch's, so a pitcher lands in
    # exactly one stratum and the batch spans the arm-slot range evenly.
    byPitcher = pool.groupby('pitcher')['arm_angle'].mean().sort_values()
    strata = np.array_split(byPitcher.index.values, N_STRATA)
    perStratum = N_PITCHERS // N_STRATA
    chosen = []
    for group in strata:
        take = min(perStratum, len(group))
        chosen.extend(rng.choice(group, size=take, replace=False).tolist())
    # Top up to N_PITCHERS from whoever is left, so integer division loses nobody.
    remaining = [p for p in byPitcher.index.values if p not in set(chosen)]
    while len(chosen) < N_PITCHERS and remaining:
        pick = rng.choice(len(remaining))
        chosen.append(remaining.pop(pick))

    rows = []
    for pitcher in chosen:
        owned = pool[pool['pitcher'] == pitcher]
        # Prefer distinct games per pitcher: park/broadcast variety inside a pitcher.
        games = owned['game_pk'].unique()
        rng.shuffle(games)
        for gamePk in games[:PER_PITCHER]:
            candidates = owned[owned['game_pk'] == gamePk]
            rows.append(candidates.iloc[rng.integers(len(candidates))])

    batch = pd.DataFrame(rows).reset_index(drop=True)
    order = rng.permutation(len(batch))
    batch = batch.iloc[order].reset_index(drop=True)

    blind, sealed = [], []
    for i, row in batch.iterrows():
        evalId = f'eval{i + 1:02d}'
        blind.append({
            'evalId': evalId,
            'gamePk': int(row['game_pk']),
            'gameDate': str(row['game_date']),
            'pitcherId': int(row['pitcher']),
            'pitcherName': str(row['player_name']),
            'pThrows': str(row['p_throws']),
            'pitchType': str(row['pitch_type']),
            'releaseSpeed': float(row['release_speed']),
            'atBatNumber': int(row['at_bat_number']),
            'pitchNumber': int(row['pitch_number']),
            'inning': int(row['inning']),
            'homeTeam': str(row['home_team']),
            'awayTeam': str(row['away_team']),
        })
        sealed.append({'evalId': evalId,
                       'gamePk': int(row['game_pk']),
                       'pitcherId': int(row['pitcher']),
                       'atBatNumber': int(row['at_bat_number']),
                       'pitchNumber': int(row['pitch_number']),
                       'armAngle': float(row['arm_angle'])})

    (OUT / 'manifest_blind.json').write_text(json.dumps(
        {'seed': SEED, 'note': 'No reference angles. Sealed in sealed/reference.json.',
         'pitches': blind}, indent=2))
    (OUT / 'sealed' / 'reference.json').write_text(json.dumps(
        {'seed': SEED, 'pitches': sealed}, indent=2))

    print(f'queued {len(blind)} candidate pitches from {batch["pitcher"].nunique()} unseen pitchers')
    print('reference angles sealed -- do not read until labeling is complete')


if __name__ == '__main__':
    main()
