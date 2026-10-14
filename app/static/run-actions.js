import {isCheck, checkComparable} from './checks.js';

export function comparisonEligible(run) {
  return run && (checkComparable(run) || run.status === 'completed' && !['protocol-job', 'matrix-job'].includes(run.kind) && Object.keys(run.metrics || {}).length > 0);
}
export function comparisonQuery(params, runs) {
  const ids = params.getAll('id');
  if (!ids.length) return null;
  if (ids.length < 2 || ids.some((id) => !/^[a-f0-9]{32}$/.test(id)) || new Set(ids).size !== ids.length) return {ids: [], error: 'The comparison link must name at least two different valid saved attempts. Choose a baseline and additional test to continue.'};
  const records = ids.map((id) => runs.find((run) => run.id === id));
  if (records.some((run) => !comparisonEligible(run))) return {ids, error: 'One or more requested attempts are unavailable, still active, or not eligible for an individual comparison. Open saved history or choose eligible tests.'};
  const checks = records.filter(isCheck);
  if (checks.length && (checks.length !== records.length || checks.some((run) => run.check_type !== checks[0].check_type))) return {ids, error: 'The comparison link mixes measurement types. Compare DNS, ICMP and throughput evidence separately.'};
  return {ids, error: ''};
}

const lineageMarkup = new WeakMap();
export function renderLineage(run, runs, {escape: esc}) {
  const element = document.getElementById('run-lineage'); if (!element) return;
  const sourceId = run.lineage?.source_run_id;
  const source = /^[a-f0-9]{32}$/.test(sourceId || '') ? runs.find((row) => row.id === sourceId) : null;
  const group = ['protocol-job', 'matrix-job'].includes(run.kind) || ['protocol-job', 'matrix-job'].includes(source?.kind);
  const matching = source && (isCheck(run) ? isCheck(source) && run.check_type === source.check_type : !isCheck(source) && (run.kind === 'flent-run') === (source.kind === 'flent-run'));
  const comparable = matching && !group && comparisonEligible(run) && comparisonEligible(source);
  const external = run.lineage?.external_source_id;
  const html = `${source ? `New attempt from copied configuration. <a href="/?run=${sourceId}">Original attempt</a>. ${group ? 'Open individual children to compare their measured evidence; jobs have no combined metric. ' : ''}${comparable ? `<a href="/comparison?${new URLSearchParams([['id', sourceId], ['id', run.id]]).toString()}">Compare with original</a>. ` : ''}` : sourceId ? 'The local original attempt is unavailable. ' : external ? `Imported external ancestor: ${esc(external)}. ` : 'No local rerun source is recorded. '}<a href="/history">Saved history</a>.`;
  if (lineageMarkup.get(element) === html) return;
  const active = document.activeElement, focusedHref = element.contains?.(active) ? active?.getAttribute?.('href') : null;
  element.innerHTML = html; lineageMarkup.set(element, html);
  if (focusedHref) [...element.querySelectorAll('a')].find((link) => link.getAttribute('href') === focusedHref)?.focus({preventScroll: true});
}

export function requestId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID().replaceAll('-', '');
  if (globalThis.crypto?.getRandomValues) return [...crypto.getRandomValues(new Uint8Array(16))].map((value) => value.toString(16).padStart(2, '0')).join('');
  throw new Error('This browser cannot create a secure request identifier. Use a current browser on localhost or HTTPS.');
}

// One explicit intent owns one immutable request body. Only an uncertain
// transport failure exposes Retry; a new start always gets a new identifier.
export function createAttempt({api, start, status, retryButton}) {
  let pending = null, busy = false;
  const run = async (intent, retry = false) => {
    if (busy) return;
    const attempt = retry ? pending : {path: intent.path, body: {...structuredClone(intent.body), request_id: requestId()}};
    if (!attempt) return;
    busy = true; if (retryButton) retryButton.hidden = true;
    try {
      const result = await api(attempt.path, {method: 'POST', body: JSON.stringify(attempt.body)});
      const record = result.run || result;
      pending = null; await start(record); return record;
    } catch (error) {
      pending = error.status ? null : attempt;
      if (retryButton) retryButton.hidden = !pending;
      error.message += pending ? ' The response is uncertain. Retry this same request to recover its accepted attempt without starting twice.' : '';
      status(error.message);
      throw error;
    } finally { busy = false; }
  };
  retryButton?.addEventListener('click', () => run(null, true).catch(() => {}));
  return {run, busy: () => busy, hasRetry: () => Boolean(pending), clear: () => { pending = null; if (retryButton) retryButton.hidden = true; }};
}

export function planDraft(plan) {
  const config = structuredClone(plan.config || plan.settings || {});
  return {kind: plan.kind || 'run', check_type: plan.check_type, config, matrix: structuredClone(plan.matrix || config.matrix || null)};
}

export function loadSavedDraft(draft, handlers) {
  const config = draft.config, $ = (id) => document.getElementById(id);
  if (draft.kind === 'diagnostic-check') { handlers.check({...config, check_type: draft.check_type || config.check_type}); handlers.metadata(config); return; }
  if (!['iperf-run', 'run', 'protocol-job', 'matrix-job', 'flent-run'].includes(draft.kind)) throw new Error('This saved measurement type cannot be loaded by this app version.');
  handlers.tool('iperf');
  if (draft.kind === 'flent-run') { handlers.iperf({...config, role: 'client'}); handlers.flent({kind: draft.kind, settings: {...config, port: config.netserver_port}}); return; }
  if (draft.kind === 'matrix-job') {
    handlers.iperf(config.settings);
    for (const key of ['protocol', 'mode']) document.querySelectorAll(`input[name="matrix-${key}"]`).forEach((input) => { input.checked = config[key === 'protocol' ? 'protocols' : 'modes'].includes(input.value); });
    $('matrix-streams').value = config.streams.join(','); $('matrix-bandwidth').value = config.settings.bandwidth || '10M';
    $('matrix-editor').open = true; $('matrix-streams').focus(); return;
  }
  handlers.iperf(config, true); $('target').focus();
}

export function createRunActions(context) {
  const $ = (id) => document.getElementById(id);
  let sourceId = null, plan = null, loading = false;
  const attempt = createAttempt({api: context.api, start: context.start,
    status: (text) => { $('run-again-status').textContent = text; }, retryButton: $('run-again-retry')});
  const controls = () => {
    if (!$('run-again-button')) return;
    const unavailable = !context.selected() || loading || attempt.busy() || Boolean(context.active());
    $('run-again-button').disabled = unavailable;
    if ($('rerun-button')) $('rerun-button').disabled = !context.selected() || loading;
  };
  const getPlan = async () => {
    const id = context.selected()?.id;
    if (!id) throw new Error('Open a saved test first.');
    const value = await context.api(`/api/runs/${encodeURIComponent(id)}/rerun-plan`);
    if (id !== context.selected()?.id) throw new Error('Selected test changed. Review its configuration again.');
    sourceId = id; plan = value; return value;
  };
  const load = async () => {
    if (loading) return; loading = true; controls();
    try { await context.load(planDraft(await getPlan())); $('run-again-status').textContent = 'Saved configuration loaded. Edit it or start a new attempt when ready.'; }
    catch (error) { $('run-again-status').textContent = error.message; }
    finally { loading = false; controls(); }
  };
  const execute = async (confirmations = {}) => {
    if (!plan || sourceId !== context.selected()?.id) return;
    try { await attempt.run({path: `/api/runs/${sourceId}/rerun`, body: {source_fingerprint: plan.source_fingerprint, ...confirmations}}); $('run-again-review').hidden = true; }
    catch { /* The request status retains an explicit recovery action. */ }
    finally { controls(); }
  };
  const again = async () => {
    if (loading || attempt.busy() || context.active()) return;
    loading = true; controls(); attempt.clear();
    try {
      await getPlan();
      const required = plan.required_confirmations || [];
      const flent = plan.kind === 'flent-run' || required.includes('confirm_netserver');
      const incoming = plan.config?.role === 'server' || required.includes('confirm_wait_for_client');
      $('run-again-netserver').checked = false; $('run-again-incoming').checked = false;
      $('run-again-flent-confirmation').hidden = !flent; $('run-again-incoming-confirmation').hidden = !incoming;
      $('run-again-config').textContent = JSON.stringify(plan.config || plan.settings || {}, null, 2);
      $('run-again-review').hidden = !(flent || incoming);
      if (flent || incoming) { $('run-again-status').textContent = 'Review the saved configuration and confirm this new bounded attempt.'; $('run-again-review').focus(); }
      else await execute();
    } catch (error) { $('run-again-status').textContent = error.message; }
    finally { loading = false; controls(); }
  };
  $('rerun-button')?.addEventListener('click', load);
  $('run-again-button')?.addEventListener('click', again);
  $('run-again-cancel')?.addEventListener('click', () => { $('run-again-review').hidden = true; $('run-again-button').focus(); });
  $('run-again-confirm')?.addEventListener('click', () => {
    const flent = !$('run-again-flent-confirmation').hidden, incoming = !$('run-again-incoming-confirmation').hidden;
    if (flent && !$('run-again-netserver').checked || incoming && !$('run-again-incoming').checked) { $('run-again-status').textContent = 'Confirm the required peer or one-off incoming wait before starting.'; return; }
    return execute({...flent ? {confirm_netserver: true} : {}, ...incoming ? {confirm_wait_for_client: true} : {}});
  });
  return {controls, selected() { if (sourceId && sourceId !== context.selected()?.id) { attempt.clear(); plan = null; sourceId = null; $('run-again-review').hidden = true; } controls(); }};
}
