import {rateScale, formatRate, formatMbps, runColor} from './units.js';
// Local, offline-capable measurement workflows. No chart values are synthesized.
export function createWorkflows(context) {
  const {api, message, escape: esc, number: num, date, title, smoothPath, download} = context;
  const $ = (id) => document.getElementById(id);
  const keys = ['site', 'device', 'circuit', 'ticket'];
  const metricLabel = (key) => key?.replace(/_mbps$/, ' bitrate').replaceAll('_', ' ');
  const metricValue = (key, value) => key?.endsWith('_mbps') ? formatMbps(value) : key?.endsWith('bits_per_second') ? formatRate(value) : num(value);
  let projects = [], projectId = null, projectReport = null, annotationId = null;
  let liveVersion = 0, liveBusy = false, liveData = null, lastLiveKey = '', initialized = false;
  let reportSignature = '', reportPreview = null;
  let flentCapability = null, flentRun = null;
  let matrixStarting = false, flentStarting = false, reportPending = false;
  let annotationTimestamp = null;
  let reportSourceState = '';
  const leaf = (run) => !['protocol-job', 'matrix-job'].includes(run.kind);
  const complete = (run) => run.status === 'completed' && leaf(run) && Object.keys(run.metrics || {}).length;
  const tagValues = (prefix) => Object.fromEntries(keys.map((key) => [key, $(prefix + key)?.value.trim() || '']));
  const guard = (action) => async (event) => { try { await action(event); } catch (error) { message(error.message); } };
  const busy = async (id, action) => {
    const button = $(id); if (button.disabled) return;
    button.disabled = true; const text = button.textContent; button.textContent = 'Saving…';
    try { return await action(); } finally { button.disabled = false; button.textContent = text; controls(); }
  };
  function fill(select, rows, empty, label, requested) {
    if (!select) return;
    const chosen = requested === undefined ? select.value : requested;
    const signature = JSON.stringify(rows.map((row) => [row.id, label(row)]));
    if (select.dataset.options !== signature) {
      select.replaceChildren(new Option(empty, ''));
      rows.forEach((row) => select.add(new Option(label(row), row.id)));
      select.dataset.options = signature;
    }
    select.value = rows.some((row) => row.id === chosen) ? chosen : '';
  }
  function settings() {
    if (!$('test-form')) return {};
    return {project_id: $('project_id').value || null, tags: tagValues('tag-'),
      latency: $('latency').checked, endpoint_health: $('endpoint_health').checked, mtu: $('mtu').checked};
  }
  function loadSettings(values) {
    if (!$('test-form')) return;
    $('project_id').value = values.project_id || '';
    keys.forEach((key) => { $('tag-' + key).value = values.tags?.[key] || ''; });
    ['latency', 'endpoint_health', 'mtu'].forEach((key) => { $(key).checked = Boolean(values[key]); });
  }
  function controls() {
    if ($('matrix-start')) $('matrix-start').disabled = matrixStarting || !context.state().engineReady || Boolean(context.state().activeId);
    if ($('flent-start')) $('flent-start').disabled = flentStarting || !flentCapability?.available || Boolean(context.state().activeId) || !$('flent-confirm').checked;
    if ($('report-preview-button')) {
      const ids = context.reportIds(), valid = ids.length > 0 && (document.body.dataset.page !== 'comparison' || ids.length >= 2);
      $('report-preview-button').disabled = reportPending || !valid;
      $('report-raw').disabled = $('report-redact').checked;
      if ($('report-redact').checked) $('report-raw').checked = false;
      $('report-download').disabled = reportPending || !valid || !reportPreview || reportSignature !== JSON.stringify(reportOptions());
      const sourceState = JSON.stringify(ids.map((id) => {
        const row = context.state().selected?.id === id ? context.state().selected : context.state().runs.find((run) => run.id === id);
        return row ? {id, settings: row.settings, annotations: row.annotations, status: row.status, event_count: row.event_count} : {id};
      }));
      if (reportSourceState && reportSourceState !== sourceState) {
        reportPreview = null; $('report-download').disabled = true;
        $('report-status').textContent = 'Selected evidence changed. Preview the report again before downloading.';
      }
      reportSourceState = sourceState;
    }
  }
  function status(value) {
    flentCapability = value.flent || value.iperf?.flent;
    if ($('flent-capability')) $('flent-capability').textContent = flentCapability?.available ? `Available: ${flentCapability.version || 'local Flent'} · ${flentCapability.platform || 'local platform'}` : `Unavailable on this host. ${flentCapability?.error || 'Flent and netperf are optional native tools; native Windows is unsupported.'}`;
    if ($('diagnostic-capabilities')) $('diagnostic-capabilities').textContent = Object.entries(value.diagnostics || {}).map(([key, capability]) => `${key.replaceAll('_', ' ')}: ${capability.available ? 'available' : 'unavailable'}${capability.tool ? ` (${capability.tool})` : ''}`).join(' · ') || 'Probe availability will be recorded with the test.';
    controls();
  }
  function tags(run) {
    const values = run.settings.tags || {};
    const project = projects.find((row) => row.id === run.settings.project_id);
    return `<div class="workflow-chips">${project ? `<a class="workflow-chip" href="/projects?project=${encodeURIComponent(project.id)}">${esc(project.name)}</a>` : ''}${keys.filter((key) => values[key]).map((key) => `<span class="workflow-chip">${key}: ${esc(values[key])}</span>`).join('')}</div>`;
  }
  function matches(run) {
    const project = $('history-project')?.value;
    return !project || (project === 'unassigned' ? !run.settings.project_id : run.settings.project_id === project);
  }
  function history(runs) {
    fill($('live-baseline'), runs.filter((run) => complete(run) && run.kind !== 'flent-run'), 'Choose a completed test', (run) => `${title(run)} · ${date(run.created_at)}`);
    if ($('live-baseline') && !initialized) {
      $('live-baseline').value = localStorage.getItem('flowbench-live-baseline') || '';
      initialized = true;
    }
    if (projectReport) projectBaselines(projectReport);
  }
  async function loadProjects() {
    projects = (await api('/api/projects')).projects;
    for (const id of ['project_id', 'saved-project']) fill($(id), projects, 'No project', (row) => row.name);
    fill($('history-project'), [{id: 'unassigned', name: 'No project'}, ...projects], 'All projects', (row) => row.name);
    if ($('project-list')) {
      $('project-list').innerHTML = projects.map((row) => `<button type="button" class="workflow-project ${row.id === projectId ? 'selected' : ''}" data-project="${esc(row.id)}" ${row.id === projectId ? 'aria-current="true"' : ''}><strong>${esc(row.name)}</strong><span>${num(row.run_count, 0)} test(s)${row.baseline_id ? ' · baseline selected' : ''}</span></button>`).join('') || '<p class="form-text">No projects yet.</p>';
    }
  }
  function projectBaselines(report) {
    fill($('project-baseline'), (report.runs || []).filter(complete), 'No baseline selected',
      (run) => `${title(run)} · ${date(run.created_at)}`, report.project.baseline_id || '');
  }
  async function openProject(id, reset = true) {
    projectId = id;
    const report = await api(`/api/projects/${id}`);
    if (projectId !== id) return;
    projectReport = report;
    $('project-empty').classList.add('d-none'); $('project-detail').classList.remove('d-none');
    if (reset) {
      $('project-edit-name').value = report.project.name;
      $('project-edit-description').value = report.project.description || '';
    }
    projectBaselines(report);
    $('project-new-test').href = `/?project=${encodeURIComponent(id)}`;
    $('project-detail-status').textContent = report.note || 'Choose a completed baseline from this project to compare later tests.';
    const rows = report.comparisons || [];
    $('project-report').innerHTML = rows.length ? `<div class="workflow-report">${rows.map((row) => {
      const after = report.runs.find((run) => run.id === row.after_id);
      return `<article><h3 class="fs-6">${esc(after ? title(after) : row.after_id)}</h3><p><strong>${row.comparable ? esc(row.result) : 'Settings differ — comparison needs review'}</strong></p><p class="form-text">${esc(row.caveat || '')}${row.settings_differences?.length ? ` Different settings: ${esc(row.settings_differences.join(', '))}.` : ''}</p><div class="table-responsive"><table class="table"><thead><tr><th>Metric</th><th>Baseline</th><th>After</th><th>Change</th></tr></thead><tbody>${metricRows(row.metrics)}</tbody></table></div></article>`;
    }).join('')}</div>` : '<p class="form-text">No comparable completed candidates yet. Add tests to this project and save a baseline.</p>';
    const excluded = report.excluded || [];
    if (excluded.length) $('project-report').innerHTML += `<details><summary>${excluded.length} test(s) excluded from change summary</summary><ul>${excluded.map((row) => `<li>${esc(row.id)}: ${esc(row.reason)}</li>`).join('')}</ul></details>`;
    $('project-runs').innerHTML = `<table class="table mt-3"><thead><tr><th>Test</th><th>Status</th><th>Settings</th><th>Evidence</th></tr></thead><tbody>${report.runs.map((run) => `<tr><td>${esc(title(run))}${tags(run)}</td><td>${esc(run.status)}</td><td>${run.settings.flent_workload ? 'Flent RRUL · 8 TCP load flows + latency probes' : `${esc(run.settings.protocol)} / ${esc(run.settings.mode)} / ${run.settings.streams} streams`}</td><td><a href="/?run=${encodeURIComponent(run.id)}">Open test</a></td></tr>`).join('') || '<tr><td colspan="4">No tests assigned yet.</td></tr>'}</tbody></table>`;
    await loadProjects();
  }
  function metricRows(metrics) {
    const rows = Array.isArray(metrics) ? metrics.map((value) => [value.metric || value.name || value.key, value]) : Object.entries(metrics || {});
    return rows.map(([key, value]) => `<tr><td>${esc(metricLabel(key))}</td><td>${metricValue(key,value.before ?? value.baseline)}</td><td>${metricValue(key,value.after ?? value.candidate)}</td><td>${metricValue(key,value.delta ?? value.change)}${Number.isFinite(value.percent_change ?? value.percent ?? value.change_percent) ? ` (${num(value.percent_change ?? value.percent ?? value.change_percent)}%)` : ''}</td></tr>`).join('') || '<tr><td colspan="4">No shared numeric metrics.</td></tr>';
  }
  function matrixData() {
    const checked = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((item) => item.value);
    const protocols = checked('matrix-protocol'), modes = checked('matrix-mode');
    const streams = $('matrix-streams').value.split(',').map((value) => Number(value.trim()));
    const duration = Number($('duration').value), count = protocols.length * modes.length * streams.length;
    const problems = [];
    if (!protocols.length || !modes.length) problems.push('Select at least one protocol and direction.');
    if (!streams.length || streams.some((value) => !Number.isInteger(value) || value < 1 || value > 8) || new Set(streams).size !== streams.length) problems.push('Enter unique stream counts between 1 and 8.');
    if (count > 12) problems.push('Reduce the matrix to at most 12 cases.');
    if (!Number.isInteger(duration) || duration < 1 || duration > 60 || count * duration > 360) problems.push('Use 1–60 seconds per case and at most 360 planned traffic seconds.');
    const bandwidth = $('matrix-bandwidth').value.trim();
    const rateMatch = /^([0-9]+(?:\.[0-9]+)?)([KMG]?)$/i.exec(bandwidth);
    const rate = rateMatch ? Number(rateMatch[1]) * ({'': 1, K: 1e3, M: 1e6, G: 1e9}[rateMatch[2].toUpperCase()]) : NaN;
    if (protocols.includes('udp') && (!Number.isFinite(rate) || rate <= 0 || rate * Math.max(...streams) * (modes.includes('bidir') ? 2 : 1) > 100e6)) problems.push('UDP requires a finite bandwidth and no more than 100 Mbps in aggregate.');
    const caps = context.state().capabilities;
    if (modes.includes('bidir') && (!caps.bidir || (protocols.includes('udp') && !caps.udp_bidir))) problems.push('This engine cannot run the selected bidirectional cases. Choose upload/download.');
    $('matrix-preview').textContent = `${count} sequential case(s) · ${num(count * duration, 0)} planned traffic seconds${problems.length ? ' · adjust the selections below' : ''}`;
    return {problems, data: {settings: {...context.settings(), bandwidth}, protocols, modes, streams}};
  }
  async function startMatrix() {
    if (matrixStarting || context.state().activeId) return;
    if (!$('test-form').reportValidity()) return;
    const plan = matrixData(), error = $('matrix-error');
    error.classList.toggle('d-none', !plan.problems.length); error.textContent = plan.problems.join(' ');
    if (plan.problems.length) { error.focus(); return; }
    matrixStarting = true; $('matrix-start').disabled = true; $('matrix-start').textContent = 'Starting batch…';
    try {
      const run = await api('/api/matrices', {method: 'POST', body: JSON.stringify(plan.data)});
      await context.start(run); error.classList.add('d-none');
    } catch (problem) { error.textContent = problem.message; error.classList.remove('d-none'); error.focus(); }
    finally { matrixStarting = false; $('matrix-start').textContent = 'Start sequential batch'; controls(); }
  }
  function matrix(run) {
    $('matrix-job-card')?.classList.toggle('d-none', run.kind !== 'matrix-job');
    if (run.kind !== 'matrix-job') return;
    const rows = run.matrix_runs || [];
    $('matrix-job-status').textContent = `Batch ${run.status}: ${rows.filter((row) => row.status === 'completed').length} of ${rows.length} completed.${run.error ? ' ' + run.error : ''}`;
    $('matrix-job-results').innerHTML = `<table class="table"><thead><tr><th>Case</th><th>Status</th><th>Upload rate</th><th>Download rate</th><th>Evidence</th></tr></thead><tbody>${rows.map((row, index) => `<tr><td>${index + 1}. ${esc(row.protocol?.toUpperCase())} / ${esc(row.mode)} / ${row.streams} streams</td><td>${esc(row.status)}${row.error ? `<p class="form-text">${esc(row.error)}</p>` : ''}</td><td>${formatMbps(row.metrics?.upload_mbps)}</td><td>${formatMbps(row.metrics?.download_mbps)}</td><td><a href="/?run=${encodeURIComponent(row.id)}">Open case ${index + 1}</a></td></tr>`).join('')}</tbody></table>`;
  }
  function latencySummary(stage) {
    const summary = stage?.summary || {};
    return `${esc(stage?.status || 'unavailable')} · ${num(summary.replies, 0)} replies / ${num(summary.attempted, 0)} probes · ${num(summary.missing, 0)} missing`;
  }
  function diagnostics(run) {
    const record = run.diagnostics;
    $('diagnostics-card')?.classList.toggle('d-none', !record);
    if (!record || !$('diagnostics-summary')) return;
    const latency = record.latency || {}, health = record.endpoint_health || {}, path = record.path_context || {};
    let output = '<h3 class="fs-6">ICMP latency under load</h3>';
    if (!latency.enabled) output += '<p class="form-text">Not requested for this test.</p>';
    else {
      if (latency.error) output += `<p class="text-warning-emphasis">${esc(latency.error)}</p>`;
      output += `<p>${latencySummary(latency.baseline)} before load<br>${latencySummary(latency.loaded)} during load</p><div class="table-responsive"><table class="table"><thead><tr><th>RTT statistic (ms)</th><th>Before load</th><th>During load</th></tr></thead><tbody>${['min_ms', 'mean_ms', 'median_ms', 'p95_ms', 'max_ms', 'stddev_ms'].map((key) => `<tr><td>${esc(key.replaceAll('_ms', '').replaceAll('_', ' '))}</td><td>${num(latency.baseline?.summary?.[key])}</td><td>${num(latency.loaded?.summary?.[key])}</td></tr>`).join('')}</tbody></table></div><p>Mean increase: ${num(latency.mean_increase_ms)} ms.</p><p class="form-text">${esc(latency.note || 'ICMP echo RTT is not application latency. Missing replies remain unavailable.')}${latency.loaded?.sample_limit_reached ? ' Loaded probe limit reached; later traffic has no probes.' : ''}</p>`;
    }
    output += '<h3 class="fs-6 mt-4">Endpoint health</h3>';
    if (!health.enabled) output += '<p class="form-text">Not requested for this test.</p>';
    else {
      const local = health.local || {}, delta = local.delta || {};
      output += `<p>Local: ${esc(local.provenance || 'unavailable')} · before ${esc(local.before?.status || 'unavailable')} / after ${esc(local.after?.status || 'pending')}</p><p>Local CPU utilization over measured interval: ${num(delta.cpu_utilization_percent)}%.</p>`;
      if (Array.isArray(delta.interfaces)) output += `<div class="table-responsive"><table class="table"><thead><tr><th>Local interface</th><th>Status</th><th>Counter changes</th></tr></thead><tbody>${delta.interfaces.map((row) => `<tr><td>${esc(row.name)}</td><td>${esc(row.status)}</td><td>${esc(Object.entries(row.counters || {}).map(([key, value]) => `${key}: ${value}`).join('; ') || 'Unavailable')}</td></tr>`).join('')}</tbody></table></div>`;
      output += `<p class="form-text">${esc(local.before?.note || '')} ${esc(local.after?.note || '')}</p><p class="form-text">Remote: ${esc(health.remote?.status || 'unavailable')}. ${esc(health.remote?.note || 'Plain iPerf peers do not provide remote system counters.')}</p>`;
    }
    if (health.checkpoint_error) output += `<p class="text-warning-emphasis">Diagnostic persistence: ${esc(health.checkpoint_error)}</p>`;
    output += `<h3 class="fs-6 mt-4">Connection &amp; path context</h3><dl class="workflow-dl"><dt>Target</dt><dd>${esc(path.target || run.settings.target || 'Unavailable')}</dd><dt>Bind</dt><dd>${esc(path.bind || 'Automatic')}</dd><dt>Requested IP version</dt><dd>${esc(path.requested_ip_version || run.settings.ip_version || 'Automatic')}</dd><dt>Probe IP version</dt><dd>${esc(path.probe_ip_version || 'Unavailable')}</dd><dt>MTU probe</dt><dd>${esc(path.mtu?.status || 'Not requested')}</dd><dt>Largest confirmed packet</dt><dd>${num(path.mtu?.largest_confirmed_packet_bytes, 0)} bytes</dd></dl><p class="form-text">${esc(path.note || '')} ${esc(path.mtu?.note || '')} ${esc(path.route?.note || '')} Route and MTU evidence do not establish a path bottleneck.</p>`;
    $('diagnostics-summary').innerHTML = output;
    $('diagnostics-output').textContent = JSON.stringify(record, null, 2);
  }
  function selected(run) { matrix(run); diagnostics(run); renderFlent(run); renderTimeline(run); updateLive(); controls(); }
  function edit(run) {
    fill($('saved-project'), projects, 'No project', (row) => row.name, run.settings.project_id || '');
    keys.forEach((key) => { $('saved-tag-' + key).value = run.settings.tags?.[key] || ''; });
    $('saved-color').value = runColor(run);
    resetAnnotation(); renderTimeline(run);
  }
  function annotationSettings() { return {project_id: $('saved-project').value || null, tags: tagValues('saved-tag-'), color: $('saved-color').value}; }
  function resetAnnotation() {
    annotationId = null;
    annotationTimestamp = null;
    if (!$('timeline-form')) return;
    $('timeline-form').reset(); $('timeline-timestamp').value = new Date().toISOString().slice(0, 19);
    $('timeline-save').textContent = 'Add timeline note'; $('timeline-cancel').classList.add('d-none');
    $('timeline-error').textContent = '';
  }
  function renderTimeline(run) {
    if (!$('timeline-notes')) return;
    const content = (run.annotations || []).map((row) => `<article class="workflow-annotation"><p>${esc(row.text)}</p><span class="form-text">${esc(date(row.timestamp))}${Number.isFinite(row.seconds) ? ` · ${num(row.seconds, 3)} elapsed seconds` : ' · no elapsed position'}</span><div class="d-flex gap-2 mt-2"><button type="button" class="btn btn-sm btn-outline-secondary" data-edit-note="${esc(row.id)}">Edit note</button><button type="button" class="btn btn-sm btn-outline-danger" data-delete-note="${esc(row.id)}">Remove note</button></div></article>`).join('') || '<p class="form-text">No timeline notes yet. Timestamps and optional elapsed positions are retained in exports.</p>';
    if ($('timeline-notes').innerHTML !== content) $('timeline-notes').innerHTML = content;
  }
  async function patchAnnotations(rows) {
    const id = context.state().selectedId;
    await api(`/api/runs/${id}`, {method: 'PATCH', body: JSON.stringify({annotations: rows})});
    await context.reload();
  }
  function drawLive() {
    const chart = $('live-chart'); if (!chart) return;
    const width = Math.max(230, chart.getBoundingClientRect().width || 760);
    chart.setAttribute('viewBox', `0 0 ${width} 240`);
    const records = liveData?.runs || [], colors = records.map(runColor);
    const lines = records.flatMap((run, index) => ['upload', 'download'].map((direction) => ({index, direction,
      data: [...new Map((run.series || []).filter((row) => row.direction === direction && Number.isFinite(row.seconds) && Number.isFinite(row.mbps)).map((row) => [row.seconds, row])).values()].sort((a, b) => a.seconds - b.seconds)}))).filter((row) => row.data.length);
    const points = lines.flatMap((line) => line.data);
    $('live-legend').innerHTML = records.map((run, index) => `<span><span class="comparison-color-swatch" style="background:${colors[index]}"></span>${index ? 'Selected' : 'Baseline'}: ${esc(title(run))}</span>`).join('');
    if (!points.length) { $('live-unit').textContent = 'bps'; chart.innerHTML = `<text x="${width / 2}" y="115" text-anchor="middle" fill="#aab6bf" font-size="12">No measured intervals to overlay</text>`; return; }
    const maxTime = points.reduce((max, row) => Math.max(max, row.seconds), 1), maxRate = points.reduce((max, row) => Math.max(max, row.mbps), 0) * 1.08 || 1e-6;
    const scale = rateScale(points.map((row) => row.mbps * 1e6));
    $('live-unit').textContent = scale.unit;
    chart.setAttribute('aria-label', `Baseline and selected throughput by elapsed seconds in ${scale.unit}`);
    const x = (seconds) => 55 + seconds / maxTime * (width - 70), y = (mbps) => 200 - mbps / maxRate * 175;
    let svg = '';
    for (let step = 0; step <= 3; step++) svg += `<line x1="55" y1="${y(maxRate * step / 3)}" x2="${width - 15}" y2="${y(maxRate * step / 3)}" stroke="#41494d"/><text x="47" y="${y(maxRate * step / 3) + 4}" text-anchor="end" fill="#aab6bf" font-size="12">${num(maxRate * step / 3*1e6/scale.divisor, 1)}</text><text x="${x(maxTime * step / 3)}" y="222" text-anchor="${step ? step === 3 ? 'end' : 'middle' : 'start'}" fill="#aab6bf" font-size="12">${num(maxTime * step / 3, 1)}s</text>`;
    for (const line of lines) {
      const path = line.data.map((row, index) => `${index && Number.isFinite(row.start) && row.start <= line.data[index - 1].seconds + 0.000001 ? 'L' : 'M'} ${x(row.seconds)},${y(row.mbps)}`).join(' ');
      svg += `<path d="${path}" stroke="${colors[line.index]}" stroke-width="2" fill="none" ${line.direction === 'download' ? 'stroke-dasharray="6 4"' : ''}/>`;
      for (const row of line.data) svg += `<circle cx="${x(row.seconds)}" cy="${y(row.mbps)}" r="3" fill="${colors[line.index]}"><title>${line.index ? 'Selected' : 'Baseline'} ${line.direction}: ${formatMbps(row.mbps,2,scale)} at ${num(row.seconds)}s</title></circle>`;
    }
    chart.innerHTML = svg;
  }
  async function updateLive(force = false) {
    if (!$('live-baseline')) return;
    const baseline = $('live-baseline').value, run = context.state().selected;
    if (!baseline || !run || !leaf(run) || run.kind === 'flent-run' || baseline === run.id) {
      liveVersion++; liveData = null; drawLive();
      $('live-note').textContent = run?.kind === 'flent-run' ? 'Flent native units are shown in its own evidence chart. This live overlay compares individual iPerf tests.' : baseline === run?.id ? 'Choose a different baseline test.' : 'Choose a completed baseline and open an individual test. Batch parents have no combined throughput curve.';
      return;
    }
    const key = `${baseline}:${run.id}:${run.status}:${run.series?.length}`;
    if (liveBusy || (!force && key === lastLiveKey)) return;
    liveBusy = true; const generation = ++liveVersion;
    try {
      const result = await api(`/api/live-comparison?${new URLSearchParams({baseline, active: run.id})}`);
      if (generation !== liveVersion || baseline !== $('live-baseline').value || run.id !== context.state().selectedId) return;
      liveData = result; lastLiveKey = key;
      $('live-note').textContent = `${result.settings_differences?.length ? `Settings differ: ${result.settings_differences.join(', ')}. ` : 'Settings match. '}${result.interpretation || 'Curves share elapsed seconds; repeat under matching conditions.'}`;
      drawLive();
    } catch (error) { if (generation === liveVersion) { liveData = null; drawLive(); $('live-note').textContent = error.message; } }
    finally { liveBusy = false; }
  }
  function reportOptions() { return {ids: context.reportIds(), redact: $('report-redact').checked, include_raw: $('report-raw').checked && !$('report-redact').checked}; }
  async function previewReport() {
    if (reportPending) return;
    reportPending = true;
    $('report-status').textContent = 'Preparing preview…';
    const options = reportOptions(), signature = JSON.stringify(options);
    $('report-preview-button').disabled = true; $('report-download').disabled = true;
    try {
      const result = await api('/api/reports/preview', {method: 'POST', body: signature});
      if (signature !== JSON.stringify(reportOptions())) return;
      reportPreview = result; reportSignature = signature;
      $('report-status').textContent = `Preview ready. ${result.redacted ? 'Sensitive context redacted.' : 'Captured user and endpoint context included.'} ${result.include_raw ? 'Raw evidence included.' : 'Raw evidence excluded.'}`;
      $('report-preview').innerHTML = `<h3 class="fs-5">${esc(result.title)}</h3><p class="form-text">Generated ${esc(date(result.generated_at))} · FlowBench ${esc(result.version)}</p>${(result.runs || []).map((run, index) => `<article class="workflow-annotation"><h4 class="fs-6"><span class="comparison-color-swatch" style="background:${runColor(run)}"></span>${index + 1}. ${esc(run.label)}</h4><p>${esc(date(run.created_at))}</p><div class="table-responsive"><table class="table"><tbody>${Object.entries(run.metrics || {}).map(([key, value]) => `<tr><th>${esc(metricLabel(key))}</th><td>${metricValue(key,value)}</td></tr>`).join('')}</tbody></table></div></article>`).join('')}<ul>${(result.limitations || []).map((text) => `<li>${esc(text)}</li>`).join('')}</ul><details><summary>Complete report content preview</summary><pre class="peer-output mt-3" tabindex="0">${esc(JSON.stringify(result, null, 2))}</pre></details>`;
    } catch (error) { reportPreview = null; $('report-status').textContent = error.message; message(error.message); }
    finally { reportPending = false; controls(); }
  }
  async function downloadReport() {
    if (reportPending || !reportPreview || reportSignature !== JSON.stringify(reportOptions())) return;
    reportPending = true;
    const format = $('report-format').value;
    $('report-download').disabled = true; $('report-status').textContent = 'Generating report…';
    try {
      const body = JSON.stringify({...JSON.parse(reportSignature), fingerprint: reportPreview.fingerprint});
      const response = await fetch(`/api/reports/download?format=${format}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body});
      if (!response.ok) { const result = await response.json(); throw new Error(result.error || 'Report generation failed.'); }
      const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = `flowbench-report.${format}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      $('report-status').textContent = 'Report downloaded with the previewed options.';
    } catch (error) { $('report-status').textContent = error.message; message(error.message); }
    finally { reportPending = false; controls(); }
  }
  async function startFlent() {
    if (flentStarting || context.state().activeId) return;
    const error = $('flent-error'); error.classList.add('d-none');
    const values = context.settings();
    if (!$('target').reportValidity() || !$('duration').reportValidity() || !$('flent-port').reportValidity()) return;
    const duration = Number(values.duration);
    if (duration > 60 || !$('flent-confirm').checked) { error.textContent = 'Use 1–60 seconds and confirm a controlled, ready netserver peer.'; error.classList.remove('d-none'); error.focus(); return; }
    const data = {target: values.target, duration, netserver_port: Number($('flent-port').value), workload: 'rrul', confirm_netserver: true,
      label: values.label, notes: values.notes, project_id: values.project_id, tags: values.tags};
    flentStarting = true; $('flent-start').disabled = true; $('flent-start').textContent = 'Starting Flent…';
    try { await context.start(await api('/api/flent', {method: 'POST', body: JSON.stringify(data)})); }
    catch (problem) { error.textContent = problem.message; error.classList.remove('d-none'); error.focus(); }
    finally { flentStarting = false; $('flent-start').textContent = 'Start Flent RRUL'; controls(); }
  }
  function loadFlentSettings(run) {
    if (run.kind !== 'flent-run') return false;
    for (const key of ['target', 'duration', 'label', 'notes']) $(key).value = run.settings[key] || '';
    loadSettings(run.settings); $('flent-port').value = run.settings.port; $('flent-confirm').checked = false;
    $('flent-editor').open = true; $('flent-port').focus(); controls();
    message('Flent settings loaded. Confirm the ready netserver peer before starting the RRUL workload.', 'success');
    return true;
  }
  function renderFlent(run) {
    $('flent-results')?.classList.toggle('d-none', run.kind !== 'flent-run');
    if (run.kind !== 'flent-run') return;
    flentRun = run;
    const native = run.flent || {}, summaries = native.summaries || [];
    $('flent-status').textContent = `Flent ${native.workload || 'RRUL'} · ${native.status || run.status}${run.error ? ' · ' + run.error : ''}`;
    $('flent-summaries').innerHTML = `<table class="table"><thead><tr><th>Native series</th><th>Unit</th><th>Samples</th><th>Missing</th><th>Mean</th><th>Min / max</th></tr></thead><tbody>${summaries.map((row) => `<tr><td>${esc(row.name)}</td><td>${row.unit === 'Mbits/s' ? 'SI bitrate' : esc(row.unit)}</td><td>${num(row.count, 0)}</td><td>${num(row.missing, 0)}</td><td>${row.unit === 'Mbits/s' ? formatMbps(row.mean) : num(row.mean)}</td><td>${row.unit === 'Mbits/s' ? formatMbps(row.min) : num(row.min)} / ${row.unit === 'Mbits/s' ? formatMbps(row.max) : num(row.max)}</td></tr>`).join('') || '<tr><td colspan="6">Native result samples are not yet available.</td></tr>'}</tbody></table>`;
    const rows = (native.series || []).map((row, index) => ({...row, id: String(index)}));
    fill($('flent-series'), rows, 'Choose native series', (row) => `${row.name} (${row.unit === 'Mbits/s' ? 'SI bitrate' : row.unit || 'native units'})`);
    if (!$('flent-series').value && rows.length) $('flent-series').value = '0';
    $('flent-output').textContent = JSON.stringify(native, null, 2);
    drawFlent();
  }
  function drawFlent() {
    if (!$('flent-chart')) return;
    const record = flentRun?.flent?.series?.[Number($('flent-series').value)], chart = $('flent-chart');
    const points = record?.points || [], measured = points.filter((row) => Number.isFinite(row.seconds) && Number.isFinite(row.value));
    const width = Math.max(230, chart.getBoundingClientRect().width || 760);
    chart.setAttribute('viewBox', `0 0 ${width} 240`);
    if (!measured.length) { chart.innerHTML = `<text x="${width / 2}" y="115" text-anchor="middle" fill="#aab6bf" font-size="12">No measured native samples</text>`; return; }
    const maxTime = measured.reduce((max, row) => Math.max(max, row.seconds), 1), maxValue = measured.reduce((max, row) => Math.max(max, row.value), 0) * 1.08 || 1e-6;
    const rate = record.unit === 'Mbits/s', scale = rate ? rateScale(measured.map((row) => row.value * 1e6)) : null;
    const x = (seconds) => 55 + seconds / maxTime * (width - 70), y = (value) => 200 - value / maxValue * 175;
    let svg = '', path = '', previous = false;
    for (let step = 0; step <= 3; step++) svg += `<line x1="55" y1="${y(maxValue * step / 3)}" x2="${width - 15}" y2="${y(maxValue * step / 3)}" stroke="#41494d"/><text x="47" y="${y(maxValue * step / 3) + 4}" text-anchor="end" fill="#aab6bf" font-size="12">${num(maxValue * step / 3 * (rate ? 1e6 / scale.divisor : 1), 1)}</text><text x="${x(maxTime * step / 3)}" y="222" text-anchor="${step ? step === 3 ? 'end' : 'middle' : 'start'}" fill="#aab6bf" font-size="12">${num(maxTime * step / 3, 1)}s</text>`;
    for (const row of points) {
      if (!Number.isFinite(row.seconds) || !Number.isFinite(row.value)) { previous = false; continue; }
      path += `${previous ? 'L' : 'M'} ${x(row.seconds)},${y(row.value)} `; previous = true;
      svg += `<circle cx="${x(row.seconds)}" cy="${y(row.value)}" r="3" fill="${runColor(flentRun)}"><title>${rate ? formatMbps(row.value,2,scale) : num(row.value) + ' ' + esc(record.unit)} at ${num(row.seconds)}s</title></circle>`;
    }
    chart.setAttribute('aria-label', `${record.name} in ${scale?.unit || record.unit || 'native units'} over elapsed seconds`);
    $('flent-chart-unit').textContent = scale?.unit || record.unit || 'native units';
    chart.innerHTML = svg + `<path d="${path}" stroke="${runColor(flentRun)}" stroke-width="2" fill="none"/>`;
  }
  async function init() {
    await loadProjects();
    const query = new URLSearchParams(location.search);
    if ($('project_id') && query.get('project')) $('project_id').value = query.get('project');
    if ($('project-form') && (query.get('project') || projects[0]?.id)) await openProject(query.get('project') || projects[0].id);
    $('history-project')?.addEventListener('change', guard(context.refresh));
    $('live-baseline')?.addEventListener('change', () => { localStorage.setItem('flowbench-live-baseline', $('live-baseline').value); liveVersion++; updateLive(true); });
    $('matrix-start')?.addEventListener('click', startMatrix);
    $('flent-start')?.addEventListener('click', startFlent);
    $('flent-confirm')?.addEventListener('change', controls);
    $('flent-series')?.addEventListener('change', drawFlent);
    for (const id of ['matrix-editor', 'duration', 'matrix-streams', 'matrix-bandwidth']) $(id)?.addEventListener('input', matrixData);
    if ($('matrix-preview')) matrixData();
    $('project-form')?.addEventListener('submit', async (event) => {
      event.preventDefault(); $('project-error').textContent = '';
      try { await busy('project-create', async () => {
        const row = await api('/api/projects', {method: 'POST', body: JSON.stringify({name: $('project-name').value, description: $('project-description').value})});
        $('project-form').reset(); await loadProjects(); await openProject(row.id || row.project?.id);
      }); } catch (error) { $('project-error').textContent = error.message; }
    });
    $('project-list')?.addEventListener('click', guard(async (event) => { const id = event.target.closest('[data-project]')?.dataset.project; if (id) await openProject(id); }));
    $('project-edit-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      try { await busy('project-save', async () => {
        await api(`/api/projects/${projectId}`, {method: 'PATCH', body: JSON.stringify({name: $('project-edit-name').value, description: $('project-edit-description').value, baseline_id: $('project-baseline').value || null})});
        await openProject(projectId, false); $('project-detail-status').textContent = 'Project and baseline saved.';
      }); } catch (error) { $('project-detail-status').textContent = error.message; message(error.message); }
    });
    $('project-export')?.addEventListener('click', guard(() => download(`/api/projects/${projectId}/export`, 'flowbench-project.json')));
    $('project-import')?.addEventListener('click', () => $('project-file').click());
    $('project-file')?.addEventListener('change', guard(async () => {
      const file = $('project-file').files[0]; if (!file) return;
      try { await busy('project-import', async () => {
        if (file.size > 20 * 1024 * 1024) throw new Error('Project file exceeds 20 MiB.');
        const result = await api('/api/projects/import', {method: 'POST', body: JSON.stringify({content: await file.text(), filename: file.name})});
        await context.refresh(); await loadProjects(); await openProject(result.project.id);
        message(`Project imported. ${result.imported?.length || 0} tests added; ${result.duplicates?.length || 0} duplicates retained.`, 'success');
      }); } finally { $('project-file').value = ''; }
    }));
    $('timeline-cancel')?.addEventListener('click', resetAnnotation);
    $('report-preview-button')?.addEventListener('click', previewReport);
    $('report-download')?.addEventListener('click', downloadReport);
    for (const id of ['report-redact', 'report-raw']) $(id)?.addEventListener('change', controls);
    $('comparison-selections')?.addEventListener('change', controls);
    $('timeline-form')?.addEventListener('submit', async (event) => {
      event.preventDefault(); $('timeline-error').textContent = '';
      try { await busy('timeline-save', async () => {
        const timestamp = new Date($('timeline-timestamp').value + 'Z');
        if (!Number.isFinite(timestamp.getTime())) throw new Error('Enter a valid UTC timestamp.');
        const recordedTime = annotationTimestamp && $('timeline-timestamp').value === new Date(annotationTimestamp).toISOString().slice(0, 19) ? annotationTimestamp : timestamp.toISOString();
        const note = {timestamp: recordedTime, seconds: $('timeline-seconds').value === '' ? null : Number($('timeline-seconds').value), text: $('timeline-text').value};
        if (annotationId) note.id = annotationId;
        const rows = annotationId ? (context.state().selected.annotations || []).map((row) => row.id === annotationId ? note : row) : [...(context.state().selected.annotations || []), note];
        await patchAnnotations(rows); resetAnnotation();
      }); } catch (error) { $('timeline-error').textContent = error.message; }
    });
    $('timeline-notes')?.addEventListener('click', guard(async (event) => {
      const button = event.target.closest('button'); if (!button) return;
      const rows = context.state().selected.annotations || [];
      if (button.dataset.editNote) {
        const row = rows.find((note) => note.id === button.dataset.editNote); if (!row) return;
        annotationId = row.id; annotationTimestamp = row.timestamp; $('timeline-timestamp').value = new Date(row.timestamp).toISOString().slice(0, 19);
        $('timeline-seconds').value = row.seconds ?? ''; $('timeline-text').value = row.text;
        $('timeline-save').textContent = 'Save timeline note'; $('timeline-cancel').classList.remove('d-none'); $('timeline-text').focus();
      }
      if (button.dataset.deleteNote) { button.disabled = true; try { await patchAnnotations(rows.filter((row) => row.id !== button.dataset.deleteNote)); resetAnnotation(); } catch (error) { button.disabled = false; throw error; } }
    }));
    window.addEventListener('resize', () => { drawLive(); drawFlent(); });
    await context.refresh();
    drawLive(); controls();
  }
  return {init, settings, loadSettings, loadFlentSettings, controls, status, history, selected, edit, annotationSettings, matches, tags};
}
