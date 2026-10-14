// Display preferences never alter saved UTC instants or clock provenance.
let mode = 'local';
try { mode = localStorage.getItem('flowbench-timezone') === 'utc' ? 'utc' : 'local'; } catch { /* Storage is optional. */ }
export function browserTimeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
}
export function displayTimestamp(value, requestedMode = mode, localZone = browserTimeZone()) {
  if (typeof value !== 'string' || !/T.*(?:Z|[+-]\d\d:\d\d)$/i.test(value)) return value ? 'Timezone unavailable' : 'Unavailable';
  const instant = new Date(value);
  if (!Number.isFinite(instant.getTime())) return 'Unavailable';
  let zone = requestedMode === 'utc' ? 'UTC' : localZone;
  let formatter;
  const options = {year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZoneName: 'longOffset'};
  try { formatter = new Intl.DateTimeFormat(undefined, {...options, timeZone: zone}); }
  catch { zone = 'UTC'; formatter = new Intl.DateTimeFormat(undefined, {...options, timeZone: zone}); }
  const text = formatter.formatToParts(instant).map((part) => part.type === 'timeZoneName'
    ? /^(GMT|UTC)$/.test(part.value) ? 'UTC+00:00' : part.value.replace(/^GMT/, 'UTC') : part.value).join('');
  return `${text} [${zone}]`;
}
export function initTimestamps(onChange) {
  const select = document.getElementById('display-timezone'), label = document.getElementById('display-timezone-note');
  if (!select) return;
  select.value = mode;
  const update = () => { if (label) label.textContent = `Displayed timestamps: ${mode === 'utc' ? 'UTC' : browserTimeZone()}. Each instant includes its UTC offset. Clock evidence and the note editor stay explicitly UTC.`; };
  update();
  select.addEventListener('change', () => {
    mode = select.value === 'utc' ? 'utc' : 'local';
    try { localStorage.setItem('flowbench-timezone', mode); } catch { /* Storage is optional. */ }
    update(); onChange();
  });
}
