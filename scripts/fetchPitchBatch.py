"""
Phase 0 -- build a batch of pitches with Statcast ground-truth arm_angle + clips.

Reads the local Statcast export (no remote Statcast pull needed -- the local file
already spans 412 pitchers / 93 games / 22 parks with arm_angle from -64.8 to 80.7).
Resolves play_id via the /gf game feed, scrapes the mp4, and lays out:

    pitches/<game_pk>_<play_id>/clip.mp4
    pitches/<game_pk>_<play_id>/meta.json

Sampling is stratified on arm_angle bucket x venue so the batch spans both a wide
range of arm slots and multiple park camera positions.

Usage:
    python scripts/fetchPitchBatch.py --nPitches 250
    python scripts/fetchPitchBatch.py --nPitches 5 --dryRun
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from armangle import savantUtils
DEFAULT_STATCAST_CSV = os.path.join(savantUtils.PROJECT_DIR, "data", "savant_data.csv")

# Pitches where the broadcast holds on the delivery rather than cutting to the ball.
PREFERRED_DESCRIPTIONS = {
    "called_strike",
    "swinging_strike",
    "swinging_strike_blocked",
    "ball",
    "blocked_ball",
    "foul",
    "foul_tip",
}


def loadStatcast(csvPath):
    frame = pd.read_csv(csvPath, low_memory=False)
    frame.columns = [c.strip().strip('"').lstrip("﻿") for c in frame.columns]
    return frame


def selectPitches(frame, nPitches, maxPerPitcher, nAngleBins, seed):
    """Stratify across arm_angle bins x venue, capping pitches per pitcher."""
    rng = np.random.default_rng(seed)

    # Null arm_angle removes the 2026-08-16 games entirely (not yet published).
    pool = frame[frame["arm_angle"].notna()].copy()
    pool = pool.dropna(subset=["game_pk", "at_bat_number", "pitch_number", "pitcher"])

    droppedDates = sorted(set(frame["game_date"]) - set(pool["game_date"]))
    if droppedDates:
        print(f"  dropped dates with no arm_angle: {droppedDates}")

    # Prefer clean looks at the delivery; keep the rest as fallback.
    pool["isPreferred"] = pool["description"].isin(PREFERRED_DESCRIPTIONS)

    # Park proxy: home_team. Real venue_id gets attached later from /gf.
    pool["parkKey"] = pool["home_team"].astype(str)

    edges = np.linspace(pool["arm_angle"].min(), pool["arm_angle"].max(), nAngleBins + 1)
    pool["angleBin"] = np.clip(
        np.digitize(pool["arm_angle"].values, edges[1:-1]), 0, nAngleBins - 1
    )

    # Shuffle, then rank within (angleBin, park, pitcher) so we round-robin fairly.
    pool = pool.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    pool = pool.sort_values("isPreferred", ascending=False, kind="stable")
    pool["pitcherRank"] = pool.groupby("pitcher").cumcount()
    pool = pool[pool["pitcherRank"] < maxPerPitcher]

    # Round-robin: cycle (angleBin, park) cells, taking one pitch from each pass.
    pool["cellRank"] = pool.groupby(["angleBin", "parkKey"]).cumcount()
    pool = pool.sort_values(
        ["cellRank", "angleBin", "parkKey"], kind="stable"
    ).reset_index(drop=True)

    selected = pool.head(nPitches).copy()

    print(f"  candidate pool: {len(pool)} pitches")
    print(f"  selected: {len(selected)}")
    if not selected.empty:
        print(f"  arm_angle range: {selected['arm_angle'].min():.1f} to "
              f"{selected['arm_angle'].max():.1f}")
        print(f"  distinct pitchers: {selected['pitcher'].nunique()}, "
              f"parks: {selected['parkKey'].nunique()}, "
              f"games: {selected['game_pk'].nunique()}")
        print(f"  per angle bin: {selected['angleBin'].value_counts().sort_index().to_dict()}")
    return selected


def buildMeta(row, playId, gameInfo):
    def scalar(key, cast=None):
        value = row.get(key)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        return cast(value) if cast else value

    return {
        "playId": playId,
        "gamePk": int(row["game_pk"]),
        "venueId": gameInfo.get("venue_id"),
        "gameDate": scalar("game_date"),
        "pitcherId": int(row["pitcher"]),
        "pitcherName": scalar("player_name"),
        "homeTeam": scalar("home_team"),
        "awayTeam": scalar("away_team"),
        "pitchType": scalar("pitch_type"),
        "pitchName": scalar("pitch_name"),
        "releaseSpeed": scalar("release_speed", float),
        "pThrows": scalar("p_throws"),
        "armAngle": scalar("arm_angle", float),
        "atBatNumber": int(row["at_bat_number"]),
        "pitchNumber": int(row["pitch_number"]),
        "releasePosX": scalar("release_pos_x", float),
        "releasePosZ": scalar("release_pos_z", float),
        "releaseExtension": scalar("release_extension", float),
        "description": scalar("description"),
        "inning": scalar("inning", int),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csvPath", default=DEFAULT_STATCAST_CSV)
    parser.add_argument("--nPitches", type=int, default=250)
    parser.add_argument("--maxPerPitcher", type=int, default=3)
    parser.add_argument("--nAngleBins", type=int, default=8)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--dryRun", action="store_true",
                        help="select and resolve play_ids but download nothing")
    args = parser.parse_args()

    print(f"Loading {args.csvPath}")
    frame = loadStatcast(args.csvPath)
    print(f"  {len(frame)} rows, {frame['game_pk'].nunique()} games")

    print("\nSelecting pitches")
    selected = selectPitches(
        frame, args.nPitches, args.maxPerPitcher, args.nAngleBins, args.seed
    )
    if selected.empty:
        print("Nothing selected.")
        return 1

    session = savantUtils.makeSession()
    os.makedirs(savantUtils.PITCHES_DIR, exist_ok=True)

    # One /gf call per game, cached.
    gamePks = sorted(selected["game_pk"].unique().tolist())
    print(f"\nResolving play_ids for {len(gamePks)} games")
    feedFrames = []
    gameInfoByPk = {}
    for index, gamePk in enumerate(gamePks, start=1):
        try:
            playIdFrame, gameInfo = savantUtils.playIdsForGame(int(gamePk), session)
        except Exception as error:  # noqa: BLE001
            print(f"  [{index}/{len(gamePks)}] {gamePk} FAILED: {error}")
            continue
        gameInfoByPk[int(gamePk)] = gameInfo
        if not playIdFrame.empty:
            feedFrames.append(playIdFrame)
        print(f"  [{index}/{len(gamePks)}] {gamePk}: {len(playIdFrame)} pitches "
              f"(venue {gameInfo.get('venue_id')})")

    if not feedFrames:
        print("No play_ids resolved.")
        return 1

    feed = pd.concat(feedFrames, ignore_index=True)
    merged = selected.merge(
        feed, on=["game_pk", "at_bat_number", "pitch_number"], how="left"
    )
    matchRate = merged["play_id"].notna().mean()
    print(f"\nplay_id join: {merged['play_id'].notna().sum()}/{len(merged)} "
          f"({matchRate:.1%})")
    if matchRate < 0.90:
        print("  WARNING: join rate below 90% -- expected ~98%.")

    resolved = merged[merged["play_id"].notna()].copy()
    if args.dryRun:
        print("\n--dryRun set, stopping before download.")
        return 0

    print(f"\nDownloading {len(resolved)} clips")
    manifest = []
    okCount = 0
    for index, (_, row) in enumerate(resolved.iterrows(), start=1):
        playId = row["play_id"]
        gamePk = int(row["game_pk"])
        pitchDir = os.path.join(
            savantUtils.PITCHES_DIR, savantUtils.pitchDirName(gamePk, playId)
        )
        clipPath = os.path.join(pitchDir, "clip.mp4")
        metaPath = os.path.join(pitchDir, "meta.json")

        meta = buildMeta(row, playId, gameInfoByPk.get(gamePk, {}))

        if os.path.exists(clipPath) and os.path.exists(metaPath):
            manifest.append(meta)
            okCount += 1
            print(f"  [{index}/{len(resolved)}] {playId[:8]} cached")
            continue

        try:
            clipUrl = savantUtils.resolveClipUrl(playId, session)
            if not clipUrl:
                print(f"  [{index}/{len(resolved)}] {playId[:8]} no mp4 on page")
                continue
            os.makedirs(pitchDir, exist_ok=True)
            if not savantUtils.downloadClip(clipUrl, clipPath, session):
                print(f"  [{index}/{len(resolved)}] {playId[:8]} clip too small")
                continue
        except Exception as error:  # noqa: BLE001
            print(f"  [{index}/{len(resolved)}] {playId[:8]} FAILED: {error}")
            continue

        with open(metaPath, "w") as handle:
            json.dump(meta, handle, indent=2)
        manifest.append(meta)
        okCount += 1
        sizeMb = os.path.getsize(clipPath) / 1e6
        print(f"  [{index}/{len(resolved)}] {playId[:8]} ok "
              f"({sizeMb:.1f}MB, arm {meta['armAngle']}, {meta['homeTeam']})")

    manifestPath = os.path.join(savantUtils.PITCHES_DIR, "manifest.json")
    with open(manifestPath, "w") as handle:
        json.dump(manifest, handle, indent=2)

    print(f"\nDone: {okCount}/{len(resolved)} pitches in {savantUtils.PITCHES_DIR}")
    print(f"Manifest: {manifestPath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
