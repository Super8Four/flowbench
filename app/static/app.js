const $ = (id) => document.getElementById(id);
const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
let runs = [], activeId = null, selectedId = null, selected = null, events = [];
let engineReady = false, capabilities = {}, selectionVersion = 0, lastHistory = 0, comparisonVersion = 0;
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
  $('run-button').disabled = !engineReady || Boolean(activeId);
  $('run-button').textContent = activeId ? 'Session running…' : '▶ Start session';
  $('stop-button').disabled = !activeId;
}
function fields() {
  const server = $('role').value === 'server';
  $('target-field').classList.toggle('d-none', server);
  $('target').required = !server;
  $('target').disabled = server;
  $('client-fields').classList.toggle('d-none', server);
  $('server-fields').classList.toggle('d-none', !server);
  $('client-fields').querySelectorAll('input, select').forEach((element) => { element.disabled = server; });
  $('idle_timeout').disabled = !server;
  $('bandwidth').disabled = server || $('protocol').value !== 'udp';
  $('bandwidth-field').classList.toggle('d-none', $('protocol').value !== 'udp');
  const bidir = $('mode').querySelector('[value="bidir"]');
  bidir.disabled = !capabilities.bidir || ($('protocol').value === 'udp' && !capabilities.udp_bidir);
  if (bidir.disabled && $('mode').value === 'bidir') $('mode').value = 'upload';
}
function renderLogs() {
  const filter = $('log-filter').value, search = $('log-search').value.toLowerCase();
  const visible = events.filter((row) => (filter === 'all' || row.source === filter) && row.message.toLowerCase().includes(search));
  $('log-output').textContent = visible.map((row) => `[${date(row.timestamp)}] [${row.source}] ${row.message}`).join('\n') || 'No matching output.';
  if ($('autoscroll').checked) $('log-output').scrollTop = $('log-output').scrollHeight;
}
function chart(points) {
  points = points.filter((p) => Number.isFinite(p.seconds) && Number.isFinite(p.mbps));
  if (!points.length) {
    $('throughput-chart').innerHTML = '<text x="380" y="85" text-anchor="middle" fill="#8793a5" font-size="14">Interval measurements will appear here</text>';
    return;
  }
  const maxTime = Math.max(...points.map((p) => p.seconds), 1);
  const maxRate = Math.max(...points.map((p) => p.mbps), 1) * 1.15;
  const x = (t) => 65 + t / maxTime * 665, y = (n) => 128 - n / maxRate * 105;
  let svg = '';
  for (let i = 0; i <= 3; i++) {
    const value = maxRate * i / 3, height = y(value);
    svg += `<line x1="65" y1="${height}" x2="730" y2="${height}" stroke="#e8edf4"/><text x="55" y="${height + 4}" text-anchor="end" fill="#8793a5" font-size="10">${number(value, 0)}</text>`;
  }
  svg += `<text x="65" y="150" fill="#8793a5" font-size="10">0s</text><text x="730" y="150" text-anchor="end" fill="#8793a5" font-size="10">${number(maxTime, 1)}s</text><text x="5" y="12" fill="#8793a5" font-size="10">Mbps</text>`;
  for (const [direction, color] of [['upload', '#3478f6'], ['download', '#16a68b']]) {
    const data = points.filter((p) => p.direction === direction).sort((a, b) => a.seconds - b.seconds);
    if (!data.length) continue;
    svg += `<polyline points="${data.map((p) => `${x(p.seconds)},${y(p.mbps)}`).join(' ')}" stroke="${color}" stroke-width="2.5" fill="none"/>`;
    for (const p of data) svg += `<circle cx="${x(p.seconds)}" cy="${y(p.mbps)}" r="3" fill="${color}"><title>${direction}: ${number(p.mbps)} Mbps at ${number(p.seconds)}s</title></circle>`;
  }
  $('throughput-chart').innerHTML = svg;
}
function renderSelected() {
  if (!selected) return;
  $('session-status').textContent = selected.status[0].toUpperCase() + selected.status.slice(1);
  $('session-date').textContent = date(selected.created_at);
  $('selected-label').textContent = title(selected);
  $('event-count').textContent = number(selected.event_count, 0);
  const metrics = selected.metrics || {};
  for (const direction of ['upload', 'download']) {
    const latest = (selected.series || []).filter((p) => p.direction === direction).at(-1);
    $(direction + '-rate').textContent = number(metrics[direction + '_mbps'] ?? (!terminal.has(selected.status) ? latest?.mbps : undefined));
  }
  $('exit-code').textContent = selected.error || (selected.exit_code === null ? 'Capturing output to disk…' : `Process exit code: ${selected.exit_code} · ${date(selected.finished_at)}`);
  $('export-button').disabled = false;
  $('rerun-button').disabled = false;
  chart(selected.series || []);
  renderLogs();
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
  selectionVersion++;
  selectedId = id; selected = null; events = [];
  await loadSelected(true);
  if (!selected || selected.id !== id) return;
  $('annotation-card').classList.remove('d-none');
  $('saved-label').value = selected.settings.label;
  $('saved-notes').value = selected.settings.notes;
}
function renderHistory() {
  const search = $('history-search').value.toLowerCase();
  const visible = runs.filter((run) => `${title(run)} ${run.settings.target} ${run.settings.notes}`.toLowerCase().includes(search));
  $('history-body').innerHTML = visible.map((run) => {
    const config = run.effective_settings || run.settings;
    const color = {completed: 'success', failed: 'danger', cancelled: 'secondary', interrupted: 'warning', running: 'primary', starting: 'primary'}[run.status] || 'secondary';
    return `<tr><td><div class="history-label">${escape(title(run))}</div><div class="history-date">${escape(date(run.created_at))}</div></td><td>${escape(run.settings.target || run.settings.bind || 'All interfaces')}<div class="history-date">Port ${run.settings.port} · ${escape(run.settings.role)}</div></td><td>${escape(config.protocol.toUpperCase())} · ${escape(config.mode)}<div class="history-date">${config.duration}s · ${config.streams} stream(s)</div></td><td><span class="badge text-bg-${color}">${escape(run.status)}</span></td><td>${number(run.metrics.upload_mbps)} / ${number(run.metrics.download_mbps)}<div class="history-date">Mbps</div></td><td class="text-end text-nowrap"><button class="btn btn-sm btn-outline-primary" data-open="${run.id}">Open</button> <button class="btn btn-sm btn-outline-secondary" data-export="${run.id}">Export</button></td></tr>`;
  }).join('') || '<tr><td colspan="6" class="text-center py-5 text-body-secondary">No matching saved sessions.</td></tr>';
}
function comparisonOptions() {
  const complete = runs.filter((run) => run.status === 'completed' && Object.keys(run.metrics).length);
  for (const id of ['baseline', 'candidate']) {
    const chosen = $(id).value || localStorage.getItem(`iperf-${id}`) || '';
    $(id).replaceChildren(new Option('Choose a completed test', ''));
    for (const run of complete) $(id).add(new Option(`${title(run)} · ${date(run.created_at)}`, run.id));
    if (complete.some((run) => run.id === chosen)) $(id).value = chosen;
  }
}
async function loadHistory() {
  const data = await api('/api/runs');
  runs = data.runs; activeId = data.active_run; lastHistory = Date.now();
  $('saved-count').textContent = `${runs.length} saved session${runs.length === 1 ? '' : 's'}`;
  controls(); renderHistory(); comparisonOptions();
}
async function compare() {
  const generation = ++comparisonVersion;
  const before = $('baseline').value, after = $('candidate').value;
  $('export-comparison').disabled = true;
  for (const id of ['baseline', 'candidate']) localStorage.setItem(`iperf-${id}`, $(id).value);
  if (!before || !after) {
    $('comparison-result').textContent = 'Choose two completed tests to compare throughput, retransmits, jitter, and packet loss where available.';
    return;
  }
  try {
    const data = await api(`/api/comparison?before=${before}&after=${after}`);
    if (generation !== comparisonVersion) return;
    const rows = data.metrics.filter((row) => !row.metric.endsWith('_packets') || row.metric.endsWith('_lost_packets'));
    $('comparison-result').innerHTML = `<div class="table-responsive"><table class="table"><thead><tr><th>Metric</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>${rows.map((row) => {
      const unit = row.metric.endsWith('mbps') ? ' Mbps' : row.metric.endsWith('jitter_ms') ? ' ms' : row.metric.endsWith('lost_percent') ? '%' : '';
      const delta = `${row.delta > 0 ? '+' : ''}${number(row.delta)}${unit}`;
      const percent = row.percent_change === null ? 'No % from zero' : `${row.percent_change > 0 ? '+' : ''}${number(row.percent_change, 1)}%`;
      const color = row.direction === 'improved' ? 'success' : row.direction === 'worse' ? 'danger' : 'secondary';
      return `<tr><td>${escape(row.metric.replaceAll('_', ' '))}</td><td>${number(row.before)}${unit}</td><td>${number(row.after)}${unit}</td><td class="text-${color}">${delta}<div class="small">${percent}</div></td></tr>`;
    }).join('')}</tbody></table></div>${data.settings_differences.length ? `<p class="comparison-note text-warning-emphasis">Settings differ: ${escape(data.settings_differences.join(', '))}. Use matching conditions for a meaningful before/after comparison.</p>` : '<p class="comparison-note text-success">Test settings match.</p>'}<p class="comparison-note">${escape(data.interpretation)}</p>`;
    $('export-comparison').disabled = false;
  } catch (error) {
    if (generation === comparisonVersion) $('comparison-result').textContent = error.message;
  }
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

$('test-form').addEventListener('submit', safe(async (event) => {
  event.preventDefault();
  $('message').classList.add('d-none');
  const data = Object.fromEntries(new FormData(event.target));
  data.debug = $('debug').checked;
  $('run-button').disabled = true;
  try {
    const run = await api('/api/runs', {method: 'POST', body: JSON.stringify(data)});
    activeId = run.id; controls(); await selectRun(run.id); await loadHistory();
  } finally { controls(); }
}));
$('stop-button').addEventListener('click', safe(async () => {
  if (activeId) await api(`/api/runs/${activeId}/stop`, {method: 'POST', body: '{}'});
  await loadHistory();
}));
$('role').addEventListener('change', fields); $('protocol').addEventListener('change', fields);
$('log-filter').addEventListener('change', renderLogs); $('log-search').addEventListener('input', renderLogs);
$('autoscroll').addEventListener('change', renderLogs); $('history-search').addEventListener('input', renderHistory);
$('refresh-history').addEventListener('click', safe(loadHistory));
$('history-body').addEventListener('click', safe(async (event) => {
  const button = event.target.closest('button');
  if (button?.dataset.open) { await selectRun(button.dataset.open); $('workspace').scrollIntoView({behavior: 'smooth'}); }
  if (button?.dataset.export) await exportRun(button.dataset.export);
}));
$('export-button').addEventListener('click', safe(() => exportRun(selectedId)));
$('rerun-button').addEventListener('click', () => {
  if (!selected) return;
  for (const [key, value] of Object.entries(selected.settings)) {
    if (!$(key)) continue;
    if ($(key).type === 'checkbox') $(key).checked = value; else $(key).value = value;
  }
  fields(); $('target').focus();
});
$('save-notes').addEventListener('click', safe(async () => {
  await api(`/api/runs/${selectedId}`, {method: 'PATCH', body: JSON.stringify({label: $('saved-label').value, notes: $('saved-notes').value})});
  await loadSelected(); await loadHistory(); message('Session notes saved.', 'success');
}));
for (const id of ['baseline', 'candidate']) $(id).addEventListener('change', safe(compare));
$('export-comparison').addEventListener('click', safe(() => download(`/api/comparison?before=${$('baseline').value}&after=${$('candidate').value}&format=${$('export-format').value}`, 'iperf-comparison')));
document.querySelectorAll('.sidebar-menu .nav-link').forEach((link) => link.addEventListener('click', () => {
  document.querySelectorAll('.sidebar-menu .nav-link').forEach((other) => other.classList.toggle('active', other === link));
}));

async function tick() {
  try {
    if (Date.now() - lastHistory > 3000) await loadHistory();
    if (selectedId && selected && !terminal.has(selected.status)) await loadSelected();
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
    $('storage-path').textContent = `Saved on disk: ${status.storage}`;
    if (!engineReady) message(status.storage_error || status.iperf.error);
    if (!status.iperf.bidir) $('mode').querySelector('[value="bidir"]').disabled = true;
    await loadHistory();
    if (activeId || runs.length) await selectRun(activeId || runs[0].id);
    await compare(); fields();
  } catch (error) { message(error.message); }
  tick();
}
init();
