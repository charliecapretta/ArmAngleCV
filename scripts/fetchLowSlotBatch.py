"""
Close the sidearm / submarine coverage gap.

The first 100 pitches came from one week of Statcast and left only 5 of 99
labels below -10 deg (2 below -30). That is where the identity relationship
between pixel angle and Statcast's arm_angle is least tested, and where a
keypoint model would be weakest.

This pulls low-slot pitchers by name off the season arm-angle leaderboard, then
their whole season from Statcast search, and samples across as many distinct
parks and games as possible.

A hard limit worth knowing before running it: MLB barely has these pitchers.
On the 2026 leaderboard (min 50 pitches) only 3 of 720 sit below -10 deg and 17
below +10. So the low end cannot be made into a continuum -- it is a few tight
clusters. Sampling many parks per pitcher is what makes those clusters useful:
it tests whether the relationship holds at extreme angles across cameras,
rather than testing one pitcher in one stadium.

Usage:
    python scripts/fetchLowSlotBatch.py --dryRun
    python scripts/fetchLowSlotBatch.py --maxArmAngle 10 --perPitcher 6
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from armangle import savantUtils
from fetchPitchBatch import PREFERRED_DESCRIPTIONS, buildMeta


def selectForPitcher(frame, perPitcher, seed):
    """
    Up to `perPitcher` pitches for one pitcher, spread over distinct parks/games.

    Park variety is the point: these pitchers each sit at one arm angle, so the
    only useful variation left is the camera.
    """
    pool = frame[frame["arm_angle"].notna()].copy()
    if pool.empty:
        return pool
    pool["isPreferred"] = pool["description"].isin(PREFERRED_DESCRIPTIONS)
    pool = pool.sample(frac=1.0, random_state=seed)
    pool = pool.sort_values("isPreferred", ascending=False, kind="stable")
    # One pass per park first, then a second pitch from each, and so on.
    pool["parkRank"] = pool.groupby("home_team").cumcount()
    pool["gameRank"] = pool.groupby("game_pk").cumcount()
    pool = pool[pool["gameRank"] == 0]
    pool = pool.sort_values(["parkRank"], kind="stable")
    return pool.head(perPitcher)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--dateFrom", default="2026-03-01")
    parser.add_argument("--dateTo", default="2026-08-21")
    parser.add_argument("--maxArmAngle", type=float, default=10.0,
                        help="leaderboard ball_angle ceiling for target pitchers")
    parser.add_argument("--perPitcher", type=int, default=4,
                        help="pitches per pitcher in the merely-low band")
    parser.add_argument("--perPitcherLow", type=int, default=10,
                        help="pitches per pitcher below --lowThreshold, where "
                             "coverage is thinnest and pitchers are scarcest")
    parser.add_argument("--lowThreshold", type=float, default=-10.0)
    parser.add_argument("--minPitches", type=int, default=50,
                        help="leaderboard minimum, to exclude tiny samples")
    parser.add_argument("--seed", type=int, default=23)
    parser.add_argument("--dryRun", action="store_true")
    args = parser.parse_args()

    session = savantUtils.makeSession()

    print(f"Arm-angle leaderboard, {args.season}")
    board = savantUtils.fetchArmAngleLeaderboard(session, args.season, args.minPitches)
    targets = board[board["ball_angle"] <= args.maxArmAngle].sort_values("ball_angle")
    print(f"  {len(board)} pitchers, {len(targets)} at or below {args.maxArmAngle:+.0f} deg")
    for _, row in targets.iterrows():
        print(f"    {row['ball_angle']:+6.1f}  {row['pitcher_name']:<22} "
              f"({row['pitch_hand']}, {int(row['n_pitches'])} pitches)")
    if targets.empty:
        return 1

    print(f"\nStatcast {args.dateFrom} -> {args.dateTo} for {len(targets)} pitchers")
    statcast = savantUtils.fetchStatcastForPitchers(
        session, targets["pitcher"].tolist(), args.season, args.dateFrom, args.dateTo
    )
    print(f"  {len(statcast)} pitches, {statcast['arm_angle'].notna().sum()} with arm_angle")

    # Only 3 pitchers in MLB sit below -10 deg, so take proportionally more from
    # each of them -- the shortage is pitchers, not pitches.
    angleById = dict(zip(targets["pitcher"], targets["ball_angle"]))

    selected = []
    for pitcherId, group in statcast.groupby("pitcher"):
        quota = (args.perPitcherLow
                 if angleById.get(pitcherId, 99) <= args.lowThreshold
                 else args.perPitcher)
        picked = selectForPitcher(group, quota, args.seed)
        if picked.empty:
            continue
        selected.append(picked)
        name = picked["player_name"].iloc[0]
        print(f"  {name:<22} {len(picked)} pitches  "
              f"arm {picked['arm_angle'].mean():+6.1f}  "
              f"parks {picked['home_team'].nunique()}")
    if not selected:
        print("Nothing selected.")
        return 1

    selected = pd.concat(selected, ignore_index=True)
    print(f"\nselected {len(selected)} pitches, "
          f"{selected['pitcher'].nunique()} pitchers, "
          f"{selected['home_team'].nunique()} parks, "
          f"{selected['game_pk'].nunique()} games")
    print("  arm_angle bins:")
    for lo, hi in [(-70, -30), (-30, -10), (-10, 0), (0, 10)]:
        n = ((selected["arm_angle"] >= lo) & (selected["arm_angle"] < hi)).sum()
        print(f"    {lo:>4} to {hi:>3}: {n}")

    # Skip anything already downloaded from the first batch.
    existing = set(os.listdir(savantUtils.PITCHES_DIR)) \
        if os.path.isdir(savantUtils.PITCHES_DIR) else set()

    gamePks = sorted(selected["game_pk"].unique().tolist())
    print(f"\nResolving play_ids for {len(gamePks)} games")
    feeds, gameInfoByPk = [], {}
    for index, gamePk in enumerate(gamePks, start=1):
        try:
            playIdFrame, gameInfo = savantUtils.playIdsForGame(int(gamePk), session)
        except Exception as error:  # noqa: BLE001
            print(f"  [{index}/{len(gamePks)}] {gamePk} FAILED: {error}")
            continue
        gameInfoByPk[int(gamePk)] = gameInfo
        if not playIdFrame.empty:
            feeds.append(playIdFrame)
        if index % 10 == 0 or index == len(gamePks):
            print(f"  [{index}/{len(gamePks)}] …")

    if not feeds:
        print("No play_ids resolved.")
        return 1
    feed = pd.concat(feeds, ignore_index=True)
    merged = selected.merge(feed, on=["game_pk", "at_bat_number", "pitch_number"], how="left")
    rate = merged["play_id"].notna().mean()
    print(f"play_id join: {merged['play_id'].notna().sum()}/{len(merged)} ({rate:.1%})")

    resolved = merged[merged["play_id"].notna()].copy()
    if args.dryRun:
        print("\n--dryRun set, stopping before download.")
        return 0

    print(f"\nDownloading {len(resolved)} clips")
    added, skipped = [], 0
    for index, (_, row) in enumerate(resolved.iterrows(), start=1):
        playId, gamePk = row["play_id"], int(row["game_pk"])
        dirName = savantUtils.pitchDirName(gamePk, playId)
        pitchDir = os.path.join(savantUtils.PITCHES_DIR, dirName)
        clipPath = os.path.join(pitchDir, "clip.mp4")
        metaPath = os.path.join(pitchDir, "meta.json")
        meta = buildMeta(row, playId, gameInfoByPk.get(gamePk, {}))

        if dirName in existing and os.path.exists(clipPath) and os.path.exists(metaPath):
            skipped += 1
            continue
        try:
            clipUrl = savantUtils.resolveClipUrl(playId, session)
            if not clipUrl:
                print(f"  [{index}/{len(resolved)}] {playId[:8]} no mp4")
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
        added.append(meta)
        print(f"  [{index}/{len(resolved)}] {playId[:8]} ok  "
              f"arm {meta['armAngle']:+6.1f}  {meta['homeTeam']}  {meta['pitcherName']}")

    # Merge into the manifest rather than overwriting the first batch.
    manifestPath = os.path.join(savantUtils.PITCHES_DIR, "manifest.json")
    manifest = savantUtils.loadManifest()
    known = {m.get("playId") for m in manifest}
    manifest.extend(m for m in added if m["playId"] not in known)
    with open(manifestPath, "w") as handle:
        json.dump(manifest, handle, indent=2)

    print(f"\nAdded {len(added)} pitches ({skipped} already present). "
          f"{len(manifest)} in manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
