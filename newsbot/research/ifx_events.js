// Разметка заголовков архива Интерфакса (research/news_hist.py) словарём бота (nlp.js): о какой компании новость и какое событие.
// Выход: RESEARCH_HIST/ifx_events.json.gz — [тикер, время (unix, UTC), тип, тон, слух, официально] — тот же формат, что news_events.json.gz.
'use strict';
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');
const N = require('../nlp.js');

const OUT = process.env.RESEARCH_DIR || path.join(__dirname, 'out');
const HIST = process.env.RESEARCH_HIST || path.join(path.dirname(OUT), 'research-hist');
const DIR = path.join(HIST, 'ifx');
const files = fs.existsSync(DIR) ? fs.readdirSync(DIR).filter(f => f.endsWith('.json.gz')).sort() : [];
const ev = [];
let heads = 0, withTk = 0;
const byYear = {};
for (const f of files) {
  const js = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(DIR, f))).toString('utf8'));
  for (const [day, items] of Object.entries(js.days || {})) {
    for (const [hhmm, , title] of items) {
      heads++;
      const tks = N.subjectTickers(title, null, 3);
      if (!tks.length) continue;
      withTk++;
      const c = N.classify(title);
      const ty = c.types.length ? c.types[0][0] : 'tone';
      const ts = Math.round(Date.parse(`${day}T${hhmm}:00+03:00`) / 1000);
      for (const tk of tks) ev.push([tk, ts, ty, Math.sign(c.s), c.rumor ? 1 : 0, c.official ? 1 : 0]);
      const y = day.slice(0, 4);
      byYear[y] = (byYear[y] || 0) + tks.length;
    }
  }
}
ev.sort((a, b) => a[1] - b[1]);
fs.writeFileSync(path.join(HIST, 'ifx_events.json.gz'), zlib.gzipSync(JSON.stringify(ev)));
const tkCount = {};
for (const e of ev) tkCount[e[0]] = (tkCount[e[0]] || 0) + 1;
const top = Object.entries(tkCount).sort((a, b) => b[1] - a[1]).slice(0, 15).map(([t, n]) => `${t} ${n}`).join(', ');
console.log(new Date().toISOString().slice(11, 19), '[новости 2008+] заголовков', heads, '| о компаниях', withTk, '| событий', ev.length, '| бумаг', Object.keys(tkCount).length);
console.log(new Date().toISOString().slice(11, 19), '[новости 2008+] событий по годам:', Object.entries(byYear).sort().map(([y, n]) => `${y}: ${n}`).join(', '));
console.log(new Date().toISOString().slice(11, 19), '[новости 2008+] чаще всего:', top);
