import {createWorkflows} from './workflows.js';
import {rateScale, formatRate, formatMbps, runColor, palette} from './units.js';
const $ = (id) => document.getElementById(id);
const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted', 'partial', 'skipped']);
let runs = [], activeId = null, selectedId = null, selected = null, events = [];
let engineReady = false, capabilities = {}, selectionVersion = 0, lastHistory = 0, comparisonVersion = 0;
let presets = [], presetCatalog = {}, editingPreset = null, comparisonData = null, listenerState = null;
let comparisonController = null, comparisonRowsInitialized = false, comparisonRowSequence = 0, comparisonRenderVersion = 0;
let comparisonPlotVersion = 0;
let comparisonTableVersion = 0;
const hiddenComparisonRuns = new Set();
const knownComparisonRuns = new Set();
const testPage = Boolean($('test-form'));
const query = new URLSearchParams(location.search);
const number = (value, digits = 2) => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: digits}) : '—';
const date = (value) => value ? new Date(value).toLocaleString() : '—';
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const title = (run) => run.kind === 'flent-run' || run.settings.flent_workload ? `${run.settings.label || run.settings.target} · Flent RRUL` : `${run.settings.label || (run.settings.role === 'server' ? 'Server' : run.settings.target)} · ${run.kind === 'matrix-job' ? 'Matrix batch' : run.kind === 'protocol-job' ? 'Both job' : (run.effective_settings?.protocol || run.settings.protocol).toUpperCase()} · ${run.effective_settings?.mode || run.settings.mode}`;
const metricLabel = (key) => key.replace(/_mbps$/, ' bitrate').replaceAll('_', ' ');
const metricValue = (key, value) => key.endsWith('_mbps') ? formatMbps(value) : key.endsWith('bits_per_second') ? formatRate(value) : number(value);
const workflows = createWorkflows({api, message, escape, number, date, title, smoothPath, download,
  settings: settingsData, state: () => ({runs, selected, selectedId, activeId, engineReady, capabilities}),
  reportIds: () => $('comparison') ? comparisonIds() : selected && selected.status === 'completed' && !['protocol-job', 'matrix-job'].includes(selected.kind) ? [selected.id] : [],
  select: selectRun, refresh: loadHistory, reload: loadSelected,
  start: async (run) => { activeId = run.id; controls(); await selectRun(run.id); await loadHistory(); }});

async function api(path, options = {}) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
  const abort = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  options.signal?.addEventListener('abort', abort, {once: true});
  try {
    const response = await fetch(path, {cache: 'no-store', ...options, signal: controller.signal,
      headers: {'Content-Type': 'application/json', ...options.headers}});
    let data;
    try { data = await response.json(); } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new Error(`Server returned ${response.status} without a JSON response. The selected evidence may exceed available request or memory limits; reduce the selection and retry.`);
    }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  } catch (error) {
    throw new Error(error.name === 'AbortError' ? 'Request timed out. Refresh status before retrying; an accepted test may still be running.' : error.message);
  } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); }
}
function rateCard(id, value, unitId) {
  const scale = rateScale([Number.isFinite(value) ? value * 1e6 : NaN]);
  $(id).textContent = Number.isFinite(value) ? number(value * 1e6 / scale.divisor) : '—';
  $(unitId).textContent = scale.unit;
}
function message(text, type = 'danger') {
  $('message').className = `alert alert-${type}`;
  if ($('message').textContent !== text) $('message').textContent = text;
  $('message').setAttribute('role', type === 'danger' ? 'alert' : 'status');
}
function safe(action) {
  return async (event) => { try { await action(event); } catch (error) { message(error.message); } };
}
function controls() {
  if (!testPage) return;
  $('run-button').disabled = !engineReady || Boolean(activeId);
  $('run-button').textContent = activeId ? 'Test running' : 'Start test';
  $('stop-button').disabled = !activeId;
  workflows.controls();
}
function renderStatus(status) {
  capabilities = status.iperf;
  engineReady = status.iperf.available && !status.storage_error;
  $('engine-badge').textContent = engineReady ? '● iPerf3 connected' : status.storage_error ? 'Archive unavailable' : 'iPerf3 unavailable';
  if ($('engine-version')) $('engine-version').textContent = status.iperf.version || 'iPerf3 not installed';
  if ($('storage-path')) $('storage-path').textContent = `Saved on disk: ${status.storage}`;
  if ($('configuration-engine')) $('configuration-engine').textContent = status.iperf.version || 'Unavailable';
  if ($('configuration-storage')) $('configuration-storage').textContent = status.database;
  renderListener(status.listener);
  workflows.status(status);
  if (!engineReady) message(status.storage_error || status.iperf.error);
  controls();
}
function renderListener(state) {
  listenerState = state;
  if (!$('listener-form') || !state) return;
  const fields = {bind: 'listener-bind', port: 'listener-port', max_duration: 'listener-duration', label: 'listener-label', notes: 'listener-notes'};
  if (!$('listener-form').dataset.loaded) {
    for (const [key, id] of Object.entries(fields)) $(id).value = state.settings[key];
    $('listener-form').dataset.loaded = 'true';
  }
  const running = state.state === 'listening';
  for (const id of Object.values(fields)) $(id).disabled = running;
  $('listener-start').disabled = running || !engineReady;
  $('listener-stop').disabled = !running && !state.settings.enabled;
  $('listener-state').className = `badge ms-auto status-badge status-${state.state}`;
  $('listener-state').textContent = `Listener ${state.state}`;
  $('listener-active').innerHTML = state.active_run ? `Capturing incoming test. <a href="/?run=${encodeURIComponent(state.active_run)}">Open live evidence</a>` : 'No incoming test is active. Accepted sessions appear in History automatically.';
  if (!$('listener-form').dataset.localError) {
    $('listener-error').classList.toggle('d-none', !state.error);
    if (state.error) $('listener-error').textContent = state.error;
  }
}
function listenerFormError(error) {
  $('listener-form').dataset.localError = 'true';
  $('listener-error').classList.remove('d-none');
  $('listener-error').textContent = error.message;
  $('listener-error').tabIndex = -1;
  $('listener-error').focus();
  const field = /bind/i.test(error.message) ? 'listener-bind' : /port/i.test(error.message) ? 'listener-port' : /duration/i.test(error.message) ? 'listener-duration' : null;
  if (field) {
    $(field).setAttribute('aria-invalid', 'true');
    $(field).setAttribute('aria-describedby', field + '-error');
    let inline = $(field + '-error');
    if (!inline) { inline = document.createElement('p'); inline.id = field + '-error'; inline.className = 'field-error mt-2 mb-0'; $(field).parentElement.append(inline); }
    inline.textContent = error.message;
    const link = document.createElement('a'); link.href = '#' + field; link.textContent = 'Review this field';
    $('listener-error').append(document.createElement('br'), link);
  }
}
function fields() {
  const server = $('role').value === 'server';
  if (server) { location.href = '/configuration'; return; }
  $('target-field').classList.toggle('d-none', server);
  $('target').required = !server;
  $('target').disabled = server;
  $('preset-picker').classList.toggle('d-none', server);
  $('client-fields').classList.toggle('d-none', server);
  $('server-fields').classList.toggle('d-none', !server);
  $('client-fields').querySelectorAll('input, select').forEach((element) => { element.disabled = server; });
  $('idle_timeout').disabled = !server;
  $('traceroute').disabled = server;
  $('traceroute').closest('.form-check').classList.toggle('d-none', server);
  $('custom-editor').closest('.card').classList.toggle('d-none', server);
  const usesUdp = ['udp', 'both'].includes($('protocol').value);
  $('bandwidth').disabled = server || !usesUdp;
  $('bandwidth-field').classList.toggle('d-none', !usesUdp);
  $('both-note').classList.toggle('d-none', $('protocol').value !== 'both');
  const unavailable = !capabilities.bidir || (usesUdp && !capabilities.udp_bidir);
  $('protocol-support').textContent = !server && $('mode').value === 'bidir' && unavailable
    ? 'This iPerf3 build cannot run the selected bidirectional mode. Choose upload/download, or use the Linux container. Saved settings remain selected.' : '';
  presetDetail();
}

function settingsData() {
  const data = Object.fromEntries(new FormData($('test-form')));
  data.debug = $('debug').checked;
  data.traceroute = !($('role').value === 'server') && $('traceroute').checked;
  // Disabled UDP fields still belong to a reusable configuration.
  data.bandwidth = $('bandwidth').value;
  return {...data, ...workflows.settings()};
}
function loadSettings(settings) {
  for (const [key, value] of Object.entries({...settings, ip_version: settings.ip_version || 'auto'})) {
    if (!$(key)) continue;
    if ($(key).type === 'checkbox') $(key).checked = value; else $(key).value = value;
  }
  fields();
  workflows.loadSettings(settings);
}
function presetMatches(row, query) {
  return `${row.name} ${row.settings.target} ${row.country} ${row.city} ${row.continent} ${row.provider} ${row.notes}`.toLowerCase().includes(query);
}
function presetDetail() {
  const row = presets.find((item) => item.id === $('server-preset').value);
  if (!row) {
    $('preset-detail').textContent = 'Choose a preset to load its defaults, then adjust the settings below.';
    return;
  }
  const notices = [];
  if ($('target').value !== row.settings.target) notices.push('Host has been changed from this preset.');
  const [low, high] = row.ports.includes('-') ? row.ports.split('-').map(Number) : [Number(row.ports), Number(row.ports)];
  if (Number($('port').value) < low || Number($('port').value) > high) notices.push('Port is outside the listed range.');
  const required = $('protocol').value === 'both' ? ['tcp', 'udp'] : [$('protocol').value];
  if (required.some((protocol) => !row.protocols.includes(protocol))) notices.push('This protocol is not listed as supported.');
  if (!row.modes.includes($('mode').value)) notices.push('This direction is not listed as supported.');
  if ($('ip_version').value === '6' && !row.ipv6) notices.push('IPv6 support is not listed.');
  const description = `Ports ${row.ports} · ${row.protocols.map((v) => v.toUpperCase()).join('/')} · ${row.modes.join('/')} · ${row.ipv6 ? 'IPv6 listed' : 'IPv6 not listed'}`;
  $('preset-detail').textContent = [description, row.notes, ...notices].filter(Boolean).join(' ');
  $('preset-detail').classList.toggle('text-warning-emphasis', Boolean(notices.length));
}
function renderPresetPicker() {
  if (!testPage) return;
  const current = $('server-preset').value;
  const query = $('preset-search').value.toLowerCase();
  $('server-preset').replaceChildren(new Option('Custom settings · enter a server below', ''));
  for (const kind of ['custom', 'public']) {
    const group = document.createElement('optgroup'); group.label = kind === 'custom' ? 'My custom presets' : 'Public servers';
    for (const row of presets.filter((p) => p.kind === kind && (presetMatches(p, query) || p.id === current))) {
      group.append(new Option(`${row.name} · ${row.settings.target}:${row.ports}`, row.id));
    }
    if (group.children.length) $('server-preset').append(group);
  }
  $('server-preset').value = current;
  presetDetail();
}
function renderCatalog() {
  if (!$('preset-body')) return;
  const query = $('catalog-search').value.toLowerCase(), kind = $('catalog-kind').value;
  const visible = presets.filter((p) => (kind === 'all' || p.kind === kind) && presetMatches(p, query));
  $('preset-count').textContent = `${presets.filter((p) => p.kind === 'public').length} public · ${presets.filter((p) => p.kind === 'custom').length} custom`;
  $('catalog-date').textContent = `Public source fetched: ${date(presetCatalog.source_checked_at)} · Showing ${visible.length} presets`;
  $('preset-body').innerHTML = visible.map((p) => `<tr><td><div class="history-label">${escape(p.name)}</div><span class="badge text-bg-${p.kind === 'public' ? 'secondary' : 'primary'}">${p.kind}</span><div class="history-date">${escape(p.provider)} ${escape(p.continent)}${p.capacity ? ` · Listed ${escape(p.capacity)} Gb/s` : ''}</div>${p.notes ? `<div class="history-date">${escape(p.notes)}</div>` : ''}</td><td>${escape(p.settings.target)}<div class="history-date">Ports ${escape(p.ports)}</div></td><td>${escape(p.protocols.map((v) => v.toUpperCase()).join('/'))} · ${escape(p.modes.join('/'))}<div class="history-date">${p.ipv6 ? 'IPv6 listed' : 'IPv6 not listed'} · Default ${escape(p.settings.protocol.toUpperCase())} ${escape(p.settings.mode)}, ${p.settings.duration}s, ${p.settings.streams} stream(s)</div></td><td class="text-end text-nowrap"><button class="btn btn-sm btn-outline-primary" data-preset="${p.id}">Load</button>${p.kind === 'custom' ? ` <button class="btn btn-sm btn-outline-danger" data-remove-preset="${p.id}">Remove</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="4" class="text-center py-4">No matching presets.</td></tr>';
}
async function loadPresets() {
  presetCatalog = await api('/api/presets'); presets = presetCatalog.presets;
  if (!presets.some((p) => p.id === editingPreset)) editingPreset = null;
  if ($('update-preset')) $('update-preset').disabled = !editingPreset;
  renderPresetPicker(); renderCatalog();
}
function applyPreset(id) {
  const row = presets.find((p) => p.id === id);
  if (!row) { editingPreset = null; $('update-preset').disabled = true; presetDetail(); return; }
  $('preset-search').value = ''; renderPresetPicker(); $('server-preset').value = row.id;
  loadSettings({...row.settings, label: row.settings.label || row.name});
  editingPreset = row.kind === 'custom' ? row.id : null;
  $('update-preset').disabled = !editingPreset;
  $('preset-name').value = row.kind === 'custom' ? row.name : `${row.name} · custom`;
  $('preset-ports').value = row.ports; $('preset-notes').value = row.notes;
  presetDetail();
}
async function savePreset(update) {
  if (update && !editingPreset) throw new Error('Load a custom preset first.');
  const data = {name: $('preset-name').value, ports: $('preset-ports').value || $('port').value,
    notes: $('preset-notes').value, settings: settingsData()};
  const row = await api(update ? `/api/presets/${editingPreset}` : '/api/presets', {
    method: update ? 'PATCH' : 'POST', body: JSON.stringify(data)});
  await loadPresets(); applyPreset(row.id); message(`Custom preset “${row.name}” saved on disk.`, 'success');
}
async function uploadFile(file, path, maxBytes) {
  if (!file || !file.size || file.size > maxBytes) throw new Error(`Choose a non-empty file up to ${maxBytes / 1024 / 1024} MiB.`);
  const content = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer());
  return api(path, {method: 'POST', body: JSON.stringify({filename: file.name, content})});
}
function renderLogs() {
  const filter = $('log-filter').value, search = $('log-search').value.toLowerCase();
  const visible = events.filter((row) => (filter === 'all' || row.source === filter) && row.message.toLowerCase().includes(search));
  $('log-output').textContent = visible.map((row) => `[${date(row.timestamp)}] [${row.source}] ${row.message}`).join('\n') || (events.length ? 'No output matches these filters.' : selected ? 'No captured output is available for this session.' : 'Select a session to view captured output.');
  if ($('autoscroll').checked) $('log-output').scrollTop = $('log-output').scrollHeight;
}
function smoothPath(data, x, y) {
  // Draw measured samples directly; never fit a smoothed curve across missing intervals.
  return data.map((point, index) => {
    const gap = index && Number.isFinite(point.start) && point.start > data[index - 1].seconds + 0.000001;
    return `${!index || gap ? 'M' : 'L'} ${x(point.seconds)},${y(point.mbps)}`;
  }).join(' ');
}
function chartWidth(id) {
  const width = Math.max(220, $(id).getBoundingClientRect().width || 760);
  $(id).setAttribute('viewBox', `0 0 ${width} 240`);
  return width;
}

function chart(localPoints, remotePoints = []) {
  const width = chartWidth('throughput-chart'), right = width - 15, ticks = width < 600 ? 3 : 6;
  const perspective = $('chart-perspective').value;
  const lines = [];
  for (const [source, input] of [['local', localPoints], ['remote', remotePoints]]) {
    if (perspective !== 'compare' && source !== perspective) continue;
    for (const direction of ['upload', 'download']) {
      const color = runColor(selected || {});
      const valid = (input || []).filter((p) => p.direction === direction && Number.isFinite(p.seconds) && Number.isFinite(p.mbps));
      const data = Array.from(new Map(valid.map((p) => [p.seconds, p])).values()).sort((a, b) => a.seconds - b.seconds);
      if (data.length) lines.push({source, direction, color, data});
    }
  }
  const points = lines.flatMap((line) => line.data);
  const scale = rateScale(points.map((point) => point.mbps * 1e6));
  $('throughput-unit').textContent = scale.unit;
  $('throughput-chart').setAttribute('aria-label', `Throughput by elapsed seconds in ${scale.unit}`);
  $('peer-legend').classList.toggle('d-none', perspective !== 'compare');
  if (!points.length) {
    $('throughput-chart').innerHTML = `<text x="${width / 2}" y="110" text-anchor="middle" fill="#aab6bf" font-size="12">${perspective === 'remote' ? 'No remote interval measurements' : 'Interval measurements'}</text><text x="${width / 2}" y="130" text-anchor="middle" fill="#aab6bf" font-size="12">${perspective === 'remote' ? 'are available for this session' : 'will appear here'}</text>`;
    return;
  }
  const maxTime = points.reduce((max, p) => Math.max(max, p.seconds), 1);
  const rawMax = points.reduce((max, p) => Math.max(max, p.mbps), 0) * 1.08 || 1e-6;
  const step = 10 ** Math.floor(Math.log10(rawMax)) / 2;
  const maxRate = Math.ceil(rawMax / step) * step;
  const x = (t) => 57 + t / maxTime * (width - 72), y = (n) => 202 - n / maxRate * 177;
  let svg = '<defs>';
  for (const line of lines) svg += `<linearGradient id="fill-${line.source}-${line.direction}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${line.color}" stop-opacity="${line.source === 'remote' && perspective === 'compare' ? '.14' : '.32'}"/><stop offset="1" stop-color="${line.color}" stop-opacity=".025"/></linearGradient>`;
  svg += '</defs>';
  for (let i = 0; i <= ticks; i++) {
    const value = maxRate * i / ticks, height = y(value);
    svg += `<line x1="57" y1="${height}" x2="${right}" y2="${height}" stroke="#353c41"/><text x="47" y="${height + 3}" text-anchor="end" fill="#aab3bb" font-size="12">${number(value*1e6/scale.divisor, 1)}</text>`;
  }
  for (let i = 0; i <= ticks; i++) {
    const seconds = maxTime * i / ticks;
    svg += `<text x="${x(seconds)}" y="222" text-anchor="${i === 0 ? 'start' : i === ticks ? 'end' : 'middle'}" fill="#aab3bb" font-size="12">${number(seconds, 1)}s</text>`;
  }
  for (const line of lines) {
    const path = smoothPath(line.data, x, y), remote = line.source === 'remote' && perspective === 'compare';
    const pattern = remote ? line.direction === 'download' ? '8 3 2 3' : '2 4' : line.direction === 'download' ? '7 5' : '';
    svg += `<path d="${path}" stroke="${line.color}" stroke-width="${remote ? '1.6' : '1.9'}" ${pattern ? `stroke-dasharray="${pattern}"` : ''} fill="none"/>`;
    for (const p of line.data) svg += `<circle cx="${x(p.seconds)}" cy="${y(p.mbps)}" r="${line.data.length === 1 ? 3 : 5}" fill="${line.color}" fill-opacity="${line.data.length === 1 ? 1 : 0}"><title>${line.source} ${line.direction}: ${formatMbps(p.mbps,2,scale)} at ${number(p.seconds, 2)}s</title></circle>`;
  }
  $('throughput-chart').innerHTML = svg;
}
function renderPeer() {
  if (selected?.kind === 'flent-run') {
    $('throughput-chart').innerHTML = '';
    $('chart-source-note').textContent = 'This test uses Flent. Native throughput and latency measurements are in the Flent evidence panel.';
    $('peer-summary').classList.add('d-none'); $('peer-output').textContent = 'Flent native results are captured separately.';
    return;
  }
  if (['protocol-job', 'matrix-job'].includes(selected?.kind)) {
    const width = chartWidth('throughput-chart');
    $('chart-source-note').textContent = 'Batch results stay separate. Open a child test to view its measured directions, chart and remote output.';
    $('throughput-chart').innerHTML = `<text x="${width / 2}" y="110" text-anchor="middle" fill="#aab6bf" font-size="12">Open TCP or UDP above</text><text x="${width / 2}" y="130" text-anchor="middle" fill="#aab6bf" font-size="12">for its throughput chart</text>`;
    $('peer-summary').classList.add('d-none');
    $('peer-output').textContent = 'Open a protocol test to view its remote output.';
    return;
  }
  const peer = selected?.peer;
  $('peer-output').textContent = peer?.raw_output || peer?.note || 'Remote output will appear after the server supplies it.';
  const perspective = $('chart-perspective').value;
  $('chart-source-note').textContent = perspective === 'local' ? 'Local iPerf measurements · solid: upload · dashed: download. Saved run color identifies this session; measured point tooltips include SI units.' : (peer?.note || 'Choose a session to view the remote server output.');
  const visible = perspective !== 'local';
  $('peer-summary').classList.toggle('d-none', !visible);
  if (visible) {
    const left = selected?.metrics || {}, right = peer?.metrics || {};
    const keys = Object.keys({...left, ...right}).sort();
    $('peer-summary').innerHTML = keys.length ? `<div class="table-responsive"><table class="table"><thead><tr><th>Metric · client perspective</th><th>Local report</th><th>Remote report</th></tr></thead><tbody>${keys.map((key) => `<tr><td>${escape(metricLabel(key))}</td><td>${metricValue(key,left[key])}</td><td>${metricValue(key,right[key])}</td></tr>`).join('')}</tbody></table></div><p class="small text-body-secondary">${escape(peer?.note || '')}</p>` : `<p class="small text-body-secondary">${escape(peer?.note || 'No results yet.')}</p>`;
  }
  chart(selected?.series || [], peer?.series || []);
}
function renderSelected() {
  if (!selected) return;
  $('session-status').textContent = selected.status[0].toUpperCase() + selected.status.slice(1);
  $('session-date').textContent = date(selected.created_at);
  if (Number.isFinite(selected.overview?.runtime_seconds)) $('session-date').textContent += ` · Run time ${number(selected.overview.runtime_seconds)}s`;
  $('selected-label').textContent = title(selected);
  $('event-count').textContent = number(selected.event_count, 0);
  const metrics = selected.metrics || {};
  for (const direction of ['upload', 'download']) {
    const latest = (selected.series || []).filter((p) => p.direction === direction).at(-1);
    rateCard(direction + '-rate', metrics[direction + '_mbps'] ?? (!terminal.has(selected.status) ? latest?.mbps : undefined), direction + '-unit');
    $(direction + '-peak').textContent = `Peak ${formatMbps(selected.overview?.['max_' + direction + '_mbps'])}`;
  }
  $('exit-code').textContent = selected.error || (selected.imported ? `Imported from ${selected.imported.filename} · ${date(selected.imported.at)}` : (selected.incoming && selected.finished_at ? `Incoming session captured · ${date(selected.finished_at)}. The listener has a separate lifetime.` : selected.exit_code === null ? 'Capturing output to disk…' : `Process exit code: ${selected.exit_code} · ${date(selected.finished_at)}`));
  $('export-button').disabled = false;
  $('rerun-button').disabled = false;
  $('protocol-job-card').classList.toggle('d-none', selected.kind !== 'protocol-job');
  if (selected.kind === 'protocol-job') {
    const tests = selected.protocol_runs || [];
    $('protocol-job-status').textContent = `Job: ${selected.status}. ${tests.filter((test) => test.status === 'completed').length} of 2 protocols completed.`;
    $('protocol-job-results').innerHTML = `<table class="table"><thead><tr><th>Protocol / direction</th><th>Status</th><th>Upload rate</th><th>Download rate</th><th>Evidence</th></tr></thead><tbody>${tests.map((test) => `<tr><td>${escape(test.protocol.toUpperCase())} / ${escape(test.mode)}</td><td>${escape(test.status)}${test.error ? `<div class="form-text">${escape(test.error)}</div>` : ''}</td><td>${formatMbps(test.metrics.upload_mbps)}</td><td>${formatMbps(test.metrics.download_mbps)}</td><td><a href="/?run=${encodeURIComponent(test.id)}">Open ${escape(test.protocol.toUpperCase())}</a></td></tr>`).join('')}</tbody></table>`;
  }
  renderPeer();
  if (selected.kind === 'protocol-job') {
    $('exit-code').textContent = selected.error || `Both job ${selected.status}. Each protocol retains its own logs and process outcome.`;
  }
  renderLogs();
  if (selected.kind === 'protocol-job') $('log-output').textContent = 'Open a protocol test above to view its captured output.';
  renderResults();
  workflows.selected(selected);
}
async function loadSelected(reset = false) {
  if (!selectedId) return;
  const id = selectedId, generation = selectionVersion;
  const after = reset ? 0 : events.at(-1)?.sequence || 0;
  const data = await api(`/api/runs/${id}?after=${after}`);
  if (id !== selectedId || generation !== selectionVersion) return;
  selected = data;
  // Requests can overlap when opening a row while a poll is finishing.
  const cursor = reset ? 0 : events.at(-1)?.sequence || 0;
  events = reset ? data.events : events.concat(data.events.filter((row) => row.sequence > cursor));
  renderSelected();
}
async function selectRun(id) {
  if (!testPage) { location.href = `/?run=${encodeURIComponent(id)}`; return; }
  selectionVersion++;
  selectedId = id; selected = null; events = [];
  await loadSelected(true);
  if (!selected || selected.id !== id) return;
  $('annotation-card').classList.remove('d-none');
  $('saved-label').value = selected.settings.label;
  $('saved-notes').value = selected.settings.notes;
  workflows.edit(selected);
}
function historyTrend(overview, run) {
  const points = overview?.trend || [];
  if (!points.length) return '<span class="history-muted">No interval data</span>';
  const maxSeconds = points.reduce((max, point) => Math.max(max, point.seconds), 1);
  const maxRate = points.reduce((max, point) => Math.max(max, point.mbps), 0) || 1e-6;
  const x = (point) => (4 + point.seconds / maxSeconds * 128).toFixed(2);
  const y = (point) => (32 - point.mbps / maxRate * 28).toFixed(2);
  const lines = ['upload', 'download'].map((direction) => {
    const values = points.filter((point) => point.direction === direction);
    if (!values.length) return '';
    const color = runColor(run);
    return `<polyline points="${values.map((point) => `${x(point)},${y(point)}`).join(' ')}" fill="none" stroke="${color}" ${direction === 'download' ? 'stroke-dasharray="4 3"' : ''} stroke-width="1.6" stroke-linejoin="round"/>` +
      values.map((point) => `<circle cx="${x(point)}" cy="${y(point)}" r="1.3" fill="${color}"><title>${direction}: ${formatMbps(point.mbps)} at ${number(point.seconds)}s</title></circle>`).join('');
  }).join('');
  return `<svg class="history-sparkline" viewBox="0 0 140 36" role="img" aria-label="Upload and download throughput trend, scaled to this test"><title>Solid upload, dashed download. Peak ${formatMbps(maxRate)}; ${number(maxSeconds)} seconds. Scale varies by test; color is saved with the run.</title>${lines}</svg>`;
}
function renderHistory() {
  if (!$('history-body')) return;
  const search = $('history-search').value.toLowerCase();
  const visible = runs.filter((run) => `${title(run)} ${run.settings.target} ${run.settings.notes} ${Object.values(run.settings.tags || {}).join(' ')}`.toLowerCase().includes(search) && workflows.matches(run));
  $('history-body').innerHTML = visible.map((run) => {
    const config = run.effective_settings || run.settings;
    const stats = run.overview || {};
    const color = {completed: 'success', failed: 'danger', cancelled: 'secondary', interrupted: 'warning', running: 'primary', starting: 'primary'}[run.status] || 'secondary';
    return `<tr><td class="history-session"><button class="history-link" data-open="${run.id}">${escape(title(run))}</button><div class="history-date">${escape(date(run.created_at))} ${run.imported ? '<span class="badge text-bg-secondary">Imported</span>' : ''}</div>${workflows.tags(run)}</td>
      <td>${escape(run.settings.target || run.settings.bind || 'All interfaces')}:${run.settings.port}<div class="history-date">${run.kind === 'flent-run' ? 'Flent RRUL · 8 TCP load flows + latency probes' : `${escape(config.protocol.toUpperCase())} · ${escape(config.mode)} · ${config.streams} stream(s) · ${escape(run.settings.role)}`}</div></td>
      <td><span class="badge text-bg-${color}">${escape(run.status)}</span></td>
      <td class="history-speed"><strong>${formatMbps(stats.max_upload_mbps)}</strong><div class="history-date">Avg ${formatMbps(run.metrics.upload_mbps)}</div></td>
      <td class="history-speed"><strong>${formatMbps(stats.max_download_mbps)}</strong><div class="history-date">Avg ${formatMbps(run.metrics.download_mbps)}</div></td>
      <td class="text-nowrap"><strong>${number(stats.runtime_seconds)}${Number.isFinite(stats.runtime_seconds) ? 's' : ''}</strong><div class="history-date">${config.role === 'server' && !run.test_parameters ? 'Measured time' : `Set ${number(config.duration)}s`}</div></td>
      <td>${historyTrend(stats, run)}</td>
      <td class="text-end text-nowrap"><button class="btn btn-sm btn-outline-primary" data-open="${run.id}">Open</button> <button class="btn btn-sm btn-outline-secondary" data-export="${run.id}">Export</button></td></tr>`;
  }).join('') || '<tr><td colspan="8" class="text-center py-5 history-muted">No matching saved sessions.</td></tr>';
  $('history-count').textContent = `${visible.length} of ${runs.length} saved sessions`;
}
function comparisonOptions() {
  if (!$('baseline')) return;
  if (!comparisonRowsInitialized) {
    let saved;
    try { saved = JSON.parse(localStorage.getItem('flowbench-comparison-ids')); } catch { saved = null; }
    if (!Array.isArray(saved)) saved = ['baseline', 'candidate', 'third'].map((key) => localStorage.getItem(`iperf-${key}`)).filter(Boolean);
    const ids = [...new Set(saved.filter((value) => typeof value === 'string'))];
    $('baseline').dataset.chosen = ids[0] || '';
    (ids.length > 1 ? ids.slice(1) : ['']).forEach((id) => addComparisonRow(id, false));
    comparisonRowsInitialized = true;
  }
  const complete = runs.filter((run) => run.status === 'completed' && !['protocol-job', 'matrix-job'].includes(run.kind) && Object.keys(run.metrics || {}).length);
  const signature = JSON.stringify(complete.map((run) => [run.id, title(run), run.created_at]));
  for (const select of document.querySelectorAll('.comparison-select')) {
    const chosen = select.value || select.dataset.chosen || '';
    if (select.dataset.options !== signature) {
      select.replaceChildren(new Option('Choose a completed test', ''));
      const fragment = document.createDocumentFragment();
      complete.forEach((run) => fragment.append(new Option(`${title(run)} · ${date(run.created_at)}`, run.id)));
      select.append(fragment); select.dataset.options = signature;
    }
    select.value = complete.some((run) => run.id === chosen) ? chosen : '';
    delete select.dataset.chosen;
  }
}
function addComparisonRow(chosen = '', focus = true) {
  const id = `comparison-run-${++comparisonRowSequence}`, row = document.createElement('div');
  row.className = 'comparison-selection'; row.dataset.comparisonRow = id;
  const label = document.createElement('label'); label.htmlFor = id; label.className = 'form-label'; label.textContent = 'Additional test';
  const select = document.createElement('select'); select.id = id; select.className = 'form-select comparison-select'; select.dataset.chosen = chosen;
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn btn-outline-secondary'; remove.dataset.removeComparison = id; remove.textContent = 'Remove test';
  row.append(label, select, remove); $('comparison-selections').append(row);
  if (comparisonRowsInitialized) comparisonOptions();
  if (focus) select.focus();
}
function comparisonSelectionChanged() {
  comparisonVersion++; comparisonController?.abort();
  comparisonData = null; comparisonRenderVersion++; comparisonTableVersion++; comparisonPlotVersion++;
  $('comparison-result').textContent = 'Updating the selected evidence…';
  $('comparison-legend').replaceChildren(); $('comparison-chart').innerHTML = '';
  $('export-comparison').disabled = true; workflows.controls();
  try { localStorage.setItem('flowbench-comparison-ids', JSON.stringify(comparisonIds())); } catch { message('This browser could not remember the comparison selection.', 'warning'); }
}
async function persistComparisonColor(id) {
  const run = runs.find((row) => row.id === id);
  if (!run || run.settings.color) return;
  const used = new Set(comparisonIds().filter((value) => value !== id).map((value) => runs.find((row) => row.id === value)).filter(Boolean).map(runColor));
  const current = runColor(run), color = used.has(current) ? palette.find((value) => !used.has(value)) || current : current;
  await api(`/api/runs/${id}`, {method: 'PATCH', body: JSON.stringify({color})});
  run.settings.color = color;
}
async function loadHistory() {
  const status = await api('/api/status');
  renderStatus(status);
  if (!$('history-body') && !$('baseline') && !testPage && !$('project-form')) {
    activeId = status.active_run; lastHistory = Date.now();
    $('saved-count').textContent = `${status.saved_runs} saved sessions`; controls(); return;
  }
  const data = await api('/api/runs');
  runs = data.runs; activeId = data.active_run; lastHistory = Date.now();
  $('saved-count').textContent = `${runs.length} saved session${runs.length === 1 ? '' : 's'}`;
  controls(); renderHistory(); comparisonOptions();
  workflows.history(runs);
}
function comparisonIds() { return [...document.querySelectorAll('.comparison-select')].map((select) => select.value).filter(Boolean); }
function comparisonPath() { return '/api/comparison?' + new URLSearchParams(comparisonIds().map((id) => ['id', id])); }
async function overlay(renderLegend = true) {
  const plotGeneration = ++comparisonPlotVersion;
  const width = chartWidth('comparison-chart'), right = width - 15, ticks = width < 600 ? 3 : 5;
  const remote = $('comparison-perspective').value === 'remote';
  const records = comparisonData?.runs || [];
  records.forEach((run, index) => { if (!knownComparisonRuns.has(run.id)) { if (index >= 8) hiddenComparisonRuns.add(run.id); knownComparisonRuns.add(run.id); } });
  const lines = [];
  records.forEach((run, index) => {
    for (const direction of ['upload', 'download']) {
      const input = (remote ? run.peer?.series : run.series) || [];
      const valid = input.filter((p) => p.direction === direction && Number.isFinite(p.seconds) && Number.isFinite(p.mbps));
      const data = [...new Map(valid.map((p) => [p.seconds, p])).values()].sort((a,b) => a.seconds-b.seconds);
      if (data.length) lines.push({data, direction, color: runColor(run), name: `${index + 1}. ${title(run)}`, id: run.id, index});
    }
  });
  if (renderLegend) renderComparisonLegend(records, remote);
  const points = lines.flatMap((l) => l.data);
  if (!points.length) { $('comparison-unit').textContent = 'bps'; $('comparison-visible-count').textContent = 'No iPerf intervals are available for this selection. All selected evidence remains in tables and exports.'; $('comparison-chart').innerHTML = `<text x="${width / 2}" y="115" text-anchor="middle" fill="#aab3bb">No iPerf interval data for this selection</text>`; return; }
  const maxTime = points.reduce((max, point) => Math.max(max, point.seconds), 1), maxRate = points.reduce((max, point) => Math.max(max, point.mbps), 0) * 1.1 || 1e-6;
  const scale = rateScale(points.map((point) => point.mbps * 1e6));
  $('comparison-unit').textContent = scale.unit;
  $('comparison-chart').setAttribute('aria-label', `Selected throughput traces by elapsed seconds in ${scale.unit}`);
  const x = (t) => 57 + t / maxTime * (width - 72), y = (v) => 202 - v / maxRate * 177;
  let svg = '';
  for (let i=0;i<=ticks;i++) {
    svg += `<line x1="57" y1="${y(i*maxRate/ticks)}" x2="${right}" y2="${y(i*maxRate/ticks)}" stroke="#353c41"/><text x="47" y="${y(i*maxRate/ticks)+3}" text-anchor="end" fill="#aab3bb" font-size="12">${number(i*maxRate/ticks*1e6/scale.divisor,1)}</text><text x="${x(i*maxTime/ticks)}" y="222" text-anchor="${i ? i === ticks ? 'end' : 'middle' : 'start'}" fill="#aab3bb" font-size="12">${number(i*maxTime/ticks,1)}s</text>`;
  }
  for (const line of lines) {
    if (hiddenComparisonRuns.has(line.id)) continue;
    let path = '', markers = '', markerStride = Math.max(1, Math.ceil(line.data.length / 500));
    for (let index = 0; index < line.data.length; index++) {
      if (index && index % 2500 === 0) { await new Promise((resolve) => requestAnimationFrame(resolve)); if (plotGeneration !== comparisonPlotVersion) return; }
      const point = line.data[index], previous = line.data[index - 1];
      path += `${index && Number.isFinite(point.start) && point.start <= previous.seconds + 0.000001 ? 'L' : 'M'} ${x(point.seconds)},${y(point.mbps)} `;
      if (index % markerStride === 0 || index === line.data.length - 1) markers += `<circle cx="${x(point.seconds)}" cy="${y(point.mbps)}" r="3" fill="${line.color}"><title>${escape(line.name)} · ${line.direction}: ${formatMbps(point.mbps, 2, scale)} at ${number(point.seconds)}s</title></circle>`;
    }
    svg += `<path d="${path}" fill="none" stroke="${line.color}" stroke-width="2.4" ${line.direction === 'download' ? 'stroke-dasharray="7 5"' : ''}><title>${escape(line.name)} ${line.direction} measured curve; point tooltips are sampled for display only.</title></path>` + markers;
    const last = line.data.at(-1);
    svg += `<text x="${x(last.seconds)}" y="${Math.max(15, y(last.mbps) + (line.direction === 'upload' ? -8 : 13))}" text-anchor="end" fill="${line.color}" font-size="11">${line.index + 1} ${line.direction === 'upload' ? 'U' : 'D'}<title>${escape(line.name)} ends at ${number(last.seconds)} elapsed seconds; ${line.direction}</title></text>`;
  }
  if (plotGeneration !== comparisonPlotVersion) return;
  $('comparison-chart').innerHTML = svg;
  const shown = records.filter((run) => !hiddenComparisonRuns.has(run.id)).length;
  $('comparison-visible-count').textContent = `${shown} of ${records.length} traces shown. All selected runs remain in tables, exports and reports. Point markers are sampled on dense curves; measured paths and saved values are retained.`;
}
function renderComparisonLegend(records, remote) {
  const generation = ++comparisonRenderVersion; $('comparison-legend').replaceChildren();
  let index = 0;
  const batch = () => {
    if (generation !== comparisonRenderVersion) return;
    const fragment = document.createDocumentFragment(), end = Math.min(index + 20, records.length);
    for (; index < end; index++) {
      const run = records[index], item = document.createElement('div'); item.className = 'comparison-legend-row';
      item.innerHTML = `<label class="comparison-trace"><input type="checkbox" data-visible-run="${escape(run.id)}" ${hiddenComparisonRuns.has(run.id) ? '' : 'checked'}><span class="comparison-color-swatch" style="background:${runColor(run)}"></span><span>${index + 1}. ${escape(title(run))}${index === 0 ? ' · baseline' : ''}${remote && !run.peer?.series?.length ? ' · remote intervals unavailable' : ''}</span></label><label class="comparison-color-label">Saved color<input type="color" class="comparison-color" data-run-color="${escape(run.id)}" value="${runColor(run)}" aria-label="Saved trace color for ${escape(title(run))}"></label>`;
      fragment.append(item);
    }
    $('comparison-legend').append(fragment);
    if (index < records.length) requestAnimationFrame(batch);
  };
  batch();
}
function renderComparison() {
  overlay();
  const generation = ++comparisonTableVersion;
  const remote = $('comparison-perspective').value === 'remote';
  const records = comparisonData.runs;
  const values = records.map((run) => remote ? run.peer?.metrics || {} : run.metrics || {});
  const keys = [...new Set(values.flatMap((v) => Object.keys(v)))].sort();
  $('comparison-result').innerHTML = `<p class="comparison-note">Shared measured window: ${number(comparisonData.common_window_seconds)} seconds. The table contains whole-run final aggregates; 30-second and 10-second averages represent different durations. Curves share elapsed seconds and stop at their own last interval. ${escape(comparisonData.common_window?.note || '')}</p><div class="table-responsive"><table class="table"><caption>Whole-run final measurements for all ${records.length} selected tests. Missing measurements stay unavailable.</caption><thead><tr><th>Test</th>${keys.map((key) => `<th>${escape(metricLabel(key))}</th>`).join('')}</tr></thead><tbody id="comparison-metric-rows"></tbody></table></div><div id="comparison-change-details"></div><p class="comparison-note">Repeat under matching conditions to establish improvement. Missing metrics display —.</p>`;
  const reports = comparisonData.comparisons;
  let index = 0;
  const batch = () => {
    if (generation !== comparisonTableVersion) return;
    const rowFragment = document.createDocumentFragment(), detailFragment = document.createDocumentFragment();
    const end = Math.min(index + 20, records.length);
    for (; index < end; index++) {
      const run = records[index], tr = document.createElement('tr');
      tr.innerHTML = `<th scope="row"><span class="comparison-color-swatch" style="background:${runColor(run)}"></span>${index + 1}. ${escape(title(run))}${index === 0 ? ' · baseline' : ''}</th>${keys.map((key) => `<td>${metricValue(key,values[index][key])}</td>`).join('')}`;
      rowFragment.append(tr);
      if (!index) continue;
      const report = reports[index - 1], details = document.createElement('details'); details.className = 'mt-3';
      const common = Object.keys(values[0]).filter((key) => Number.isFinite(values[0][key]) && Number.isFinite(values[index][key]));
      details.innerHTML = `<summary>Change from baseline → ${index + 1}. ${escape(title(run))}</summary><p class="comparison-note">${report?.settings_differences?.length ? 'Settings differ: ' + escape(report.settings_differences.join(', ')) : 'Settings match.'} ${escape(report?.interpretation || '')}</p><div class="table-responsive"><table class="table"><thead><tr><th>Whole-run metric</th><th>Change</th><th>Change %</th></tr></thead><tbody>${common.map((key) => {
        const delta = values[index][key] - values[0][key], percent = values[0][key] ? delta / values[0][key] * 100 : null;
        return `<tr><td>${escape(metricLabel(key))}</td><td>${delta > 0 ? '+' : ''}${metricValue(key,delta)}</td><td>${percent === null ? 'Undefined from zero' : (percent > 0 ? '+' : '') + number(percent,1)+'%'}</td></tr>`;
      }).join('')}</tbody></table></div>`;
      detailFragment.append(details);
    }
    $('comparison-metric-rows').append(rowFragment); $('comparison-change-details').append(detailFragment);
    if (index < records.length) requestAnimationFrame(batch);
  };
  batch();
}
async function compare() {
  if (!$('baseline')) return;
  const generation = ++comparisonVersion;
  comparisonTableVersion++;
  comparisonController?.abort(); comparisonController = new AbortController();
  $('export-comparison').disabled = true;
  try { localStorage.setItem('flowbench-comparison-ids', JSON.stringify(comparisonIds())); } catch { /* Optional local preference only. */ }
  comparisonData = null; overlay();
  const ids = comparisonIds();
  if (!$('baseline').value || ids.length < 2) { $('comparison-result').textContent = 'Choose a completed baseline and at least one additional test.'; return; }
  if (new Set(ids).size !== ids.length) { $('comparison-result').textContent = 'Choose different tests in each row.'; return; }
  $('comparison-result').textContent = `Loading ${ids.length} selected tests…`;
  try {
    for (const id of ids) {
      await persistComparisonColor(id);
      if (generation !== comparisonVersion) return;
    }
    const data = await api(comparisonPath(), {signal: comparisonController.signal});
    if (generation !== comparisonVersion) return;
    comparisonData = data; renderComparison(); $('export-comparison').disabled = false;
    workflows.controls();
  } catch(error) { if (generation === comparisonVersion) $('comparison-result').textContent = error.message; }
}
async function download(path, fallback) {
  const link = document.createElement('a');
  // Use the server's attachment response rather than copying the full archive
  // into a browser blob. This also supports ordinary browser download handling.
  link.href = path;
  link.download = `${fallback}.${$('export-format').value}`;
  document.body.append(link); link.click(); link.remove();
}
function exportRun(id) { return download(`/api/runs/${id}/export?format=${$('export-format').value}`, `iperf-${id}`); }

$('test-form')?.addEventListener('submit', safe(async (event) => {
  event.preventDefault();
  $('message').classList.add('d-none');
  const data = settingsData();
  $('run-button').disabled = true;
  try {
    const run = await api('/api/runs', {method: 'POST', body: JSON.stringify(data)});
    activeId = run.id; controls(); await selectRun(run.id); await loadHistory();
  } finally { controls(); }
}));
$('stop-button')?.addEventListener('click', safe(async () => {
  if (activeId) await api(`/api/runs/${activeId}/stop`, {method: 'POST', body: '{}'});
  await loadHistory();
}));
$('role')?.addEventListener('change', fields); $('protocol')?.addEventListener('change', fields);
$('server-preset')?.addEventListener('change', () => applyPreset($('server-preset').value));
$('preset-search')?.addEventListener('input', renderPresetPicker);
for (const id of ['target', 'port', 'mode', 'ip_version']) $(id)?.addEventListener('change', fields);
$('catalog-search')?.addEventListener('input', renderCatalog); $('catalog-kind')?.addEventListener('change', renderCatalog);
$('save-preset')?.addEventListener('click', safe(() => savePreset(false)));
$('update-preset')?.addEventListener('click', safe(() => savePreset(true)));
$('preset-body')?.addEventListener('click', safe(async (event) => {
  const button = event.target.closest('button');
  if (button?.dataset.preset) location.href = `/?preset=${encodeURIComponent(button.dataset.preset)}`;
  if (button?.dataset.removePreset) {
    const row = presets.find((p) => p.id === button.dataset.removePreset);
    await api(`/api/presets/${row.id}`, {method: 'DELETE', body: '{}'});
    await loadPresets(); message(`Custom preset “${row.name}” removed.`, 'success');
  }
}));
$('refresh-presets')?.addEventListener('click', safe(async () => {
  $('refresh-presets').disabled = true;
  try { const result = await api('/api/presets/refresh', {method: 'POST', body: '{}'}); await loadPresets(); message(`Public list refreshed: ${result.public} servers. Your ${result.custom} custom presets are saved.`, 'success'); }
  finally { $('refresh-presets').disabled = false; }
}));
$('import-presets')?.addEventListener('click', () => $('preset-file').click());
$('preset-file')?.addEventListener('change', safe(async () => {
  try { const result = await uploadFile($('preset-file').files[0], '/api/presets/import', 8 * 1024 * 1024); await loadPresets(); message(`Server list imported: ${result.added} added, ${result.updated} updated.`, 'success'); }
  finally { $('preset-file').value = ''; }
}));
$('import-runs')?.addEventListener('click', () => $('run-files').click());
$('run-files')?.addEventListener('change', safe(async () => {
  $('import-runs').disabled = true;
  let count = 0, skipped = 0; const errors = [];
  try {
    for (const file of $('run-files').files) {
      try { const result = await uploadFile(file, '/api/runs/import', 20 * 1024 * 1024); count += result.imported.length; skipped += result.duplicates.length; }
      catch (error) { errors.push(`${file.name}: ${error.message}`); }
    }
    await loadHistory(); await compare();
    message(`${count} tests imported; ${skipped} duplicates skipped.${errors.length ? ` ${errors.join(' ')}` : ''}`, errors.length ? 'warning' : 'success');
  } finally { $('run-files').value = ''; $('import-runs').disabled = false; }
}));
$('log-filter')?.addEventListener('change', renderLogs); $('log-search')?.addEventListener('input', renderLogs);
$('chart-perspective')?.addEventListener('change', renderPeer);
$('comparison-perspective')?.addEventListener('change', () => comparisonData ? renderComparison() : overlay());
$('autoscroll')?.addEventListener('change', renderLogs); $('history-search')?.addEventListener('input', renderHistory);
$('refresh-history')?.addEventListener('click', safe(loadHistory));
$('history-body')?.addEventListener('click', safe(async (event) => {
  const button = event.target.closest('button');
  if (button?.dataset.open) location.href = `/?run=${encodeURIComponent(button.dataset.open)}`;
  if (button?.dataset.export) await exportRun(button.dataset.export);
}));
$('export-button')?.addEventListener('click', safe(() => exportRun(selectedId)));
$('rerun-button')?.addEventListener('click', () => {
  if (!selected) return;
  if (workflows.loadFlentSettings(selected)) return;
  $('server-preset').value = ''; editingPreset = null; $('update-preset').disabled = true;
  loadSettings(selected.settings); $('target').focus();
});
$('save-notes')?.addEventListener('click', async () => {
  if ($('save-notes').disabled) return;
  $('save-notes').disabled = true;
  try {
    await api(`/api/runs/${selectedId}`, {method: 'PATCH', body: JSON.stringify({label: $('saved-label').value, notes: $('saved-notes').value, ...workflows.annotationSettings()})});
    await loadSelected(); await loadHistory(); $('notes-status').textContent = 'Label, notes, project and tags saved.';
  } catch (error) { $('notes-status').textContent = error.message; message(error.message); }
  finally { $('save-notes').disabled = false; }
});
$('add-comparison')?.addEventListener('click', () => addComparisonRow());
$('comparison-selections')?.addEventListener('change', safe(async (event) => {
  if (!event.target.matches('.comparison-select')) return;
  comparisonSelectionChanged();
  if (event.target.value) await persistComparisonColor(event.target.value);
  await compare();
}));
$('comparison-selections')?.addEventListener('click', safe(async (event) => {
  const button = event.target.closest('[data-remove-comparison]'); if (!button) return;
  const row = button.closest('.comparison-selection'), previous = row.previousElementSibling;
  row.remove(); comparisonSelectionChanged(); previous?.querySelector('select')?.focus(); await compare();
}));
$('comparison-legend')?.addEventListener('change', safe(async (event) => {
  const input = event.target;
  if (input.dataset.visibleRun) {
    if (input.checked) hiddenComparisonRuns.delete(input.dataset.visibleRun); else hiddenComparisonRuns.add(input.dataset.visibleRun);
    await overlay(false);
  }
  if (input.dataset.runColor) {
    const id = input.dataset.runColor; input.disabled = true;
    try {
      await api(`/api/runs/${id}`, {method: 'PATCH', body: JSON.stringify({color: input.value})});
      for (const row of [...runs, ...(comparisonData?.runs || [])]) if (row.id === id) row.settings.color = input.value;
      workflows.controls(); await overlay(false);
      input.closest('.comparison-legend-row').querySelector('.comparison-color-swatch').style.background = input.value;
    } finally { input.disabled = false; }
  }
}));
$('export-comparison')?.addEventListener('click', safe(() => download(`${comparisonPath()}&format=${$('export-format').value}`, 'iperf-comparison')));
document.querySelectorAll('.sidebar-menu .nav-link').forEach((link) => {
  if (link.getAttribute('href') === location.pathname) link.setAttribute('aria-current', 'page');
});

async function tick() {
  try {
    if (document.hidden) return;
    if (Date.now() - lastHistory > 3000) await loadHistory();
    if (selectedId && selected && (activeId === selectedId || !terminal.has(selected.status))) await loadSelected();
  } catch (error) { message(`Connection problem: ${error.message}`); }
  finally { setTimeout(tick, 800); }
}
async function init() {
  try {
    const status = await api('/api/status');
    renderStatus(status);
    await loadHistory();
    await workflows.init();
    if (testPage || $('preset-body')) await loadPresets();
    if (testPage) {
      fields();
      if (query.get('preset')) applyPreset(query.get('preset'));
      if (query.get('run') || activeId) await selectRun(query.get('run') || activeId);
    }
    await compare();
  } catch (error) { message(error.message); }
  tick();
}
init();

$('listener-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  $('listener-start').disabled = true;
  $('listener-start').textContent = 'Enabling…';
  $('listener-error').classList.add('d-none');
  delete $('listener-form').dataset.localError;
  $('listener-form').querySelectorAll('.field-error').forEach((field) => field.remove());
  $('listener-form').querySelectorAll('[aria-invalid]').forEach((field) => field.removeAttribute('aria-invalid'));
  try {
    const state = await api('/api/listener', {method: 'POST', body: JSON.stringify({bind: $('listener-bind').value, port: $('listener-port').value,
      max_duration: $('listener-duration').value, label: $('listener-label').value, notes: $('listener-notes').value})});
    renderListener(state);
    message('Managed listener enabled. Incoming tests will be saved without keeping this page open.', 'success');
  } catch (error) { listenerFormError(error); }
  finally { $('listener-start').textContent = 'Enable listener'; if (listenerState?.state !== 'listening') $('listener-start').disabled = !engineReady; }
});
$('listener-stop')?.addEventListener('click', async () => {
  $('listener-stop').disabled = true;
  $('listener-stop').textContent = 'Stopping…';
  try { renderListener(await api('/api/listener/stop', {method: 'POST', body: '{}'})); message('Listener stopped and disabled. Saved evidence is retained.', 'success'); }
  catch (error) { listenerFormError(error); }
  finally { $('listener-stop').textContent = 'Stop listener'; }
});

function renderResults() {
  const job = ['protocol-job', 'matrix-job', 'flent-run'].includes(selected.kind);
  $('result-cards').classList.toggle('d-none', job || !terminal.has(selected.status));
  $('live-metrics').classList.toggle('d-none', job || terminal.has(selected.status));
  const metrics = selected.metrics || {};
  for (const direction of ['upload','download']) {
    rateCard('result-' + direction, metrics[direction+'_mbps'], 'result-' + direction + '-unit');
    $('result-' + direction + '-foot').textContent = Number.isFinite(metrics[direction+'_mbps']) ? 'Final receiver report' : 'No final measurement reported';
  }
  $('result-runtime').textContent = number(selected.overview?.runtime_seconds);
  $('result-outcome').textContent = selected.status;
  $('result-outcome-box').className = `metric-card outcome-card outcome-${selected.status}`;
  const losses = ['upload','download'].filter((d) => Number.isFinite(metrics[d+'_lost_percent'])).map((d) => `${d} loss ${number(metrics[d+'_lost_percent'])}%`);
  $('result-quality').textContent = losses.join(' · ') || 'No packet-loss measurement reported';
  const route = selected.route;
  $('route-note').textContent = route ? `${route.status}: ${route.note || ''}` : 'No route evidence in this saved run.';
  $('route-output').textContent = route?.raw_output || '';
  const hops = route?.hops || [];
  $('route-hops').innerHTML = hops.length ? `<table class="table"><thead><tr><th>Hop</th><th>Address</th><th>Probe RTT · ms</th></tr></thead><tbody>${hops.map((h) => `<tr><td>${escape(h.hop)}</td><td>${escape(h.address || 'No reply')}</td><td>${[...(h.rtt_ms || []).map((v) => number(v)), ...(h.rtt_upper_bound_ms || []).map((v) => '&lt;' + number(v))].join(' / ') || '—'}</td></tr>`).join('')}</tbody></table>` : '';
  if (selected.incoming || selected.concurrency) {
    const notes = [];
    if (selected.incoming) notes.push(`Managed incoming capture from ${selected.incoming.remote_host || 'unknown peer'}. Directions use that client's perspective: upload enters this server, download leaves it.`);
    if (selected.concurrency) notes.push(selected.concurrency.note);
    $('route-note').textContent = notes.join(' ');
  }
}

window.addEventListener('resize', () => { if (testPage && selected) renderPeer(); if ($('comparison-chart')) overlay(); });
