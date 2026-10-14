// Render normalized API evidence only. This module never resolves a hostname,
// inspects browser interfaces, or infers a NAT/public address.
const unavailable = 'Unavailable';
const groups = new Set(['protocol-job', 'matrix-job']);
const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted', 'partial', 'skipped']);
const family = (point) => point?.family === 4 ? 'IPv4' : point?.family === 6 ? 'IPv6' : 'family unavailable';
const address = (point) => typeof point?.address === 'string' && point.address ? point.address : null;
const port = (point) => Number.isInteger(point?.port) && point.port >= 1 && point.port <= 65535 ? point.port : null;
const unique = (values) => [...new Set(values)];
export function endpointPoint(point) {
  const ip = address(point), service = port(point);
  if (!ip) return unavailable;
  return `${service ? ip.includes(':') ? `[${ip}]:${service}` : `${ip}:${service}` : ip} · ${family(point)}`;
}
function requested(run) {
  const settings = run?.settings || {};
  if (run?.check_type === 'dns') return `Query: ${settings.target || 'not selected'} (${settings.record_type || 'A'})\nResolver: ${settings.resolver_mode === 'custom' ? settings.resolver_address || 'not selected' : 'configured system resolver'} · port ${settings.resolver_port || 53} · ${settings.transport || 'udp'}`;
  if (run?.check_type === 'loss') return `${settings.target || 'Not selected'} · requested IP family ${settings.address_family === '4' ? 'IPv4' : settings.address_family === '6' ? 'IPv6' : 'automatic'}`;
  return `${settings.target || (settings.role === 'server' ? 'Waiting for an incoming client' : 'Not selected')}${settings.port ? ' · port ' + settings.port : ''}`;
}
const provenanceLabels = {captured_archive: 'Recorded run metadata', imported_archive: 'Imported archive evidence', preserved_native: 'Derived from preserved native evidence', not_recorded: 'Not recorded'};
export function endpointPanelModel(run, current, {loadingId = null, failed = false, draft = null} = {}) {
  const selected = Boolean(run), group = groups.has(run?.kind);
  const view = run?.endpoint_view?.schema_version === 1 ? run.endpoint_view : null;
  const sockets = Array.isArray(view?.sockets) ? view.sockets : [];
  const controls = Array.isArray(view?.control_peers) ? view.control_peers : [];
  const destinations = Array.isArray(view?.destinations) ? view.destinations : [];
  const localSockets = sockets.filter((row) => row.reporter === 'local');
  const local = unique(localSockets.filter((row) => address(row.local)).map((row) => endpointPoint(row.local)));
  const remote = unique(localSockets.filter((row) => address(row.remote)).map((row) => endpointPoint(row.remote)));
  let peerLabel = 'Measured peer', peer = remote;
  const destinationRows = run?.check_type === 'dns' ? destinations.filter((row) => row.purpose === 'dns_resolver')
    : run?.check_type === 'loss' ? destinations.filter((row) => row.purpose === 'icmp_target')
      : run?.kind === 'flent-run' ? destinations.filter((row) => row.purpose === 'flent_reported_target') : [];
  if (run?.check_type === 'dns') peerLabel = 'Resolver used';
  if (run?.check_type === 'loss') peerLabel = 'Pinned destination';
  if (run?.kind === 'flent-run') peerLabel = 'Tool-reported target';
  if (destinationRows.length) peer = unique(destinationRows.map((row) => `${address(row.endpoint) ? endpointPoint(row.endpoint) : row.reported_host || unavailable}${row.transport ? ' · ' + row.transport.toUpperCase() : ''}`));
  const hostname = selected ? view?.execution_hostname || unavailable : loadingId ? unavailable : current?.execution_hostname || unavailable;
  const status = loadingId ? failed ? 'Selected evidence unavailable' : 'Loading selected attempt' : selected ? (view?.status || 'not_recorded').replaceAll('_', ' ') : 'No attempt selected';
  const provenance = selected ? `${run.imported ? 'Imported / historical · ' : ''}${provenanceLabels[view?.provenance] || 'Not recorded'}` : loadingId ? 'Selected attempt' : 'Current runtime; no measured socket yet';
  const settingsRun = selected ? run : loadingId ? null : draft;
  const target = settingsRun ? requested(settingsRun) : unavailable;
  const bind = settingsRun ? settingsRun.settings?.bind || 'Automatic (requested)' : unavailable;
  const note = loadingId ? `Attempt ${loadingId}. ${failed ? 'Its saved endpoint evidence could not be loaded.' : 'Previous endpoint copy values have been cleared.'}`
    : group ? 'This job has no combined traffic endpoint. Open an individual child below for its recorded connections.'
      : !selected ? 'Tests execute in FlowBench. A browser controls the test; its web connection is not the measured traffic source.'
        : view?.provenance === 'not_recorded' || !view ? 'Endpoint metadata was not recorded for this saved attempt. Current runtime addresses are not substituted.'
          : view.status === 'pending' && !terminal.has(run.status) ? 'Waiting for measured endpoint evidence. Requested settings are not an observed connection.'
            : !sockets.length && !destinations.length ? 'No measured data endpoint was captured for this attempt. Requested settings and any control peer remain separate.'
          : run?.kind === 'flent-run' ? 'Flent target metadata is a tool declaration, not proof of a measured socket or reached peer.'
            : run?.check_type === 'loss' ? 'The selected ICMP destination is recorded. Native ping does not establish an observed local source address or socket port.'
              : run?.check_type === 'dns' ? 'Resolver destinations are captured from query attempts. DNS answers are not the resolver or the traffic source.'
                : 'Recorded data sockets; local and remote are relative to each reporting process.';
  const knownLocal = local.length || selected && view?.execution_hostname || !selected && !loadingId && current?.execution_hostname;
  const knownPeer = remote.length || destinationRows.some((row) => address(row.endpoint) || typeof row.reported_host === 'string' && row.reported_host);
  return {identity: loadingId ? `loading:${loadingId}` : run?.id || 'draft', selected, group, status, provenance, hostname, local, peer, peerLabel, target, bind, note, sockets, controls, destinations,
    initiator: run?.initiator_context || null, current: current?.web_request || null,
    copies: {local: !loadingId && knownLocal ? `FlowBench runtime (${selected ? 'recorded' : 'current'}): ${hostname}\nMeasured local: ${local.join('; ') || unavailable}\nRequested bind: ${bind}\nEvidence: ${provenance}` : '',
      peer: !loadingId && knownPeer ? `${peerLabel}: ${peer.join('; ')}\nRequested: ${target}\nEvidence: ${provenance}` : ''}};
}

export function createEndpointPanel(context) {
  const $ = (id) => document.getElementById(id), panel = $('test-endpoints');
  if (!panel) return {init: async () => {}, render() {}, loading() {}, failed() {}, refresh() {}};
  const {escape: esc, date} = context;
  let run = null, current = null, currentState = 'loading', loadingId = null, failed = false, model = null, busyCopy = null, generation = 0;
  const htmlSources = new WeakMap();
  const text = (id, value) => { if ($(id).textContent !== value) $(id).textContent = value; };
  const html = (id, value) => { if (htmlSources.get($(id)) !== value) { $(id).innerHTML = value; htmlSources.set($(id), value); } };
  const contextText = (value) => value?.source === 'wsgi_remote_addr' && address(value.peer)
    ? `${endpointPoint(value.peer)} · request port unavailable\nObserved ${date(value.observed_at)}` : 'Not recorded';
  const copyControls = () => {
    for (const kind of ['local', 'peer', 'web']) $('endpoint-copy-' + kind).disabled = Boolean(busyCopy) || !model?.copies[kind];
  };
  function refresh() {
    const previousIdentity = model?.identity;
    model = endpointPanelModel(run, current, {loadingId, failed, draft: !run && !loadingId ? context.draft?.() : null});
    if (previousIdentity !== model.identity) { generation++; $('endpoint-copy-fallback').hidden = true; text('endpoint-copy-feedback', ''); }
    text('endpoint-status', model.status); text('endpoint-scope', model.note); text('endpoint-provenance', model.provenance);
    text('endpoint-runtime-host', model.hostname); text('endpoint-local', model.local.length ? model.local.slice(0, 2).join('\n') + (model.local.length > 2 ? `\n${model.local.length - 2} more; see recorded connections.` : '') : unavailable);
    text('endpoint-runtime-label', model.selected ? 'Recorded runtime name' : 'Current runtime name');
    text('endpoint-requested-bind', model.bind); text('endpoint-peer-label', model.peerLabel);
    text('endpoint-peer', model.peer.length ? model.peer.slice(0, 2).join('\n') + (model.peer.length > 2 ? `\n${model.peer.length - 2} more; see recorded connections.` : '') : unavailable);
    text('endpoint-requested-target', model.target);
    text('endpoint-namespace-note', 'Runtime names and local addresses belong to the reporting process/container. They do not identify a physical host, NAT egress or public IP.');
    text('endpoint-saved-web', model.selected ? contextText(model.initiator) : loadingId ? unavailable : 'No attempt selected');
    const currentText = currentState === 'loading' ? 'Loading current viewing context…' : currentState === 'unavailable' ? 'Current viewing context unavailable' : contextText(model.current);
    text('endpoint-current-web', currentText);
    text('endpoint-current-runtime', current?.execution_hostname || unavailable);
    const webKnown = address(model.initiator?.peer) || address(model.current?.peer);
    model.copies.web = webKnown ? `Saved initiating web peer (not traffic source): ${contextText(model.initiator)}\nCurrent viewing web peer (not traffic source): ${currentText}` : '';
    const socketRows = model.sockets.map((row) => `<tr><td>${row.reporter === 'local' ? 'Local native report' : 'Peer native report'}</td><td>${esc(row.observer || 'unknown')}</td><td>${esc(row.socket_id ?? unavailable)}</td><td>${esc(row.protocol?.toUpperCase() || unavailable)}</td><td>${esc(endpointPoint(row.local))}</td><td>${esc(endpointPoint(row.remote))}</td></tr>`).join('');
    const controlRows = model.controls.map((row) => `<tr><td>${row.reporter === 'local' ? 'Local native report' : 'Peer native report'}</td><td>${esc(endpointPoint(row.peer))}</td></tr>`).join('');
    const destinationRows = model.destinations.map((row) => `<tr><td>${esc(({dns_resolver: 'DNS resolver used', icmp_target: 'Pinned ICMP destination', flent_reported_target: 'Flent tool-reported target'})[row.purpose] || row.purpose)}</td><td>${esc(endpointPoint(row.endpoint))}</td><td>${esc(row.reported_host || unavailable)}</td><td>${esc(row.transport?.toUpperCase() || unavailable)}</td><td>${esc(({attempt_destination: 'Query attempt', pinned_destination: 'Pinned resolution', tool_metadata: 'Tool metadata'})[row.source] || row.source)}</td></tr>`).join('');
    text('endpoint-connections-summary', `Recorded connections: ${model.sockets.length} data socket(s), ${model.controls.length} control peer(s), ${model.destinations.length} destination(s)`);
    html('endpoint-socket-rows', socketRows || '<tr><td colspan="6">No recorded data sockets. Requested addresses are not measured socket evidence.</td></tr>');
    html('endpoint-control-rows', controlRows || '<tr><td colspan="2">No accepted control peer recorded.</td></tr>');
    html('endpoint-destination-rows', destinationRows || '<tr><td colspan="5">No recorded check or tool destination.</td></tr>');
    copyControls();
  }
  async function copy(kind) {
    const value = model?.copies[kind]; if (!value || busyCopy) return;
    const token = generation, identity = model.identity;
    busyCopy = kind; copyControls();
    try {
      if (context.copy) await context.copy(value);
      else {
        if (!globalThis.navigator?.clipboard?.writeText) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(value);
      }
      if (token === generation && identity === model.identity) text('endpoint-copy-feedback', `${kind === 'web' ? 'Web context' : kind === 'peer' ? model.peerLabel : 'Execution details'} copied.`);
    } catch {
      if (token === generation && identity === model.identity) {
        $('endpoint-copy-value').value = value; $('endpoint-copy-fallback').hidden = false;
        text('endpoint-copy-feedback', "Automatic copy is unavailable. Select the text below and use your device's Copy command.");
        $('endpoint-copy-value').focus(); $('endpoint-copy-value').select();
      }
    } finally { busyCopy = null; copyControls(); }
  }
  for (const kind of ['local', 'peer', 'web']) $('endpoint-copy-' + kind).addEventListener('click', () => copy(kind));
  return {
    async init() {
      refresh();
      try { const value = await context.api('/api/context'); current = value?.schema_version === 1 ? value : null; currentState = current ? 'captured' : 'unavailable'; }
      catch { current = null; currentState = 'unavailable'; }
      refresh();
    },
    render(value) { run = value; loadingId = null; failed = false; refresh(); },
    loading(id) { run = null; loadingId = id; failed = false; refresh(); },
    failed(id) { run = null; loadingId = id; failed = true; refresh(); },
    refresh,
  };
}
