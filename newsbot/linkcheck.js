// =====================================================================================================
// Ревизор связей «новость → бумага».
// Название компании часто совпадает с обычным словом: «самолёт» (SMLT) в новости о крушении или сбитом БПЛА,
// «Иркутск» (IRKT), КамАЗ в сводке ДТП, «магнитная буря» (MGNT), «Северный полюс» (PLZL), «час пик» (PIKK)…
// Такая связь портит и посты (не та бумага), и модель аномалий (ложные «новости о компании»).
// Правила — newsbot/link_rules.json (те же читает anomaly/linkcheck.py для истории новостей перед обучением).
// Связь снимается, если в тексте есть признак обычного значения слова (veto) и нет явного признака компании (strong).
// Каждое снятие пишется в журнал (STATE_DIR/linkcheck_log.jsonl) — по нему видно, какие правила срабатывают.
// =====================================================================================================
'use strict';
const fs = require('fs');
const path = require('path');

const RULES = JSON.parse(fs.readFileSync(path.join(__dirname, 'link_rules.json'), 'utf8'));
// \b в JS не понимает кириллицу — заменяем на границу слова по Unicode
const WB = '(?:(?<![\\p{L}\\p{N}_])(?=[\\p{L}\\p{N}_])|(?<=[\\p{L}\\p{N}_])(?![\\p{L}\\p{N}_]))';
const RX = s => new RegExp(String(s).replace(/\\b/g, WB), 'u');
const GENERIC = RX(RULES.generic_strong);
const AMB = {};
for (const [tk, r] of Object.entries(RULES.ambiguous)) AMB[tk] = { veto: RX(r.veto), strong: RX(r.strong), meaning: r.meaning };
const low = t => ' ' + String(t || '').toLowerCase().replace(/ё/g, 'е').replace(/\s+/g, ' ') + ' ';

let STATS = { day: '', checked: 0, dropped: 0, by: {} };

// Проверка одной связи: { ok, why }
function check(tk, text) {
  const r = AMB[tk];
  if (!r) return { ok: true };
  const l = low(text);
  const v = l.match(r.veto);
  if (!v) return { ok: true };
  if (r.strong.test(l) || GENERIC.test(l) || new RegExp('\\$' + tk + '\\b').test(String(text))) return { ok: true, note: `есть «${v[0].trim()}», но есть и признак компании` };
  return { ok: false, why: `$${tk}: «${v[0].trim()}» — ${r.meaning}, признаков компании нет` };
}

// Отфильтровать список тикеров новости; снятые связи — в журнал
function filter(tks, text, meta = {}) {
  const keep = [], drop = [];
  for (const tk of tks || []) {
    const c = check(tk, text);
    (c.ok ? keep : drop).push(c.ok ? tk : { tk, why: c.why });
  }
  const day = new Date().toISOString().slice(0, 10);
  if (STATS.day !== day) STATS = { day, checked: 0, dropped: 0, by: {} };
  STATS.checked += (tks || []).length;
  STATS.dropped += drop.length;
  for (const d of drop) STATS.by[d.tk] = (STATS.by[d.tk] || 0) + 1;
  if (drop.length && meta.stateDir) {
    try {
      fs.appendFileSync(path.join(meta.stateDir, 'linkcheck_log.jsonl'), JSON.stringify({ ts: Math.round(Date.now() / 1000), ch: meta.ch || '', drop, tx: String(text).slice(0, 240) }) + '\n');
    } catch (e) { /* журнал не критичен */ }
  }
  return { keep, drop };
}

function stats() { return STATS; }

module.exports = { check, filter, stats, RULES };
