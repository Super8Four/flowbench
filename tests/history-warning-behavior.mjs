// Execute the real history controller with a disposable API/DOM fixture.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

class Element {
  constructor(value = '') { this.value = value; this.textContent = ''; this.innerHTML = ''; this.dataset = {}; this.classList = {add() {}, remove() {}, toggle() {}}; }
  addEventListener() {}
  setAttribute() {}
}
const elements = new Map(['history-body', 'history-search', 'history-count', 'engine-badge', 'saved-count', 'message'].map((id) => [id, new Element()]));
globalThis.document = {getElementById: (id) => elements.get(id) || null, querySelectorAll: () => [], hidden: false, body: {dataset: {page: 'history'}}};
globalThis.window = {addEventListener() {}};
globalThis.location = {search: '', pathname: '/history'};
// Disable polling/abort timers. API responses below are immediately resolved.
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};

const settings = {label: 'Historical fixture', target: '127.0.0.1', role: 'client', protocol: 'tcp', mode: 'upload', streams: 1, port: 5201, duration: 10, notes: '', tags: {}};
const invalid = {id: '1'.repeat(32), created_at: '2026-01-01T00:00:00Z', status: 'completed', settings,
  metrics: {upload_mbps: 2000}, measurement_warnings: ['Invalid stored identity <svg onload="fixture">'],
  overview: {status: 'unavailable', trend: [], runtime_seconds: null,
    note: 'Stored measurement identities are invalid. Derived interval charts are unavailable; raw captured evidence is preserved.'}};
const valid = {id: '2'.repeat(32), created_at: invalid.created_at, status: 'completed', settings: {...settings, label: 'Valid fixture'},
  metrics: {upload_mbps: 1000}, overview: {max_upload_mbps: 3000, runtime_seconds: 10,
    trend: [{seconds: 1, direction: 'upload', mbps: 1000}, {seconds: 10, direction: 'upload', mbps: 3000}]}};
const status = {iperf: {available: true, version: 'fixture'}, storage_error: null, saved_runs: 2,
  storage: 'disposable-fixture', database: 'disposable-fixture', active_run: null, listener: null};
const requests = [];
globalThis.fetch = async (path) => {
  requests.push(path);
  const data = path === '/api/status' ? status : path === '/api/runs' ? {runs: [invalid, valid], active_run: null}
    : path === '/api/projects' ? {projects: []} : null;
  assert.notEqual(data, null, `Unexpected request: ${path}`);
  return {ok: true, status: 200, json: async () => data};
};
const moduleUrl = process.argv[2] ? pathToFileURL(process.argv[2]).href : new URL('../app/static/app.js', import.meta.url).href;
await import(moduleUrl);
for (let attempt = 0; attempt < 20 && !elements.get('history-body').innerHTML; attempt++) {
  await new Promise((resolve) => setImmediate(resolve));
}
const rendered = elements.get('history-body').innerHTML;
assert.match(rendered, /Historical fixture/);
assert.match(rendered, /Valid fixture/);
assert.equal((rendered.match(/<tr>/g) || []).length, 2, 'The invalid historical row must remain visible.');
assert.equal((rendered.match(/role="note" aria-label="Stored measurement warning"/g) || []).length, 1);
assert.match(rendered, /Interval measurements unavailable/);
assert.match(rendered, /raw captured evidence is preserved/);
assert.match(rendered, /Invalid stored identity &lt;svg onload=&quot;fixture&quot;&gt;/);
assert.ok(!rendered.includes('<svg onload="fixture">'), 'Warning text must not become executable markup.');
assert.match(rendered, /Avg 2 Gbps/, 'Valid whole-run summary must remain visible for the affected row.');
assert.match(rendered, /Interval evidence unavailable/);
assert.match(rendered, /3 Gbps/, 'Unaffected interval peaks must still render.');
assert.equal(elements.get('history-count').textContent, '2 of 2 saved sessions');
assert.ok(requests.includes('/api/runs'));
assert.equal(elements.get('message').textContent, '', 'A bad historical row must not break the entire page.');
console.log('History warning UI behavior passed: visible escaped warning, unavailable intervals, valid summaries and unaffected rows retained.');
