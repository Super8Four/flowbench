const $ = (id) => document.getElementById(id);
const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
let runs = [], activeId = null, selectedId = null, selected = null, events = [];
let engineReady = false, capabilities = {}, selectionVersion = 0, lastHistory = 0, comparisonVersion = 0;
let presets = [], presetCatalog = {}, editingPreset = null, comparisonData = null;
const testPage = Boolean($('test-form'));
const query = new URLSearchParams(location.search);
const number = (value, digits = 2) => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: digits}) : '—';
const date = (value) => value ? new Date(value).toLocaleString() : '—';
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const title = (run) => run.settings.label || `${run.settings.role === 'server' ? 'Server' : run.settings.target} · ${run.effective_settings?.protocol || run.settings.protocol}`;

async function api(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', ...options,
    headers: {'Content-Type': 'application/json', ...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function message(text, type = 'danger') {
  $('message').className = `alert alert-${type}`;
  $('message').textContent = text;
}
function safe(action) {
  return async (event) => { try { await action(event); } catch (error) { message(error.message); } };
}
function controls() {
  if (!testPage) return;
  $('run-button').disabled = !engineReady || Boolean(activeId);
  $('run-button').textContent = activeId ? 'Session running…' : '▶ Start session';
  $('stop-button').disabled = !activeId;
}
function fields() {
  const server = $('role').value === 'server';
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
  $('bandwidth').disabled = server || $('protocol').value !== 'udp';
  $('bandwidth-field').classList.toggle('d-none', $('protocol').value !== 'udp');
  const bidir = $('mode').querySelector('[value="bidir"]');
  bidir.disabled = !capabilities.bidir || ($('protocol').value === 'udp' && !capabilities.udp_bidir);
  if (bidir.disabled && $('mode').value === 'bidir') $('mode').value = 'upload';
  presetDetail();
}

function settingsData() {
  const data = Object.fromEntries(new FormData($('test-form')));
  data.debug = $('debug').checked;
  data.traceroute = !($('role').value === 'server') && $('traceroute').checked;
  // Disabled UDP fields still belong to a reusable configuration.
  data.bandwidth = $('bandwidth').value;
  return data;
}
function loadSettings(settings) {
  for (const [key, value] of Object.entries({...settings, ip_version: settings.ip_version || 'auto'})) {
    if (!$(key)) continue;
    if ($(key).type === 'checkbox') $(key).checked = value; else $(key).value = value;
  }
  fields();
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
  if (!row.protocols.includes($('protocol').value)) notices.push('This protocol is not listed as supported.');
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
  $('log-output').textContent = visible.map((row) => `[${date(row.timestamp)}] [${row.source}] ${row.message}`).join('\n') || 'No matching output.';
  if ($('autoscroll').checked) $('log-output').scrollTop = $('log-output').scrollHeight;
}
function smoothPath(data, x, y) {
  const points = data.map((p) => ({x: x(p.seconds), y: y(p.mbps)}));
  if (points.length === 1) return `M ${points[0].x},${points[0].y}`;
  const slopes = points.slice(1).map((p, i) => (p.y - points[i].y) / (p.x - points[i].x));
  const tangents = points.map((p, i) => {
    if (!i) return slopes[0];
    if (i === points.length - 1) return slopes.at(-1);
    const left = slopes[i - 1], right = slopes[i];
    if (left * right <= 0) return 0;
    return Math.sign(left) * Math.min(Math.abs((left + right) / 2), 3 * Math.min(Math.abs(left), Math.abs(right)));
  });
  let path = `M ${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], step = (b.x - a.x) / 3;
    path += ` C ${a.x + step},${a.y + tangents[i - 1] * step} ${b.x - step},${b.y - tangents[i] * step} ${b.x},${b.y}`;
  }
  return path;
}
function chart(localPoints, remotePoints = []) {
  const perspective = $('chart-perspective').value;
  const lines = [];
  for (const [source, input] of [['local', localPoints], ['remote', remotePoints]]) {
    if (perspective !== 'compare' && source !== perspective) continue;
    for (const [direction, color] of [['upload', '#1683ff'], ['download', '#15d3a6']]) {
      const valid = (input || []).filter((p) => p.direction === direction && Number.isFinite(p.seconds) && Number.isFinite(p.mbps));
      const data = Array.from(new Map(valid.map((p) => [p.seconds, p])).values()).sort((a, b) => a.seconds - b.seconds);
      if (data.length) lines.push({source, direction, color, data});
    }
  }
  const points = lines.flatMap((line) => line.data);
  $('peer-legend').classList.toggle('d-none', perspective !== 'compare');
  if (!points.length) {
    $('throughput-chart').innerHTML = `<text x="380" y="115" text-anchor="middle" fill="#9da7b0" font-size="13">${perspective === 'remote' ? 'No remote interval measurements are available' : 'Interval measurements will appear here'}</text>`;
    return;
  }
  const maxTime = points.reduce((max, p) => Math.max(max, p.seconds), 1);
  const rawMax = points.reduce((max, p) => Math.max(max, p.mbps), 1) * 1.08;
  const step = 10 ** Math.floor(Math.log10(rawMax)) / 2;
  const maxRate = Math.ceil(rawMax / step) * step;
  const x = (t) => 57 + t / maxTime * 688, y = (n) => 202 - n / maxRate * 177;
  let svg = '<defs>';
  for (const line of lines) svg += `<linearGradient id="fill-${line.source}-${line.direction}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${line.color}" stop-opacity="${line.source === 'remote' && perspective === 'compare' ? '.14' : '.32'}"/><stop offset="1" stop-color="${line.color}" stop-opacity=".025"/></linearGradient>`;
  svg += '</defs>';
  for (let i = 0; i <= 6; i++) {
    const value = maxRate * i / 6, height = y(value);
    svg += `<line x1="57" y1="${height}" x2="745" y2="${height}" stroke="#353c41"/><text x="47" y="${height + 3}" text-anchor="end" fill="#aab3bb" font-size="9">${number(value, maxRate < 10 ? 1 : 0)}</text>`;
  }
  for (let i = 0; i <= 6; i++) {
    const seconds = maxTime * i / 6;
    svg += `<text x="${x(seconds)}" y="222" text-anchor="${i === 0 ? 'start' : i === 6 ? 'end' : 'middle'}" fill="#aab3bb" font-size="9">${number(seconds, 1)}s</text>`;
  }
  for (const line of lines) {
    const path = smoothPath(line.data, x, y), dashed = line.source === 'remote' && perspective === 'compare';
    svg += `<path d="${path} L ${x(line.data.at(-1).seconds)},202 L ${x(line.data[0].seconds)},202 Z" fill="url(#fill-${line.source}-${line.direction})"/>`;
    svg += `<path d="${path}" stroke="${line.color}" stroke-width="${dashed ? '1.6' : '1.9'}" ${dashed ? 'stroke-dasharray="6 4"' : ''} fill="none"/>`;
    for (const p of line.data) svg += `<circle cx="${x(p.seconds)}" cy="${y(p.mbps)}" r="${line.data.length === 1 ? 3 : 5}" fill="${line.color}" fill-opacity="${line.data.length === 1 ? 1 : 0}"><title>${line.source} ${line.direction}: ${number(p.mbps)} Mbps at ${number(p.seconds, 2)}s</title></circle>`;
  }
  $('throughput-chart').innerHTML = svg;
}
function renderPeer() {
  const peer = selected?.peer;
  $('peer-output').textContent = peer?.raw_output || peer?.note || 'Remote output will appear after the server supplies it.';
  const perspective = $('chart-perspective').value;
  $('chart-source-note').textContent = perspective === 'local' ? 'Local iPerf measurements · blue: upload · green: download. Hover a measured point for its value.' : (peer?.note || 'Choose a session to view the remote server output.');
  const visible = perspective !== 'local';
  $('peer-summary').classList.toggle('d-none', !visible);
  if (visible) {
    const left = selected?.metrics || {}, right = peer?.metrics || {};
    const keys = Object.keys({...left, ...right}).sort();
    $('peer-summary').innerHTML = keys.length ? `<div class="table-responsive"><table class="table"><thead><tr><th>Metric · client perspective</th><th>Local report</th><th>Remote report</th></tr></thead><tbody>${keys.map((key) => `<tr><td>${escape(key.replaceAll('_', ' '))}</td><td>${number(left[key])}</td><td>${number(right[key])}</td></tr>`).join('')}</tbody></table></div><p class="small text-body-secondary">${escape(peer?.note || '')}</p>` : `<p class="small text-body-secondary">${escape(peer?.note || 'No results yet.')}</p>`;
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
    $(direction + '-rate').textContent = number(metrics[direction + '_mbps'] ?? (!terminal.has(selected.status) ? latest?.mbps : undefined));
    $(direction + '-peak').textContent = `Mbps · Peak ${number(selected.overview?.['max_' + direction + '_mbps'])} Mbps`;
  }
  $('exit-code').textContent = selected.error || (selected.imported ? `Imported from ${selected.imported.filename} · ${date(selected.imported.at)}` : (selected.exit_code === null ? 'Capturing output to disk…' : `Process exit code: ${selected.exit_code} · ${date(selected.finished_at)}`));
  $('export-button').disabled = false;
  $('rerun-button').disabled = false;
  renderPeer();
  renderLogs();
  renderResults();
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
}
function historyTrend(overview) {
  const points = overview?.trend || [];
  if (!points.length) return '<span class="history-muted">No interval data</span>';
  const maxSeconds = Math.max(1, ...points.map((point) => point.seconds));
  const maxRate = Math.max(1, ...points.map((point) => point.mbps));
  const x = (point) => (4 + point.seconds / maxSeconds * 128).toFixed(2);
  const y = (point) => (32 - point.mbps / maxRate * 28).toFixed(2);
  const lines = ['upload', 'download'].map((direction) => {
    const values = points.filter((point) => point.direction === direction);
    if (!values.length) return '';
    const color = direction === 'upload' ? '#1683ff' : '#15d3a6';
    return `<polyline points="${values.map((point) => `${x(point)},${y(point)}`).join(' ')}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round"/>` +
      values.map((point) => `<circle cx="${x(point)}" cy="${y(point)}" r="1.3" fill="${color}"><title>${direction}: ${number(point.mbps)} Mbps at ${number(point.seconds)}s</title></circle>`).join('');
  }).join('');
  return `<svg class="history-sparkline" viewBox="0 0 140 36" role="img" aria-label="Upload and download throughput trend, scaled to this test"><title>Upload (blue), download (green). Peak ${number(maxRate)} Mbps; ${number(maxSeconds)} seconds. Scale varies by test.</title>${lines}</svg>`;
}
function renderHistory() {
  if (!$('history-body')) return;
  const search = $('history-search').value.toLowerCase();
  const visible = runs.filter((run) => `${title(run)} ${run.settings.target} ${run.settings.notes}`.toLowerCase().includes(search));
  $('history-body').innerHTML = visible.map((run) => {
    const config = run.effective_settings || run.settings;
    const stats = run.overview || {};
    const color = {completed: 'success', failed: 'danger', cancelled: 'secondary', interrupted: 'warning', running: 'primary', starting: 'primary'}[run.status] || 'secondary';
    return `<tr><td class="history-session"><button class="history-link" data-open="${run.id}">${escape(title(run))}</button><div class="history-date">${escape(date(run.created_at))} ${run.imported ? '<span class="badge text-bg-secondary">Imported</span>' : ''}</div></td>
      <td>${escape(run.settings.target || run.settings.bind || 'All interfaces')}:${run.settings.port}<div class="history-date">${escape(config.protocol.toUpperCase())} · ${escape(config.mode)} · ${config.streams} stream(s) · ${escape(run.settings.role)}</div></td>
      <td><span class="badge text-bg-${color}">${escape(run.status)}</span></td>
      <td class="history-speed"><strong>${number(stats.max_upload_mbps)}</strong><div class="history-date">Avg ${number(run.metrics.upload_mbps)}</div></td>
      <td class="history-speed"><strong>${number(stats.max_download_mbps)}</strong><div class="history-date">Avg ${number(run.metrics.download_mbps)}</div></td>
      <td class="text-nowrap"><strong>${number(stats.runtime_seconds)}${Number.isFinite(stats.runtime_seconds) ? 's' : ''}</strong><div class="history-date">${config.role === 'server' && !run.test_parameters ? 'Measured time' : `Set ${number(config.duration)}s`}</div></td>
      <td>${historyTrend(stats)}</td>
      <td class="text-end text-nowrap"><button class="btn btn-sm btn-outline-primary" data-open="${run.id}">Open</button> <button class="btn btn-sm btn-outline-secondary" data-export="${run.id}">Export</button></td></tr>`;
  }).join('') || '<tr><td colspan="8" class="text-center py-5 history-muted">No matching saved sessions.</td></tr>';
  $('history-count').textContent = `${visible.length} of ${runs.length} saved sessions`;
}
function comparisonOptions() {
  if (!$('baseline')) return;
  const complete = runs.filter((run) => run.status === 'completed' && Object.keys(run.metrics).length);
  for (const id of ['baseline', 'candidate', 'third']) {
    const chosen = $(id).value || localStorage.getItem(`iperf-${id}`) || '';
    $(id).replaceChildren(new Option('Choose a completed test', ''));
    for (const run of complete) $(id).add(new Option(`${title(run)} · ${date(run.created_at)}`, run.id));
    if (complete.some((run) => run.id === chosen)) $(id).value = chosen;
  }
}
async function loadHistory() {
  if (!$('history-body') && !$('baseline')) {
    const data = await api('/api/status'); activeId = data.active_run; lastHistory = Date.now();
    $('saved-count').textContent = `${data.saved_runs} saved sessions`; controls(); return;
  }
  const data = await api('/api/runs');
  runs = data.runs; activeId = data.active_run; lastHistory = Date.now();
  $('saved-count').textContent = `${runs.length} saved session${runs.length === 1 ? '' : 's'}`;
  controls(); renderHistory(); comparisonOptions();
}
function comparisonIds() { return ['baseline', 'candidate', 'third'].map((id) => $(id).value).filter(Boolean); }
function comparisonPath() { return '/api/comparison?' + new URLSearchParams(comparisonIds().map((id) => ['id', id])); }
function overlay() {
  const colors = ['#1683ff', '#15d3a6', '#ffc857'];
  const remote = $('comparison-perspective').value === 'remote';
  const records = comparisonData?.runs || [];
  const lines = [];
  records.forEach((run, index) => {
    for (const direction of ['upload', 'download']) {
      const input = (remote ? run.peer?.series : run.series) || [];
      const valid = input.filter((p) => p.direction === direction && Number.isFinite(p.seconds) && Number.isFinite(p.mbps));
      const data = [...new Map(valid.map((p) => [p.seconds, p])).values()].sort((a,b) => a.seconds-b.seconds);
      if (data.length) lines.push({data, direction, color: colors[index], name: title(run)});
    }
  });
  $('comparison-legend').innerHTML = records.map((run,i) => `<span style="color:${colors[i]}">● ${escape(title(run))} · ${escape(date(run.created_at))}${remote && !run.peer?.series?.length ? ' · remote intervals unavailable' : ''}</span>`).join('');
  const points = lines.flatMap((l) => l.data);
  if (!points.length) { $('comparison-chart').innerHTML = '<text x="380" y="115" text-anchor="middle" fill="#aab3bb">No interval data for this selection</text>'; return; }
  const maxTime = Math.max(1, ...points.map((p) => p.seconds)), maxRate = Math.max(1, ...points.map((p) => p.mbps)) * 1.1;
  const x = (t) => 57 + t / maxTime * 688, y = (v) => 202 - v / maxRate * 177;
  let svg = '';
  for (let i=0;i<=5;i++) {
    svg += `<line x1="57" y1="${y(i*maxRate/5)}" x2="745" y2="${y(i*maxRate/5)}" stroke="#353c41"/><text x="47" y="${y(i*maxRate/5)+3}" text-anchor="end" fill="#aab3bb" font-size="10">${number(i*maxRate/5,1)}</text><text x="${x(i*maxTime/5)}" y="222" text-anchor="middle" fill="#aab3bb" font-size="10">${number(i*maxTime/5,1)}s</text>`;
  }
  for (const line of lines) {
    svg += `<path d="${smoothPath(line.data,x,y)}" fill="none" stroke="${line.color}" stroke-width="2.4" ${line.direction === 'download' ? 'stroke-dasharray="7 5"' : ''}/>`;
    for (const p of line.data) svg += `<circle cx="${x(p.seconds)}" cy="${y(p.mbps)}" r="3" fill="${line.color}"><title>${escape(line.name)} · ${line.direction}: ${number(p.mbps)} Mbps at ${number(p.seconds)}s</title></circle>`;
  }
  $('comparison-chart').innerHTML = svg;
}
function renderComparison() {
  overlay();
  const remote = $('comparison-perspective').value === 'remote';
  const records = comparisonData.runs;
  const values = records.map((run) => remote ? run.peer?.metrics || {} : run.metrics || {});
  const keys = [...new Set(values.flatMap((v) => Object.keys(v)))].sort();
  $('comparison-result').innerHTML = `<div class="table-responsive"><table class="table"><thead><tr><th>Metric</th>${records.map((r) => `<th>${escape(title(r))}</th>`).join('')}</tr></thead><tbody>${keys.map((key) => `<tr><td>${escape(key.replaceAll('_',' '))}</td>${values.map((v) => `<td>${number(v[key])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` + comparisonData.comparisons.map((report,i) => `<p class="comparison-note ${report.settings_differences.length ? 'text-warning-emphasis' : 'text-success-emphasis'}">Baseline → ${escape(title(records[i+1]))}: ${report.settings_differences.length ? 'Settings differ: ' + escape(report.settings_differences.join(', ')) : 'Settings match.'}</p>`).join('') + '<p class="comparison-note">Repeat under matching conditions to establish improvement. Missing metrics display —. Each curve ends at its own last interval.</p>';
  $('comparison-result').innerHTML += records.slice(1).map((run,i) => {
    const left = values[0], right = values[i+1];
    const common = Object.keys(left).filter((key) => Number.isFinite(left[key]) && Number.isFinite(right[key]));
    return `<details class="mt-2"><summary>Change from baseline → ${escape(title(run))}</summary><div class="table-responsive"><table class="table"><thead><tr><th>Metric</th><th>Change</th><th>Change %</th></tr></thead><tbody>${common.map((key) => {
      const delta = right[key]-left[key], percent = left[key] ? delta/left[key]*100 : null;
      return `<tr><td>${escape(key.replaceAll('_',' '))}</td><td>${delta > 0 ? '+' : ''}${number(delta)}</td><td>${percent === null ? 'Undefined from zero' : (percent > 0 ? '+' : '') + number(percent,1)+'%'}</td></tr>`;
    }).join('')}</tbody></table></div></details>`;
  }).join('');

}
async function compare() {
  if (!$('baseline')) return;
  const generation = ++comparisonVersion;
  $('export-comparison').disabled = true;
  for (const id of ['baseline', 'candidate', 'third']) localStorage.setItem(`iperf-${id}`, $(id).value);
  comparisonData = null; overlay();
  if (!$('baseline').value || !$('candidate').value) { $('comparison-result').textContent = 'Choose a baseline and second test; the third is optional.'; return; }
  try {
    const data = await api(comparisonPath());
    if (generation !== comparisonVersion) return;
    comparisonData = data; renderComparison(); $('export-comparison').disabled = false;
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
for (const id of ['target', 'port', 'mode', 'ip_version']) $(id)?.addEventListener('change', presetDetail);
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
  $('server-preset').value = ''; editingPreset = null; $('update-preset').disabled = true;
  loadSettings(selected.settings); $('target').focus();
});
$('save-notes')?.addEventListener('click', safe(async () => {
  await api(`/api/runs/${selectedId}`, {method: 'PATCH', body: JSON.stringify({label: $('saved-label').value, notes: $('saved-notes').value})});
  await loadSelected(); await loadHistory(); message('Session notes saved.', 'success');
}));
for (const id of ['baseline', 'candidate', 'third']) $(id)?.addEventListener('change', safe(compare));
$('export-comparison')?.addEventListener('click', safe(() => download(`${comparisonPath()}&format=${$('export-format').value}`, 'iperf-comparison')));
document.querySelectorAll('.sidebar-menu .nav-link').forEach((link) => link?.addEventListener('click', () => {
  document.querySelectorAll('.sidebar-menu .nav-link').forEach((other) => other.classList.toggle('active', other === link));
}));

async function tick() {
  try {
    if (Date.now() - lastHistory > 3000) await loadHistory();
    if (selectedId && selected && (activeId === selectedId || !terminal.has(selected.status))) await loadSelected();
  } catch (error) { message(`Connection problem: ${error.message}`); }
  finally { setTimeout(tick, 800); }
}
async function init() {
  try {
    const status = await api('/api/status');
    capabilities = status.iperf;
    engineReady = status.iperf.available && !status.storage_error;
    $('engine-badge').textContent = engineReady ? '● iPerf3 connected' : 'iPerf3 unavailable';
    $('engine-version').textContent = status.iperf.version || 'iPerf3 not installed';
    if ($('storage-path')) $('storage-path').textContent = `Saved on disk: ${status.storage}`;
    if (!engineReady) message(status.storage_error || status.iperf.error);
    if (testPage && !status.iperf.bidir) $('mode').querySelector('[value="bidir"]').disabled = true;
    await loadHistory();
    if (testPage || $('preset-body')) await loadPresets();
    if (testPage) {
      if (document.body.dataset.page === 'server') $('role').value = 'server';
      fields();
      if (query.get('preset')) applyPreset(query.get('preset'));
      if (query.get('run') || activeId) await selectRun(query.get('run') || activeId);
    }
    await compare();
  } catch (error) { message(error.message); }
  tick();
}
init();

function renderResults() {
  $('result-cards').classList.toggle('d-none', !terminal.has(selected.status));
  $('live-metrics').classList.toggle('d-none', terminal.has(selected.status));
  const metrics = selected.metrics || {};
  for (const direction of ['upload','download']) {
    $('result-' + direction).textContent = number(metrics[direction+'_mbps']);
    $('result-' + direction + '-foot').textContent = Number.isFinite(metrics[direction+'_mbps']) ? 'Final receiver report' : 'No final measurement reported';
  }
  $('result-runtime').textContent = number(selected.overview?.runtime_seconds);
  $('result-outcome').textContent = selected.status;
  $('result-outcome-box').className = `small-box text-bg-${selected.status === 'completed' ? 'success' : selected.status === 'failed' ? 'danger' : 'warning'}`;
  const losses = ['upload','download'].filter((d) => Number.isFinite(metrics[d+'_lost_percent'])).map((d) => `${d} loss ${number(metrics[d+'_lost_percent'])}%`);
  $('result-quality').textContent = losses.join(' · ') || 'No packet-loss measurement reported';
  const route = selected.route;
  $('route-note').textContent = route ? `${route.status}: ${route.note || ''}` : 'No route evidence in this saved run.';
  $('route-output').textContent = route?.raw_output || '';
  const hops = route?.hops || [];
  $('route-hops').innerHTML = hops.length ? `<table class="table"><thead><tr><th>Hop</th><th>Address</th><th>Probe RTT · ms</th></tr></thead><tbody>${hops.map((h) => `<tr><td>${escape(h.hop)}</td><td>${escape(h.address || 'No reply')}</td><td>${(h.rtt_ms || []).map((v) => number(v)).join(' / ') || '—'}</td></tr>`).join('')}</tbody></table>` : '';
}
