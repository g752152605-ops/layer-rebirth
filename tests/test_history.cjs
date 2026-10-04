// Exercise history persistence and failure handling without a browser dependency.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function editor() {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value: 'marketing', hidden: false, disabled: false,
      addEventListener() {}, closest() { return { title: '' }; },
      classList: { toggle() {} },
    });
    return nodes.get(selector);
  };
  let saved;
  let fail = false;
  const context = vm.createContext({
    document: { querySelector: node, querySelectorAll: () => [], addEventListener() {} },
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {}, AbortController,
    fetch: async (url, options) => {
      if (url === '/api/health') return { ok: true, json: async () => ({ ok: true, license: { licensed: false } }) };
      if (fail) { fail = false; throw new Error('Simulated save failure'); }
      saved = JSON.parse(options.body).project;
      return { ok: true, json: async () => ({ ok: true, project: saved }) };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8'), context);
  const run = source => vm.runInContext(source, context);
  run(`renderProject = () => {};
    state.current = { project: { id: 'abc', name: 'Original', layers: [] } };
    resetHistory();`);
  return { run, saved: () => saved, failNext: () => { fail = true; } };
}

test('task selection controls settings and never switches tasks when multiple files are dropped', () => {
  const app = editor();
  app.run(`state.licensed = true; selectedMode = () => 'logo'; updateModeUI();`);
  assert.equal(app.run('els.traceSettings.hidden'), false);
  assert.equal(app.run('els.batchSettings.hidden'), true);
  app.run(`setFiles([{name:'one.png',type:'image/png',size:10},{name:'two.png',type:'image/png',size:10}])`);
  assert.equal(app.run('state.files.length'), 1);
  assert.equal(app.run('selectedMode()'), 'logo');
  app.run(`selectedMode = () => 'format'; updateModeUI();
    setFiles([{name:'one.png',type:'image/png',size:10},{name:'two.png',type:'image/png',size:10}]);`);
  assert.equal(app.run('state.files.length'), 2);
  assert.equal(app.run('els.fileInput.multiple'), true);
  assert.equal(app.run('els.traceSettings.hidden'), true);
  app.run(`selectedMode = () => 'marketing'; updateModeUI();`);
  assert.equal(app.run('state.files.length'), 1);
  assert.equal(app.run('els.fileInput.multiple'), false);
  assert.equal(app.run('els.traceSettings.hidden'), true);
  assert.equal(app.run('els.batchSettings.hidden'), true);
});

test('task picker exposes four explicit tasks with text editing as default', () => {
  const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  const modes = [...html.matchAll(/name="mode" value="([^"]+)"([^>]*)/g)];
  assert.deepEqual(modes.map(match => match[1]), ['marketing', 'logo', 'format', 'cleanup']);
  assert.match(modes[0][2], /checked/);
});

test('clicking canvas text focuses the side editor without adding a covering textarea', () => {
  const app = editor();
  app.run(`state.current.project.layers = [{id:'text-1', kind:'text', content:'Main', style:{}}];
    renderLayers = renderEditor = renderCanvasTextTargets = () => {};
    els.textOverlayLayer.children = [{dataset:{layerId:'text-1'}}];
    els.textContent.focus = () => { els.textContent.focused = true; };
    els.textContent.select = els.textContent.scrollIntoView = () => {};
    beginCanvasTextEdit('text-1');`);
  assert.equal(app.run('els.textContent.focused'), true);
  assert.equal(app.run('state.selectedLayerId'), 'text-1');
});

test('undo and redo persist edits; a new edit discards the redo branch', async () => {
  const app = editor();
  await app.run(`state.current.project.name = 'Changed'; saveProject({collect:false})`);
  assert.equal(app.saved().name, 'Changed');
  await app.run('travelHistory()');
  assert.equal(app.saved().name, 'Original');
  await app.run('travelHistory(true)');
  assert.equal(app.saved().name, 'Changed');
  await app.run('travelHistory()');
  await app.run(`state.current.project.name = 'New branch'; saveProject({collect:false})`);
  assert.equal(app.run('state.redo.length'), 0);
});

test('failed save and failed undo preserve the draft and committed history', async () => {
  const app = editor();
  await app.run(`state.current.project.name = 'Saved'; saveProject({collect:false})`);
  app.failNext();
  assert.equal(await app.run(`state.current.project.name = 'Rejected'; saveProject({collect:false})`), false);
  assert.equal(app.run('state.current.project.name'), 'Rejected');
  assert.equal(app.run('state.undo.length'), 1);
  app.failNext();
  await app.run('travelHistory()');
  assert.equal(app.run('state.current.project.name'), 'Rejected');
  assert.equal(app.run('state.undo.length'), 1);
  await app.run('travelHistory()');
  assert.equal(app.saved().name, 'Saved');
  await app.run('travelHistory()');
  assert.equal(app.saved().name, 'Original');
});

test('typing during a save is coalesced without an old response overwriting the draft', async () => {
  const app = editor();
  app.run(`let pending; api = (path, body) => new Promise(resolve => { pending = {body, resolve}; });`);
  const first = app.run(`state.current.project.name = 'First'; saveProject({collect:false})`);
  app.run(`state.current.project.name = 'Newest'; pending.resolve({project:{...pending.body.project,revision:1}});`);
  await new Promise(setImmediate);
  assert.equal(app.run('state.current.project.name'), 'Newest');
  assert.equal(app.run('pending.body.project.name'), 'Newest');
  assert.equal(app.run('pending.body.base_revision'), 1);
  app.run(`pending.resolve({project:{...pending.body.project,revision:2}})`);
  assert.equal(await first, true);
  assert.equal(app.run('JSON.parse(state.committed).name'), 'Newest');
});

test('history is bounded and resetting a project cannot undo into another project', async () => {
  const app = editor();
  for (let index = 0; index < 35; index++) {
    await app.run(`state.current.project.name = 'Edit ${index}'; saveProject({collect:false})`);
  }
  assert.equal(app.run('state.undo.length'), 30);
  app.run(`state.current = { project: { id:'def', name:'Other', layers:[] } }; resetHistory()`);
  assert.equal(app.run('state.undo.length'), 0);
  assert.equal(app.run('JSON.parse(state.committed).id'), 'def');
});

test('save response during IME composition never rebuilds the editor', async()=>{
 const app=editor();
 app.run(`const workbench={composing:true,status(){}};let renders=0;renderProject=()=>renders++;let finish;api=(path,body)=>new Promise(resolve=>finish=()=>resolve({project:{...body.project,revision:1}}));`);
 const saving=app.run(`state.current.project.name='Before composition';saveProject({collect:false})`);
 app.run('finish()');await saving;
 assert.equal(app.run('renders'),0);
 assert.equal(app.run('state.current.project.revision'),1);
});

test('format and batch import are enabled without any license response',()=>{
 const app=editor();
 app.run(`selectedMode=()=> 'format';updateModeUI();setFiles([{name:'a.png',type:'image/png',size:10},{name:'b.png',type:'image/png',size:10}])`);
 assert.equal(app.run('state.files.length'),2);
 assert.equal(app.run('els.fileInput.multiple'),true);
 const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
 assert(!html.includes('licenseButton'));
 assert(!html.includes('data-paid'));
});
