// Actual typed check, replay and timestamp controllers; no traffic or server.
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
globalThis.crypto ||= webcrypto;
const source = (name) => process.argv[2] ? pathToFileURL(join(process.argv[2], name)).href : new URL('../app/static/' + name, import.meta.url).href;
const {createChecks, renderCheck, renderCheckComparison, renderCheckReportChanges, checkComparable} = await import(source('checks.js'));
const {createAttempt, createRunActions, loadSavedDraft, planDraft, renderLineage, comparisonQuery} = await import(source('run-actions.js'));
const {displayTimestamp} = await import(source('timestamps.js'));
class Field {
  constructor(id = '') { this.id = id; this.value = ''; this.checked = false; this.disabled = false; this.hidden = false; this.type = ''; this.dataset = {}; this.listeners = {}; this.textContent = ''; this.innerHTML = ''; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  async dispatch(name) { return this.listeners[name]?.({target: this}); }
  querySelectorAll() { return [...fields.values()].filter((field) => field.id !== 'test-form'); }
  focus() { document.activeElement = this; }
}
let fields = new Map();
const reset = (ids) => { fields = new Map(ids.map((id) => [id, new Field(id)])); };
const field = (id) => fields.get(id);
let matrixInputs = [];
globalThis.document = {getElementById: (id) => fields.get(id) || null, querySelectorAll: (selector) => matrixInputs.filter((input) => selector.includes(input.name)), body: {dataset: {page: 'test'}}};
globalThis.fetch = () => { throw new Error('No real network is allowed in this fixture.'); };
const context = {escape: (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character])), number: (value) => Number.isFinite(value) ? String(value) : 'Unavailable', date: (value) => displayTimestamp(value, 'utc'), title: (run) => run.settings.label || run.settings.target};
reset(['test-form', 'measurement-type', 'iperf-fields', 'dns-fields', 'loss-fields', 'target', 'label', 'notes', 'dns-target', 'dns-record-type', 'dns-resolver-mode', 'dns-resolver-address', 'dns-custom-resolver', 'dns-resolver-port', 'dns-transport', 'dns-count', 'dns-timeout', 'loss-target', 'loss-family', 'loss-count', 'check-policy', 'check-capability']);
field('measurement-type').value = 'iperf'; field('target').value = 'saved-iperf.example';
const checks = createChecks({...context, controls() {}, typeChanged() {}, metadata: () => ({label: field('label').value, notes: field('notes').value, project_id: null, tags: {site: 'fixture'}, color: '#6db5ff'})});
field('measurement-type').value = 'dns'; await field('measurement-type').dispatch('change'); field('dns-target').value = 'early-draft.test.';
checks.status({checks: {dns: {status: 'available'}, loss: {status: 'unavailable', note: 'No native ping fixture'}}});
checks.show(checks.type()); assert.equal(checks.type(), 'dns'); assert.equal(field('dns-target').value, 'early-draft.test.', 'Finishing capability loading must preserve an early tool choice and edited draft.');
checks.show('dns'); field('dns-target').value = 'example.test.'; field('dns-record-type').value = 'TXT'; field('dns-resolver-mode').value = 'custom'; field('dns-resolver-address').value = '::1'; field('dns-resolver-port').value = '5353'; field('dns-transport').value = 'tcp'; field('dns-count').value = '3'; field('dns-timeout').value = '500'; field('label').value = 'DNS draft';
assert.equal(checks.ready(), true);
assert.equal(field('iperf-fields').disabled, true); assert.equal(field('dns-fields').disabled, false);
const settings = checks.settings();
assert.deepEqual({...settings, label: undefined, notes: undefined, project_id: undefined, tags: undefined, color: undefined}, {check_type: 'dns', target: 'example.test.', record_type: 'TXT', resolver_mode: 'custom', resolver_address: '::1', resolver_port: 5353, transport: 'tcp', count: 3, timeout_ms: 500, label: undefined, notes: undefined, project_id: undefined, tags: undefined, color: undefined});
checks.show('loss'); field('loss-target').value = '127.0.0.1'; field('loss-family').value = '4'; field('loss-count').value = '4'; field('label').value = 'ICMP draft';
assert.equal(checks.ready(), false, 'Missing tools disable only their own check type.');
checks.show('iperf'); assert.equal(field('target').value, 'saved-iperf.example');
checks.show('dns'); assert.equal(field('dns-target').value, 'example.test.'); assert.equal(field('label').value, 'DNS draft'); assert.equal(field('dns-record-type').value, 'TXT');
checks.show('loss'); assert.equal(field('loss-target').value, '127.0.0.1'); assert.equal(field('label').value, 'ICMP draft');
checks.load({...settings, resolver_mode: 'system', resolver_address: null}); assert.equal(field('dns-resolver-address').disabled, true); assert.equal(checks.settings().resolver_address, null);

reset(['check-results', 'check-result-heading', 'check-result-status', 'check-result-note', 'check-result-content']);
const stamp = '2026-10-14T12:00:00Z';
const dns = {id: '1'.repeat(32), kind: 'diagnostic-check', check_type: 'dns', status: 'completed', settings: {target: 'example.test.', label: 'DNS fixture', record_type: 'TXT'}, result: {status: 'captured', started_at: stamp, finished_at: stamp, summary: {responses: 1, requested_queries: 1, attempted_queries: 1, transport_attempts: 2, outcomes: {answer: 1}}, attempts: [{query_index: 1, status: 'answer', rcode: 'NOERROR', resolver_address: '::1', transport: 'tcp', started_at: stamp, finished_at: stamp, question: {name: 'example.test.', type: 'TXT', class: 'IN'}, answers: [{name: 'example.test.', type: 'TXT', class: 'IN', ttl: 0, rdata: '<svg onload="fixture">'}]}]}};
renderCheck(dns, context); assert.equal(field('check-results').hidden, false); assert.match(field('check-result-content').innerHTML, /NOERROR/); assert.match(field('check-result-content').innerHTML, /&lt;svg onload=&quot;fixture&quot;&gt;/); assert.ok(!field('check-result-content').innerHTML.includes('<svg onload='));
const loss = {...dns, check_type: 'loss', status: 'cancelled', result: {status: 'cancelled', summary: {requested: 10, attempted: 1, transmitted: null, confirmed_transmitted: 1, echo_replies: 0, confirmed_nonresponses: 0, indeterminate_attempts: 1, nonresponse_percent: null}, attempts: [{sequence: 1, status: 'reply', rtt_upper_bound_ms: 1, transmitted: 1, echo_replies: 1}]}};
renderCheck(loss, context); assert.match(field('check-result-status').textContent, /cancelled/); assert.match(field('check-result-content').innerHTML, /&lt;1 ms/); assert.match(field('check-result-status').textContent, /percentage unavailable/); assert.ok(!field('check-result-status').textContent.includes('0%'));
assert.equal(checkComparable(loss), true); assert.equal(checkComparable({...loss, status: 'running'}), false);
assert.equal(checkComparable({...dns, result: {summary: dns.result.summary}, check_comparable: true}), true, 'Compact history eligibility must not require raw attempt bodies.');
assert.equal(checkComparable({...dns, check_comparable: false}), false, 'Canonical ineligibility must override a historical result envelope.');
const comparison = renderCheckComparison({check_type: 'dns', runs: [dns, {...dns, status: 'failed'}], comparisons: [{settings_differences: ['transport'], caveats: ['Partial result'], rows: [{metric: 'responses', label: 'DNS responses', unit: 'responses', before: 1, after: null, delta: null}], answer_sets: {added: null, removed: []}}]}, context);
assert.match(comparison, /DNS responses/); assert.match(comparison, /Unavailable: no complete captured answer set/); assert.match(comparison, /Captured empty set/); assert.ok(!comparison.includes('Mbps'));
const reportChanges = renderCheckReportChanges({report_type: 'diagnostic-check', comparisons: [{metrics: [{metric: 'Mean ICMP echo RTT', unit: 'ms', before: 10, after: 8, delta: -2}]}]}, context);
assert.match(reportChanges, /-2 ms/); assert.ok(!reportChanges.includes('-2%') && !reportChanges.includes('Change %'));

// A controlled DOM materializes disclosure/focus nodes from the actual
// renderer's markup. Replacing HTML detaches the old nodes, as in a browser.
class EvidenceDOM {
  constructor() { this.html = ''; this.writes = 0; this.details = []; this.focusable = []; }
  get innerHTML() { return this.html; }
  set innerHTML(value) {
    if (this.contains(document.activeElement)) document.activeElement = document.body;
    this.html = value; this.writes++;
    this.details = [...value.matchAll(/<details\b[^>]*data-check-key="([^"]+)"/g)].map(([, key]) => ({dataset: {checkKey: key}, open: false}));
    this.focusable = [...value.matchAll(/<(?:summary|pre)\b[^>]*data-check-focus="([^"]+)"/g)].map(([, key]) => ({dataset: {checkFocus: key}, scrollTop: 0, scrollLeft: 0, closest() { return this; }, focus() { document.activeElement = this; }}));
  }
  contains(element) { return this.focusable.includes(element); }
  querySelectorAll(selector) { return selector.startsWith('details') ? this.details : this.focusable; }
  detail(key) { return this.details.find((row) => row.dataset.checkKey === key); }
  control(key) { return this.focusable.find((row) => row.dataset.checkFocus === key); }
}
const evidence = new EvidenceDOM(); fields.set('check-result-content', evidence);
const observed = structuredClone(dns); observed.result.resolution = {status: 'captured'}; observed.result.attempts[0].sequence = 1;
renderCheck(observed, context);
for (const key of ['resolution', 'attempt:1', 'native:1']) evidence.detail(key).open = true;
const nativeFocus = evidence.control('native:1:raw'); nativeFocus.scrollTop = 37; nativeFocus.scrollLeft = 12; nativeFocus.focus();
const appended = structuredClone(observed); appended.result.elapsed_seconds = 2; appended.result.attempts.push({...observed.result.attempts[0], sequence: 2, query_index: 2});
renderCheck(appended, context);
for (const key of ['resolution', 'attempt:1', 'native:1']) assert.equal(evidence.detail(key).open, true, `${key} remains open after a changed checkpoint.`);
assert.equal(evidence.detail('attempt:2').open, false); assert.equal(evidence.detail('native:2').open, false, 'New evidence never opens itself.');
assert.notEqual(document.activeElement, nativeFocus, 'The fixture actually detached the old focused native node.');
assert.equal(document.activeElement, evidence.control('native:1:raw')); assert.equal(document.activeElement.scrollTop, 37); assert.equal(document.activeElement.scrollLeft, 12);
const stableNode = document.activeElement, stableWrites = evidence.writes; renderCheck(appended, context); assert.equal(document.activeElement, stableNode); assert.equal(evidence.writes, stableWrites);
field('check-result-heading').focus(); appended.result.elapsed_seconds = 3; renderCheck(appended, context); assert.equal(document.activeElement, field('check-result-heading'), 'A changed checkpoint must never steal outside focus.');
evidence.control('native:1:summary').focus(); appended.result.attempts.shift(); renderCheck(appended, context); assert.equal(document.activeElement, document.body, 'Removed keys do not redirect focus to another row.');
renderCheck({...appended, id: 'different-check'}, context); assert.ok(evidence.details.every((row) => !row.open), 'Disclosure state never crosses into another check.');

class LinkDOM {
  constructor() { this.html = ''; this.writes = 0; this.links = []; }
  get innerHTML() { return this.html; }
  set innerHTML(value) { this.html = value; this.writes++; this.links = [...value.matchAll(/<a href="([^"]+)"/g)].map(([, href]) => ({getAttribute: () => href.replaceAll('&amp;', '&'), focus() { document.activeElement = this; }})); }
  contains(element) { return this.links.includes(element); }
  querySelectorAll() { return this.links; }
}
const lineage = new LinkDOM(); fields.set('run-lineage', lineage);
const originalRun = {...dns, id: 'a'.repeat(32)}, repeatedRun = {...dns, id: 'b'.repeat(32), lineage: {source_run_id: 'a'.repeat(32), external_source_id: null}};
renderLineage(repeatedRun, [originalRun, repeatedRun], context);
assert.match(lineage.innerHTML, /Original attempt/); assert.match(lineage.innerHTML, /Saved history/);
const compareLink = lineage.links.find((link) => link.getAttribute('href').startsWith('/comparison?'));
assert.deepEqual(new URL(compareLink.getAttribute('href'), 'http://fixture').searchParams.getAll('id'), [originalRun.id, repeatedRun.id]);
lineage.links[0].focus(); const originalLinkNode = document.activeElement, lineageWrites = lineage.writes;
renderLineage({...repeatedRun, event_count: 5}, [originalRun, repeatedRun], context); assert.equal(lineage.writes, lineageWrites); assert.equal(document.activeElement, originalLinkNode, 'Unchanged lineage polling preserves the actual link node.');
renderLineage({...repeatedRun, status: 'running'}, [originalRun, repeatedRun], context); assert.notEqual(document.activeElement, originalLinkNode); assert.equal(document.activeElement.getAttribute('href'), '/?run=' + originalRun.id, 'Changing comparison eligibility restores the same original link when it still exists.');
renderLineage({...repeatedRun, kind: 'matrix-job'}, [originalRun, repeatedRun], context); assert.match(lineage.innerHTML, /Open individual children/); assert.ok(!lineage.innerHTML.includes('Compare with original'));
renderLineage({...repeatedRun, lineage: {external_source_id: '<external fixture>'}}, [originalRun], context); assert.match(lineage.innerHTML, /&lt;external fixture&gt;/); assert.equal(lineage.links.length, 1, 'External ancestry gets only saved history, never a guessed local original link.');
const params = new URLSearchParams([['id', originalRun.id], ['id', repeatedRun.id]]);
assert.deepEqual(comparisonQuery(params, [originalRun, repeatedRun]), {ids: [originalRun.id, repeatedRun.id], error: ''});
assert.match(comparisonQuery(new URLSearchParams('id=invalid&id=also-invalid'), [originalRun, repeatedRun]).error, /valid saved attempts/);
assert.match(comparisonQuery(params, [originalRun]).error, /unavailable/);
assert.match(comparisonQuery(params, [originalRun, {...repeatedRun, check_type: 'loss'}]).error, /mixes measurement types/);
assert.equal(comparisonQuery(new URLSearchParams(), [originalRun]), null, 'Saved preference fallback is permitted only when no explicit IDs were supplied.');

// Lost-response retries retain the exact deep snapshot and UUID, whereas a new
// action gets a new UUID. The replay envelope is unwrapped before selection.
reset(['retry']); let requests = [], starts = [], fail = true;
const attempt = createAttempt({retryButton: field('retry'), status() {}, api: async (path, options) => { requests.push({path, body: JSON.parse(options.body)}); if (fail) { fail = false; throw new Error('Lost response'); } return {run: {id: 'accepted'}, replayed: true}; }, start: async (run) => starts.push(run)});
const original = {settings: {duration: 2}, protocols: ['tcp']};
await assert.rejects(attempt.run({path: '/api/matrices', body: original})); original.protocols.push('udp'); original.settings.duration = 60;
assert.equal(field('retry').hidden, false); await field('retry').dispatch('click'); await new Promise((resolve) => setImmediate(resolve));
assert.deepEqual(requests[0], requests[1]); assert.deepEqual(requests[1].body.protocols, ['tcp']); assert.equal(requests[1].body.settings.duration, 2); assert.match(requests[1].body.request_id, /^[a-f0-9]{32}$/); assert.equal(starts[0].id, 'accepted');
await attempt.run({path: '/api/matrices', body: original}); assert.notEqual(requests[2].body.request_id, requests[0].body.request_id);

// Exercise every plan kind via the real action event handlers, with fresh
// Flent/server confirmations and exact reviewed fingerprints.
const actionIds = ['run-again-button', 'rerun-button', 'run-again-status', 'run-again-retry', 'run-again-review', 'run-again-netserver', 'run-again-incoming', 'run-again-flent-confirmation', 'run-again-incoming-confirmation', 'run-again-config', 'run-again-confirm', 'run-again-cancel'];
for (const kind of ['iperf-run', 'protocol-job', 'matrix-job', 'flent-run', 'diagnostic-check', 'incoming']) {
  reset(actionIds); let posts = [], loaded = [], selected = {id: 'a'.repeat(32)};
  const plan = {kind: kind === 'incoming' ? 'iperf-run' : kind, check_type: kind === 'diagnostic-check' ? 'dns' : null, config: kind === 'incoming' ? {role: 'server', bind: '127.0.0.1', idle_timeout: 30} : {target: 'fixture.test.'}, source_fingerprint: 'f'.repeat(64), required_confirmations: kind === 'incoming' ? ['confirm_wait_for_client'] : kind === 'flent-run' ? ['confirm_netserver'] : []};
  createRunActions({selected: () => selected, active: () => null, load: (draft) => loaded.push(draft), start: async () => {}, api: async (path, options) => { if (!options) return plan; posts.push(JSON.parse(options.body)); return {run: {id: 'new'}, replayed: false}; }});
  await field('rerun-button').dispatch('click'); assert.equal(loaded[0].kind, plan.kind); assert.equal(posts.length, 0, 'Load settings never starts traffic.');
  field('run-again-netserver').checked = true; field('run-again-incoming').checked = true;
  await field('run-again-button').dispatch('click');
  if (plan.required_confirmations.length) { assert.equal(posts.length, 0); assert.equal(field('run-again-netserver').checked, false); await field('run-again-confirm').dispatch('click'); assert.equal(posts.length, 0); field(kind === 'incoming' ? 'run-again-incoming' : 'run-again-netserver').checked = true; await field('run-again-confirm').dispatch('click'); }
  assert.equal(posts.length, 1, kind); assert.equal(posts[0].source_fingerprint, plan.source_fingerprint); assert.match(posts[0].request_id, /^[a-f0-9]{32}$/); if (kind === 'incoming') assert.equal(posts[0].confirm_wait_for_client, true); if (kind === 'flent-run') assert.equal(posts[0].confirm_netserver, true);
}

reset(['target', 'matrix-streams', 'matrix-bandwidth', 'matrix-editor']);
matrixInputs = [{name: 'matrix-protocol', value: 'tcp'}, {name: 'matrix-protocol', value: 'udp'}, {name: 'matrix-mode', value: 'upload'}, {name: 'matrix-mode', value: 'download'}];
const calls = []; const handlers = Object.fromEntries(['tool', 'check', 'iperf', 'metadata', 'flent'].map((name) => [name, (...args) => calls.push([name, ...args])]));
loadSavedDraft(planDraft({kind: 'matrix-job', config: {settings: {duration: 3, bandwidth: '2M'}, protocols: ['udp'], modes: ['download'], streams: [1, 4]}}), handlers);
assert.equal(field('matrix-streams').value, '1,4'); assert.equal(field('matrix-bandwidth').value, '2M'); assert.deepEqual(matrixInputs.map((input) => Boolean(input.checked)), [false, true, false, true]);
loadSavedDraft(planDraft({kind: 'protocol-job', config: {protocol: 'both'}}), handlers); assert.equal(calls.at(-1)[1].protocol, 'both');
loadSavedDraft(planDraft({kind: 'flent-run', config: {netserver_port: 12866}}), handlers); assert.equal(calls.at(-1)[1].settings.port, 12866);
loadSavedDraft(planDraft({kind: 'diagnostic-check', check_type: 'loss', config: {target: '::1', address_family: '6', count: 4}}), handlers); assert.equal(calls.at(-2)[1].address_family, '6'); assert.equal(calls.at(-2)[1].count, 4);
loadSavedDraft(planDraft({kind: 'iperf-run', config: {role: 'server', idle_timeout: 30}}), handlers); assert.equal(calls.at(-1)[2], true);

// Two instants with the same fall-back wall time retain different offsets.
const summer = displayTimestamp('2024-10-27T00:30:00Z', 'local', 'Europe/Oslo');
const winter = displayTimestamp('2024-10-27T01:30:00Z', 'local', 'Europe/Oslo');
assert.match(summer, /UTC\+02:00/); assert.match(winter, /UTC\+01:00/); assert.match(summer, /Europe\/Oslo/);
assert.match(displayTimestamp('2024-03-31T00:30:00Z', 'local', 'Europe/Oslo'), /UTC\+01:00/); assert.match(displayTimestamp('2024-03-31T01:30:00Z', 'local', 'Europe/Oslo'), /UTC\+02:00/);
assert.match(displayTimestamp(stamp, 'utc'), /UTC\+00:00.*\[UTC\]/); assert.equal(displayTimestamp('2026-10-14T12:00:00'), 'Timezone unavailable');
console.log('Network checks UI behavior passed: typed drafts/results/comparison, escaped records, nullable counts and RTT bounds, all-kind loaders/replay confirmations, immutable UUID retries and explicit DST offsets.');
