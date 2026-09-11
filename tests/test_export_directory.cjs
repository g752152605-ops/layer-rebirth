const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('export dialog confirms a chosen path and cancels without exporting', async () => {
  const nodes = new Map();
  function node(selector) {
    if (!nodes.has(selector)) {
      const events = {};
      nodes.set(selector, {
        value: '', open: false, classList: { toggle() {} }, closest: () => ({}),
        addEventListener(name, callback) { events[name] = callback; },
        showModal() { this.open = true; },
        close(value = '') { this.returnValue = value; this.open = false; events.close(); },
        events,
      });
    }
    return nodes.get(selector);
  }
  const context = vm.createContext({
    window: {}, document: { querySelector: node, querySelectorAll: () => [], addEventListener() {} },
    setTimeout: () => 0, clearTimeout() {}, AbortController,
    fetch: async url => ({ ok: true, json: async () => url === '/api/export-directory'
      ? { directory: 'D:\\中文 导出' } : url === '/api/fonts' ? { fonts: [] } : { license: {} } }),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8'), context);
  const pending = vm.runInContext('chooseExportDirectory()', context);
  await new Promise(setImmediate);
  assert.equal(node('#exportDirectoryPath').value, 'D:\\中文 导出');
  assert.equal(node('#browseExportDirectory').hidden, true);
  node('#exportDirectoryPath').value = 'D:\\新位置';
  node('#exportDirectoryForm').events.submit({ preventDefault() {} });
  assert.equal(await pending, 'D:\\新位置');
  const cancelled = vm.runInContext('chooseExportDirectory()', context);
  await new Promise(setImmediate);
  node('#cancelExportDirectory').events.click();
  assert.equal(await cancelled, null);
});
