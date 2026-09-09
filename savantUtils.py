"""
Shared helpers for pulling Baseball Savant pitch data, play_ids, and clips.

Facts baked in here so they don't get re-derived per script:
  - The /gf endpoint 403s without a browser User-Agent.
  - Feed ab_number/pitch_number join exactly onto Statcast at_bat_number/pitch_number
    (verified 98.5% match, no duplicate keys) -- no fuzzy inning/count/batter join needed.
  - venue_id is only available from /gf, not from the Statcast CSV export.
  - The mp4 lives in a <video><source type="video/mp4"> tag on the sporty-videos page.
"""

import json
import os
import time
import random

import pandas as pd
import requests
from bs4 import BeautifulSoup

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PROJECT_DIR, "cache", "gf")
PITCHES_DIR = os.path.join(PROJECT_DIR, "pitches")

GF_URL = "https://baseballsavant.mlb.com/gf?game_pk={gamePk}"
VIDEO_PAGE_URL = "https://baseballsavant.mlb.com/sporty-videos?playId={playId}"

REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 4


def makeSession():
    """Session carrying the browser UA. Required -- bare requests/urllib get a 403."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def politeGet(url, session, timeout=45, stream=False):
    """GET with backoff on 429/5xx. Raises on final failure."""
    lastError = None
    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(url, timeout=timeout, stream=stream)
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"status {response.status_code}")
            response.raise_for_status()
            return response
        except Exception as error:  # noqa: BLE001 - retry on anything transient
            lastError = error
            sleepFor = (2 ** attempt) + random.uniform(0, 0.5)
            time.sleep(sleepFor)
    raise RuntimeError(f"GET failed after {MAX_RETRIES} attempts: {url} ({lastError})")


def fetchGameFeed(gamePk, session, useCache=True):
    """Fetch and cache the /gf payload for one game. One call per game, not per pitch."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cachePath = os.path.join(CACHE_DIR, f"{gamePk}.json")

    if useCache and os.path.exists(cachePath):
        try:
            with open(cachePath, "r") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            pass  # corrupt cache entry, refetch

    response = politeGet(GF_URL.format(gamePk=gamePk), session)
    payload = response.json()
    with open(cachePath, "w") as handle:
        json.dump(payload, handle)
    time.sleep(REQUEST_DELAY_SECONDS)
    return payload


def playIdsForGame(gamePk, session, useCache=True):
    """
    Return (playIdFrame, gameInfo) for one game.

    playIdFrame columns: game_pk, at_bat_number, pitch_number, play_id
    gameInfo: {'venue_id', 'game_date'} -- venue_id is the park key for Phase 3
              and is not present in the Statcast CSV.
    """
    payload = fetchGameFeed(gamePk, session, useCache=useCache)

    events = []
    for sideKey in ("team_home", "team_away"):
        for event in payload.get(sideKey) or []:
            if event.get("type") != "pitch":
                continue
            if not event.get("play_id"):
                continue
            events.append({
                "game_pk": gamePk,
                "at_bat_number": event.get("ab_number"),
                "pitch_number": event.get("pitch_number"),
                "play_id": event.get("play_id"),
            })

    playIdFrame = pd.DataFrame(events)
    if not playIdFrame.empty:
        playIdFrame = playIdFrame.dropna(subset=["at_bat_number", "pitch_number"])
        playIdFrame["at_bat_number"] = playIdFrame["at_bat_number"].astype(int)
        playIdFrame["pitch_number"] = playIdFrame["pitch_number"].astype(int)
        playIdFrame = playIdFrame.drop_duplicates(
            subset=["game_pk", "at_bat_number", "pitch_number"]
        )

    gameInfo = {
        "venue_id": payload.get("venue_id"),
        "game_date": payload.get("game_date") or payload.get("gameDate"),
    }
    return playIdFrame, gameInfo


def resolveClipUrl(playId, session):
    """Scrape the mp4 URL off the sporty-videos page. Returns None if absent."""
    response = politeGet(VIDEO_PAGE_URL.format(playId=playId), session)
    soup = BeautifulSoup(response.text, "html.parser")

    sourceTag = soup.select_one('video source[type="video/mp4"]')
    if sourceTag is None:
        sourceTag = soup.select_one("video source")
    time.sleep(REQUEST_DELAY_SECONDS)

    if sourceTag is None:
        return None
    return sourceTag.get("src")


def downloadClip(url, destPath, session, minBytes=50_000):
    """Stream a clip to disk. Skips if already present and plausibly sized."""
    if os.path.exists(destPath) and os.path.getsize(destPath) >= minBytes:
        return True

    os.makedirs(os.path.dirname(destPath), exist_ok=True)
    partialPath = destPath + ".part"

    response = politeGet(url, session, stream=True)
    with open(partialPath, "wb") as handle:
        for chunk in response.iter_content(chunk_size=1 << 16):
            if chunk:
                handle.write(chunk)

    if os.path.getsize(partialPath) < minBytes:
        os.remove(partialPath)
        return False

    os.replace(partialPath, destPath)
    return True


ARM_ANGLE_LEADERBOARD = (
    "https://baseballsavant.mlb.com/leaderboard/pitcher-arm-angles"
    "?type=Pitcher&min={minPitches}&season={season}&csv=true"
)

STATCAST_SEARCH = (
    "https://baseballsavant.mlb.com/statcast_search/csv?all=true&type=details"
    "&hfSea={season}%7C&player_type=pitcher{pitcherParams}"
    "&game_date_gt={dateFrom}&game_date_lt={dateTo}"
)


def fetchArmAngleLeaderboard(session, season, minPitches=50):
    """
    Season arm-angle leaderboard as a DataFrame.

    `ball_angle` is the pitcher's average arm angle. The other columns
    (relative_shoulder_x / shoulder_z / relative_release_ball_x / release_ball_z)
    are what Statcast derives it from -- shoulder to ball, in the x-z plane.
    """
    import io
    response = politeGet(
        ARM_ANGLE_LEADERBOARD.format(season=season, minPitches=minPitches), session
    )
    frame = pd.read_csv(io.BytesIO(response.content))
    frame.columns = [c.strip().strip('"').lstrip("\ufeff") for c in frame.columns]
    frame["ball_angle"] = pd.to_numeric(frame["ball_angle"], errors="coerce")
    time.sleep(REQUEST_DELAY_SECONDS)
    return frame.dropna(subset=["ball_angle"])


def fetchStatcastForPitchers(session, pitcherIds, season, dateFrom, dateTo):
    """Statcast detail rows for specific pitchers over a date range."""
    import io
    pitcherParams = "".join(f"&pitchers_lookup%5B%5D={int(p)}" for p in pitcherIds)
    url = STATCAST_SEARCH.format(
        season=season, pitcherParams=pitcherParams, dateFrom=dateFrom, dateTo=dateTo
    )
    response = politeGet(url, session, timeout=180)
    frame = pd.read_csv(io.BytesIO(response.content), low_memory=False)
    frame.columns = [c.strip().strip('"').lstrip("\ufeff") for c in frame.columns]
    time.sleep(REQUEST_DELAY_SECONDS)
    return frame


def pitchDirName(gamePk, playId):
    return f"{gamePk}_{playId}"


def loadManifest(pitchesDir=PITCHES_DIR):
    manifestPath = os.path.join(pitchesDir, "manifest.json")
    if not os.path.exists(manifestPath):
        return []
    with open(manifestPath, "r") as handle:
        return json.load(handle)
