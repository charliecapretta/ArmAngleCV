"""Add a small, separate MLB UI trial batch without replacing existing manifests."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import savantUtils as s
from fetchPitchBatch import loadStatcast,buildMeta,PREFERRED_DESCRIPTIONS
from labelServer import probeClip
ROOT=Path(__file__).resolve().parent
BATCH=ROOT/'uiTestBatch.json'

def main():
    if BATCH.exists():
        existing=json.loads(BATCH.read_text())
        if len(existing.get('pitches',[]))>=12:
            print('UI batch already available:',len(existing['pitches']));return
    else:existing={'name':'MLB UI trial · September 8','pitches':[]}
    table=loadStatcast(str(ROOT/'savant_data (10).csv'))
    table=table[table.arm_angle.notna() & table.description.isin(PREFERRED_DESCRIPTIONS)].copy()
    # Local cached feeds already carry exact pitch-to-play joins.
    feeds=[];infos={};session=s.makeSession()
    for file in sorted(Path(s.CACHE_DIR).glob('*.json')):
        try:
            pk=int(file.stem)
            if pk not in table.game_pk.values:continue
            feed,info=s.playIdsForGame(pk,session)
            if not feed.empty:feeds.append(feed);infos[pk]=info
        except (ValueError,KeyError):continue
    if not feeds:
        selected=[];pitchers=set()
        for target in np.linspace(-60,75,12):
            candidates=table[~table.pitcher.isin(pitchers)].copy()
            candidates['distance']=abs(candidates.arm_angle-target)
            row=candidates.sort_values('distance').iloc[0]
            selected.append(int(row.game_pk));pitchers.add(int(row.pitcher))
        for pk in dict.fromkeys(selected):
            try:
                feed,info=s.playIdsForGame(pk,session)
                if not feed.empty:feeds.append(feed);infos[pk]=info
                print('Resolved game',pk,flush=True)
            except Exception as error:print('Game unavailable',pk,str(error),flush=True)
    if not feeds:raise RuntimeError('Could not resolve any game feeds')
    pool=table.merge(pd.concat(feeds,ignore_index=True),on=['game_pk','at_bat_number','pitch_number'])
    pool=pool[~pool.apply(lambda r:(Path(s.PITCHES_DIR)/s.pitchDirName(int(r.game_pk),r.play_id)/'clip.mp4').exists(),axis=1)]
    used={p['pitcherId'] for p in existing['pitches']};attempted=set();records=existing['pitches']
    for target in np.linspace(-60,75,12):
        if len(records)>=12:break
        candidates=pool[~pool.pitcher.isin(used) & ~pool.play_id.isin(attempted)].copy()
        candidates['distance']=abs(candidates.arm_angle-target)
        for _,row in candidates.sort_values('distance').head(3).iterrows():
            attempted.add(row.play_id);pk=int(row.game_pk);directory=Path(s.PITCHES_DIR)/s.pitchDirName(pk,row.play_id)
            try:
                url=s.resolveClipUrl(row.play_id,session)
                if not url:continue
                directory.mkdir(parents=True,exist_ok=True)
                if not s.downloadClip(url,str(directory/'clip.mp4'),session):continue
                info=probeClip(str(directory/'clip.mp4'))
                if not info or not info['nFrames']:continue
                meta=buildMeta(row,row.play_id,infos[pk]);meta['uiTestBatch']=True
                (directory/'meta.json').write_text(json.dumps(meta,indent=2))
                records.append(meta);used.add(int(row.pitcher));BATCH.write_text(json.dumps({'name':existing['name'],'pitches':records},indent=2))
                print(f"{len(records)}/12 {meta['pitcherName']} {meta['homeTeam']} {meta['armAngle']:+.1f} deg {info['fps']:.2f}fps",flush=True)
                break
            except Exception as e:print('Could not fetch',row.play_id,str(e),flush=True)
    print('Added',len(records),'new clips. Existing clips, labels, and manifest unchanged.',flush=True)
if __name__=='__main__':main()
