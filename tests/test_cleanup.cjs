const {test}=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
function harness(width=400){
 const nodes=new Map();const keys={};
 const node=s=>{if(!nodes.has(s)){const handlers={};nodes.set(s,{value:s.includes('cleanupBrush')?'erase':s.includes('Radius')?'12':'fit',checked:true,hidden:false,style:{},handlers,addEventListener:(n,cb)=>handlers[n]=cb,removeEventListener:n=>delete handlers[n],setPointerCapture(){},hasPointerCapture:()=>true,releasePointerCapture(){},getBoundingClientRect:()=>({left:0,top:0,width,height:width/2})});}return nodes.get(s)};
 const context=vm.createContext({$:node,structuredClone,document:{addEventListener:(n,cb)=>keys[n]=cb},state:{current:{project:{id:'p',revision:0,canvas:{width:800,height:400},layers:[{style:{cleanup_strokes:[]}}]}}},api:async()=>{throw Error('offline')},setBusy(){},workbench:{},els:{},renderProject(){}});
 vm.runInContext(fs.readFileSync('web/cleanup.js','utf8'),context);
 const run=s=>vm.runInContext(s,context);run('cleanupTool.session={id:"p",revision:0,strokes:[],selectionUndo:[],result:{}};cleanupTool.draw=()=>{}');
 const canvas=node('#cleanupCanvas');
 return{node,run,keys,down:(x,y)=>canvas.handlers.pointerdown({button:0,pointerId:1,clientX:x,clientY:y,currentTarget:canvas,preventDefault(){}}),move:(x,y)=>canvas.handlers.pointermove({clientX:x,clientY:y}),up:()=>canvas.handlers.pointerup()};
}
test('brush maps display to source coordinates and waits for explicit repair',()=>{
 const a=harness();a.down(20,30);a.move(40,50);a.up();
 assert.equal(a.run('cleanupTool.session.strokes.length'),1);assert.deepEqual(JSON.parse(a.run('JSON.stringify(cleanupTool.session.strokes[0].points)')),[[40,60],[80,100]]);
});
test('rectangle stores two corners rather than a dense brush trail',()=>{
 const a=harness();a.node("input[name='cleanupBrush']:checked").value='rect';a.down(10,20);a.move(60,70);a.up();assert.equal(a.run('cleanupTool.session.strokes[0].shape'),'rect');assert.equal(a.run('cleanupTool.session.strokes[0].points.length'),2);
});
test('pointer cancellation does not add a repair stroke',()=>{
 const a=harness();a.down(10,10);a.node('#cleanupCanvas').handlers.pointercancel();assert.equal(a.run('cleanupTool.session.strokes.length'),0);
});
test('failed repair retains all selections for retry',async()=>{
 const a=harness();a.down(20,20);a.up();const before=a.run('JSON.stringify(cleanupTool.session.strokes)');await a.run('cleanupTool.apply()');assert.equal(a.run('JSON.stringify(cleanupTool.session.strokes)'),before);assert.equal(a.run('cleanupTool.busy'),false);assert.match(a.node('#cleanupStatus').textContent,/offline/);
});
