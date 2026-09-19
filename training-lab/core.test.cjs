const test=require('node:test');const assert=require('node:assert/strict');const lab=require('./core.js');
const p={shoulder:{x:100,y:100},off_shoulder:{x:80,y:100},elbow:{x:120,y:90},wrist:{x:140,y:70},ball:{x:150,y:50}};
test('identical repeats have zero error',()=>{const r=lab.compare([{id:'a'}],[{id:'a',pass:1,status:'labeled',points:p},{id:'a',pass:2,status:'labeled',points:p}])[0];assert.equal(r.angleDifference,0);assert.equal(r.meanPointShift,0);});
test('translated points preserve angle while exposing point shift',()=>{const q=Object.fromEntries(Object.entries(p).map(([k,v])=>[k,{x:v.x+3,y:v.y+4}]));const r=lab.compare([{id:'a'}],[{id:'a',pass:1,status:'labeled',points:p},{id:'a',pass:2,status:'labeled',points:q}])[0];assert.equal(r.angleDifference,0);assert.equal(r.meanPointShift,5);});
test('missing and skipped repeats cannot flatter the result',()=>{assert.equal(lab.compare([{id:'a'}],[{id:'a',pass:1,status:'labeled',points:p},{id:'a',pass:2,status:'skipped',points:p}])[0].comparable,false);assert.equal(lab.compare([{id:'b'}],[])[0].comparable,false);assert.equal(lab.median([]),null);});
test('geometry supports left handed and downward arms',()=>{assert.equal(lab.angle(p),45);assert.equal(lab.angle({...p,ball:{x:50,y:50}}),45);assert.equal(lab.angle({...p,ball:{x:150,y:150}}),-45);assert.equal(lab.complete(p),true);assert.equal(lab.complete({...p,ball:null}),false);});

test('coincident shoulder and ball are not a measurable angle',()=>{assert.equal(lab.complete({...p,ball:p.shoulder}),false);});

test('different release frames are excluded from repeatability',()=>{assert.equal(lab.compare([{id:'a'}],[{id:'a',pass:1,status:'labeled',points:p,frameIndex:180},{id:'a',pass:2,status:'labeled',points:p,frameIndex:181}])[0].comparable,false);});
