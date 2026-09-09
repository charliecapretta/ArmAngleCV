"""
Can frame differencing find the ball where a detector could not?

Dead end #2 used a YOLO detector: it found a plausible ball on 1 of 8 release
frames, at conf 0.09. That was appearance-based. The camera is locked (measured
border frame-diff 0.00), so motion is a signal nobody has used.

Ground truth here is strong: labels.csv holds the hand-clicked ball position at
the hand-picked release frame, so a candidate can be scored in pixels.
"""
import os, sys, cv2, numpy as np, pandas as pd
os.chdir("/Users/charliecapretta/Desktop/ArmAngleAttempt")

PRE, POST = 6, 26          # frames of window before / after the release frame
AREA_MIN, AREA_MAX = 3, 600
THRESH = 22

def candidates(gray, bg):
    diff = cv2.absdiff(gray, bg)
    _, th = cv2.threshold(diff, THRESH, 255, cv2.THRESH_BINARY)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, _, stats, cent = cv2.connectedComponentsWithStats(th, connectivity=8)
    out = []
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if AREA_MIN <= a <= AREA_MAX and max(w, h) <= 60:
            out.append((cent[i][0], cent[i][1], a))
    return out

d = pd.read_csv("labels.csv")
d = d[d.frame_index.notna() & (d.game_pk.astype(str) != "local")
      & d.ball_x.notna() & d.ball_y.notna()].copy()
d["f"] = d.frame_index.astype(int)
d = d[(d.f > 100) & (d.f < 300)]
sample = d.sample(n=10, random_state=1)

hits, dists, nCand = [], [], []
print("clip                     ball@release   nearest motion blob   dist   candidates")
for _, r in sample.iterrows():
    path = f"pitches/{int(r.game_pk)}_{r.play_id}/clip.mp4"
    if not os.path.exists(path):
        continue
    f0 = int(r.f)
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0 - PRE)
    frames = []
    for _ in range(PRE + POST):
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
    cap.release()
    if len(frames) < PRE + 8:
        continue
    bg = np.median(np.array(frames), axis=0).astype(np.uint8)

    cands = candidates(frames[PRE], bg)          # the release frame itself
    truth = np.array([float(r.ball_x), float(r.ball_y)])
    if cands:
        arr = np.array([[c[0], c[1]] for c in cands])
        k = int(np.argmin(np.linalg.norm(arr - truth, axis=1)))
        dist = float(np.linalg.norm(arr[k] - truth))
    else:
        dist = np.inf
    hits.append(dist <= 15); dists.append(dist); nCand.append(len(cands))
    print("  %-22s (%4.0f,%4.0f)   (%6.1f,%6.1f)  %6.1f   %4d"
          % (str(r.pitcher_name)[:22], truth[0], truth[1],
             arr[k][0] if cands else -1, arr[k][1] if cands else -1, dist, len(cands)))

dists = np.array(dists)
print("\nn = %d clips" % len(dists))
print("a motion blob within 15 px of the hand-clicked ball: %d of %d (%.0f%%)"
      % (sum(hits), len(hits), 100*np.mean(hits)))
print("median distance to nearest blob: %.1f px" % np.median(dists))
print("median candidate blobs per frame: %.0f  (clutter to disambiguate)" % np.median(nCand))
print("\nfor comparison, dead end #2 (YOLO detector): 1 of 8 at conf 0.09")
