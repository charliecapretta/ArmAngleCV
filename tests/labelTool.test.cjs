const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../web/labelTool.js'),'utf8').replace(/boot\(\);\s*$/, '');
function harness(workspace='training', hosted=false, example=false) {
  const elements = new Map();
  class Element {
    constructor(tag='DIV') { this.tagName=tag; this.dataset={}; this.value='all'; this.width=1280; this.height=720; this.hidden=false; this.children=[]; this.style={}; this.classList={add(){},remove(){}}; }
    addEventListener(type,fn) { (this.handlers ||= {})[type]=fn; } replaceChildren(...x) { this.children=x; } append(...x) { this.children.push(...x); }
    click() { this.clicked=true; }
    getContext() { return new Proxy({}, {get:()=>()=>{}}); } focus() {}
  }
  const context = vm.createContext({console,Map,Math,Number,JSON,Error,Promise,performance,URLSearchParams,
    FormData:class {constructor(){this.fields=[];}append(...args){this.fields.push(args);}},
    setTimeout:()=>1,clearTimeout(){},fetch:async()=>({ok:true,json:async()=>({ok:true,undoToken:'token'})}),
    document:{documentElement:{dataset:{workspace,hosted:String(hosted),example:example?'true':undefined}},querySelector:()=>({content:'test-csrf-token'}),getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement:tag=>new Element(tag.toUpperCase()),handlers:{},addEventListener(type,fn){this.handlers[type]=fn}},
    window:{addEventListener(){}},Image:class {constructor(){this.width=1280;this.height=720;}set src(v){this.url=v;Promise.resolve().then(()=>this.onload());}}
  });
  vm.runInContext(source,context);
  const run=code=>vm.runInContext(code,context);
  run(`state.pitches=[{gamePk:1,playId:'a',dirName:'1_a',nFrames:10,fps:30,imgW:1280,imgH:720},{gamePk:1,playId:'b',dirName:'1_b',nFrames:10,fps:60,imgW:1280,imgH:720}]; state.current=state.pitches[0]; state.frame=2; state.img={width:1280,height:720};`);
  return {run,context,elements};
}
test('release checkpoint is incomplete, legacy complete labels still count',()=>{
 const {run}=harness();
 assert.equal(run(`isComplete({status:'release_selected'})`),false);
 assert.equal(run(`isComplete({status:'labeled',ball_x:0,ball_y:0})`),false);
 assert.equal(run(`isComplete({status:'skipped'})`),true);
});
test('failed save keeps pitch, labels, history, and points unchanged',async()=>{
 const {run}=harness();
 run(`state.mode='points'; state.points={shoulder:{x:100,y:200},offShoulder:{x:120,y:200},elbow:{x:150,y:200},wrist:{x:200,y:200},ball:{x:220,y:200}}; request=async()=>{throw new Error('disk full')};`);
 await run(`saveCurrent()`);
 assert.equal(run('state.index'),0); assert.equal(run('state.labels.size'),0); assert.equal(run('state.history.length'),0);
 assert.equal(run('state.points.ball.x'),220); assert.match(run(`el('error').textContent`),/Save failed/);
});
test('confirming release saves an empty checkpoint without auto-labeling',async()=>{
 const {run}=harness();
 run(`let sent=[];request=async(url,row)=>{sent.push({url,row});return {ok:true,undoToken:'t'}};let poseCalls=0;poseSeed=async()=>{poseCalls++};`);
 await run('confirmRelease()');
 assert.equal(run('poseCalls'),0);
 assert.equal(run(`state.labels.get('1_a').status`),'release_selected');
 assert.equal(run(`state.labels.get('1_a').shoulder_x`),''); assert.equal(run(`state.mode`),'points');
});
test('changing release frame clears point coordinates and occlusion',async()=>{
 const {run}=harness();
 run(`state.mode='points';state.points={shoulder:{x:100,y:200}};state.ballOccluded=true;changeRelease();`);
 await run('goToFrame(3)');
 assert.equal(run('Object.keys(state.points).length'),0);assert.equal(run('state.ballOccluded'),false);assert.equal(run('state.frame'),3);
});
test('point mode prevents frame navigation',async()=>{
 const {run}=harness();run(`state.mode='points'`);await run('goToFrame(4)');assert.equal(run('state.frame'),2);
});
test('late pose response cannot overwrite another pitch',async()=>{
 const {run}=harness();run(`state.mode='points';let resolvePose;request=()=>new Promise(r=>resolvePose=r);let pending=poseSeed();state.viewToken++;state.points={};resolvePose({people:[{shoulder:{x:5,y:5,conf:1}}]});`);
 await run('pending');assert.equal(run('Object.keys(state.points).length'),0);
});
test('frame timing uses decoded timestamps when available',()=>{
 const {run}=harness();assert.equal(run('timeAt(3)'),.1);
 run(`state.current.frameTimes=[0,.03,.069,.101]`);assert.equal(run('timeAt(3)'),.101);
});
test('occluded label never sends invented ball coordinates',()=>{
 const {run}=harness();run(`state.points.ball={x:20,y:20}`);
 assert.equal(run(`buildRow('occluded').ball_x`),'');assert.equal(run(`buildRow('occluded').ball_visibility`),'occluded');
});
test('explicit point selection permits precise placement next to existing points',()=>{
 const {run}=harness();run(`state.mode='points';state.points.ball={x:20,y:20};replacePoint('ball')`);
 assert.equal(run('state.active'),'ball');assert.equal(run('state.placingPoint'),true);
 assert.equal(run('state.points.ball'),null);
 run('clearPoints()');assert.equal(run('state.placingPoint'),false);
});
test('changing points invalidates an in-flight angle prediction',async()=>{
 const {run}=harness();
 run(`state.mode='points';state.points={shoulder:{x:100,y:200},offShoulder:{x:120,y:200},elbow:{x:150,y:200},wrist:{x:200,y:200},ball:{x:220,y:200}};let resolvePrediction;request=()=>new Promise(r=>resolvePrediction=r);let prediction=predictFromPoints();markDirty();resolvePrediction({predicted_arm_angle:25});`);
 await run('prediction');assert.doesNotMatch(run(`el('anglePrediction').textContent`),/25/);
});
test('quality flags are included without pretending an exact release',()=>{
 const {run}=harness();run(`state.releaseQuality='unclear';state.pointQuality='uncertain'`);
 assert.equal(run(`buildRow('labeled').release_quality`),'unclear');
 assert.equal(run(`buildRow('labeled').point_quality`),'uncertain');
 assert.equal(run(`buildRow('release_selected').point_quality`),'unreviewed');
});

test('placing the last point enables review and training reference stays hidden until review',async()=>{
 const {run}=harness();
 run(`state.mode='points';renderWorkflow()`);
 assert.equal(run(`el('reviewPoints').disabled`),true);
 assert.equal(run(`el('truthDetails').hidden`),true);
 run(`state.points={shoulder:{x:100,y:200},offShoulder:{x:120,y:200},elbow:{x:150,y:200},wrist:{x:200,y:200},ball:{x:220,y:200}};draw()`);
 assert.equal(run(`el('reviewPoints').disabled`),false);
 await run('goReview()');
 assert.equal(run('state.reviewing'),true);
 assert.equal(run(`el('truthDetails').hidden`),false);
 run('editPoints()');
 assert.equal(run(`el('truthDetails').hidden`),true);
});
test('analysis requests use their own workspace and review calculates the estimate',async()=>{
 const {run,context}=harness('analysis');const requests=[];
 context.fetch=async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({ok:true,predicted_arm_angle:12.5})}};
 await run(`request('/api/labels')`);
 assert.equal(requests[0].url,'/api/labels?workspace=analysis');
 run(`state.mode='points';state.points={shoulder:{x:100,y:200},offShoulder:{x:120,y:200},elbow:{x:150,y:200},wrist:{x:200,y:200},ball:{x:220,y:200}}`);
 await run('goReview()');
 assert.equal(run('state.predictedAngle'),12.5);
 assert.equal(run(`el('reviewBallAngle').textContent`),'0.0°');
 assert.equal(JSON.parse(requests[1].options.body).workspace,'analysis');
 assert.equal(run(`el('truthDetails').hidden`),true);
 assert.equal(run(`el('savePoints').disabled`),false);
 run('markDirty()');await run('saveCurrent()');
 assert.equal(requests.length,2);
});

test('session export blocks unsaved edits, including another pitch draft',()=>{
 const {run}=harness('analysis');
 run(`state.pitches.forEach(p=>p.sessionId='s1');state.labels.set('1_a',{status:'release_selected'});`);
 assert.equal(run('canExportSession()'),true);
 run('markDirty()');assert.equal(run('canExportSession()'),false);
 run(`state.dirty=false;state.drafts.set('1_b',{dirty:true});`);
 assert.equal(run('canExportSession()'),false);
 run(`state.pitches[1].sessionId='s2'`);assert.equal(run('canExportSession()'),true);
});
test('session list includes unfinished pitches and filters the queue',()=>{
 const {run}=harness('analysis');
 run(`state.pitches[0].sessionId='s1';state.pitches[1].sessionId='s2';state.sessionFilter='s1';state.labels.set('1_a',{status:'release_selected',frame_index:2});renderSession()`);
 assert.equal(run('visiblePitches().length'),1);
 assert.equal(run(`el('sessionPitches').children.length`),1);
 assert.match(run(`el('sessionPitches').children[0].textContent`),/needs points/);
});
test('upload handles failure without losing existing annotations or leaving controls busy',async()=>{
 const {run}=harness('analysis');
 run(`el('videoFile').files=[{name:'test.mp4',size:100}];state.labels.set('1_a',{status:'release_selected'});sendVideo=async()=>{throw new Error('Cannot decode this MP4')};`);
 await run('uploadVideo()');
 assert.equal(run('state.pitches.length'),2);
 assert.equal(run('state.labels.size'),1);
 assert.equal(run('state.busy'),false);
 assert.match(run(`el('importError').textContent`),/Cannot decode/);
});
test('upload size validation allows hosted clips through 200 MiB and preserves the local limit',async()=>{
 for (const {hosted,size,accepted,limit} of [
   {hosted:true,size:51*1024*1024,accepted:true,limit:200},
   {hosted:true,size:200*1024*1024,accepted:true,limit:200},
   {hosted:true,size:200*1024*1024+1,accepted:false,limit:200},
   {hosted:false,size:100*1024*1024,accepted:true,limit:100},
   {hosted:false,size:100*1024*1024+1,accepted:false,limit:100},
 ]) {
   const {run,context}=harness('analysis',hosted);
   context.uploadSize=size;
   run(`el('videoFile').files=[{name:'test.mp4',size:uploadSize}];let uploadCalls=0;sendVideo=async()=>{uploadCalls++;throw new Error('Reached upload transport')};`);
   await run('uploadVideo()');
   assert.equal(run('uploadCalls'),Number(accepted),`hosted=${hosted}, size=${size}`);
   assert.equal(run(`el('importError').textContent`),accepted?'Reached upload transport':`Choose an MP4 video no larger than ${limit} MB.`);
   assert.equal(run('state.busy'),false);
 }
});
test('successful upload opens its named session and retains the previous draft',async()=>{
 const {run}=harness('analysis');
 run(`el('videoFile').files=[{name:'test.mp4',size:100}];state.dirty=true;sendVideo=async()=>({ok:true,pitch:{gamePk:'local',playId:'new',dirName:'local_new',isLocal:true,sessionId:'new-session',sessionName:'Bullpen',nFrames:10,fps:30,imgW:1280,imgH:720,frameTimes:Array.from({length:10},(_,i)=>i/30)}});`);
 await run('uploadVideo()');
 assert.equal(run('state.current.playId'),'new');
 assert.equal(run('state.sessionFilter'),'new-session');
 assert.equal(run(`state.drafts.has('1_a')`),true);
 assert.equal(run('state.busy'),false);
 assert.equal(run(`el('importPanel').hidden`),true);
});

test('export downloads a server attachment and offers a direct retry link',async()=>{
 const {run,context}=harness('analysis');const urls=[];
 run(`state.pitches.forEach(p=>p.sessionId='s1');state.labels.set('1_a',{status:'release_selected'});`);
 context.fetch=async url=>{urls.push(url);return {ok:true,text:async()=> 'session_id,status\\ns1,release_selected'}};
 await run('exportSession()');
 assert.deepEqual(urls,['/api/export?session=s1']);
 assert.equal(run(`el('csvFallback').href`),'/api/export?session=s1');
 assert.equal(run(`el('csvFallback').clicked`),true);
 assert.equal(run(`el('csvFallback').hidden`),false);
 run('markDirty();renderWorkflow()');
 assert.equal(run(`el('csvFallback').hidden`),true);
 assert.equal(run(`el('exportStatus').textContent`),'');
});

test('hosted upload sends CSRF and waits for receive plus preparation budget', async()=>{
 const {run,context}=harness('analysis',true);
 let upload;
 context.XMLHttpRequest=class {
   constructor(){upload=this;this.upload={};this.headers={};this.status=200;this.responseText=JSON.stringify({ok:true,pitch:{playId:'uploaded'}});}
   open(method,url){this.method=method;this.url=url;}
   setRequestHeader(name,value){this.headers[name]=value;}
   send(file){this.file=file;this.onload();}
 };
 const result=await run(`sendVideo({name:'test.mp4'}, {filename:'test.mp4'})`);
 assert.equal(result.pitch.playId,'uploaded');
 assert.equal(upload.method,'POST');
 assert.equal(upload.headers['X-CSRF-Token'],'test-csrf-token');
 assert.equal(upload.headers['Content-Type'],undefined);
 assert.equal(upload.url,'/api/upload');
 assert.equal(upload.file.fields[0][0],'video');
 assert.equal(upload.timeout,900000);
 assert.ok(upload.timeout > 600000 + 100000);
});

test('hosted examples load all five pitches with navigation and reuse saved progress', async()=>{
 const {run,elements}=harness('analysis',true,true);
 run(`let asked=[];const examples=Array.from({length:5},(_,i)=>({gamePk:'local',playId:'ex'+i,dirName:'local_ex'+i,sessionId:'vid'+i,pitcherName:'Pitcher '+i,example:'mlb-'+i,nFrames:10,fps:30,imgW:1280,imgH:720,isLocal:true,uiTestBatch:true,armAngle:38.2}));request=async(url,body)=>{asked.push({url,body});return {ok:true,pitches:examples,pitch:examples[0]}};showPitch=async i=>{state.shown=i;state.current=state.pitches[i];renderQueue();renderSession()};`);
 await run('openExample()');
 assert.deepEqual(run('asked[0].url'),'/api/example');
 assert.equal(run('state.pitches.length'),7);
 assert.equal(run('state.shown'),2);
 assert.equal(run('state.sessionFilter'),'');
 assert.equal(elements.get('queueFilter').value,'batch');
 assert.equal(run('visiblePitches().length'),5);
 assert.equal(elements.get('sessionPitches').children.length,5);
 assert.equal(elements.get('pitchSelect').children.length,5);
 assert.match(elements.get('sessionSummary').textContent,/5 pitches/);
 for (let i=0;i<5;i++) {
   elements.get('sessionPitches').children[i].handlers.click();
   assert.equal(run('state.current.playId'),'ex'+i);
   assert.equal(elements.get('sessionPitches').children.length,5);
 }
 run(`state.labels.set('local_ex0',{status:'skipped'});state.drafts.set('local_ex4',{dirty:true});`);
 // Reopening resumes an unfinished pitch without duplicating any clips or losing drafts.
 await run('openExample()');
 assert.equal(run('state.pitches.length'),7);
 assert.equal(run('state.shown'),3);
 assert.equal(run(`state.drafts.has('local_ex4')`),true);
 assert.equal(elements.get('startExample').disabled,false);
});
test('hosted example failure explains itself and leaves the button usable', async()=>{
 const {run,elements}=harness('analysis',true,true);
 run(`request=async()=>{throw new Error('No practice clip is installed on this server.')};`);
 await run('openExample()');
 assert.match(elements.get('intro').textContent,/No practice clip is installed/);
 assert.equal(elements.get('startExample').disabled,false);
 assert.equal(run('state.busy'),false);
 assert.equal(run('state.pitches.length'),2);
});
test('the example tile is offered only when the hosted page advertises a clip', ()=>{
 for (const [advertised,hidden] of [[true,false],[false,true]]) {
   const {run,elements}=harness('analysis',true,advertised);
   run('configureWorkspace()');
   assert.equal(elements.get('startExample').hidden,hidden);
 }
});

test('the Statcast reference stays hidden for uploads and is revealed on the example', ()=>{
 const {run,elements}=harness('analysis',true,true);
 run(`state.mode='points';state.reviewing=true;state.points={shoulder:{x:100,y:200},offShoulder:{x:120,y:200},elbow:{x:150,y:180},wrist:{x:200,y:150},ball:{x:220,y:140}};`);
 run(`state.current={...state.pitches[0],armAngle:38.2};renderWorkflow();`);
 assert.equal(elements.get('truthDetails').hidden,true,'an uploaded pitch has no reference to reveal');
 assert.equal(elements.get('reviewBallLabel').textContent,'MEASURED SHOULDER \u2192 BALL','an upload leads with its own measurement');
 run(`state.current={...state.pitches[0],armAngle:38.2,example:'mlb-example'};renderWorkflow();renderMeta();renderMeasure();`);
 assert.equal(elements.get('truthDetails').hidden,false);
 assert.equal(elements.get('practiceComparison').hidden,false);
 assert.equal(elements.get('sanityWarn').hidden,true);
 // On an MLB clip the reference leads and the measured angle moves below it.
 assert.equal(elements.get('reviewBallLabel').textContent,'STATCAST REFERENCE');
 assert.equal(elements.get('reviewBallAngle').textContent,'38.2\u00b0');
 assert.equal(elements.get('pTruthLabel').textContent,'Measured shoulder \u2192 ball');
 assert.equal(elements.get('pTruth').textContent,run(`pixelAngle('ball').toFixed(1)+'\u00b0'`));
});
test('training keeps the measurement in the lead so the reference cannot anchor a label', ()=>{
 const {run,elements}=harness();
 run(`state.mode='points';state.reviewing=true;state.points={shoulder:{x:100,y:200},offShoulder:{x:120,y:200},elbow:{x:150,y:180},wrist:{x:200,y:150},ball:{x:220,y:140}};`);
 run(`state.current={...state.pitches[0],armAngle:38.2};renderWorkflow();`);
 assert.equal(elements.get('truthDetails').hidden,false);
 assert.equal(elements.get('reviewBallLabel').textContent,'MEASURED SHOULDER \u2192 BALL');
 assert.equal(elements.get('pTruth').textContent,'38.2\u00b0');
});

const view = run => JSON.parse(run('JSON.stringify(viewTransform())'));
const SAVED_NEIGHBOUR = `{status:'labeled',img_w:1280,shoulder_x:600,shoulder_y:300,off_shoulder_x:640,off_shoulder_y:300,elbow_x:660,elbow_y:330,wrist_x:700,wrist_y:340,ball_x:720,ball_y:330}`;

test('one placed point is enough to zoom, and the view stays inside the frame',()=>{
 const {run}=harness();
 run(`state.mode='points';state.points={shoulder:{x:20,y:20}};fitZoom()`);
 assert.equal(run('state.zoom'),true);
 const v=view(run);
 assert.ok(v.sw<1280,'a single point should zoom');
 assert.ok(Math.abs(v.sw/v.sh-1280/720)<1e-9,'the view keeps the frame aspect ratio');
 assert.ok(v.sx>=0&&v.sy>=0&&v.sx+v.sw<=1280&&v.sy+v.sh<=720,'the view is clamped to the image');
});
test('wheel zoom holds the pixel under the cursor and lets go at the full frame',()=>{
 const {run}=harness();
 run(`state.mode='points';zoomBy(2,{x:900,y:500})`);
 const v=view(run);
 assert.ok(Math.abs(v.sw-640)<1e-9);
 assert.ok(Math.abs((900-v.sx)/v.sw-900/1280)<1e-9,'the anchor keeps its place in the view');
 run(`zoomBy(.1,{x:900,y:500})`);
 assert.equal(run('state.zoom'),false);
 assert.equal(view(run).sw,1280);
});
test('zoom stops at the tightest view instead of running away',()=>{
 const {run}=harness();
 run(`state.mode='points';zoomBy(500,{x:640,y:360})`);
 assert.ok(view(run).sw>=1280/MAX_ZOOM_FOR_TEST(run)-1e-9);
});
const MAX_ZOOM_FOR_TEST = run => run('MAX_ZOOM');
test('clicks are recorded in image pixels while zoomed',()=>{
 const {run,elements}=harness();
 run(`state.mode='points';zoomBy(2,{x:640,y:360})`);
 elements.get('stage').getBoundingClientRect=()=>({left:0,top:0,width:640,height:360});
 const v=view(run);
 const point=JSON.parse(run(`JSON.stringify(eventToImage({clientX:320,clientY:180}))`));
 assert.ok(Math.abs(point.x-(v.sx+v.sw/2))<1e-6,'the middle of the canvas is the middle of the view');
 assert.ok(Math.abs(point.y-(v.sy+v.sh/2))<1e-6);
});
test('the default crop holds where pitchers actually are',()=>{
 const {run}=harness();
 run(`state.mode='points';state.points={};zoomToPitcher()`);
 assert.equal(run('state.zoom'),true);
 const v=view(run);
 // Every labeled point in the repository falls in x .13-.65, y .37-.81.
 const covers=(lo,hi,from,to,size)=>from/size<=lo+1e-9&&to/size>=hi-1e-9;
 assert.ok(covers(.13,.65,v.sx,v.sx+v.sw,1280),'the crop spans the labeled x range');
 assert.ok(covers(.37,.81,v.sy,v.sy+v.sh,720),'the crop spans the labeled y range');
 assert.ok(v.sw<1280,'it is still a zoom');
});
test('a portrait clip is cropped by its own proportions, not the frame size',()=>{
 const {run}=harness();
 run(`state.img={width:1080,height:1920};state.current.imgW=1080;state.current.imgH=1920;state.mode='points';state.points={};zoomToPitcher()`);
 const v=view(run);
 assert.ok(Math.abs(v.sw/v.sh-1080/1920)<1e-9);
 assert.ok(v.sy/1920<=.37+1e-9&&(v.sy+v.sh)/1920>=.81-1e-9);
});
test('placed points outrank the default crop',()=>{
 const {run}=harness();
 run(`state.mode='points';state.points={shoulder:{x:600,y:300},wrist:{x:700,y:340}};zoomToPitcher()`);
 const v=view(run);
 assert.ok(v.sx<=600&&v.sx+v.sw>=700&&v.sy<=300&&v.sy+v.sh>=340,'the placed points are framed');
 assert.ok(v.sw<1280*.52,'an exact answer crops tighter than the general one');
});
test('locking the release frame zooms to the pitcher',async()=>{
 const {run}=harness();
 run(`let zoomCalls=0;zoomToPitcher=()=>{zoomCalls++};request=async()=>({ok:true,undoToken:'t'})`);
 await run('confirmRelease()');
 assert.equal(run('state.mode'),'points');
 assert.equal(run('zoomCalls'),1);
});
test('changing the release frame returns to the full frame',()=>{
 const {run}=harness();
 run(`state.mode='points';state.points={shoulder:{x:600,y:300}};fitZoom();changeRelease()`);
 assert.equal(run('state.zoom'),false);
 assert.equal(run('state.pan'),null);
 assert.equal(view(run).sw,1280);
});

const press = (run,key) => run(`document.handlers.keydown({key:'${key}',target:{tagName:'CANVAS'},preventDefault(){}})`);

test('Z toggles the zoom while placing points and while reviewing them',()=>{
 const {run}=harness();
 run(`state.mode='points';state.points={shoulder:{x:600,y:300},wrist:{x:700,y:340}}`);
 press(run,'z');
 assert.equal(run('state.zoom'),true);
 assert.ok(view(run).sw<1280);
 press(run,'z');
 assert.equal(run('state.zoom'),false);
 assert.equal(view(run).sw,1280);
 run(`state.reviewing=true`);
 press(run,'z');
 assert.equal(run('state.zoom'),true,'review is where points get checked, so zoom belongs there too');
});
test('+ and - step the zoom, and a pose suggestion in flight holds the view still',()=>{
 const {run}=harness();
 run(`state.mode='points'`);
 press(run,'+');
 const zoomed=view(run).sw;
 assert.ok(zoomed<1280);
 press(run,'-');
 assert.ok(view(run).sw>zoomed);
 run(`state.poseBusy=true`);
 const held=view(run).sw;
 press(run,'+');
 assert.equal(view(run).sw,held);
});

test('preview and full-size clicks store identical original coordinates, including zoom',()=>{
 const {run}=harness('analysis',true);
 run(`canvas.getBoundingClientRect=()=>({left:10,top:20,width:640,height:360});
      state.mode='points'; state.img={width:640,height:360}; draw();
      let previewPoint=eventToImage({clientX:330,clientY:200});
      state.points.shoulder=previewPoint; let previewRow=buildRow('release_selected');
      state.img={width:1280,height:720}; draw();
      let fullPoint=eventToImage({clientX:330,clientY:200});
      state.points.shoulder=fullPoint; let fullRow=buildRow('release_selected');`);
 assert.equal(run('previewPoint.x'),640); assert.equal(run('previewPoint.y'),360);
 assert.equal(run('previewRow.shoulder_x'),run('fullRow.shoulder_x'));
 assert.equal(run('previewRow.shoulder_y'),run('fullRow.shoulder_y'));
 run(`state.zoom=true;state.zoomView={sx:400,sy:200,sw:400,sh:225};
      state.img={width:640,height:360};draw();previewPoint=eventToImage({clientX:330,clientY:200});
      state.img={width:1280,height:720};draw();fullPoint=eventToImage({clientX:330,clientY:200});`);
 assert.equal(run('previewPoint.x'),run('fullPoint.x'));
 assert.equal(run('previewPoint.y'),run('fullPoint.y'));
 assert.equal(run('fullPoint.x'),600);
});
test('hosted browsing requests scrub frames, locking fetches only one full frame',async()=>{
 const {run}=harness('analysis',true);
 run(`let imageUrls=[];imageFor=async url=>{imageUrls.push(url);return {width:1280,height:720}};`);
 await run('loadFrame()');
 assert.equal(run(`imageUrls.every(url=>url.includes('size=scrub&privacy=2'))`),true);
 run(`imageUrls=[];request=async()=>({ok:true});`);
 await run('confirmRelease()');
 assert.equal(run('imageUrls.length'),1);
 assert.equal(run(`imageUrls[0].includes('size=full&privacy=2')`),true);
 run('renderThumbs()');
 assert.equal(run(`el('thumbs').children.every(f=>f.children[0].children[0].src.includes('size=scrub&privacy=2'))`),true);
});

test('analysis confirms release without a separate inspection checkbox',async()=>{
 const {run,elements}=harness('analysis');
 run(`let saves=0;request=async()=>{saves++;return {ok:true}};renderWorkflow()`);
 assert.equal(elements.get('confirmRelease').disabled,false);
 await run('goToFrame(3)');
 assert.equal(elements.get('confirmRelease').disabled,false);
 await run('confirmRelease()');
 assert.equal(run('state.mode'),'points');
 assert.equal(run('saves'),1);
});
test('MLB previews begin at frame 180 while saved work and uploads retain their frame',async()=>{
 const {run}=harness('analysis');
 run(`state.current.nFrames=300;state.current.frameTimes=[];`);
 await run('showPitch(0)');
 assert.equal(run('state.frame'),180);
 assert.equal(run(`initialFrame(state.current,{frame_index:175})`),175);
 assert.equal(run(`initialFrame({isLocal:true,startFrame:42})`),42);
 run(`state.current.nFrames=100`);
 await run('showPitch(0)');
 assert.equal(run('state.frame'),99);
});
test('practice error compares the model estimate with Statcast to one decimal',()=>{
 const {run,elements}=harness('analysis');
 run(`state.current.example='rogers';state.current.armAngle=-59.9;state.predictedAngle=-40.6;state.mode='points';state.reviewing=true;renderWorkflow()`);
 assert.equal(elements.get('practiceError').textContent,'Off by 19.3°');
 assert.match(elements.get('practiceTip').textContent,/Check your release frame/);
 run(`state.predictedAngle=-59.9;renderWorkflow()`);
 assert.equal(elements.get('practiceError').textContent,'Off by 0.0°');
 run(`state.predictedAngle=null;renderWorkflow()`);
 assert.equal(elements.get('practiceError').textContent,'Comparison unavailable');
});
test('saved practice offers the next unfinished pitch and consistent exports',async()=>{
 const {run,elements}=harness('analysis',true,true);
 run(`state.pitches.forEach((p,i)=>Object.assign(p,{example:'example-'+i,uiTestBatch:true,sessionId:'session-'+i,pitcherName:i?'Weissert':'Rogers'}));el('queueFilter').value='batch';state.mode='points';state.reviewing=true;state.predictedAngle=20;state.points={shoulder:{x:100,y:200},offShoulder:{x:120,y:200},elbow:{x:150,y:180},wrist:{x:200,y:150},ball:{x:220,y:140}};state.labels.set(keyOf(state.current),buildRow('labeled'));renderWorkflow()`);
 assert.equal(elements.get('savePoints').hidden,true);
 assert.equal(elements.get('nextReviewPitch').hidden,false);
 assert.match(elements.get('nextReviewPitch').textContent,/Weissert/);
 assert.equal(elements.get('workspaceBadge').textContent,'MLB PRACTICE');
 assert.equal(elements.get('exportSession').textContent,elements.get('downloadResult').textContent);
 assert.doesNotMatch(elements.get('stepHelp').textContent,/another pitch from this video/);
 run(`let opened=-1;showPitch=async index=>{opened=index}`);
 await run('continueAnalysis()');
 assert.equal(run('opened'),1);
 run(`state.labels.set(keyOf(state.pitches[1]),buildRow('labeled'));renderWorkflow()`);
 assert.equal(elements.get('nextReviewPitch').hidden,true);
 assert.match(elements.get('reviewSaved').textContent,/All pitches/);
});
test('unreviewed optional quality rows stay out of review summary',()=>{
 const {run,elements}=harness('analysis');
 run('renderReview()');
 assert.deepEqual(elements.get('reviewSummary').children.filter((_,i)=>i%2===0).map(e=>e.textContent),['Release','Points']);
 run(`state.releaseQuality='unclear';state.pointQuality='uncertain';renderReview()`);
 assert.deepEqual(elements.get('reviewSummary').children.filter((_,i)=>i%2===0).map(e=>e.textContent),['Release','Points','Timing','Clarity']);
});
test('five rapid nearby clicks place five landmarks instead of grabbing an earlier point',()=>{
 const {run,elements}=harness('analysis');
 run(`state.mode='points'`);
 const stage=elements.get('stage');
 stage.getBoundingClientRect=()=>({left:0,top:0,width:1280,height:720});
 stage.setPointerCapture=()=>{};
 for(let i=0;i<5;i++) {
   stage.handlers.pointerdown({clientX:100+i*2,clientY:200,button:0,pointerId:1});
   stage.handlers.pointerup({pointerId:1});
 }
 assert.equal(run('Object.keys(state.points).length'),5);
 assert.equal(run('state.points.ball.x'),108);
 assert.equal(elements.get('reviewPoints').disabled,false);
 stage.handlers.pointerdown({clientX:100,clientY:200,button:0,pointerId:1});
 assert.equal(run('state.drag'),'shoulder','completed points still support dragging');
});
