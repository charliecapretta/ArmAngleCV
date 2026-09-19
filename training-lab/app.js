'use strict';
const $=id=>document.getElementById(id);
const names=['Throwing shoulder','Glove-side shoulder','Throwing elbow','Throwing wrist','Ball center'];
const hints=['Mark the center of the throwing shoulder joint, not the edge of the jersey.','Mark the center of the opposite shoulder joint.','Mark the center of the throwing elbow joint.','Mark the wrist joint where the throwing hand meets the forearm.','Mark the center of the baseball. If you cannot locate it, skip rather than guess.'];
const canvas=$('frameCanvas'), ctx=canvas.getContext('2d'), loupe=$('loupe'), lc=loupe.getContext('2d');
let manifest,state,key,image,active=0,loaded=false;
function fail(message){$('error').textContent=message;$('error').hidden=false;}
function persist(){try{localStorage.setItem(key,JSON.stringify(state));$('saveStatus').textContent='Saved in this browser';return true;}catch(e){fail('Browser storage is unavailable or full. Keep this tab open and download your progress.');return false;}}
function current(){const order=state.pass===1?state.order1:state.order2;return manifest.items.find(i=>i.id===order[state.index]);}
function selecting(){return state.releaseSelection && !state.lockedFrames[current().id];}
function frameIndex(){return state.lockedFrames?.[current().id]?.frameIndex ?? state.candidateFrame ?? current().frameIndex;}
function draft(){return state.draft || (state.draft={points:{},notes:''});}
function show(id){$('upgrade').hidden=!!state.releaseSelection;for(const name of ['welcome','workspace','break','results'])$(name).hidden=name!==id;}
function resize(){if(!loaded)return;const width=$('viewport').clientWidth*Number($('zoom').value);canvas.style.width=width+'px';canvas.style.height=(width*canvas.height/canvas.width)+'px';draw();}
function draw(){if(!loaded)return;ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0);const scale=canvas.width/(canvas.clientWidth||canvas.width);Lab.keys.forEach((k,i)=>{if(selecting())return;const p=draft().points[k];if(!p)return;ctx.strokeStyle=i===active?'#ffe071':'#88ffcc';ctx.lineWidth=2*scale;ctx.beginPath();ctx.arc(p.x,p.y,5*scale,0,Math.PI*2);ctx.stroke();ctx.fillStyle='#142c26';ctx.fillRect(p.x+8*scale,p.y-17*scale,16*scale,17*scale);ctx.fillStyle='#fff';ctx.font=`${12*scale}px sans-serif`;ctx.fillText(String(i+1),p.x+12*scale,p.y-4*scale);});}
function controls(){ $('pointButtons').replaceChildren();Lab.keys.forEach((k,i)=>{const b=document.createElement('button');b.className='point'+(active===i?' active':'');b.setAttribute('aria-pressed',String(active===i));b.textContent=`${i+1}. ${names[i]}${draft().points[k]?' ✓':''}`;b.onclick=()=>{active=i;controls();draw();};$('pointButtons').append(b);});$('guidance').textContent=hints[active];$('save').disabled=!loaded||!Lab.complete(draft().points); if(Lab.keys.every(k=>draft().points[k])&&!Lab.complete(draft().points)) $('guidance').textContent='The shoulder and ball cannot occupy the same point. Check those marks before saving.'; }
let frameRequest=0;
async function loadFrame(){
 const ticket=++frameRequest, item=current(), index=frameIndex();loaded=false;$('lockFrame').disabled=true;$('skip').disabled=true;controls();
 $('frame').textContent=index;$('frameSlider').value=index;$('frameNumber').value=index;
 try {
  const url=state.releaseSelection?`/api/frame?id=${encodeURIComponent(item.id)}&frame=${index}`:item.image;
  const response=await fetch(url);if(!response.ok)throw Error('Frame unavailable');
  const sha=response.headers.get('X-Frame-Sha256')||item.imageSha256;
  const blob=await response.blob();if(ticket!==frameRequest)return;
  const objectUrl=URL.createObjectURL(blob), nextImage=new Image();
  nextImage.onload=()=>{URL.revokeObjectURL(objectUrl);if(ticket!==frameRequest)return;image=nextImage;canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;loaded=true;state.displayedFrame={frameIndex:index,imageSha256:sha};resize();requestAnimationFrame(draw);controls();$('lockFrame').disabled=false;$('skip').disabled=false;};
  nextImage.onerror=()=>{URL.revokeObjectURL(objectUrl);fail('This frame could not be displayed. Choose a neighboring frame.');};nextImage.src=objectUrl;
 }catch(e){if(ticket===frameRequest)fail('Could not load this frame. Check that the practice server is running, then choose a neighboring frame.');}
}
async function render(){show(state.stage);if(state.stage==='results'){results();return;}if(state.stage!=='workspace')return;
 const item=current();loaded=false;controls();ctx.clearRect(0,0,canvas.width,canvas.height);lc.clearRect(0,0,180,140);
 $('progress').textContent=`PASS ${state.pass} OF 2 · PITCH ${state.index+1} OF 5`;
 $('hand').textContent=item.throws==='L'?'Left-handed pitcher':'Right-handed pitcher';$('notes').value=draft().notes;$('zoom').value='1';$('viewport').scrollTo(0,0);
 $('releaseControls').hidden=!selecting();$('labelControls').hidden=!!selecting();$('changeFrame').hidden=!state.releaseSelection||state.pass!==1||selecting();$('frameMode').textContent=selecting()?'Choose release':'Locked frame';
 $('frameSlider').max=item.nFrames-1;$('frameNumber').max=item.nFrames-1;
 active=Math.max(0,Lab.keys.findIndex(k=>!draft().points[k]));await loadFrame();
}
function chooseFrame(value){if(!selecting())return;const index=Number(value);if(!Number.isInteger(index))return;state.candidateFrame=Math.max(0,Math.min(current().nFrames-1,index));persist();loadFrame();}
$('frameSlider').oninput=e=>chooseFrame(e.target.value);$('frameNumber').onchange=e=>chooseFrame(e.target.value);
for(const [id,step] of [['back10',-10],['back1',-1],['next1',1],['next10',10]])$(id).onclick=()=>chooseFrame(frameIndex()+step);
$('lockFrame').onclick=()=>{if(!loaded||!selecting()||state.displayedFrame.frameIndex!==frameIndex())return;state.lockedFrames[current().id]={...state.displayedFrame,lockedAt:new Date().toISOString()};state.candidateFrame=null;persist();render();};
$('changeFrame').onclick=()=>{if(state.pass!==1)return;state.candidateFrame=frameIndex();delete state.lockedFrames[current().id];state.draft={points:{},notes:''};persist();render();};
$('newRound').onclick=()=>{const prior=structuredClone(state);state={sessionId:crypto.randomUUID(),startedAt:new Date().toISOString(),stage:'workspace',pass:1,index:0,records:[],draft:{points:{},notes:''},order1:manifest.items.map(i=>i.id),order2:[2,4,1,0,3].map(i=>manifest.items[i].id),releaseSelection:true,lockedFrames:{},previousRound:prior};persist();render();};
function coordinate(event){const r=canvas.getBoundingClientRect();return {x:Math.max(0,Math.min(canvas.width-1,(event.clientX-r.left)*canvas.width/r.width)),y:Math.max(0,Math.min(canvas.height-1,(event.clientY-r.top)*canvas.height/r.height))};}
canvas.addEventListener('click',e=>{if(!loaded||selecting())return;draft().points[Lab.keys[active]]=coordinate(e);const next=Lab.keys.findIndex(k=>!draft().points[k]);if(next>=0)active=next;persist();controls();draw();});
canvas.addEventListener('pointermove',e=>{if(!loaded)return;const p=coordinate(e);lc.fillStyle='#182a26';lc.fillRect(0,0,180,140);lc.imageSmoothingEnabled=false;lc.drawImage(image,p.x-22.5,p.y-17.5,45,35,0,0,180,140);lc.strokeStyle='#ffe071';lc.lineWidth=1;lc.beginPath();lc.moveTo(90,0);lc.lineTo(90,140);lc.moveTo(0,70);lc.lineTo(180,70);lc.stroke();});
$('zoom').onchange=resize;window.addEventListener('resize',resize);
$('notes').oninput=()=>{draft().notes=$('notes').value;persist();};
$('clear').onclick=()=>{state.draft.points={};active=0;persist();controls();draw();};
function advance(status){if(!loaded || selecting() || (status==='labeled'&&!Lab.complete(draft().points)))return;const item=current();state.records.push({id:item.id,pass:state.pass,frameIndex:frameIndex(),imageSha256:state.lockedFrames?.[item.id]?.imageSha256||item.imageSha256,status,skipReason:status==='skipped'?$('skipReason').value:null,points:structuredClone(draft().points),notes:draft().notes,completedAt:new Date().toISOString()});state.draft={points:{},notes:''};state.candidateFrame=null;state.index++;if(state.index===5)state.stage=state.pass===1?'break':'results';persist();render();window.scrollTo(0,0);}
$('save').onclick=()=>advance('labeled');$('skip').onclick=()=>advance('skipped');
$('start').onclick=()=>{state.stage='workspace';persist();render();};
$('second').onclick=()=>{state.stage='workspace';state.pass=2;state.index=0;state.secondPassStartedAt=new Date().toISOString();persist();render();};
function results(){const rows=Lab.compare(manifest.items,state.records), good=rows.filter(r=>r.comparable);$('summary').replaceChildren();for(const [value,title] of [[`${good.length} / 5`,'Comparable pairs'],[good.length?Lab.median(good.map(r=>r.angleDifference)).toFixed(1)+'°':'—','Median angle difference'],[good.length?Lab.median(good.map(r=>r.meanPointShift)).toFixed(1)+' px':'—','Median point shift']]){const a=document.createElement('article'),b=document.createElement('b'),h=document.createElement('h3');b.textContent=value;h.textContent=title;a.append(b,h);$('summary').append(a);}$('rows').replaceChildren();rows.forEach((r,i)=>{const tr=document.createElement('tr');for(const val of [`Pitch ${i+1}`,r.comparable?r.angleDifference.toFixed(1)+'°':'—',r.comparable?r.meanPointShift.toFixed(1)+' px':'—',r.comparable?'Compared':'Skipped in one or both passes']){const td=document.createElement('td');td.textContent=val;tr.append(td);}$('rows').append(tr);});}
async function download(){
 const data={schemaVersion:1,protocol:manifest.protocol,exportedAt:new Date().toISOString(),session:state,frames:{...manifest,items:manifest.items.map(item=>({...item,...(state.lockedFrames?.[item.id]||{}),image:state.lockedFrames?.[item.id]?`/api/frame?id=${item.id}&frame=${state.lockedFrames[item.id].frameIndex}`:item.image}))},comparison:state.stage==='results'?Lab.compare(manifest.items,state.records):null};
 try {
  const response=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(!response.ok)throw Error('Export could not be saved');
  const saved=await response.json();
  $('exportStatus').textContent='Saved separately in '+saved.file;
  const a=document.createElement('a');a.href=saved.url;a.download=saved.file.split('/').pop();document.body.append(a);a.click();a.remove();
 }catch(e){fail('Export failed. Keep this tab open, restart the practice server, and try again. Your browser progress is still available.');}
}
document.querySelectorAll('.export').forEach(b=>b.onclick=download);
(async()=>{try{const response=await fetch('assets/session.json');if(!response.ok)throw Error('Missing session');manifest=await response.json();key='arm-angle-practice-v1-'+manifest.id;const saved=localStorage.getItem(key);state=saved?JSON.parse(saved):{sessionId:crypto.randomUUID(),startedAt:new Date().toISOString(),releaseSelection:true,lockedFrames:{},stage:'welcome',pass:1,index:0,records:[],draft:{points:{},notes:''},order1:manifest.items.map(i=>i.id),order2:[2,4,1,0,3].map(i=>manifest.items[i].id)};await render();}catch(e){fail('Unable to open the practice session. Start training-lab/serve.py and open its local address. If the problem persists, keep your browser data and report this error: '+e.message);}})();
