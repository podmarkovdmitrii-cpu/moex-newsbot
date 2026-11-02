// =====================================================================================================
// Агент качества информации: каждая новость перед публикацией оценивается по четырём требованиям.
//   • Адекватность — текст соразмерен фактам: нет громких слов без цифр, неправдоподобных чисел, «капса», кликбейта,
//     сводок и рекламы; заголовок-вопрос без фактов не годится.
//   • Оперативность — свежая (до 2 ч с первой публикации), событие не датировано прошлым (старые даты в тексте),
//     не повтор уже опубликованной новости (сравнение с постами за 3 дня).
//   • Достоверность — есть атрибуция (кто сообщил/раскрыл), официальный источник или два независимых; признаки слуха
//     («источники», «может», «рассматривает») — только с пометкой «слух»; у отчётности и добычи есть цифры.
//   • Надёжность — репутация источника: тип (ведомство, компания, агентство, СМИ, лента, автор), история подтверждений его
//     слухов (reliability.json) и доля его новостей, забракованных проверками в последние 30 дней.
// Итог: ok / причины; строка для поста. Журнал — state/audit_log.jsonl (agent: «качество»).
// =====================================================================================================
'use strict';
const fs = require('fs');
const path = require('path');

const RELIAB = (() => { try { return JSON.parse(fs.readFileSync(path.join(__dirname, 'reliability.json'), 'utf8')); } catch (e) { return {}; } })();
const KIND_SCORE = { 'офиц': 1.0, 'политик': 0.95, 'компания': 0.95, 'агентство': 0.9, 'СМИ': 0.8, 'лента': 0.6, 'автор': 0.3 };
const MONTHS = ['январ', 'феврал', 'март', 'апрел', 'ма[яй]', 'июн', 'июл', 'август', 'сентябр', 'октябр', 'ноябр', 'декабр'];
const norm = t => String(t || '').toLowerCase().replace(/ё/g, 'е').replace(/[  ]/g, ' ').replace(/\s+/g, ' ').trim();

const HYPE = /(обвал|крах|катастроф|взлет|взрывн|шок|сенсац|паник|бомба|разгром|ракетой|в космос|улетел)/;
const RUMOR = /(источник(и|ов)? (сообщ|говор|рассказ|знаком)|по данным источник|по информации источник|неофициальн|слух|инсайд|может (быть|стать|провести|выплатить|продать|купить)|рассматрива|не исключ|обсужда|планирует рассмотреть|якобы|по неподтвержденн)/;
const ATTR = /(сообщ|заяв|раскры|опубликов|отчита|объяв|утверд|решил|рекомендова|по данным|пресс-служб|e-disclosure|центр раскрытия|интерфакс|тасс|риа|прайм|ведомост|коммерсант|рбк|говорится|согласно|приказ|постановлен|указ|протокол)/;
const AD = /(промокод|розыгрыш|подписывайтесь|подпишись|реклама|erid|скидк|переходите по ссылке|наш канал)/;
const DIGEST = /^(главное (за|к) |дайджест|итоги дня|утренний обзор|что происходит|новости к этому часу|коротко о главном)/;

function staleDate(text, now = new Date()) {
  const t = norm(text);
  let worst = 0;
  const y = now.getUTCFullYear();
  for (let m = 0; m < 12; m++) {
    for (const x of t.matchAll(new RegExp('(?<![\\d])(\\d{1,2}) ' + MONTHS[m] + '[а-я]*(?: (20\\d\\d))?', 'g'))) {
      const d = +x[1], yy = x[2] ? +x[2] : y;
      if (d < 1 || d > 31) continue;
      let dt = Date.UTC(yy, m, d);
      if (!x[2] && dt > now.getTime() + 2 * 864e5) dt = Date.UTC(y - 1, m, d); // «15 декабря» в январе — прошлый год
      const age = (now.getTime() - dt) / 864e5;
      if (age > worst) worst = age;
    }
  }
  // если в тексте есть и будущие даты (отсечка, собрание) — старая дата может быть датой отчётного периода: не штрафуем
  const future = /(состоится|пройдет|запланирован|до \d{1,2} |реестр|отсечк|дата закрытия|будет выплачен)/.test(t);
  return future ? 0 : worst;
}

function shingles(t) {
  const w = norm(t).replace(/[^a-zа-я0-9 ]/g, ' ').split(' ').filter(x => x.length > 2);
  const s = new Set();
  for (let i = 0; i + 2 < w.length; i++) s.add(w[i] + ' ' + w[i + 1] + ' ' + w[i + 2]);
  return s;
}
function jaccard(a, b) { let i = 0; for (const x of a) if (b.has(x)) i++; return a.size + b.size - i ? i / (a.size + b.size - i) : 0; }

// ctx: { text, ageMin, sources:[{ch, kind, trusted, official, rumor}], rumor (пост помечается как слух), ty, tk, S }
function assess(ctx) {
  const S = ctx.S || {};
  const Q = S.qa = S.qa || { recent: [], src: {} };
  const now = Date.now() / 1000;
  Q.recent = (Q.recent || []).filter(r => now - r.ts < 3 * 86400).slice(-300);
  const t = String(ctx.text || ''), l = norm(t);
  const crit = {};

  // --- адекватность
  {
    const why = [];
    const hasNum = /\d/.test(t);
    if (HYPE.test(l) && !hasNum) why.push('громкие слова без цифр');
    const caps = (t.match(/[A-ZА-ЯЁ]/g) || []).length / Math.max(1, (t.match(/[A-Za-zА-Яа-яЁё]/g) || []).length);
    if (caps > 0.35 && t.length > 60) why.push('текст капсом');
    const pcts = [...l.matchAll(/(\d+(?:[.,]\d+)?)\s?%/g)].map(m => parseFloat(m[1].replace(',', '.')));
    if (pcts.some(p => p > 1000)) why.push('неправдоподобный процент (>1000%)');
    if (/(в|на) (\d{3,}) раз/.test(l)) why.push('неправдоподобная кратность');
    if (AD.test(l)) why.push('реклама/призыв подписаться');
    if (DIGEST.test(l)) why.push('сводка, а не одна новость');
    if (/^[^.!]{0,140}\?/.test(t.trim()) && !hasNum) why.push('заголовок-вопрос без фактов');
    if (t.replace(/https?:\S+/g, '').trim().length < 60) why.push('слишком мало текста');
    crit['адекватность'] = { ok: !why.length, why };
  }
  // --- оперативность
  {
    const why = [];
    if (ctx.ageMin != null && ctx.ageMin > 120) why.push(`прошло ${Math.round(ctx.ageMin)} мин с первой публикации`);
    const st = staleDate(t);
    if (st > 3) why.push(`событие датировано ${Math.round(st)} дн. назад`);
    if (/^(ранее|напомним|как сообщалось|как мы писали)/.test(l)) why.push('пересказ ранее опубликованного');
    const sh = shingles(t);
    const dup = Q.recent.find(r => r.tk === ctx.tk && jaccard(sh, new Set(r.sh)) >= 0.5);
    if (dup) why.push('повтор новости, уже опубликованной ' + Math.round((now - dup.ts) / 3600) + ' ч назад');
    crit['оперативность'] = { ok: !why.length, why, age: ctx.ageMin != null ? Math.round(ctx.ageMin) : null };
  }
  // --- достоверность
  const srcs = ctx.sources || [];
  const facts = srcs.filter(s => !s.rumor);
  {
    const why = [];
    const strong = facts.some(s => s.official || s.trusted);
    const indep = new Set(facts.map(s => s.ch)).size;
    if (!ctx.rumor && !strong && indep < 2) why.push('нет официального/надёжного источника и второго подтверждения');
    if (!ctx.rumor && RUMOR.test(l) && !strong) why.push('в тексте признаки слуха — без пометки «слух» публиковать нельзя');
    if (!ATTR.test(l) && !strong && indep < 2 && RUMOR.test(l)) why.push('нет атрибуции: непонятно, кто сообщил');
    if (/^(results_|production_)/.test(ctx.ty || '') && !/\d/.test(t)) why.push('отчётность без цифр');
    crit['достоверность'] = { ok: !why.length, why, n: indep, strong };
  }
  // --- надёжность
  {
    const why = [];
    let best = 0, bestCh = '';
    for (const s of srcs) {
      let sc = KIND_SCORE[s.kind] != null ? KIND_SCORE[s.kind] : (s.official ? 1 : s.trusted ? 0.85 : 0.5);
      const r = RELIAB[s.ch];
      if (s.rumor && r && r.confirm != null) sc = Math.min(sc, 0.3 + r.confirm); // слухи этого канала подтверждались редко — ниже
      const h = Q.src[s.ch];
      if (h && h.n >= 10 && h.bad / h.n > 0.5) { sc -= 0.2; why.push(`источник ${s.ch}: ${Math.round(h.bad / h.n * 100)}% его новостей не прошли проверки за 30 дней`); }
      if (sc > best) { best = sc; bestCh = s.ch; }
    }
    const indep = new Set(facts.map(s => s.ch)).size;
    const need = ctx.rumor ? 0.35 : indep >= 2 ? 0.5 : 0.6;
    if (best < need) why.unshift(`надёжность источника ${Math.round(best * 100)}% ниже порога ${Math.round(need * 100)}%`);
    crit['надёжность'] = { ok: best >= need, why, score: +best.toFixed(2), ch: bestCh };
  }
  const ok = Object.values(crit).every(c => c.ok);
  return { ok, crit, fails: Object.entries(crit).filter(([, c]) => !c.ok).map(([k, c]) => `${k}: ${c.why.join('; ')}`) };
}

// после проверки: учесть результат у источников (для надёжности) и запомнить опубликованный текст (для поиска повторов)
function record(S, ctx, ok, published) {
  const Q = S.qa = S.qa || { recent: [], src: {} };
  const now = Date.now() / 1000;
  for (const s of ctx.sources || []) {
    const h = Q.src[s.ch] = Q.src[s.ch] && now - Q.src[s.ch].since < 30 * 86400 ? Q.src[s.ch] : { n: 0, bad: 0, since: now };
    h.n++; if (!ok) h.bad++;
  }
  if (published) Q.recent.push({ ts: now, tk: ctx.tk, sh: [...shingles(ctx.text)].slice(0, 400) });
  try {
    const dir = process.env.STATE_DIR || path.join(__dirname, 'state');
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(path.join(dir, 'audit_log.jsonl'), JSON.stringify({ ts: Math.round(now), agent: 'качество', tk: ctx.tk, ty: ctx.ty, ok, fails: ctx.fails || [] }) + '\n');
  } catch (e) { /* журнал не критичен */ }
}

function line(q) {
  const c = q.crit;
  const o = c['оперативность'], d = c['достоверность'], n = c['надёжность'];
  return `✅ <b>Проверка информации:</b> адекватность ✓ · оперативность ✓${o.age != null ? ` (${o.age} мин)` : ''} · достоверность ✓ (${d.strong ? 'надёжный источник' : d.n + ' независимых источника'}) · надёжность ✓ (${Math.round(n.score * 100)}%)`;
}

module.exports = { assess, record, line, staleDate };
