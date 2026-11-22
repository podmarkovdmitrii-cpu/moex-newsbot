// Проверка №35: стратегия v3 (newsbot/strategy.json, движок newsbot/regime.js) на дневной истории — дневная кривая капитала.
// node v3run.js in.json out.json  ; in = {days, sec:{t:[[d,o,h,l,c,value]]}, imoex:[[...]]}; дивиденды — newsbot/divhist.json (как в bot.js)
'use strict';
const fs = require('fs');
const path = require('path');
const RG = require(path.join(__dirname, '..', 'regime.js'));
const STRAT = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'strategy.json'), 'utf8'));
const DIVS = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'divhist.json'), 'utf8'));
const [inp, out, start, capArg] = process.argv.slice(2);
const H = JSON.parse(fs.readFileSync(inp, 'utf8'));

function divAdjust(t, rows) { // как bot.js
  const ev = [], idx = new Map(rows.map((r, i) => [r[0], i]));
  for (const [d, v] of DIVS[t] || []) {
    let i = idx.get(d);
    if (i === undefined) { i = rows.findIndex(r => r[0] >= d); if (i < 0) continue; }
    if (i > 0 && v / rows[i - 1][4] > 0 && v / rows[i - 1][4] < 0.25) ev.push([i, v]);
  }
  ev.sort((a, b) => a[0] - b[0]);
  const f = new Array(rows.length).fill(1); let k = 1, e = ev.length - 1;
  for (let i = rows.length - 1; i >= 0; i--) { f[i] = k; while (e >= 0 && ev[e][0] === i) { k *= 1 - ev[e][1] / rows[i - 1][4]; e--; } }
  return { f, div: ev.map(([i, v]) => [rows[i][0], v * f[i - 1]]) };
}

const sec = {}, div = {};
for (const [t, rows] of Object.entries(H.sec)) {
  if (rows.length < 60) continue;
  const A = divAdjust(t, rows);
  sec[t] = rows.map((r, i) => [r[0], r[1] * A.f[i], r[2] * A.f[i], r[3] * A.f[i], r[4] * A.f[i], r[5]]);
  div[t] = A.div;
}
const D = RG.prepare({ days: H.days, sec, imoex: H.imoex, div, divTaxOnly: 1 });
const from = Math.max(0, H.days.findIndex(d => d >= (start || '2009-01-01')));
const P = Object.assign({}, STRAT, { capital: +(capArg || 1000000) });
const r = RG.run(D, P, { from, to: D.N - 1 });
const m = RG.metrics(r, from, D.N - 1, P.capital);
const per = {};
for (const [a, b] of [['2009-01-01', '2015-12-31'], ['2016-01-01', '2020-12-31'], ['2021-01-01', '2026-12-31'], ['2011-01-01', '2026-12-31'], ['2020-01-01', '2026-12-31']]) {
  const ia = H.days.findIndex(d => d >= a); let ib = H.days.findIndex(d => d > b); ib = ib < 0 ? D.N - 1 : ib - 1;
  const mm = ia >= 0 ? RG.metrics(r, Math.max(ia, from), ib, P.capital) : null;
  if (mm) per[a.slice(0, 4) + '–' + b.slice(0, 4)] = { cagr: mm.cagr, mdd: mm.mdd, pf: mm.pf, n: mm.n, win: mm.win, expo: mm.expo };
}
fs.writeFileSync(out, JSON.stringify({
  name: STRAT.name, metrics: m, per,
  daily: r.daily.map(x => [H.days[x.i], +x.equity.toFixed(2), +x.expo.toFixed(4)]),
  trades: r.trades.length,
}));
console.log('v3:', STRAT.name, 'сделок', r.trades.length, 'годовых', (m.cagr * 100).toFixed(1) + '%', 'просадка', (m.mdd * 100).toFixed(1) + '%', 'PF', m.pf.toFixed(2));
