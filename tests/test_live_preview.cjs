const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function setup(){
 const calls=[];
 const ctx=new Proxy({measureText:t=>({width:t.length*10,actualBoundingBoxLeft:0,actualBoundingBoxRight:t.length*10,actualBoundingBoxAscent:16,actualBoundingBoxDescent:4})},{get:(o,k)=>k in o?o[k]:(...args)=>calls.push([k,...args])});
 const canvas={hidden:true,getContext:()=>ctx};
 const layer={id:'text',kind:'text',content:'Hello',bbox:{x:10,y:20,width:100,height:30},opacity:1,style:{appearance:'editable',font_size:20,background_mode:'auto'}};
 const box=vm.createContext({$:()=>canvas,currentLayer:()=>layer,state:{current:{project:{id:'p',canvas:{width:800,height:400}},preview_url:'saved'}},workbench:{prepareAssets:async()=>({baseImage:{}}),loadImage:async()=>({})},setView:()=>{}});
 vm.runInContext(fs.readFileSync('web/live-preview.js','utf8'),box);
 return {box,canvas,calls,layer,run:s=>vm.runInContext(s,box)};
}
test('draft draws new text locally before any save',async()=>{
 const a=setup();await a.run('livePreview.update()');
 assert.equal(a.canvas.hidden,false);assert(a.calls.some(c=>c[0]==='fillText'&&c[1]==='Hello'));
 assert.equal(a.canvas.width,800);
});
test('late draft assets cannot revive a cleared preview',async()=>{
 const a=setup();a.run('let release;workbench.prepareAssets=()=>new Promise(r=>release=r);let pending=livePreview.update();livePreview.clear();release({baseImage:{}})');
 await a.run('pending');assert.equal(a.canvas.hidden,true);assert.equal(a.calls.length,0);
});
test('saved image loading cannot hide a newer draft',async()=>{
 const a=setup();a.run('let release;workbench.loadImage=()=>new Promise(r=>release=r);livePreview.settled()');
 await a.run('livePreview.update()');a.run('release({})');await new Promise(setImmediate);
 assert.equal(a.canvas.hidden,false);
});
test('transparent background waits for authoritative render instead of erasing source',async()=>{
 const a=setup();a.layer.style.background_mode='transparent';await a.run('livePreview.update()');assert.equal(a.canvas.hidden,true);assert.equal(a.calls.length,0);
});
