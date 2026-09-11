const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(displayWidth=400) {
  const nodes=new Map();
  function node(selector) {
    if(!nodes.has(selector)) {
      const handlers={};
      nodes.set(selector,{value:'marketing',style:{},children:[],dataset:{},
        classList:{toggle(){},add(){},remove(){}},closest:()=>({}),focus(){},
        addEventListener(name,callback){handlers[name]=callback;},removeEventListener(name){delete handlers[name];},
        emit(name,event){return handlers[name]?.(event);},setPointerCapture(){},hasPointerCapture(){return true;},releasePointerCapture(){},
        getBoundingClientRect:()=>({left:0,top:0,width:displayWidth,height:displayWidth/2}),setAttribute(name,value){this[name]=value;},
        replaceChildren(){},querySelectorAll:()=>[],reportValidity:()=>true,
      });
    }
    return nodes.get(selector);
  }
  const context=vm.createContext({document:{querySelector:node,querySelectorAll:()=>[],addEventListener(){}},
    setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},AbortController,
    fetch:async()=>({ok:true,json:async()=>({license:{},fonts:[]})}),
  });
  for(const name of ['app.js','workbench.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',name),'utf8'),context);
  const run=s=>vm.runInContext(s,context);
  run(`state.current={project:{id:'abc',revision:0,canvas:{width:800,height:400},layers:[{id:'text-1',kind:'text',bbox:{x:20,y:20,width:100,height:30},style:{appearance:'original'}}]},preview_url:'preview'};
    state.selectedLayerId='text-1'; state.committed=JSON.stringify(state.current.project);
    collectEditor=renderEditor=renderCanvasTextTargets=()=>{};
    workbench.selection=()=>{};
    workbench.prepareAssets=async()=>({base_url:'base',object_url:'object'});
    let saves=0;saveProject=async()=>{saves++;return true;};setView=v=>state.view=v;
    let target=$('#target'); target.dataset.layerId='text-1'; els.textOverlayLayer.children=[target];`);
  return {node,run};
}

test('drag uses displayed geometry, persists only at release and retains original appearance', async()=>{
  const app=harness();
  app.run(`workbench.startDrag({button:0,clientX:40,clientY:40,pointerId:1,preventDefault(){},stopPropagation(){}},target,currentLayer())`);
  await new Promise(setImmediate);
  app.node('#target').emit('pointermove',{clientX:100,clientY:60,altKey:true});
  assert.equal(app.run('saves'),0);
  assert.equal(app.run('currentLayer().bbox.x'),20);
  await app.node('#target').emit('pointerup',{});
  assert.equal(app.run('saves'),1);
  assert.equal(app.run('currentLayer().bbox.x'),140);
  assert.equal(app.run('currentLayer().bbox.y'),60);
  assert.equal(app.run('currentLayer().style.appearance'),'original');
  assert.equal(app.run('state.view'),'result');
});

test('cancelled gesture and sub-threshold click do not move or save',async()=>{
  const app=harness();
  for(const cancel of [false,true]) {
    app.run(`workbench.startDrag({button:0,clientX:40,clientY:40,pointerId:1,preventDefault(){},stopPropagation(){}},target,currentLayer())`);
    await new Promise(setImmediate);
    app.node('#target').emit('pointermove',{clientX:cancel?80:42,clientY:40,altKey:true});
    await app.node('#target').emit(cancel?'pointercancel':'pointerup',{});
  }
  assert.equal(app.run('saves'),0);
  assert.equal(app.run('currentLayer().bbox.x'),20);
});

test('comparison handle converts pointer position and clamps at image edges',()=>{
  const app=harness();app.run('updateCompare=()=>{};compareAt(200)');
  assert.equal(app.node('#compareRange').value,'50');
  app.run('compareAt(900)');assert.equal(app.node('#compareRange').value,'100');
  app.run('compareAt(-100)');assert.equal(app.node('#compareRange').value,'0');
});

for (const width of [800,1600,3200,1000,1200]) {
  test(`drag coordinate mapping at displayed width ${width}`,async()=>{
    const app=harness(width);
    app.run(`workbench.startDrag({button:0,clientX:40,clientY:40,pointerId:1,preventDefault(){},stopPropagation(){}},target,currentLayer())`);
    app.node('#target').emit('pointermove',{clientX:100,clientY:40,altKey:true});
    await app.node('#target').emit('pointerup',{});
    assert.equal(app.run('currentLayer().bbox.x'),20+60*800/width);
  });
}
