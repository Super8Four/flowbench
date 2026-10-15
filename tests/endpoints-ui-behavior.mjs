// Execute the endpoint controller with DOM events and an isolated API/clipboard.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
const source = process.argv[2] ? pathToFileURL(join(process.argv[2], 'endpoints.js')).href : new URL('../app/static/endpoints.js', import.meta.url).href;
const {createEndpointPanel, endpointPanelModel, endpointPoint} = await import(source);
const ids = ['test-endpoints', 'endpoint-status', 'endpoint-scope', 'endpoint-provenance', 'endpoint-runtime-host', 'endpoint-runtime-label', 'endpoint-local', 'endpoint-requested-bind', 'endpoint-peer-label', 'endpoint-peer', 'endpoint-requested-target', 'endpoint-namespace-note', 'endpoint-saved-web', 'endpoint-current-web', 'endpoint-current-runtime', 'endpoint-connections-summary', 'endpoint-socket-rows', 'endpoint-control-rows', 'endpoint-destination-rows', 'endpoint-copy-local', 'endpoint-copy-peer', 'endpoint-copy-web', 'endpoint-copy-feedback', 'endpoint-copy-fallback', 'endpoint-copy-value', 'endpoint-connections', 'endpoint-web-context'];
class Element {
  constructor() { this.textContent = ''; this.html = ''; this.writes = 0; this.listeners = {}; this.hidden = false; this.disabled = false; this.open = false; this.value = ''; }
  get innerHTML() { return this.html; }
  set innerHTML(value) { this.html = value; this.writes++; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  async click() { if (!this.disabled) await this.listeners.click?.(); }
  focus() { document.activeElement = this; }
  select() { this.selected = true; }
}
let fields;
const field = (id) => fields.get('endpoint-' + id);
const reset = () => { fields = new Map(ids.map((id) => [id, new Element()])); field('copy-fallback').hidden = true; };
globalThis.document = {getElementById: (id) => fields.get(id) || null};
globalThis.fetch = () => { throw new Error('No actual network permitted.'); };
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const point = (address, port = null, family = 4) => ({address, port, family});
const web = (address) => ({schema_version: 1, source: 'wsgi_remote_addr', observed_at: '2026-10-14T10:00:00Z', peer: point(address)});
const current = {schema_version: 1, execution_hostname: 'current-container', web_request: web('192.0.2.200')};
const socket = (index) => ({reporter: 'local', observer: 'client', socket_id: index, protocol: 'tcp', local: point('fe80::1%eth0', 59611 + index, 6), remote: point('2001:db8::2', 5201, 6), source: 'iperf_start_connected'});
const run = {id: 'a'.repeat(32), settings: {target: 'requested.example', bind: '::', port: 5201, role: 'client'}, initiator_context: web('198.51.100.50'),
  endpoint_view: {schema_version: 1, status: 'captured', provenance: 'captured_archive', execution_hostname: 'saved-container', sockets: [socket(1), socket(2), socket(3), {...socket(4), reporter: 'peer', observer: 'server'}], control_peers: [{reporter: 'local', peer: point('2001:db8::2', 59610, 6)}], destinations: []}};
assert.equal(endpointPoint(point('fe80::1%eth0', 5201, 6)), '[fe80::1%eth0]:5201 · IPv6');
assert.equal(endpointPoint(point('127.0.0.1', true)), '127.0.0.1 · IPv4');
const noEndpoints = {...run, status: 'running', endpoint_view: {...run.endpoint_view, status: 'pending', sockets: [], control_peers: [], destinations: []}};
const pendingModel = endpointPanelModel(noEndpoints, current);
assert.match(pendingModel.note, /Waiting for measured endpoint evidence/); assert.doesNotMatch(pendingModel.note, /Recorded data sockets/);
assert.equal(pendingModel.provenance, 'Recorded run metadata'); assert.equal(pendingModel.copies.peer, '');
for (const state of ['cancelled', 'failed', 'interrupted', 'completed']) {
  const ended = endpointPanelModel({...noEndpoints, status: state, endpoint_view: {...noEndpoints.endpoint_view, status: 'unavailable'}}, current);
  assert.match(ended.note, /No measured data endpoint was captured/); assert.doesNotMatch(ended.note, /Waiting|Recorded data sockets/); assert.deepEqual(ended.local, []); assert.deepEqual(ended.peer, []);
}
assert.match(endpointPanelModel({...noEndpoints, status: 'interrupted'}, current).note, /No measured data endpoint was captured/, 'An archived pending checkpoint must not claim preparation remains active.');
let copies = [], releaseCopy, copyError = false;
reset();
const panel = createEndpointPanel({escape, date: (value) => value + ' UTC', api: async (path) => { assert.equal(path, '/api/context'); return current; }, copy: async (value) => { copies.push(value); if (releaseCopy) await new Promise((resolve) => { releaseCopy = resolve; }); if (copyError) throw new Error('Denied'); }});
await panel.init(); assert.equal(field('runtime-label').textContent, 'Current runtime name'); assert.equal(field('runtime-host').textContent, 'current-container');
panel.render(run);
assert.equal(field('runtime-host').textContent, 'saved-container'); assert.equal(field('runtime-label').textContent, 'Recorded runtime name');
assert.match(field('local').textContent, /1 more/); assert.doesNotMatch(field('local').textContent, /59615/);
assert.match(field('socket-rows').innerHTML, /Peer native report/); assert.match(field('control-rows').innerHTML, /59610/);
assert.doesNotMatch(field('peer').textContent, /59610|requested.example/); assert.match(field('requested-target').textContent, /requested.example/);
assert.match(field('saved-web').textContent, /198.51.100.50/); assert.match(field('current-web').textContent, /192.0.2.200/);
assert.equal(copies.length, 0, 'Rendering must never copy automatically.');
field('connections').open = true; field('copy-peer').focus();
const writes = field('socket-rows').writes; panel.render(structuredClone(run));
assert.equal(field('socket-rows').writes, writes); assert.equal(document.activeElement, field('copy-peer')); assert.equal(field('connections').open, true);
const changed = structuredClone(run); changed.endpoint_view.sockets.push(socket(7)); panel.render(changed);
assert.equal(field('socket-rows').writes, writes + 1); assert.equal(document.activeElement, field('copy-peer')); assert.equal(field('connections').open, true);
await field('copy-peer').click(); assert.match(copies.at(-1), /2001:db8::2/); assert.doesNotMatch(copies.at(-1), /192.0.2.200|198.51.100.50/); assert.match(field('copy-feedback').textContent, /copied/);
copyError = true; await field('copy-local').click();
assert.equal(field('copy-fallback').hidden, false); assert.equal(document.activeElement, field('copy-value')); assert.equal(field('copy-value').selected, true);
assert.match(field('copy-value').value, /saved-container/); assert.match(field('copy-feedback').textContent, /Automatic copy is unavailable/); assert.doesNotMatch(field('copy-feedback').textContent, /copied/);
copyError = false; releaseCopy = true;
const copying = field('copy-peer').click(); assert.equal(field('copy-peer').disabled, true);
panel.loading('b'.repeat(32)); assert.equal(field('copy-peer').disabled, true); assert.equal(field('peer').textContent, 'Unavailable'); assert.equal(field('runtime-host').textContent, 'Unavailable');
releaseCopy(); await copying; releaseCopy = null;
assert.equal(field('copy-feedback').textContent, ''); assert.equal(field('copy-fallback').hidden, true, 'An old asynchronous copy cannot restore stale source text.');
panel.failed('b'.repeat(32)); assert.match(field('status').textContent, /unavailable/); assert.equal(field('copy-local').disabled, true);
const imported = {...run, imported: {filename: 'archive.json'}, endpoint_view: {...run.endpoint_view, execution_hostname: null, provenance: 'imported_archive'}};
panel.render(imported); assert.equal(field('runtime-host').textContent, 'Unavailable'); assert.match(field('provenance').textContent, /Imported archive evidence/); assert.doesNotMatch(field('local').textContent, /current-container/);
const legacy = {...run, endpoint_view: undefined, endpoints: run.endpoint_view, initiator_context: null}; panel.render(legacy);
assert.equal(field('local').textContent, 'Unavailable'); assert.equal(field('peer').textContent, 'Unavailable'); assert.equal(field('copy-local').disabled, true); assert.match(field('scope').textContent, /not recorded/);
const group = {...run, kind: 'matrix-job', endpoint_view: {schema_version: 1, status: 'not_applicable', provenance: 'captured_archive', sockets: [], controls: [], destinations: []}};
panel.render(group); assert.match(field('scope').textContent, /individual child/); assert.equal(field('copy-peer').disabled, true);
const check = (kind, purpose, endpoint, reported_host = null) => ({...run, kind: kind === 'flent' ? 'flent-run' : 'diagnostic-check', check_type: kind === 'flent' ? null : kind, settings: {target: 'query.example', record_type: 'AAAA', address_family: '6'}, endpoint_view: {...run.endpoint_view, sockets: [], control_peers: [], destinations: [{purpose, endpoint, reported_host, transport: kind === 'dns' ? 'udp' : kind === 'loss' ? 'icmp' : null, source: kind === 'dns' ? 'attempt_destination' : kind === 'loss' ? 'pinned_destination' : 'tool_metadata'}]}});
panel.render(check('dns', 'dns_resolver', point('192.0.2.53', 53))); assert.equal(field('peer-label').textContent, 'Resolver used'); assert.match(field('peer').textContent, /192.0.2.53:53/); assert.doesNotMatch(field('peer').textContent, /query.example/); assert.equal(field('local').textContent, 'Unavailable');
panel.render(check('loss', 'icmp_target', point('2001:db8::8', null, 6))); assert.equal(field('peer-label').textContent, 'Pinned destination'); assert.equal(field('local').textContent, 'Unavailable'); assert.match(field('peer').textContent, /2001:db8::8 · IPv6/);
panel.render(check('flent', 'flent_reported_target', null, '<svg onload="bad">')); assert.equal(field('peer-label').textContent, 'Tool-reported target'); assert.match(field('scope').textContent, /not proof/); assert.match(field('destination-rows').innerHTML, /&lt;svg/); assert.doesNotMatch(field('destination-rows').innerHTML, /<svg/);
let resolveContext; reset();
const delayed = createEndpointPanel({escape, date: String, api: () => new Promise((resolve) => { resolveContext = resolve; })});
const initial = delayed.init(); delayed.render(legacy); resolveContext(current); await initial;
assert.equal(field('runtime-host').textContent, 'Unavailable', 'Late current context cannot fill historical runtime.'); assert.match(field('current-web').textContent, /192.0.2.200/);
reset(); const failedContext = createEndpointPanel({escape, date: String, api: async () => { throw new Error('context unavailable'); }});
await failedContext.init(); failedContext.render(run); assert.match(field('current-web').textContent, /unavailable/); assert.equal(field('runtime-host').textContent, 'saved-container'); assert.equal(field('copy-peer').disabled, false);
fields = new Map(); let calls = 0; await createEndpointPanel({api: () => { calls++; }}).init(); assert.equal(calls, 0, 'Other pages have no endpoint request or DOM dependency.');
console.log('Endpoint UI behavior passed: provenance, namespace, typed evidence, copy/fallback, stale selection and polling focus.');
