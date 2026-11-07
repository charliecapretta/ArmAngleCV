"""Read-only release-timing audit and label-assisted plate-detector diagnostic.

This is NOT blind/end-to-end validation: saved release frames define the search
window. It asks whether the old plate-region detector works even with this help.
Caches and reports live under reports/release_detection; labels are never written.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import time
import numpy as np
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from releaseTiming import flightSeconds, speedOnlyFlightSeconds, releaseFrameFromArrival

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/release_detection'


def joinedRows():
    statcast = {(r['game_pk'],r['at_bat_number'],r['pitch_number']):r for r in csv.DictReader((ROOT/'data'/'savant_data.csv').open())}
    feeds = {}
    for p in (ROOT/'cache/gf').glob('*.json'):
        data = json.loads(p.read_text())
        for side in ['team_home','team_away']:
            for event in data.get(side,[]):
                if event.get('type')=='pitch' and event.get('play_id'):
                    feeds[(p.stem,event['play_id'])]=event
    rows=[]
    for label in csv.DictReader((ROOT/'data'/'labels.csv').open()):
        if label['status']!='labeled':continue
        key=f"{label['game_pk']}_{label['play_id']}"
        meta=json.loads((ROOT/'pitches'/key/'meta.json').read_text())
        trajectory=statcast.get(tuple(str(meta.get(k)) for k in ['gamePk','atBatNumber','pitchNumber']))
        source='local Statcast CSV'
        if trajectory is None:
            event=feeds.get((label['game_pk'],label['play_id']),{})
            trajectory={'vy0':event.get('vy0'),'ay':event.get('ay'),'release_extension':meta.get('releaseExtension')}
            source='cached game feed + metadata extension'
        row={'key':key,'label':label,'meta':meta,'trajectory':trajectory,'trajectory_source':source}
        try:row['flight_seconds']=flightSeconds(trajectory)
        except ValueError:row['flight_seconds']=None
        rows.append(row)
    return rows


def stats(values):
    values=np.asarray(values,dtype=float)
    if not len(values):return {'n':0}
    return {'n':len(values),'mean':float(values.mean()),'median':float(np.median(values)),'min':float(values.min()),'max':float(values.max())}


def audit(rows):
    values=[]
    for r in rows:
        if r['flight_seconds'] is None:continue
        m=r['meta'];actual=r['flight_seconds']
        old=speedOnlyFlightSeconds(m['releaseSpeed'],m['releaseExtension'],.92,0)
        revised=speedOnlyFlightSeconds(m['releaseSpeed'],m['releaseExtension'],.96)
        values.append({'key':r['key'],'pitcher':m['pitcherName'],'flight_seconds':actual,'legacy_seconds':old,'simple_096_seconds':revised,'legacy_minus_trajectory_ms':1000*(old-actual),'simple_minus_trajectory_ms':1000*(revised-actual),'back_vs_front_ms':1000*(flightSeconds(r['trajectory'],0)-actual),'source':r['trajectory_source']})
    with (OUT/'flight_audit.csv').open('w',newline='') as h:
        w=csv.DictWriter(h,fieldnames=list(values[0]));w.writeheader();w.writerows(values)
    result={'labeled_pitches':len(rows),'trajectory_available':len(values),'legacy_minus_trajectory_ms':stats([v['legacy_minus_trajectory_ms'] for v in values]),'simple_096_minus_trajectory_ms':stats([v['simple_minus_trajectory_ms'] for v in values]),'back_vs_front_ms':stats([v['back_vs_front_ms'] for v in values]),'note':'Formula comparison against a trajectory approximation, not observed flight-time accuracy.'}
    (OUT/'flight_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)


def decodeWindow(path,start,end):
    import av
    import cv2
    images={};times=[]
    with av.open(str(path)) as container:
        stream=container.streams.video[0]
        for i,frame in enumerate(container.decode(stream)):
            t=float(frame.time) if frame.time is not None else i/float(stream.average_rate)
            times.append(t)
            if start<=i<=end:images[i]=frame.to_ndarray(format='bgr24')
            if i>end:break
    origin=times[0]
    return images,[t-origin for t in times]


def best(dets,name,confidence=.25):
    choices=[d for d in dets if d['name']==name and d['conf']>=confidence]
    return max(choices,key=lambda d:d['conf']) if choices else None


def center(d):
    b=d['box'];return ((b[0]+b[2])/2,(b[1]+b[3])/2)


def detectDiagnostic(rows,limit):
    import cv2
    os.environ.setdefault('MPLCONFIGDIR','/tmp/armangle-release-mpl')
    from ultralytics import YOLO
    weights=ROOT/'models/od/YOLO/glove_tracking/model_weights/glove_tracking.pt'
    model=YOLO(str(weights))
    trial=[r for r in rows if r['meta'].get('uiTestBatch') and r['flight_seconds'] is not None][:limit]
    results=[]
    for n,r in enumerate(trial,1):
        begin=time.monotonic();ref=int(r['label']['frame_index']);m=r['meta']
        # Label-assisted window intentionally explicit: five frames before the
        # hand label through 0.75 s afterward. Detection sees no labeled points.
        cap=cv2.VideoCapture(str(ROOT/'pitches'/r['key']/'clip.mp4'));fps=cap.get(cv2.CAP_PROP_FPS);cap.release()
        start=max(0,ref-5);end=ref+int(np.ceil(.75*fps))
        images,times=decodeWindow(ROOT/'pitches'/r['key']/'clip.mp4',start,end)
        cachePath=OUT/(r['key']+'_detections.json')
        signature={'label_frame':ref,'window':[start,end],'imgsz':640,'confidence':.15,'weights_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),'video_size':(ROOT/'pitches'/r['key']/'clip.mp4').stat().st_size}
        cached=json.loads(cachePath.read_text()) if cachePath.exists() else {}
        if cached.get('signature')==signature:
            detections={int(k):v for k,v in cached['detections'].items()}
        else:
            detections={}
            for i,image in images.items():
                pred=model.predict([image],conf=.15,imgsz=640,verbose=False,device='cpu')[0]
                detections[i]=[{'name':model.names[int(b.cls[0])],'conf':float(b.conf[0]),'box':b.xyxy[0].tolist()} for b in pred.boxes]
            cachePath.write_text(json.dumps({'signature':signature,'detections':detections}))
        plates=[center(d) for ds in detections.values() if (d:=best(ds,'homeplate'))]
        plate=np.median(plates,axis=0).tolist() if plates else None
        h,w=images[start].shape[:2];accepted=[]
        for i,ds in sorted(detections.items()):
            ball=best(ds,'baseball')
            if ball is None or plate is None:continue
            x,y=center(ball);b=ball['box']
            if abs(x-plate[0])<=w*.1 and plate[1]-h*.28<=y<=plate[1]+h*.04 and b[2]-b[0]<=w*.05 and b[3]-b[1]<=h*.08:
                accepted.append(i)
        first=accepted[0] if accepted else None
        expectedTime=times[ref]+r['flight_seconds']
        expected=min(images,key=lambda i:abs(times[i]-expectedTime))
        out={'key':r['key'],'pitcher':m['pitcherName'],'park':m['homeTeam'],'arm_angle':m['armAngle'],'description':m['description'],'reference_frame':ref,'fps':fps,'flight_seconds':r['flight_seconds'],'plate_detected':plate is not None,'baseball_detection_frames':sum(best(ds,'baseball') is not None for ds in detections.values()),'accepted_frames':accepted,'first_region_frame':first,'expected_front_plate_frame_from_label':expected,'search_window':[start,end],'label_assisted':True}
        if first is not None:
            for name,flight in [('legacy',speedOnlyFlightSeconds(m['releaseSpeed'],m['releaseExtension'],.92,0)),('trajectory',r['flight_seconds'])]:
                estimate=releaseFrameFromArrival(times[first],flight,times)
                out[name+'_release_frame']=estimate
                out[name+'_error_frames']=estimate-ref if estimate is not None else None
                out[name+'_error_ms']=1000*(times[estimate]-times[ref]) if estimate is not None else None
        panels=[]
        for title,i in [('Hand release',ref),('Expected plate (from label)',expected),('First ball in region',first),('Release from corrected flight',out.get('trajectory_release_frame'))]:
            if i not in images:
                frame=np.zeros((h,w,3),np.uint8)
                if i is not None:
                    import av
                    with av.open(str(ROOT/'pitches'/r['key']/'clip.mp4')) as c:
                        for j,f in enumerate(c.decode(video=0)):
                            if j==i:frame=f.to_ndarray(format='bgr24');break
            else:frame=images[i].copy()
            if i in detections:
                for d in detections[i]:
                    if d['conf']<.25:continue
                    x1,y1,x2,y2=map(int,d['box']);cv2.rectangle(frame,(x1,y1),(x2,y2),(0,255,255),2)
                    cv2.putText(frame,d['name'],(x1,max(15,y1-5)),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,255,255),1)
            frame=cv2.resize(frame,(640,360));cv2.rectangle(frame,(0,0),(640,30),(15,15,15),-1)
            cv2.putText(frame,f'{title}: {i}',(8,21),cv2.FONT_HERSHEY_SIMPLEX,.52,(255,255,255),1);panels.append(frame)
        cv2.imwrite(str(OUT/(r['key']+'_diagnostic.jpg')),np.vstack([np.hstack(panels[:2]),np.hstack(panels[2:])]))
        results.append(out);(OUT/'detector_diagnostic.json').write_text(json.dumps({'protocol':'Label-assisted plate-region diagnostic, NOT blind release accuracy. First accepted 2D region detection is only a proxy for arrival. No fitted thresholds or offsets.','rows':results},indent=2))
        print(f"{n}/{len(trial)} {m['pitcherName']}: plate={bool(plate)} ballFrames={out['baseball_detection_frames']} first={first} errors old/new={out.get('legacy_error_frames')}/{out.get('trajectory_error_frames')} ({time.monotonic()-begin:.1f}s)",flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--detect-limit',type=int,default=0);args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'input_snapshot.json').write_text(json.dumps({name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['data/labels.csv','localLabels.csv','model/armAngleModel.joblib','model/armAngleModelResolutionSafe.joblib']},indent=2))
    rows=joinedRows();audit(rows)
    if args.detect_limit:detectDiagnostic(rows,args.detect_limit)

if __name__=='__main__':main()
