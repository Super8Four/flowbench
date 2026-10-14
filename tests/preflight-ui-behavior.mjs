// Render the real preflight controller with portable evidence and a minimal DOM.
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const moduleUrl = process.argv[2] ? pathToFileURL(process.argv[2]).href : new URL('../app/static/workflows.js', import.meta.url).href;
const {renderPreflight, preflightSummary, connectionMapData, renderConnectionMaps} = await import(moduleUrl);
class Element {
  constructor() { this.text = ''; this.html = ''; this.writes = 0; this.attributes = {}; this.listeners = {}; this.markers = []; this.value = ''; this.width = 900; }
  get textContent() { return this.text; }
  set textContent(value) { this.text = value; this.writes++; }
  get innerHTML() { return this.html; }
  set innerHTML(value) {
    this.html = this.serializeSvg ? value.replace(/<(rect|image|path|circle)([^>]*?)\/>/g, '<$1$2></$1>') : value;
    this.writes++;
    this.markers = [...value.matchAll(/data-map-key="([^"]+)"/g)].map(([, key]) => ({
      dataset: {mapKey: key}, closest() { return this; }, focus() { document.activeElement = this; },
    }));
  }
  getAttribute(key) { return this.attributes[key] ?? null; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, listener) { (this.listeners[key] ||= []).push(listener); }
  dispatch(key, event = {}) { this.listeners[key]?.forEach((listener) => listener(event)); }
  contains(element) { return this.markers.includes(element); }
  querySelectorAll() { return this.markers; }
  getBoundingClientRect() { return {width: this.width}; }
}
const ids = ['preflight-evidence', 'preflight-status', 'preflight-phase', 'preflight-started', 'preflight-finished',
  'preflight-elapsed', 'preflight-context-note', 'route-status', 'route-note', 'route-timing', 'route-hops', 'route-output',
  'route-command', 'clock-verification', 'clock-query-status', 'clock-observed-at', 'clock-system-source', 'clock-provider',
  'clock-source', 'clock-daemon-state', 'clock-offset', 'clock-uncertainty', 'clock-uncertainty-kind', 'clock-root-delay',
  'clock-root-dispersion', 'clock-last-sync', 'clock-query-elapsed', 'clock-note', 'clock-output', 'clock-command'];
const elements = new Map(ids.map((id) => [id, new Element()]));
const field = (id) => elements.get(id);
globalThis.document = {getElementById: (id) => elements.get(id) || null};
globalThis.fetch = () => { throw new Error('Rendering saved evidence must not perform network lookups.'); };
const context = {escape: (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character])),
  number: (value, digits = 2) => Number.isFinite(value) ? value.toLocaleString('en-US', {maximumFractionDigits: digits}) : '—'};
const clock = {schema_version: 1, status: 'captured', observed_at: '2026-10-14T09:00:00+02:00', clock_source: 'system_utc',
  ntp_verified: false, provider: 'chrony', source: '<img src=x onerror="fixture">', synchronized: true,
  offset_seconds: 0, uncertainty_seconds: null, uncertainty_kind: null, root_delay_seconds: 0,
  root_dispersion_seconds: 0.002, last_sync_at: '2026-10-14T06:59:59Z', query_elapsed_seconds: 0.125,
  note: 'Read-only local daemon report; no independent NTP verification.', raw_output: '<clock fixture>', command: ['chronyc', 'tracking']};
const outgoing = {kind: 'run', preflight: {schema_version: 1, policy: 'once_per_test', status: 'partial', phase: 'before_traffic',
  started_at: '2026-10-14T07:00:00Z', finished_at: '2026-10-14T07:00:01Z', elapsed_seconds: 1, time_quality: clock},
  route: {status: 'incomplete', phase: 'before_traffic', started_at: '2026-10-14T07:00:00Z', elapsed_seconds: 1,
    note: 'Native preflight retained partial hop evidence.', raw_output: '<route fixture>', command: ['tracepath', '-n', '::1'], hops: [
      {hop: 1, observed: true, responded: true, addresses: ['::1', '2001:db8::1'], rtt_ms: [1.25, 0], rtt_upper_bound_ms: [1], detail: 'Native reply'},
      {hop: 2, observed: false, responded: false, addresses: [], rtt_ms: [], detail: 'No observation captured for this TTL.'},
      {hop: 3, observed: true, responded: false, addresses: [], rtt_ms: [], detail: '* * * Request timed out <script>fixture</script>'},
      {hop: 4, observed: true, responded: true, addresses: ['<svg onload="fixture">'], rtt_ms: [2], detail: 'Native reply'},
    ]}, concurrency: {note: 'Other test traffic overlapped this capture.'}};
renderPreflight(outgoing, context);
const map = field('route-hops').innerHTML;
assert.equal((map.match(/<li /g) || []).length, 4);
for (let hop = 1; hop < 4; hop++) assert.ok(map.indexOf(`Hop ${hop}<`) < map.indexOf(`Hop ${hop + 1}<`), 'Recorded hop order must be retained.');
assert.match(map, /<code>::1<\/code><code>2001:db8::1<\/code>/, 'Multiple native addresses must remain separate.');
assert.match(map, /No captured output/);
assert.match(map, /No reply recorded/);
assert.match(map, /Request timed out &lt;script&gt;fixture&lt;\/script&gt;/);
assert.match(map, /&lt;svg onload=&quot;fixture&quot;&gt;/);
assert.ok(!map.includes('<script>') && !map.includes('<svg onload='));
assert.match(map, /1\.25 ms \/ 0 ms \/ &lt;1 ms/, 'Exact zero and bounded RTTs must retain their meaning.');
assert.equal(field('clock-observed-at').textContent, '2026-10-14T07:00:00.000Z', 'UTC labels must not use browser-local time.');
assert.equal(field('clock-offset').textContent, '0 s');
assert.equal(field('clock-uncertainty').textContent, 'Unavailable', 'Root dispersion must not fabricate an uncertainty value.');
assert.equal(field('clock-root-delay').textContent, '0 s');
assert.equal(field('clock-source').textContent, clock.source, 'Source text must be written as text, never injected markup.');
assert.equal(field('clock-source').innerHTML, '');
assert.equal(field('clock-daemon-state').textContent, 'Reports synchronized');
assert.match(field('clock-verification').textContent, /not independently NTP-verified/);
assert.equal(field('route-note').textContent, outgoing.route.note);
renderPreflight({...outgoing, route: {...outgoing.route, status: 'captured', note: null}}, context);
assert.match(field('route-note').textContent, /Recorded native hop observations/);
assert.ok(!field('route-note').textContent.includes('No route evidence'));
renderPreflight(outgoing, context);
assert.match(field('preflight-context-note').textContent, /overlapped/);
assert.equal(field('route-output').textContent, '<route fixture>');
const writes = [...elements.values()].reduce((total, element) => total + element.writes, 0);
renderPreflight(outgoing, context);
assert.equal([...elements.values()].reduce((total, element) => total + element.writes, 0), writes, 'Identical polls must not rewrite live status or unchanged map content.');

const unsynchronized = structuredClone(outgoing);
unsynchronized.preflight.time_quality = {...clock, status: 'unsynchronized', synchronized: false, offset_seconds: -0.00002,
  uncertainty_seconds: 0.0004, uncertainty_kind: 'root_dispersion_proxy'};
renderPreflight(unsynchronized, context);
assert.equal(field('clock-daemon-state').textContent, 'Reports unsynchronized');
assert.equal(field('clock-offset').textContent, '-0.00002 s');
assert.equal(field('clock-uncertainty').textContent, '0.0004 s');
assert.equal(field('clock-uncertainty-kind').textContent, 'root dispersion proxy');

renderPreflight({kind: 'run', route: {status: 'captured', hops: [{hop: 1, address: '127.0.0.1', rtt_ms: [1]}], raw_output: 'Legacy route'}}, context);
assert.match(field('preflight-status').textContent, /Preflight not recorded/);
assert.equal(field('clock-verification').textContent, 'Clock evidence not recorded');
assert.equal(field('clock-observed-at').textContent, 'Unavailable');
assert.match(field('route-timing').textContent, /Capture phase not recorded/);
assert.match(field('route-hops').innerHTML, /127\.0\.0\.1/);

const passive = {incoming: {remote_host: '127.0.0.1'}, preflight: {status: 'not_applicable', phase: 'observed_after_start',
  time_quality: {...clock, status: 'permission_blocked', synchronized: null, source: null, offset_seconds: null}},
  route: {status: 'not_applicable', note: 'Passive test accepted without reverse probing.', hops: []}};
renderPreflight(passive, context);
assert.match(field('route-note').textContent, /No reverse probes were sent/);
assert.match(field('route-hops').innerHTML, /No route probes for passive incoming traffic/);
assert.equal(field('preflight-phase').textContent, 'Observed after incoming start');
assert.equal(field('clock-query-status').textContent, 'permission blocked');
assert.equal(field('clock-daemon-state').textContent, 'Unavailable');
assert.equal(field('clock-offset').textContent, 'Unavailable');
assert.equal(field('clock-observed-at').textContent, '2026-10-14T07:00:00.000Z');
renderPreflight({settings: {role: 'server'}, preflight: {...outgoing.preflight, phase: 'before_traffic'},
  route: {status: 'not_applicable', hops: []}}, context);
assert.equal(field('preflight-phase').textContent, 'Before traffic');
assert.match(field('preflight-status').textContent, /before local test traffic/);
assert.ok(!field('preflight-status').textContent.includes('after incoming'));
assert.match(field('route-note').textContent, /server test/);
assert.ok(!field('route-note').textContent.includes('Passive incoming'));
assert.match(field('route-hops').innerHTML, /No route probes for this server test/);

renderPreflight({kind: 'matrix-job'}, context);
assert.match(field('preflight-status').textContent, /Open an individual test/);
assert.match(field('route-hops').innerHTML, /Open a child test/);
assert.equal(field('clock-query-status').textContent, 'Per individual test');
for (const status of ['pending', 'cancelled']) {
  renderPreflight({...outgoing, preflight: {...outgoing.preflight, status}, route: {...outgoing.route, status}}, context);
  assert.match(field('preflight-status').textContent, new RegExp(status));
  assert.equal(field('route-status').textContent, status);
}
renderPreflight({...outgoing, status: 'interrupted', preflight: {...outgoing.preflight, status: 'pending',
  time_quality: {...clock, status: 'pending'}}, route: {...outgoing.route, status: 'pending', note: 'Native route preflight is pending or running.'}}, context);
assert.match(field('preflight-status').textContent, /Incomplete preflight checkpoint/);
assert.match(field('preflight-status').textContent, /No preparation is running/);
assert.equal(field('route-status').textContent, 'Incomplete checkpoint');
assert.equal(field('clock-query-status').textContent, 'Incomplete checkpoint');
assert.match(field('route-note').textContent, /no route probe is running/);
assert.ok(!field('route-note').textContent.includes('pending or running'));
assert.match(field('clock-note').textContent, /did not finish/);
assert.match(field('route-hops').innerHTML, /Hop 1/);
assert.deepEqual(preflightSummary(outgoing), {route: 'incomplete', clock: 'captured'});
assert.deepEqual(preflightSummary({status: 'skipped'}), {route: 'Not recorded', clock: 'Not recorded'});
assert.deepEqual(preflightSummary({status: 'interrupted', route: {status: 'pending'}, preflight: {time_quality: {status: 'pending'}}}),
  {route: 'Incomplete checkpoint', clock: 'Incomplete checkpoint'});
// Synthetic offline location fixtures exercise the actual map controller. No
// database, IP service, browser geolocation or test traffic is involved.
for (const id of ['route-world-map', 'route-topology-map', 'route-world-panel', 'route-topology-panel',
  'route-view-world', 'route-view-topology', 'route-geo-note', 'route-map-observation', 'route-map-selection', 'route-location-rows']) elements.set(id, new Element());
field('route-world-map').serializeSvg = true;
field('route-topology-map').serializeSvg = true;
field('route-topology-map').width = 300;
const located = (address, latitude, longitude, city = 'Synthetic area') => ({address, scope: 'public', status: 'located', approximate: true, latitude, longitude, city, country: 'Fixture country'});
const hop = (number, locations, extras = {}) => ({hop: number, observed: true, responded: true, addresses: locations.map((row) => row.address).filter(Boolean), locations, ...extras});
const geoRun = {id: 'geo-fixture', route: {geolocation: {status: 'available', note: 'Synthetic offline DB-IP fixture.'}, hops: [
  hop(1, [{address: '10.0.0.1', scope: 'internal', status: 'internal'}]),
  hop(2, [located('8.8.8.8', 0, 0)]),
  hop(3, [located('1.1.1.1', 10, 20, '<img src=x onerror="fixture">')]),
  hop(4, [{address: null, scope: 'unknown', status: 'missing'}], {observed: false, responded: false}),
  hop(5, [located('9.9.9.9', 0, 0)]),
  hop(6, [{address: '2001:db8::1', scope: 'unknown', status: 'unavailable'}]),
]}};
const geoModel = connectionMapData(geoRun.route);
assert.equal(geoModel.hops.length, 6, 'Every internal and missing hop remains in topology.');
assert.equal(geoModel.markers.length, 2, 'Exact overlapping positions form one marker without jitter.');
assert.deepEqual([geoModel.markers[0].x, geoModel.markers[0].y], [450, 225]);
assert.equal(geoModel.markers[0].records.length, 2);
assert.equal(geoModel.edges.length, 1, 'Only consecutive unambiguous public positions may be connected.');
assert.deepEqual([geoModel.edges[0].from.hop, geoModel.edges[0].to.hop], [2, 3]);
const invalidLocations = [located('8.8.8.8', null, 1), located('8.8.8.8', 91, 1), located('8.8.8.8', 1, Infinity),
  {...located('10.0.0.1', 1, 1), scope: 'internal'}, {...located('8.8.8.8', 1, 1), status: 'unavailable'}];
assert.equal(connectionMapData({hops: invalidLocations.map((location, index) => hop(index + 1, [location]))}).markers.length, 0, 'Unknown/internal/invalid coordinates must not be placed.');
assert.equal(connectionMapData({hops: [hop(1, [located('8.8.8.8', 0, 179)]), hop(2, [located('1.1.1.1', 0, -179)])]}).edges.length, 0, 'Dateline observations must not imply a line through the whole map.');
renderConnectionMaps(geoRun, context);
assert.equal(field('route-world-panel').hidden, false);
assert.equal(field('route-topology-panel').hidden, true);
assert.equal(field('route-view-world').getAttribute('aria-pressed'), 'true');
assert.match(field('route-world-map').innerHTML, /href="\/static\/maps\/world-land\.svg"/);
assert.match(field('route-world-map').innerHTML, />2x<\/text>/);
assert.match(field('route-world-map').innerHTML, /&lt;img src=x onerror=&quot;fixture&quot;&gt;/);
assert.ok(!field('route-world-map').innerHTML.includes('<img src=x'));
assert.match(field('route-location-rows').innerHTML, /No captured output/);
assert.match(field('route-location-rows').innerHTML, /Internal network/);
assert.match(field('route-location-rows').innerHTML, /2001:db8::1/);
assert.match(field('route-location-rows').innerHTML, /&lt;img src=x/);
assert.equal(field('route-topology-map').markers.length, 6);
assert.equal(field('route-topology-map').getAttribute('viewBox'), '0 0 300 296', 'Narrow topology must wrap without dropping hops.');
const overlap = field('route-world-map').markers.find((marker) => marker.dataset.mapKey === 'world:0,0');
let prevented = false, stopped = false;
overlap.focus();
field('route-world-map').dispatch('keydown', {target: overlap, key: 'Enter', preventDefault() { prevented = true; }, stopPropagation() { stopped = true; }});
assert.equal(prevented, true);
assert.equal(stopped, true, 'Handled SVG marker activation must not reach AdminLTE native-control keyboard delegation.');
assert.match(field('route-map-selection').textContent, /Hop 2: 8\.8\.8\.8.*Hop 5: 9\.9\.9\.9/);
for (const key of [' ', 'ArrowRight']) {
  prevented = false; stopped = false;
  field('route-world-map').dispatch('keydown', {target: overlap, key, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; }});
  assert.equal(prevented, key === ' ', 'Only handled activation keys prevent their default action.');
  assert.equal(stopped, key === ' ', 'Other keyboard navigation continues through normal handlers.');
}
const mapWrites = field('route-world-map').writes;
renderConnectionMaps(geoRun, context);
assert.equal(field('route-world-map').writes, mapWrites, 'Browser-style expanded SVG serialization must not trigger an unchanged rewrite.');
assert.equal(document.activeElement, overlap, 'Unchanged polling retains the exact focused marker node.');
const changedGeo = structuredClone(geoRun); changedGeo.route.hops[2].locations[0].city = 'New synthetic area';
renderConnectionMaps(changedGeo, context);
assert.equal(document.activeElement.dataset.mapKey, 'world:0,0', 'Changed route evidence restores a stable-coordinate marker identity.');
assert.notEqual(document.activeElement, overlap, 'The fixture genuinely rebuilt changed SVG content.');
field('route-view-topology').dispatch('click');
assert.equal(field('route-world-panel').hidden, true);
assert.equal(field('route-topology-panel').hidden, false);
field('route-map-observation').value = 'hop:5'; field('route-map-observation').dispatch('change');
assert.match(field('route-map-selection').textContent, /2001:db8::1.*Location unavailable/);
renderConnectionMaps(changedGeo, context);
assert.equal(field('route-map-observation').value, 'hop:5', 'Polling preserves explicit observation selection.');
assert.equal(field('route-topology-panel').hidden, false, 'Polling preserves the requested map mode.');
renderConnectionMaps({id: 'internal-fixture', route: {hops: geoRun.route.hops.filter((row) => [1, 4, 6].includes(row.hop))}}, context);
assert.equal(field('route-topology-panel').hidden, false, 'No geographic evidence defaults to an ordered topology.');
assert.match(field('route-geo-note').textContent, /No geographic positions/);
assert.equal(field('route-topology-map').markers.length, 3, 'Internal and IPv6-unknown observations remain inspectable.');
console.log('Preflight UI behavior passed: ordered/missing/native hops, safe UTC clock evidence, passive/server phases, offline geographic positions and gaps, overlap/keyboard selection, responsive topology and stable polling focus.');
