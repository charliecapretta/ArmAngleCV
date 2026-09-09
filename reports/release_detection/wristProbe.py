"""Does peak wrist speed miss release by a CONSTANT offset? 196 labels can fix a constant."""
import sys, os, cv2, numpy as np, pandas as pd
sys.path.insert(0, "/Users/charliecapretta/Desktop/ArmAngleAttempt")
os.chdir("/Users/charliecapretta/Desktop/ArmAngleAttempt")
from phase4 import detect
from ultralytics import YOLO

BEFORE, AFTER = 28, 12
model = YOLO("yolo11n-pose.pt")
d = pd.read_csv("labels.csv")
d = d[d.frame_index.notna() & (d.game_pk.astype(str) != "local")].copy()
d["f"] = d.frame_index.astype(int)
d = d[(d.f > 100) & (d.f < 300)]
sample = d.sample(n=14, random_state=0)

COCO_WRIST = {"R": 10, "L": 9}
rows = []
for _, r in sample.iterrows():
    path = f"pitches/{int(r.game_pk)}_{r.play_id}/clip.mp4"
    if not os.path.exists(path):
        continue
    f0, start = int(r.f), int(r.f) - BEFORE
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    pts = []
    for i in range(BEFORE + AFTER):
        ok, frame = cap.read()
        if not ok:
            break
        res = model.predict([frame], conf=0.25, verbose=False)[0]
        people = []
        for box, kp in zip(res.boxes.xyxy.cpu().numpy(), res.keypoints.xy.cpu().numpy()):
            people.append({"box": box, "kp": kp})
        if not people:
            pts.append((np.nan, np.nan)); continue
        areas = [(p["box"][2]-p["box"][0])*(p["box"][3]-p["box"][1]) for p in people]
        big = max(areas)
        cand = [p for p, a in zip(people, areas) if a >= 0.85*big]
        pick = max(cand, key=lambda p: p["box"][3])
        w = pick["kp"][COCO_WRIST.get(str(r.p_throws), 10)]
        pts.append((float(w[0]), float(w[1])))
    cap.release()
    pts = np.array(pts, dtype=float)
    if len(pts) < BEFORE + 4 or np.isnan(pts).all():
        continue
    speed = np.full(len(pts), np.nan)
    for i in range(1, len(pts)):
        speed[i] = np.hypot(*(pts[i] - pts[i-1]))
    if np.all(np.isnan(speed)):
        continue
    peak = int(np.nanargmax(speed))
    rows.append({"pitcher": r.pitcher_name, "true": f0,
                 "peakFrame": start + peak, "offset": (start + peak) - f0})
    print("  %-22s true %4d  peak-speed %4d  offset %+d" % (r.pitcher_name, f0, start+peak, (start+peak)-f0), flush=True)

o = np.array([x["offset"] for x in rows])
print("\nn = %d clips" % len(o))
print("offset from peak wrist speed to true release:")
print("  median %+.1f frames   mean %+.1f   sd %.2f" % (np.median(o), o.mean(), o.std()))
print("  after subtracting the median, |error| <=1 frame on %.0f%%, <=2 on %.0f%%"
      % (100*(np.abs(o-np.median(o))<=1).mean(), 100*(np.abs(o-np.median(o))<=2).mean()))
