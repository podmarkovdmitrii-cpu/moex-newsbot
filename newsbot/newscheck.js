// =====================================================================================================
// Автопроверка новости перед публикацией: тикер и контекст, оперативность, достоверность, актуальность, конкретность,
// согласованность события и сигнала, плюс проверка готового текста поста на технические ошибки.
// Если хоть одна обязательная проверка не пройдена — новость в паблик не идёт (причина пишется в лог).
// =====================================================================================================
'use strict';
const fs = require('fs');
const path = require('path');
const N = require('./nlp.js');

const low = t => ' ' + String(t || '').toLowerCase().replace(/ё/g, 'е').replace(/ /g, ' ').replace(/\s+/g, ' ') + ' ';

// ---------- 1. Актуальный список акций Мосбиржи (TQBR) ----------
// Раз в день: все акции основного режима, их краткие названия и признак «торгуется сегодня».
// Бумаги, которых нет в списке (старые тикеры, делистинг), в посты не попадают; переименованные — переводятся (N.RENAME).
let TQ = null;
async function refreshTickers(stateDir) {
  const f = path.join(stateDir || __dirname, 'tqbr.json');
  try {
    const c = JSON.parse(fs.readFileSync(f, 'utf8'));
    if (Date.now() - c.ts < 20 * 3600e3 && c.list && Object.keys(c.list).length > 100) { TQ = c; addAutoAliases(); return TQ; }
  } catch (e) { /* нет кеша */ }
  try {
    const u = 'https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json?iss.meta=off&iss.only=securities,marketdata' +
      '&securities.columns=SECID,SHORTNAME,SECNAME,INSTRID,STATUS&marketdata.columns=SECID,VALTODAY,UPDATETIME';
    const r = await fetch(u, { signal: AbortSignal.timeout(20000) });
    const js = await r.json();
    const list = {};
    for (const [id, sn, name, instr, status] of js.securities.data) if ((instr || 'EQIN') === 'EQIN') list[id] = { sn, name, active: status !== 'N' };
    if (Object.keys(list).length > 100) { TQ = { ts: Date.now(), list }; fs.mkdirSync(path.dirname(f), { recursive: true }); fs.writeFileSync(f, JSON.stringify(TQ)); }
  } catch (e) { console.log('[проверка] список акций Мосбиржи не обновлён:', e.message); }
  addAutoAliases();
  return TQ;
}
// Для акций, которых нет в словаре псевдонимов, — название с Мосбиржи, но только в «сильной» форме:
// в кавычках или после ПАО/компания/группа (чтобы обычные слова не превращались в тикеры)
function addAutoAliases() {
  if (!TQ) return;
  for (const [id, x] of Object.entries(TQ.list)) {
    if (N.ALIASES[id] || /P$/.test(id) && N.ALIASES[id.slice(0, -1)]) continue;
    const nm = String(x.name || x.sn || '').replace(/^(пао|ао|мкпао|оао)\s+/i, '').replace(/["«»()]/g, '').replace(/\s+(ао|ап|-ао|-ап|ап\.?)$/i, '').trim().toLowerCase();
    if (nm.length < 5 || /\d{3}|etf|пиф/.test(nm)) continue;
    N.ALIASES[id] = [`«${nm}»`, `пао ${nm}`, `компания ${nm}`, `группа ${nm}`];
  }
}
function known(tk) { return !TQ || !!TQ.list[tk]; }
function active(tk) { return !TQ || !TQ.list[tk] || TQ.list[tk].active; }

// ---------- 2. Тикер в контексте новости ----------
// Псевдонимы-обычные слова: требуем явный признак компании рядом (кавычки, ПАО, ГК, «акции», $TICKER)
const AMBIG = new Set(['SMLT', 'MGNT', 'PLZL', 'PIKK', 'LENT', 'POSI', 'ASTR', 'ETLN', 'ELMT', 'BAZA', 'SVET', 'BELU', 'OKEY', 'UNAC', 'GTRK', 'SFIN']);
const MARK = /(«|"|пао|гк|группа|групп[аы]|компани|девелопер|ретейлер|ритейлер|сеть|холдинг|мкпао|акци|бумаг|эмитент|\$[a-z]{1,6})/;
const SENT = t => String(t).replace(/\s+/g, ' ').split(/(?<=[.!?…])\s+(?=[«"A-ZА-ЯЁ0-9$])/u).filter(Boolean);
function mentions(tk, s) {
  const l = low(s), out = [];
  for (const m of String(s).matchAll(new RegExp('\\$' + tk + '\\b', 'g'))) out.push({ i: m.index, strong: true });
  for (const a of N.ALIASES[tk] || []) {
    let i = l.indexOf(a);
    while (i >= 0) { const ctx = l.slice(Math.max(0, i - 25), i + a.length + 25); out.push({ i, strong: /[«"]/.test(a) || MARK.test(ctx) || !AMBIG.has(tk) }); i = l.indexOf(a, i + a.length); }
  }
  return out;
}
// tk действительно субъект новости о событии ty: упомянут «сильно» и в том же предложении, где описано событие
// (или в первом предложении — тогда это и есть тема новости). Иначе: «США и Израиль готовят удар по Ирану» ≠ $SMLT.
function tickerInContext(tk, text, ty) {
  if (!tk || tk === 'РЫНОК') return { ok: true };
  if (!known(tk)) return { ok: false, why: `$${tk} нет среди акций Мосбиржи (устаревший тикер)` };
  const ss = SENT(text);
  if (!ss.length) return { ok: false, why: 'пустой текст' };
  const strongIn = s => mentions(tk, s).some(m => m.strong);
  if (!ss.some(strongIn)) return { ok: false, why: `$${tk}: название встречается только как обычное слово` };
  if (!ty || ty === 'tone') return { ok: strongIn(ss[0]) || ss.slice(0, 2).some(strongIn), why: `$${tk} не в начале новости` };
  const rx = (N.TYPES.find(t => t[0] === ty) || [])[1];
  if (!rx) return { ok: true };
  const evIdx = ss.map((s, i) => rx.test(low(s)) ? i : -1).filter(i => i >= 0);
  if (!evIdx.length) return { ok: strongIn(ss[0]), why: `событие не найдено в тексте` };
  // событие и компания в одном предложении, или компания — тема первого предложения, а событие в следующем
  const ok = evIdx.some(i => strongIn(ss[i]) || (i <= 2 && strongIn(ss[0])));
  const macro = /^(geo_|oil_|cbr_|ruble_|sanctions|tax)/.test(ty);
  // макрособытие (удары, нефть, санкции…) касается компании, если она в том же предложении или сразу следующим (следствие: «…потеря мощностей „Яндекса“»)
  if (macro) return evIdx.some(i => strongIn(ss[i]) || (ss[i + 1] && strongIn(ss[i + 1]) && !ss.slice(0, i + 1).some(strongIn))) ? { ok: true } : { ok: false, why: `событие «${ty}» не связано с $${tk} в тексте` };
  return ok ? { ok: true } : { ok: false, why: `событие описано не про $${tk}` };
}

// ---------- 3. Требования к информации ----------
const EDU = /^(что такое|почему|как (выбрать|инвестировать|заработать|все упало|устроен)|стоит ли|разбираемся|рассказываем|объясняем|гайд|подборк|топ-?\d|\d+ (акций|бумаг|причин)|вебинар|курс |промокод|розыгрыш|конкурс|опрос|мнение|колонка|интервью)/i;
const AD = /(промокод|розыгрыш|приз[ыа]? |подписывайтесь|реклама|erid|скидк)/i;
const REPOST = /^(ранее (сообщал|писал)|напомним|как сообщалось|как мы писали)/i;
const NUM = /\d/;
// ctx: { text, tk, ty, ageMin, sources:[{ch, trusted, official, rumor}], rumor, isDigest, ruRelevant, primary }
function checkNews(ctx) {
  const fails = [], notes = [];
  const t = String(ctx.text || '').trim();
  // оперативность: свежая (до 2 ч от первой публикации), не пересказ старого
  if (ctx.ageMin != null && ctx.ageMin > 120) fails.push(`не оперативно: ${Math.round(ctx.ageMin)} мин с первой публикации`);
  if (REPOST.test(t)) fails.push('не оперативно: пересказ ранее опубликованного');
  // достоверность: официальный/надёжный источник или два независимых; слух — только в рубрике слухов
  const srcs = ctx.sources || [];
  const facts = srcs.filter(s => !s.rumor);
  if (!ctx.rumor && !(facts.some(s => s.trusted || s.official) || facts.length >= 2)) fails.push('не достоверно: нет надёжного источника и второго подтверждения');
  // актуальность и относимость: российский рынок, конкретное событие, не обзор/обучение/реклама
  if (ctx.isDigest) fails.push('сводка нескольких новостей, а не одно событие');
  if (EDU.test(t) || AD.test(t)) fails.push('аналитическая/обучающая/рекламная заметка, а не новость');
  if (ctx.tk === 'РЫНОК' && ctx.ruRelevant === false) fails.push('нет связи с российским рынком');
  if (!ctx.ty || ctx.ty === 'tone') fails.push('нет конкретного события');
  if (ctx.primary && ctx.ty && ctx.primary !== ctx.ty) fails.push(`главное событие новости — ${ctx.primary}, а не ${ctx.ty}`);
  // конкретность: у отчётности, добычи, дивидендов, выкупа должны быть цифры
  if (/^(results_|production_|div_rec|buyback|guidance_)/.test(ctx.ty || '') && !NUM.test(t)) fails.push('нет цифр — событие не подтверждено данными');
  // тикер в контексте
  if (ctx.tk && ctx.tk !== 'РЫНОК') {
    const k = tickerInContext(ctx.tk, t, ctx.ty);
    if (!k.ok) fails.push('тикер: ' + k.why);
  }
  for (const x of ctx.also || []) { const k = tickerInContext(x, t, ctx.ty); if (!k.ok) notes.push({ drop: x, why: k.why }); }
  return { ok: !fails.length, fails, notes };
}

// ---------- 4. Согласованность сигнала с событием ----------
// Позитивное событие (рост прибыли, дивиденды, выкуп, повышение оценки…) — никогда не шорт; негативное — никогда не лонг.
// «Продажа на факте» на истории бывает, но читатель видит противоречие, а точность таких сигналов низкая.
function signalConsistent(ty, side) {
  const d = (N.TYPES.find(t => t[0] === ty) || [])[2];
  if (!side || !d) return { ok: true };
  if (Math.sign(side) !== Math.sign(d)) return { ok: false, why: `событие ${d > 0 ? 'позитивное' : 'негативное'}, а ${side > 0 ? 'лонг' : 'шорт'} шёл бы против него` };
  return { ok: true };
}

// ---------- 5. Проверка готового поста ----------
function lintPost(html, { tickers = [] } = {}) {
  const issues = [];
  const h = String(html || '');
  if (/\bundefined\b|\bNaN\b|\[object |>null<|\bnull ₽/.test(h.replace(/<a [^>]*>/g, ''))) issues.push('в тексте undefined/NaN/null');
  if (/[−-]0,0%|\+0,0+%/.test(h)) issues.push('нулевые проценты со знаком');
  const stack = [];
  for (const m of h.matchAll(/<(\/?)(b|i|u|s|code|pre|a|blockquote)\b[^>]*>/g)) {
    if (!m[1]) stack.push(m[2]); else if (stack.pop() !== m[2]) { issues.push('незакрытый или перепутанный тег <' + m[2] + '>'); break; }
  }
  if (stack.length) issues.push('незакрытые теги: ' + stack.join(','));
  for (const m of h.matchAll(/\$([A-Z]{1,6})\b/g)) if (TQ && !TQ.list[m[1]] && m[1] !== 'IMOEX') issues.push(`$${m[1]} — нет такой акции на Мосбирже`);
  if (!/<a href="https?:\/\//.test(h)) issues.push('нет ссылки на источник');
  const plain = h.replace(/<[^>]+>/g, '');
  if (plain.length < 60) issues.push('слишком короткий текст');
  for (const t of tickers) if (TQ && TQ.list[t] && !TQ.list[t].active) issues.push(`$${t}: торги приостановлены`);
  return [...new Set(issues)];
}

// ---------- 6. Краткое содержание вместо полной новости ----------
// 1–2 предложения: тема (первое предложение) и, если событие описано дальше, — предложение с событием. До ~320 знаков.
function summary(text, ty, lim = 320) {
  const ss = SENT(String(text || '').replace(/\s+/g, ' ').trim());
  if (!ss.length) return '';
  const rx = ty && (N.TYPES.find(t => t[0] === ty) || [])[1];
  let pick = [ss[0]];
  if (rx && !rx.test(low(ss[0]))) { const k = ss.findIndex(s => rx.test(low(s))); if (k > 0) pick.push(ss[k]); }
  let out = pick.join(' ');
  if (out.length > lim) {
    out = pick[0];
    if (out.length > lim) { const cut = out.lastIndexOf(', ', lim - 1); out = out.slice(0, cut > lim * 0.5 ? cut : out.lastIndexOf(' ', lim - 1)).replace(/[,;:—–-]\s*$/, '') + '…'; }
  }
  return out;
}

module.exports = { refreshTickers, tickerInContext, checkNews, signalConsistent, lintPost, summary, known, active, SENT, _tq: () => TQ };
