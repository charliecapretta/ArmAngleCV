"""
Seeded tracking: start at the hand-clicked ball position and try to FOLLOW it.

Separates two questions that the single-frame test conflated:
  (a) is the ball visible to motion differencing at all?
  (b) can it be found without knowing where to look?
If (a) fails, the whole motion approach is dead. If (a) works, the problem is
initialisation, which physics constrains -- the ball leaves along the arm.
"""
import os, cv2, numpy as np, pandas as pd
os.chdir("/Users/charliecapretta/Desktop/ArmAngleAttempt")

STEPS, THRESH = 10, 18

def movers(prev, cur, nxt):
    """Three-frame differencing: bright where something moved in BOTH gaps."""
    a = cv2.absdiff(cur, prev); b = cv2.absdiff(nxt, cur)
    m = cv2.bitwise_and(cv2.threshold(a, THRESH, 255, cv2.THRESH_BINARY)[1],
                        cv2.threshold(b, THRESH, 255, cv2.THRESH_BINARY)[1])
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, _, stats, cent = cv2.connectedComponentsWithStats(m, connectivity=8)
    return [(cent[i][0], cent[i][1]) for i in range(1, n)
            if 2 <= stats[i, cv2.CC_STAT_AREA] <= 500
            and max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) <= 70]

d = pd.read_csv("labels.csv")
d = d[d.frame_index.notna() & (d.game_pk.astype(str) != "local")
      & d.ball_x.notna() & d.ball_y.notna()].copy()
d["f"] = d.frame_index.astype(int)
d = d[(d.f > 100) & (d.f < 300)]
sample = d.sample(n=10, random_state=1)

lengths = []
print("clip                    track length (frames followed)   mean step px")
for _, r in sample.iterrows():
    path = f"pitches/{int(r.game_pk)}_{r.play_id}/clip.mp4"
    if not os.path.exists(path):
        continue
    f0 = int(r.f)
    cap = cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_FRAMES, f0 - 1)
    frames = []
    for _ in range(STEPS + 3):
        ok, fr = cap.read()
        if not ok: break
        frames.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
    cap.release()
    if len(frames) < STEPS + 3: continue

    pos = np.array([float(r.ball_x), float(r.ball_y)]); vel = None; steps = []
    for t in range(1, STEPS + 1):
        cands = movers(frames[t-1], frames[t], frames[t+1])
        if not cands: break
        pred = pos + vel if vel is not None else pos
        arr = np.array(cands)
        dist = np.linalg.norm(arr - pred, axis=1)
        k = int(np.argmin(dist))
        # gate: first step allows a wide search, later steps must follow velocity
        gate = 70 if vel is None else 35
        if dist[k] > gate: break
        new = arr[k]
        step = np.linalg.norm(new - pos)
        vel = new - pos; pos = new; steps.append(step)
    lengths.append(len(steps))
    print("  %-22s %2d of %d                    %s"
          % (str(r.pitcher_name)[:22], len(steps), STEPS,
             ("%.1f" % np.mean(steps)) if steps else "-"))

L = np.array(lengths)
print("\nn = %d clips" % len(L))
print("median frames followed: %.0f of %d" % (np.median(L), STEPS))
print("followed >= 4 frames (enough to fit a line): %d of %d (%.0f%%)"
      % ((L >= 4).sum(), len(L), 100*(L >= 4).mean()))
