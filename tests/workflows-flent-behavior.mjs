// Exercise the actual workflow module through DOM events; no server or traffic.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const moduleUrl = process.argv[2] ? pathToFileURL(process.argv[2]).href : new URL('../app/static/workflows.js', import.meta.url).href;
const {createWorkflows} = await import(moduleUrl);

class Element {
  constructor(value = '') {
    this.value = value;
    this.checked = false;
    this.disabled = false;
    this.dataset = {};
    this.listeners = new Map();
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  replaceChildren() {}
  add() {}
  focus() {}
  reportValidity() { return true; }
  async dispatch(type) { await this.listeners.get(type)?.({target: this}); }
}

const fields = new Map();
for (const name of ['test-form', 'project_id', 'latency', 'endpoint_health', 'mtu',
  'target', 'duration', 'label', 'notes', 'ip_version', 'flent-port', 'flent-confirm',
  'flent-start', 'flent-error', 'flent-editor', ...['site', 'device', 'circuit', 'ticket'].map((key) => 'tag-' + key)]) {
  fields.set(name, new Element());
}
const field = (id) => fields.get(id);
globalThis.document = {getElementById: (id) => fields.get(id) || null, body: {dataset: {page: 'test'}}};
globalThis.window = {addEventListener() {}};
globalThis.location = {search: ''};
globalThis.Option = class { constructor(text, value) { this.text = text; this.value = value; } };
// A missing API stub is a failure; real networking cannot be used accidentally.
globalThis.fetch = () => { throw new Error('Unexpected network access in isolated UI test.'); };

field('target').value = '::1';
field('duration').value = '10';
field('label').value = 'Isolated IPv6 fixture';
field('ip_version').value = '6';
field('flent-port').value = '12865';
field('flent-confirm').checked = true;

const requests = [], starts = [];
const workflow = createWorkflows({
  api: async (path, options) => {
    if (path === '/api/projects' && !options) return {projects: []};
    assert.equal(path, '/api/flent');
    assert.equal(options.method, 'POST');
    const body = JSON.parse(options.body);
    requests.push(body);
    return {id: '1'.repeat(32), kind: 'flent-run', settings: body};
  },
  settings: () => ({target: field('target').value, duration: field('duration').value,
    label: field('label').value, notes: field('notes').value, ip_version: field('ip_version').value,
    project_id: null, tags: {}}),
  state: () => ({engineReady: true, activeId: null, runs: [], capabilities: {}}),
  start: async (run) => { starts.push(run); },
  refresh: async () => {}, message() {}, escape: String, number: String, date: String,
  title: String, download() {}, reportIds: () => [],
});
await workflow.init();
workflow.status({flent: {available: true, version: 'fixture'}});
await field('flent-start').dispatch('click');
assert.equal(requests[0].ip_version, '6', 'The selected IPv6 family must reach the Flent API.');
assert.equal(starts.length, 1);
field('ip_version').value = '4';
field('target').value = '127.0.0.1';
await field('flent-start').dispatch('click');
assert.equal(requests[1].ip_version, '4', 'The selected IPv4 family must reach the Flent API.');

const saved = {kind: 'flent-run', settings: {target: '::1', duration: 7, label: 'Saved fixture',
  notes: '', port: 12866, ip_version: '6', tags: {}}};
assert.equal(workflow.loadFlentSettings(saved), true);
assert.equal(field('ip_version').value, '6', 'Loading saved Flent settings must restore IPv6.');
assert.equal(field('flent-port').value, 12866);
assert.equal(field('flent-confirm').checked, false, 'A rerun must require fresh peer confirmation.');
assert.equal(field('flent-start').disabled, true);
await field('flent-start').dispatch('click');
assert.equal(requests.length, 2, 'Loading settings must not authorize traffic without peer confirmation.');
field('flent-confirm').checked = true;
await field('flent-confirm').dispatch('change');
await field('flent-start').dispatch('click');
assert.equal(requests[2].ip_version, '6', 'The reloaded family must survive a new Flent request.');
workflow.loadFlentSettings({kind: 'flent-run', settings: {target: '127.0.0.1', duration: 5, port: 12865, tags: {}}});
assert.equal(field('ip_version').value, 'auto', 'Legacy saved settings must fall back to automatic family.');
field('flent-confirm').checked = true;
await field('flent-confirm').dispatch('change');
await field('flent-start').dispatch('click');
assert.equal(requests[3].ip_version, 'auto', 'Legacy automatic family must reach the new request explicitly.');
console.log('Flent UI behavior passed: IPv6/IPv4 request, saved-family reload, fresh confirmation and legacy fallback.');
