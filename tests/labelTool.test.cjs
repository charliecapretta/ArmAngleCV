const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../labelTool.js'),'utf8').replace(/boot\(\);\s*$/, '');
function harness(workspace='training', hosted=false, example=false) {
  const elements = new Map();
  class Element {
    constructor(tag='DIV') { this.tagName=tag; this.dataset={}; this.value='all'; this.width=1280; this.height=720; this.hidden=false; this.children=[]; this.style={}; this.classList={add(){},remove(){}}; }
    addEventListener() {} replaceChildren(...x) { this.children=x; } append(...x) { this.children.push(...x); }
    click() { this.clicked=true; }
    getContext() { return new Proxy({}, {get:()=>()=>{}}); } focus() {}
  }
  const context = vm.createContext({console,Map,Math,Number,JSON,Error,Promise,performance,URLSearchParams,
    setTimeout:()=>1,clearTimeout(){},fetch:async()=>({ok:true,json:async()=>({ok:true,undoToken:'token'})}),
    document:{documentElement:{dataset:{workspace,hosted:String(hosted),example:example?'true':undefined}},querySelector:()=>({content:'test-csrf-token'}),getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},createElement:tag=>new Element(tag.toUpperCase()),addEventListener(){}},
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
 assert.equal(upload.headers['Content-Type'],'video/mp4');
 assert.equal(upload.timeout,300000);
 assert.ok(upload.timeout > 120000 + 100000);
});

test('hosted example is requested from the server and opened in the visitor workspace', async()=>{
 const {run,elements}=harness('analysis',true,true);
 run(`let asked=[];request=async(url,body)=>{asked.push({url,body});return {ok:true,pitch:{gamePk:'local',playId:'ex',dirName:'local_ex',sessionId:'vid',nFrames:10,fps:30,imgW:1280,imgH:720,isLocal:true,uiTestBatch:true,armAngle:38.2}}};showPitch=async i=>{state.shown=i};`);
 await run('openExample()');
 assert.deepEqual(run('asked[0].url'),'/api/example');
 assert.equal(run('state.pitches.length'),3);
 assert.equal(run('state.shown'),2);
 assert.equal(run('state.sessionFilter'),'vid');
 assert.equal(elements.get('queueFilter').value,'local');
 // Asking twice reopens the same practice pitch rather than stacking copies.
 await run('openExample()');
 assert.equal(run('state.pitches.length'),3);
 assert.equal(run('state.shown'),2);
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
 run(`state.current={...state.pitches[0],armAngle:38.2,example:'mlb-example'};renderWorkflow();renderMeta();renderMeasure();`);
 assert.equal(elements.get('truthDetails').hidden,false);
 assert.equal(elements.get('pTruth').textContent,'38.2°');
 assert.match(elements.get('pDelta').textContent,/°/);
});
