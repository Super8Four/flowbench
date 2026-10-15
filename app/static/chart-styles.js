// Display-only styles. Directions have already been normalized to client perspective.
export const sources = ['local', 'remote'];
export const directions = ['upload', 'download'];
const colors = {local: {upload: '#42d392', download: '#ffd43b'}, remote: {upload: '#a3e6bd', download: '#cfae36'}};
export function seriesStyle(source = 'local', direction = 'upload', color = null) {
  const dash = source === 'remote' ? direction === 'download' ? '8 3 2 3' : '2 4' : direction === 'download' ? '7 5' : '';
  const label = `${source === 'remote' ? 'Remote server report' : 'Local report'} · ${direction === 'upload' ? 'Upload (client to server)' : direction === 'download' ? 'Download (server to client)' : direction === 'bidir' ? 'Bidirectional aggregate' : 'Other direction'}`;
  return {color: color || colors[source]?.[direction] || '#b79cff', dash, label, pattern: dash === '' ? 'solid' : dash === '7 5' ? 'dashed' : dash === '2 4' ? 'dotted' : 'dash-dot'};
}
export function contrast(color, background = '#212629') {
  const luminance = (hex) => [1,3,5].map((n) => parseInt(hex.slice(n,n+2),16)/255).map((v) => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4).reduce((sum,v,i) => sum+v*[.2126,.7152,.0722][i],0);
  const a = luminance(color), b = luminance(background);
  return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
}
const rgb = (color) => [1,3,5].map((n) => parseInt(color.slice(n,n+2),16));
// Greedy separation of bright RGB candidates. Extend without cycling a short palette.
const candidates = [];
for (let r=64;r<=244;r+=30) for (let g=64;g<=244;g+=30) for (let b=64;b<=244;b+=30) {
  const color = '#' + [r,g,b].map((v) => v.toString(16).padStart(2,'0')).join('');
  if (contrast(color) >= 4.5) candidates.push({color, channels: [r,g,b], distance: Infinity});
}
const generated = ['#6db5ff'];
const used = new Set(generated);
let cursor = 0;
export function comparisonColors(count) {
  while (generated.length < count) {
    if (generated.length < 128) {
      const last = rgb(generated.at(-1));
      let best = null;
      for (const candidate of candidates) {
        candidate.distance = Math.min(candidate.distance, candidate.channels.reduce((sum,v,i) => sum+(v-last[i])**2*[.3,.59,.11][i],0));
        if (!used.has(candidate.color) && (!best || candidate.distance > best.distance)) best = candidate;
      }
      if (best) { generated.push(best.color); used.add(best.color); continue; }
    }
    // A bijective 24-bit permutation avoids repeated RGB values, including large selections.
    const color = '#' + ((Math.imul(++cursor, 2654435761) >>> 0) % 16777216).toString(16).padStart(6,'0');
    if (!used.has(color) && contrast(color) >= 4.5) { generated.push(color); used.add(color); }
  }
  return generated.slice(0,count);
}
export function comparisonStyles(runs) {
  const ids = [...new Set(runs.map((run) => run.id))].sort();
  const palette = comparisonColors(ids.length * 4), result = new Map();
  ids.forEach((id,index) => sources.forEach((source,s) => directions.forEach((direction,d) => result.set(`${id}/${source}/${direction}`,seriesStyle(source,direction,palette[index*4+s*2+d])))));
  return result;
}
export function seriesKey(style) {
  return `<span class="series-key"><svg width="38" height="14" viewBox="0 0 38 14" aria-hidden="true"><path d="M1 7H37" stroke="${style.color}" stroke-width="3" ${style.dash ? `stroke-dasharray="${style.dash}"` : ''}/></svg><span>${style.label} · ${style.pattern}</span></span>`;
}
