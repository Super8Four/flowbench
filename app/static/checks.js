const terminal = new Set(['completed', 'failed', 'partial', 'cancelled', 'interrupted']);
export const isCheck = (run) => run?.kind === 'diagnostic-check';
export const checkLabel = (type) => type === 'dns' ? 'DNS lookup' : 'ICMP echo check';
export const checkComparable = (run) => isCheck(run) && terminal.has(run.status) && (run.check_comparable === undefined ? Boolean(run.result) : run.check_comparable === true);
export function checkSummary(run, {number: num}) {
  const summary = run.result?.summary || {};
  if (run.check_type === 'dns') return `${num(summary.responses, 0)} responses / ${num(summary.requested_queries, 0)} requested queries`;
  return `${num(summary.echo_replies, 0)} echo replies / ${Number.isFinite(summary.transmitted) ? num(summary.transmitted, 0) : 'unknown'} transmitted · ${Number.isFinite(summary.nonresponse_percent) ? num(summary.nonresponse_percent) + '% ICMP echo nonresponse' : 'nonresponse percentage unavailable'}`;
}
const checkContentStates = new WeakMap();
export function replaceCheckContent(content, html, identity) {
  const previous = checkContentStates.get(content);
  if (previous?.html === html && previous.identity === identity) return;
  const sameCheck = previous?.identity === identity;
  const opened = sameCheck ? new Set([...content.querySelectorAll('details[data-check-key]')].filter((element) => element.open).map((element) => element.dataset.checkKey)) : new Set();
  const active = document.activeElement;
  const focused = sameCheck && content.contains?.(active) ? active?.closest?.('[data-check-focus]') : null;
  const focusKey = focused?.dataset.checkFocus, scrollTop = focused?.scrollTop, scrollLeft = focused?.scrollLeft;
  content.innerHTML = html;
  checkContentStates.set(content, {html, identity});
  for (const detail of content.querySelectorAll('details[data-check-key]')) if (opened.has(detail.dataset.checkKey)) detail.open = true;
  if (focusKey) {
    const replacement = [...content.querySelectorAll('[data-check-focus]')].find((element) => element.dataset.checkFocus === focusKey);
    if (replacement) { replacement.focus({preventScroll: true}); replacement.scrollTop = scrollTop; replacement.scrollLeft = scrollLeft; }
  }
}
export function renderCheck(run, context) {
  const $ = (id) => document.getElementById(id), {escape: esc, number: num, date} = context;
  const card = $('check-results'); if (!card) return;
  card.hidden = !isCheck(run); if (!isCheck(run)) return;
  const result = run.result || {}, summary = result.summary || {}, attempts = result.attempts || [];
  $('check-result-heading').textContent = `${checkLabel(run.check_type)} evidence`;
  const message = `${run.status} · ${result.status || 'pending'} · ${checkSummary(run, context)}`;
  if ($('check-result-status').textContent !== message) $('check-result-status').textContent = message;
  $('check-result-note').textContent = result.error || result.note || 'Captured observations are retained, including unavailable and incomplete evidence.';
  const metric = (value, unit = '') => Number.isFinite(value) ? num(value, 3) + unit : 'Unavailable';
  const entries = run.check_type === 'dns'
    ? [['Requested queries', summary.requested_queries], ['Attempted queries', summary.attempted_queries], ['Transport attempts', summary.transport_attempts], ['DNS responses', summary.responses]]
    : [['Requested probes', summary.requested], ['Attempted probes', summary.attempted], ['Unattempted probes', summary.unattempted], ['Transmitted (known total)', summary.transmitted], ['Confirmed transmitted', summary.confirmed_transmitted], ['Echo replies', summary.echo_replies], ['Confirmed nonresponses', summary.confirmed_nonresponses], ['Indeterminate attempts', summary.indeterminate_attempts], ['ICMP echo nonresponse', metric(summary.nonresponse_percent, '%')]];
  const details = `<dl class="check-summary">${entries.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${typeof value === 'string' ? esc(value) : metric(value)}</dd>`).join('')}<dt>Started</dt><dd>${esc(date(result.started_at || run.created_at))}</dd><dt>Finished</dt><dd>${esc(date(result.finished_at || run.finished_at))}</dd><dt>Measured elapsed</dt><dd>${metric(result.elapsed_seconds, ' s')}</dd></dl>`;
  const outcomes = run.check_type === 'dns' ? `<p>Final DNS outcomes: ${esc(Object.entries(summary.outcomes || {}).map(([key, count]) => `${key}: ${count}`).join(' · ') || 'Unavailable')}</p>`
    : `<p class="form-text">ICMP echo RTT uses numeric replies only. Bounded replies such as &lt;1 ms remain bounds. Unknown transmission counts leave the percentage unavailable; no replies do not establish application failure.</p><div class="table-responsive"><table class="table"><caption>ICMP echo RTT from captured numeric replies</caption><tbody>${Object.entries(summary.rtt || {}).map(([key, value]) => `<tr><th scope="row">${esc(key.replaceAll('_', ' '))}</th><td>${metric(value, key.endsWith('_ms') ? ' ms' : '')}</td></tr>`).join('') || '<tr><td>No numeric RTT statistics are available.</td></tr>'}</tbody></table></div>`;
  const resolution = result.resolution ? `<details data-check-key="resolution"><summary data-check-focus="resolution:summary">Target resolution evidence</summary><pre class="peer-output" tabindex="0" data-check-focus="resolution:raw">${esc(JSON.stringify(result.resolution, null, 2))}</pre></details>` : '';
  const rows = attempts.map((attempt, index) => {
    const key = Number.isInteger(attempt.sequence) ? attempt.sequence : index + 1;
    const prefix = `<p>Started ${esc(date(attempt.started_at))} · finished ${esc(date(attempt.finished_at))} · duration ${metric(attempt.duration_ms, ' ms')}</p>`;
    let evidence;
    if (run.check_type === 'dns') {
      evidence = `<p>Resolver ${esc(attempt.resolver_address || 'Unavailable')}:${esc(attempt.resolver_port ?? '')} · ${esc(attempt.resolver_family || '')} · ${esc(attempt.transport || '')} · RCODE ${esc(attempt.rcode ?? 'Unavailable')}</p><p>Question: ${esc(attempt.question?.name || '')} ${esc(attempt.question?.type || '')} ${esc(attempt.question?.class || '')}</p><p>Flags: ${esc(typeof attempt.flags === 'object' ? JSON.stringify(attempt.flags) : attempt.flags || 'Unavailable')}</p>`;
      for (const section of ['answers', 'authority', 'additional']) evidence += `<h4 class="fs-6">${section}</h4><div class="table-responsive"><table class="table"><thead><tr><th>Name</th><th>Type / class</th><th>TTL</th><th>Captured data</th></tr></thead><tbody>${(attempt[section] || []).map((rr) => `<tr><td>${esc(rr.name)}</td><td>${esc(rr.type)} / ${esc(rr.class)}</td><td>${esc(rr.ttl ?? 'Unavailable')}</td><td>${esc(typeof rr.rdata === 'object' ? JSON.stringify(rr.rdata) : rr.rdata)}</td></tr>`).join('') || '<tr><td colspan="4">No records captured in this section.</td></tr>'}</tbody></table></div>`;
      if (attempt.records_truncated) evidence += '<p class="text-warning-emphasis">Stored record list was bounded; inspect truncation evidence below.</p>';
    } else evidence = `<p>ICMP echo RTT: ${Number.isFinite(attempt.rtt_ms) ? metric(attempt.rtt_ms, ' ms') : Number.isFinite(attempt.rtt_upper_bound_ms) ? '&lt;' + metric(attempt.rtt_upper_bound_ms, ' ms') : 'Unavailable'} · Transmitted ${metric(attempt.transmitted)} · Echo replies ${metric(attempt.echo_replies)}</p>`;
    return `<details class="check-attempt" data-check-key="attempt:${key}"><summary data-check-focus="attempt:${key}:summary">${run.check_type === 'dns' ? 'Query' : 'Probe'} ${esc(attempt.query_index ?? attempt.sequence ?? index + 1)} · ${esc(attempt.status || 'unavailable')}${attempt.rcode ? ' · ' + esc(attempt.rcode) : ''}</summary>${prefix}${evidence}${attempt.error ? `<p class="text-warning-emphasis">${esc(attempt.error)}</p>` : ''}<details data-check-key="native:${key}"><summary data-check-focus="native:${key}:summary">Native evidence${attempt.output_truncated ? ' (bounded output)' : ''}</summary><pre class="peer-output" tabindex="0" data-check-focus="native:${key}:raw">${esc(attempt.raw_output || 'No native output recorded.')}</pre></details></details>`;
  }).join('');
  const html = details + outcomes + resolution + (rows || '<p>No probe attempts were recorded.</p>');
  replaceCheckContent($('check-result-content'), html, `${run.id}:${run.check_type}`);
}

export function renderCheckComparison(report, context) {
  const {escape: esc, number: num, title, date} = context;
  const runs = report.runs || [];
  const value = (input, unit) => Number.isFinite(input) ? num(input, 3) + (unit ? ' ' + esc(unit) : '') : 'Unavailable';
  const answers = (rows) => rows === null || rows === undefined ? '<p>Unavailable: no complete captured answer set.</p>' : rows.length ? `<ul>${rows.map((rr) => `<li>${esc(rr.name)} ${esc(rr.type)} ${esc(rr.class)} ${esc(typeof rr.rdata === 'object' ? JSON.stringify(rr.rdata) : rr.rdata)}</li>`).join('')}</ul>` : '<p>Captured empty set.</p>';
  const pairs = (report.comparisons || []).map((pair, index) => `<details class="mt-3"><summary>Baseline → test ${index + 2}: captured evidence changes</summary><p>${esc(pair.note || pair.interpretation || pair.caveat || '')}</p><p>Settings differences: ${esc((pair.settings_differences || []).join(', ') || 'None recorded')}</p><ul>${(pair.caveats || []).map((note) => `<li>${esc(note)}</li>`).join('')}</ul><div class="table-responsive"><table class="table"><thead><tr><th>Observation</th><th>Baseline</th><th>Selected test</th><th>Difference</th></tr></thead><tbody>${(pair.rows || []).map((row) => `<tr><th scope="row">${esc(row.label || row.metric)}</th><td>${value(row.before, row.unit)}</td><td>${value(row.after, row.unit)}</td><td>${value(row.delta, row.unit)}</td></tr>`).join('')}</tbody></table></div>${pair.outcomes ? `<p>DNS outcomes: baseline ${esc(JSON.stringify(pair.outcomes.before))}; selected ${esc(JSON.stringify(pair.outcomes.after))}.</p>` : ''}${pair.answer_sets ? `<h4 class="fs-6">Added captured answers</h4>${answers(pair.answer_sets.added)}<h4 class="fs-6">Removed captured answers</h4>${answers(pair.answer_sets.removed)}` : ''}<details><summary>Complete comparison evidence</summary><pre class="peer-output" tabindex="0">${esc(JSON.stringify(pair, null, 2))}</pre></details></details>`).join('');
  return `<p class="comparison-note">${esc(checkLabel(report.check_type || runs[0]?.check_type))} comparison. Outcomes and captured subsets retain their actual status; this is not a throughput comparison.</p><div class="table-responsive"><table class="table"><thead><tr><th>Test</th><th>Status</th><th>Target / settings</th><th>Captured observations</th></tr></thead><tbody>${runs.map((run, index) => `<tr><th scope="row">${index + 1}. ${esc(title(run))}${index ? '' : ' · baseline'}<div class="form-text">${esc(date(run.created_at))}</div></th><td>${esc(run.status)} / ${esc(run.result?.status || 'unavailable')}</td><td>${esc(run.settings?.target || '')}${run.check_type === 'dns' ? ' / ' + esc(run.settings?.record_type || '') : ''}</td><td>${esc(checkSummary(run, {number: num}))}</td></tr>`).join('')}</tbody></table></div>${pairs}`;
}

export function renderCheckReportChanges(report, {escape: esc, number: num}) {
  if (report.report_type !== 'diagnostic-check') return '';
  const value = (input, unit) => Number.isFinite(input) ? num(input, 3) + (unit ? ' ' + esc(unit) : '') : 'Unavailable';
  return (report.comparisons || []).map((pair, index) => `<article class="workflow-annotation"><h4 class="fs-6">Baseline → check ${index + 2}</h4><p>${esc(pair.caveat || '')}</p><p>Settings differences: ${esc((pair.settings_differences || []).join(', ') || 'None recorded')}</p><div class="table-responsive"><table class="table"><thead><tr><th>Observation</th><th>Baseline</th><th>Selected</th><th>Change</th></tr></thead><tbody>${(pair.metrics || []).map((row) => `<tr><th scope="row">${esc(row.metric)}</th><td>${value(row.before, row.unit)}</td><td>${value(row.after, row.unit)}</td><td>${value(row.delta, row.unit)}</td></tr>`).join('')}</tbody></table></div></article>`).join('');
}

export function createChecks(context) {
  const $ = (id) => document.getElementById(id);
  let type = 'iperf', capabilities = {}, drafts = new Map();
  const capture = () => Object.fromEntries([...($('test-form')?.querySelectorAll('input, select, textarea') || [])].filter((input) => input.id && input.id !== 'measurement-type').map((input) => [input.id, {value: input.value, checked: input.checked}]));
  function show(next = $('measurement-type')?.value || 'iperf') {
    if (!$('measurement-type')) return;
    drafts.set(type, capture()); type = next;
    for (const [id, value] of Object.entries(drafts.get(type) || {})) if ($(id)) { $(id).value = value.value; if ($(id).type === 'checkbox') $(id).checked = value.checked; }
    $('measurement-type').value = type;
    for (const kind of ['iperf', 'dns', 'loss']) {
      const group = $(kind + '-fields'); if (!group) continue;
      group.hidden = kind !== type; group.disabled = kind !== type;
    }
    for (const id of ['iperf-advanced', 'iperf-family', 'preflight-policy', 'optional-iperf-context', 'matrix-panel', 'flent-panel']) if ($(id)) $(id).hidden = type !== 'iperf';
    if ($('check-policy')) $('check-policy').hidden = type === 'iperf';
    if ($('check-capability')) { $('check-capability').hidden = type === 'iperf'; $('check-capability').textContent = type === 'iperf' ? '' : `${capabilities[type]?.status || 'Checking capability'} · ${capabilities[type]?.note || ''}`; }
    resolver();
    context.controls();
  }
  function settings() {
    const common = context.metadata();
    const metadata = Object.fromEntries(['label', 'notes', 'project_id', 'tags', 'color'].filter((key) => common[key] !== undefined).map((key) => [key, common[key]]));
    if (type === 'dns') return {check_type: type, target: $('dns-target').value.trim(), record_type: $('dns-record-type').value, resolver_mode: $('dns-resolver-mode').value, resolver_address: $('dns-resolver-mode').value === 'custom' ? $('dns-resolver-address').value.trim() : null, resolver_port: Number($('dns-resolver-port').value), transport: $('dns-transport').value, count: Number($('dns-count').value), timeout_ms: Number($('dns-timeout').value), ...metadata};
    return {check_type: type, target: $('loss-target').value.trim(), address_family: $('loss-family').value, count: Number($('loss-count').value), ...metadata};
  }
  function load(config) {
    const kind = config.check_type; show(kind);
    const mapping = kind === 'dns' ? {target: 'dns-target', record_type: 'dns-record-type', resolver_mode: 'dns-resolver-mode', resolver_address: 'dns-resolver-address', resolver_port: 'dns-resolver-port', transport: 'dns-transport', count: 'dns-count', timeout_ms: 'dns-timeout'} : {target: 'loss-target', address_family: 'loss-family', count: 'loss-count'};
    for (const [key, id] of Object.entries(mapping)) if ($(id) && config[key] !== undefined) $(id).value = config[key] ?? '';
    resolver(); $(kind + '-target')?.focus();
  }
  function resolver() { if ($('dns-resolver-address')) { const custom = $('dns-resolver-mode').value === 'custom'; $('dns-custom-resolver').hidden = !custom; $('dns-resolver-address').disabled = !custom; $('dns-resolver-address').required = custom; } }
  $('measurement-type')?.addEventListener('change', () => { show(); context.typeChanged(); });
  $('dns-resolver-mode')?.addEventListener('change', resolver);
  return {type: () => type, show, settings, load, status(value) { capabilities = value.checks || value.check_capabilities || capabilities; if ($('check-capability') && type !== 'iperf') $('check-capability').textContent = `${capabilities[type]?.status || 'unavailable'} · ${capabilities[type]?.note || ''}`; }, ready: () => type === 'iperf' || capabilities[type]?.status === 'available' || capabilities[type]?.available === true, render: (run) => renderCheck(run, context)};
}
