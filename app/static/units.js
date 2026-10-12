// Display-only decimal SI units. Saved numeric evidence keeps its original units.
export const rateUnits = [['bps',1],['kbps',1e3],['Mbps',1e6],['Gbps',1e9],['Tbps',1e12]];
export const palette = ['#6db5ff','#ffc857','#59c9a5','#ef7f98','#b79cff','#ffa76b','#66d1df','#c4d77b'];
export const finite = (value) => typeof value === 'number' && Number.isFinite(value);
export function rateScale(values) {
  let maximum = 0;
  for (const value of values) if (finite(value)) maximum = Math.max(maximum, Math.abs(value));
  let unit = 'bps', divisor = 1;
  for (const [name, threshold] of rateUnits.slice(1)) {
    if (maximum < threshold) break;
    unit = name; divisor = threshold;
  }
  return {unit, divisor};
}
export function formatRate(bps, digits = 2, scale = null) {
  if (!finite(bps)) return '—';
  if (!Number.isInteger(digits) || digits < 0 || digits > 6) throw new Error('Rate precision must be from 0 to 6 decimal places.');
  scale ||= rateScale([bps]);
  const value = bps / scale.divisor;
  const text = value && (Math.abs(value) < 10 ** -digits || Math.abs(value) >= 1e12) ? value.toExponential(digits) : value.toLocaleString(undefined,{maximumFractionDigits:digits});
  return `${text} ${scale.unit}`;
}
export const formatMbps = (value, digits = 2, scale = null) => finite(value) ? formatRate(value * 1e6, digits, scale) : '—';
export function runColor(run) {
  const color = run.settings?.color || run.color;
  if (typeof color === 'string' && /^#[a-f0-9]{6}$/i.test(color)) return color.toLowerCase();
  const source = run.imported?.source_id;
  const id = typeof source === 'string' && /^[a-f0-9]{32}$/.test(source) ? source : run.id;
  return typeof id === 'string' && /^[a-f0-9]{32}$/.test(id) ? palette[parseInt(id.slice(0,8),16)%palette.length] : palette[0];
}
