// Actual listener UI controller, deferred initial settings and stubbed API only.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const fields = new Map();
class Element {
  constructor() { this.value = ''; this.textContent = ''; this.innerHTML = ''; this.disabled = false; this.hidden = false; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.children = []; this.classList = {add() {}, remove() {}, toggle() {}}; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  querySelectorAll() { return []; }
  append(...children) { this.children.push(...children); children.forEach((child) => { if (child.id) fields.set(child.id, child); }); }
  focus() { document.activeElement = this; }
}
for (const id of ['listener-form', 'listener-bind', 'listener-port', 'listener-duration', 'listener-label', 'listener-notes',
  'listener-start', 'listener-stop', 'listener-state', 'listener-active', 'listener-error', 'listener-loading', 'engine-badge', 'saved-count', 'message']) fields.set(id, new Element());
for (const id of ['listener-bind', 'listener-port', 'listener-duration', 'listener-label', 'listener-notes', 'listener-start', 'listener-stop']) fields.get(id).disabled = true;
fields.get('listener-form').setAttribute('aria-busy', 'true');
fields.get('listener-bind').parentElement = new Element();
globalThis.document = {getElementById: (id) => fields.get(id) || null, createElement: () => new Element(), querySelectorAll: () => [], hidden: false, body: {dataset: {page: 'configuration'}}};
globalThis.window = {addEventListener() {}};
globalThis.location = {search: '', pathname: '/configuration'};
let poll, now = 10000;
Date.now = () => now;
globalThis.setTimeout = (callback, delay) => { if (delay === 800) poll = callback; return 0; }; globalThis.clearTimeout = () => {};
let releaseInitial;
const initial = new Promise((resolve) => { releaseInitial = resolve; });
const savedListener = {state: 'stopped', active_run: null, error: null, settings: {bind: '127.0.0.2', port: 5203, max_duration: 30, label: 'Saved listener', notes: 'Saved conditions', enabled: false}};
const status = {iperf: {available: true, version: 'fixture'}, storage_error: null, saved_runs: 0, listener: savedListener};
const requests = [];
let statusCalls = 0;
globalThis.fetch = async (path, options) => {
  requests.push({path, options});
  if (path === '/api/status') { if (++statusCalls === 1) await initial; return {ok: true, status: 200, json: async () => status}; }
  if (path === '/api/projects') return {ok: true, status: 200, json: async () => ({projects: []})};
  assert.equal(path, '/api/listener', 'Unexpected request; this fixture must never start native traffic.');
  return {ok: false, status: 400, json: async () => ({error: 'Listener bind must be a numeric IP address.'})};
};
const moduleUrl = process.argv[2] ? pathToFileURL(process.argv[2]).href : new URL('../app/static/app.js', import.meta.url).href;
await import(moduleUrl);
assert.equal(fields.get('listener-form').dataset.loaded, undefined);
assert.equal(fields.get('listener-start').disabled, true);
// Even a manually dispatched submit cannot bypass the initial settings gate.
await fields.get('listener-form').listeners.submit({preventDefault() {}});
assert.equal(requests.filter((request) => request.path === '/api/listener').length, 0);
releaseInitial();
for (let attempt = 0; attempt < 30 && !fields.get('listener-form').dataset.loaded; attempt++) await new Promise((resolve) => setImmediate(resolve));
assert.equal(fields.get('listener-form').dataset.loaded, 'true');
assert.equal(fields.get('listener-form').getAttribute('aria-busy'), 'false');
assert.equal(fields.get('listener-loading').hidden, true);
assert.equal(fields.get('listener-bind').value, '127.0.0.2', 'Saved settings load before editing is enabled.');
assert.equal(fields.get('listener-port').value, 5203);
for (const id of ['listener-bind', 'listener-port', 'listener-duration', 'listener-label', 'listener-notes', 'listener-start']) assert.equal(fields.get(id).disabled, false);
assert.equal(fields.get('listener-stop').disabled, true, 'A stopped, disabled listener cannot be stopped again.');
fields.get('listener-bind').value = 'invalid address';
await fields.get('listener-form').listeners.submit({preventDefault() {}});
const post = requests.find((request) => request.path === '/api/listener');
assert.equal(JSON.parse(post.options.body).bind, 'invalid address', 'After loading, submit must send the actual edited value.');
assert.equal(fields.get('listener-bind').value, 'invalid address', 'Validation failure preserves the edit.');
assert.equal(fields.get('listener-bind').getAttribute('aria-invalid'), 'true');
assert.match(fields.get('listener-error').textContent, /numeric IP address/);
assert.equal(document.activeElement, fields.get('listener-error'));
assert.equal(fields.get('listener-start').disabled, false);
for (let attempt = 0; attempt < 30 && !poll; attempt++) await new Promise((resolve) => setImmediate(resolve));
assert.equal(typeof poll, 'function'); now += 4000; await poll();
assert.equal(fields.get('listener-bind').value, 'invalid address', 'Later status polling must preserve the post-load edit.');
assert.match(fields.get('listener-error').textContent, /numeric IP address/, 'Polling retains the local validation error.');
console.log('Listener UI behavior passed: initial settings gate, saved settings before enabling, edited invalid bind submitted and retained with accessible validation.');
