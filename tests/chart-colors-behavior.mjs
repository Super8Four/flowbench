import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import vm from 'node:vm';
const root = process.argv[2] || new URL('../app/static/', import.meta.url).pathname.replace(/^\/(\w:)/, '$1');
const styles = await import(pathToFileURL(join(root, 'chart-styles.js')).href);
const units = await import(pathToFileURL(join(root, 'units.js')).href);
if (process.argv.includes('--palette')) { console.log(JSON.stringify(styles.comparisonColors(800))); process.exit(0); }
const runs = Array.from({length:40}, (_,i) => ({id: i.toString(16).padStart(32,'0'), settings:{color:'#123456'}, series:[
  {start:0,seconds:1,mbps:100,direction:'upload'}, {start:3,seconds:4,mbps:120,direction:'upload'},
  {start:0,seconds:1,mbps:80,direction:'download'}], peer:{series:[{seconds:1,mbps:77,direction:'download'}]}}));
const forward = styles.comparisonStyles(runs), reversed = styles.comparisonStyles([...runs].reverse());
assert.equal(new Set([...forward.values()].map((s) => s.color)).size,160);
assert.deepEqual([...forward], [...reversed]);
assert.ok(styles.comparisonColors(800).every((c) => styles.contrast(c) >= 4.5));
const fixed = ['local','remote'].flatMap((s) => ['upload','download'].map((d) => styles.seriesStyle(s,d)));
assert.equal(new Set(fixed.map((s) => s.color)).size,4);
assert.equal(new Set(fixed.map((s) => s.dash)).size,4);
class Element {
  constructor() { this.value=''; this.innerHTML=''; this.textContent=''; this.attributes={}; this.children=[]; }
  setAttribute(k,v) { this.attributes[k]=v; }
  getBoundingClientRect() { return {width:760}; }
  replaceChildren() { this.children=[]; }
  append(item) { this.children.push(item); }
}
const fields = new Map(['throughput-chart','throughput-unit','throughput-legend','chart-perspective','comparison-chart','comparison-unit','comparison-visible-count','comparison-legend','comparison-perspective'].map((id) => [id,new Element()]));
const context = vm.createContext({...styles,...units, document:{createElement:() => new Element(),createDocumentFragment:() => new Element()},
  $:(id) => fields.get(id), number:(v) => String(v), escape:(v) => String(v), title:(run) => run.id,
  selected:runs[0], comparisonData:{runs}, hiddenComparisonRuns:new Set(), knownComparisonRuns:new Set(),
  comparisonPlotVersion:0,comparisonRenderVersion:0,requestAnimationFrame:(fn) => fn()});
const app = readFileSync(join(root,'app.js'),'utf8');
vm.runInContext(app.slice(app.indexOf('function smoothPath('),app.indexOf('function renderPeer(')) + '\n' + app.slice(app.indexOf('async function overlay('),app.indexOf('function renderComparison()')),context);
fields.get('chart-perspective').value='compare';
vm.runInContext('chart(selected.series, selected.peer.series)',context);
let svg=fields.get('throughput-chart').innerHTML, legend=fields.get('throughput-legend').innerHTML;
for (const s of [fixed[0],fixed[1],fixed[3]]) { assert.ok(svg.includes(s.color)); assert.ok(svg.includes(s.label)); assert.ok(legend.includes(s.color)); assert.ok(legend.includes(s.label)); }
assert.ok(!legend.includes('Remote server report · Upload'));
assert.equal(fields.get('throughput-unit').textContent,'Mbps');
fields.get('comparison-perspective').value='local';
await vm.runInContext('overlay()',context);
svg=fields.get('comparison-chart').innerHTML;
assert.ok(svg.includes(forward.get(`${runs[0].id}/local/upload`).color));
assert.ok(svg.includes('Local report · Upload (client to server)'), svg.slice(-1800));
assert.ok(svg.includes('1 LU'));
assert.ok(svg.includes('M 229,67.9090909090909 M 745,41.09090909090909'), 'Missing [1,3] span must begin a new subpath at second 4.');
vm.runInContext(`hiddenComparisonRuns.add('${runs[1].id}')`,context);
await vm.runInContext('overlay(false)',context);
assert.ok(fields.get('comparison-chart').innerHTML.includes(forward.get(`${runs[0].id}/local/upload`).color));
fields.get('comparison-perspective').value='remote';
await vm.runInContext('overlay()',context);
assert.ok(fields.get('comparison-chart').innerHTML.includes('stroke-dasharray="8 3 2 3"'));
assert.ok(fields.get('comparison-chart').innerHTML.includes('Remote server report · Download (server to client)'));
fields.get('chart-perspective').value='remote';
vm.runInContext('chart([], [])',context);
assert.equal(fields.get('throughput-legend').innerHTML,'');
assert.ok(fields.get('throughput-chart').innerHTML.includes('No remote interval measurements'));
console.log('Chart colors behavior passed: actual renderers, source/direction legends, gaps, units, missing data, 40 runs and stable visibility.');
