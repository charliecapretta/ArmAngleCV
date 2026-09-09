"""Is the seeded track the BALL, or did it stick to the pitcher?

A thrown ball has near-constant image velocity and moves away from the release
point toward the plate. Drift onto the pitcher's hand or on noise does neither.
"""
import os, cv2, numpy as np, pandas as pd
os.chdir("/Users/charliecapretta/Desktop/ArmAngleAttempt")
STEPS, THRESH = 10, 18

def movers(p, c, n_):
    a=cv2.absdiff(c,p); b=cv2.absdiff(n_,c)
    m=cv2.bitwise_and(cv2.threshold(a,THRESH,255,cv2.THRESH_BINARY)[1],
                      cv2.threshold(b,THRESH,255,cv2.THRESH_BINARY)[1])
    m=cv2.morphologyEx(m,cv2.MORPH_OPEN,np.ones((2,2),np.uint8))
    n,_,s,ct=cv2.connectedComponentsWithStats(m,connectivity=8)
    return [(ct[i][0],ct[i][1]) for i in range(1,n)
            if 2<=s[i,cv2.CC_STAT_AREA]<=500
            and max(s[i,cv2.CC_STAT_WIDTH],s[i,cv2.CC_STAT_HEIGHT])<=70]

d=pd.read_csv("labels.csv")
d=d[d.frame_index.notna()&(d.game_pk.astype(str)!="local")&d.ball_x.notna()].copy()
d["f"]=d.frame_index.astype(int); d=d[(d.f>100)&(d.f<300)]
sample=d.sample(n=10,random_state=1)

print("clip                    n   stepCV   net move   dir consistency   verdict")
good=0; total=0
for _,r in sample.iterrows():
    path=f"pitches/{int(r.game_pk)}_{r.play_id}/clip.mp4"
    if not os.path.exists(path): continue
    f0=int(r.f); cap=cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_FRAMES,f0-1)
    fr=[]
    for _ in range(STEPS+3):
        ok,x=cap.read()
        if not ok: break
        fr.append(cv2.cvtColor(x,cv2.COLOR_BGR2GRAY))
    cap.release()
    if len(fr)<STEPS+3: continue
    pos=np.array([float(r.ball_x),float(r.ball_y)]); vel=None; track=[pos.copy()]
    for t in range(1,STEPS+1):
        c=movers(fr[t-1],fr[t],fr[t+1])
        if not c: break
        pred=pos+vel if vel is not None else pos
        arr=np.array(c); dist=np.linalg.norm(arr-pred,axis=1); k=int(np.argmin(dist))
        if dist[k] > (70 if vel is None else 35): break
        vel=arr[k]-pos; pos=arr[k]; track.append(pos.copy())
    track=np.array(track); total+=1
    if len(track)<5:
        print("  %-22s %2d   %-7s %-10s %-16s too short"%(str(r.pitcher_name)[:22],len(track)-1,"-","-","-")); continue
    steps=np.linalg.norm(np.diff(track,axis=0),axis=1)
    cv_=steps.std()/max(steps.mean(),1e-6)
    net=np.linalg.norm(track[-1]-track[0]); path_len=steps.sum()
    straight=net/max(path_len,1e-6)          # 1.0 = perfectly straight
    ok = (cv_<0.6) and (straight>0.85) and (steps.mean()>8)
    good += ok
    print("  %-22s %2d   %5.2f    %6.1f px   %5.2f            %s"
          %(str(r.pitcher_name)[:22],len(track)-1,cv_,net,straight,"BALL-LIKE" if ok else "rejected"))
print("\nplausible ball tracks: %d of %d clips"%(good,total))
print("criteria: step CV < 0.6, straightness > 0.85, mean step > 8 px")
