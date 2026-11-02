// =====================================================================================================
// Агент-ревизор: независимая проверка готового новостного поста перед публикацией.
// Отдельный код (не повторяет newscheck.js) ищет ошибки вывода по бумаге:
//   1) тикер-омоним — название компании в тексте использовано как обычное слово («самолёт» в новости об авиаударе ≠ $SMLT,
//      «на Мосбирже» ≠ $MOEX, «Газпром нефть» ≠ $GAZP, «магнитная буря» ≠ $MGNT…);
//   2) полярность — что на самом деле написано о показателях (прибыль выросла / снизилась, убыток сократился…) против
//      типа события, цвета заголовка и направления сигнала (рост прибыли не может быть шортом и «красной» новостью);
//   3) уровни — цель сделки и «предположительный уровень» лежат в сторону сигнала / смысла события;
//   4) ИИ-ревизор (если подключён шлюз): второе мнение модели о тикере, событии и направлении — строгий JSON.
// Любая ошибка → пост не публикуется; решение и причина пишутся в state/audit_log.jsonl.
// =====================================================================================================
'use strict';
const fs = require('fs');
const path = require('path');
const N = require('./nlp.js');

const norm = t => ' ' + String(t || '').toLowerCase().replace(/ё/g, 'е').replace(/[  ]/g, ' ').replace(/\s+/g, ' ') + ' ';
const plain = h => String(h || '').replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"');
const SENT = t => norm(t).split(/(?<=[.!?…])\s+/u).filter(s => s.trim());

// ---------- 1. Омонимы: слово-название компании и его «обычные» значения ----------
// word — как слово встречается в тексте; company — признаки, что речь о компании; other — признаки обычного значения.
const HOMO = {
  SMLT: { word: /самол[её]т/, company: /(гк|групп[аы]|девелопер|застройщик|«самолет»|"самолет"|самолет девелопмент|smlt|продаж[аи] (жилья|квартир)|ипотек)/, other: /(военн|пассажирск|боев|истребител|бомбардир|авиа|аэропорт|рейс|сбит|беспилот|пилот|экипаж|летел|приземл|вылет|крушени|катастроф|ракет|ввс|су-\d|ил-\d|ту-\d|boeing|airbus|суперджет|мс-21|на самолете|самолетом|самолеты|самолетов)/ },
  MGNT: { word: /магнит/, company: /(ретейлер|ритейлер|сеть|«магнит»|"магнит"|пао магнит|mgnt|магазин)/, other: /(магнитн|магнитогорск|электромагнит|магнитол|магнит для)/ },
  PLZL: { word: /полюс/, company: /(золото|золотодобы|«полюс»|"полюс"|пао полюс|plzl|сухой лог|олимпиад)/, other: /(северн\w* полюс|южн\w* полюс|полюс холода|полюса (земли|планеты)|полюс(ы|ов) (силы|влияния|роста))/ },
  PIKK: { word: /(^|[^а-я])пик([^а-я]|$)/, company: /(девелопер|застройщик|«пик»|"пик"|гк пик|группа пик|pikk|пик сз)/, other: /(в пик|пик (продаж|спроса|нагрузк|сезона|инфляци|ставк|активност|потреблени)|на пике|пиков)/ },
  LENT: { word: /лент[аеуы]/, company: /(ретейлер|ритейлер|сеть|«лента»|"лента"|lent|гипермаркет)/, other: /(лент[аеуы] новост|новостн\w* лент|в ленте|лента\.ру|lenta\.ru|ленточн)/ },
  POSI: { word: /позитив/, company: /(positive|«позитив»|"позитив"|группа позитив|posi|кибербез)/, other: /(позитивн|позитив для|позитив на|позитив от|позитив рынк)/ },
  ASTR: { word: /астр/, company: /(astra|«астра»|"астра"|группа астра|astr|линукс|linux)/, other: /(астрахан|астроном|астролог|астрономи)/ },
  ETLN: { word: /эталон/, company: /(etalon|«эталон»|"эталон"|гк эталон|группа эталон|etln|девелопер)/, other: /(эталонн|эталон (качества|для|красоты))/ },
  ELMT: { word: /элемент/, company: /(«элемент»|"элемент"|гк элемент|группа элемент|elmt|микроэлектрон)/, other: /(элемент(ы|ов|ам)? |элементн|химическ)/ },
  BAZA: { word: /базис/, company: /(«базис»|"базис"|гк базис|группа базис|baza)/, other: /(базисн|базис (для|расчета))/ },
  MOEX: { word: /(мосбирж|московск\w* бирж)/, company: /(прибыль|выручк|дивиденд|отчет|комиссионн|доход\w* (мосбиржи|биржи)|акции (мосбиржи|московской биржи)|moex|пао «московская биржа»|набсовет|совет директоров (мосбиржи|биржи))/, other: /(на (мосбирже|московской бирже)|торг\w* на|индекс(а|у|ом)? (мосбиржи|московской биржи)|imoex|котировк\w* на|разместил\w*|листинг|делистинг|приостановил\w* торг|(начн|возобнов)\w* торг)/ },
  SPBE: { word: /спб бирж/, company: /(прибыль|выручк|дивиденд|отчет|spbe|акции спб биржи)/, other: /(на спб бирже|торг\w* на спб)/ },
  GAZP: { word: /газпром/, company: /(газпром(?! ?нефт|банк| энергохолдинг| межрегионгаз| экспорт| медиа| трансгаз| нефтехим)|gazp)/, other: /(газпром нефт|газпромбанк|газпром энергохолдинг|газпром межрегионгаз|газпром экспорт|газпром медиа|газпром трансгаз|газпром нефтехим|газпромнефть)/ },
  SBER: { word: /сбер/, company: /(сбербанк|сбер(а|у|ом)? |пао сбер|sber|греф)/, other: /(сберинвест|сбер ?инвестиц|сбер ?cib|сбериндекс|аналитик\w* сбер|сбер ?управлени|по оценке сбер|по данным сбер)/ },
  YDEX: { word: /яндекс/, company: /(мкпао яндекс|«яндекс»|ydex|яндекс (отчит|увелич|снизил|объяв|выплат|заработ|нарастил)|выручк\w* яндекс|прибыл\w* яндекс)/, other: /(яндекс[ .-]?(такси|маркет|музык|карт|погод|еда|лавк|дзен|новост|поиск|вордстат)|по данным яндекс|в яндекс)/ },
};

// признаки компании, общие для всех тёзок (финансовые слова рядом с названием)
const FIN = /(отчит|выручк|прибыл|дивиденд|облигац|акци[яиейю]|мсфо|рсбу|ebitda|капитализац|эмитент)/;
const STRICT = new Set(['MOEX', 'SPBE', 'SBER', 'YDEX', 'GAZP']); // у них финансовые слова не доказывают компанию («прибыль Газпром нефти»)

function occurrences(tk, text) {
  const l = norm(text);
  const out = [];
  const al = (N.ALIASES[tk] || []).map(a => a.trim()).filter(a => a.length >= 3);
  for (const a of al) { let i = l.indexOf(a); while (i >= 0) { out.push(i); i = l.indexOf(a, i + a.length); } }
  for (const m of l.matchAll(new RegExp('\\$' + tk.toLowerCase() + '(?![a-z])', 'g'))) out.push(m.index);
  const h = HOMO[tk];
  if (h) for (const m of l.matchAll(new RegExp(h.word.source, 'g'))) out.push(m.index);
  return { l, idx: [...new Set(out)].sort((a, b) => a - b) };
}

function tickerSense(tk, text) {
  const h = HOMO[tk];
  const { l, idx } = occurrences(tk, text);
  if (!idx.length) return { ok: false, why: `в тексте новости нет упоминания $${tk}` };
  if (!h) return { ok: true };
  let comp = 0, other = 0;
  for (const i of idx) {
    const ctx = l.slice(Math.max(0, i - 45), i + 50);
    const c = h.company.test(ctx) || (!STRICT.has(tk) && FIN.test(ctx)), o = h.other.test(ctx);
    if (c && !o) comp++; else if (o && !c) other++; else if (c && o) comp += 0.5;
  }
  if (comp < 1) return { ok: false, why: other ? `«${(l.slice(idx[0], idx[0] + 20).match(/[а-яa-z$.-]+/) || [''])[0]}» в тексте — обычное слово, а не компания $${tk}` : `нет признаков, что речь о компании $${tk}` };
  return { ok: true };
}

// ---------- 2. Полярность показателей в тексте ----------
const METRIC = /(прибыл|выручк|ebitda|оибда|доход(?!ност)|добыч|выпуск|продаж|трафик|перевез|дивиденд|маржинальн|рентабельн|выплат|капитализац|рейтинг|прогноз|оценк|целев\w* цен)/;
const NEG_METRIC = /(убыт|долг|расход|издержк|просрочк|отток|потер|штраф|себестоимост|резерв)/;
const UP = /(вырос|выросл|(?<![а-я])рост(?![а-я])|(?<![а-я])роста?(?![а-я])|(?<![а-я])ростом|увелич|нарастил|повыс|поднял|улучш|рекордн|прибавил|выше|в \d+([,.]\d+)? раза? больше|подорожа|удвоил)/;
const DOWN = /(снизил|снижен|сократил|сокращ|упал|упала|паден|уменьш|ухудш|ниже|потерял|обвал|подешев|урезал|отказал|отмен|понизил|в \d+([,.]\d+)? раза? меньше|обнулил)/;
function polarity(text) {
  let s = 0;
  const hits = [];
  for (const sent of SENT(text).slice(0, 6)) {
    for (const part of sent.split(/[;,](?= а | но | при этом | однако )|, а |, но |; /)) {
      const pos = METRIC.test(part), neg = NEG_METRIC.test(part);
      if (!pos && !neg) continue;
      const u = UP.test(part), d = DOWN.test(part);
      if (u === d) continue;
      let v = u ? 1 : -1;
      if (neg && !pos) v = -v; // «убыток сократился» — хорошо, «долг вырос» — плохо
      s += v; hits.push((v > 0 ? '+' : '−') + part.trim().slice(0, 60));
    }
  }
  return { s: Math.sign(s), raw: s, hits };
}

// ---------- 3. Направления в готовом посте ----------
function postDirections(html) {
  const p = plain(html);
  const head = p.split('\n')[0] || '';
  const color = /🟢/.test(head) ? 1 : /🔴/.test(head) ? -1 : 0;
  const sig = /🟢 ЛОНГ/.test(p) ? 1 : /🔴 ШОРТ/.test(p) ? -1 : 0;
  const issues = [];
  // цель сделки: «цель X (+p%)» должна быть в сторону сделки
  const m = p.match(/(ЛОНГ|ШОРТ)[^\n]*?\n?[^\n]*?цель\s+[\d\s,.]+\s*\(([+−-])/);
  if (m && ((m[1] === 'ЛОНГ') !== (m[2] === '+'))) issues.push('цель сделки лежит против направления сигнала');
  const lv = p.match(/Предположительный уровень[^\n]*?\(([+−-])\d/);
  return { color, sig, level: lv ? (lv[1] === '+' ? 1 : -1) : 0, issues };
}

// ---------- журнал ----------
function logAudit(rec) {
  try {
    const dir = process.env.STATE_DIR || path.join(__dirname, 'state');
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(path.join(dir, 'audit_log.jsonl'), JSON.stringify(Object.assign({ ts: Math.round(Date.now() / 1000) }, rec)) + '\n');
  } catch (e) { /* журнал не критичен */ }
}

// ctx: { html, src (текст источника), tk (главный тикер или 'РЫНОК'), ty, tks, link, AI, S }
async function audit(ctx) {
  const fails = [], notes = [];
  const tk = ctx.tk;
  const evDir = ((N.TYPES.find(t => t[0] === ctx.ty) || [])[2]) || 0;
  // 1) тикеры
  if (tk && tk !== 'РЫНОК') {
    for (const t of [tk].concat((ctx.tks || []).filter(x => x !== tk && x !== 'РЫНОК'))) {
      const r = tickerSense(t, ctx.src);
      if (!r.ok) (t === tk ? fails : notes).push(`тикер $${t}: ${r.why}`);
    }
  }
  // 2) полярность текста против события и вывода
  const pol = polarity(ctx.src);
  const d = postDirections(ctx.html);
  if (/^(results_|production_|guidance_|div_|analyst_)/.test(ctx.ty || '') && pol.s && evDir && pol.s !== evDir && Math.abs(pol.raw) >= 1)
    fails.push(`в тексте показатели ${pol.s > 0 ? 'улучшились' : 'ухудшились'}, а событие размечено как «${ctx.ty}» (${pol.hits.slice(0, 2).join(' | ')})`);
  const meaning = evDir || (/^(results_|production_|guidance_)/.test(ctx.ty || '') ? pol.s : 0);
  if (meaning && d.sig && d.sig !== meaning) fails.push(`сигнал ${d.sig > 0 ? 'ЛОНГ' : 'ШОРТ'} против смысла события`);
  if (meaning && d.color && d.color !== meaning) fails.push(`цвет заголовка ${d.color > 0 ? '🟢' : '🔴'} против смысла события`);
  if (meaning && d.level && d.level !== meaning) fails.push('предположительный уровень лежит против смысла события');
  fails.push(...d.issues);
  // 3) ИИ-ревизор: второе мнение (только если шлюз подключён и правила ошибок не нашли)
  let ai = null;
  if (!fails.length && ctx.AI && ctx.AI.ON && ctx.AI.review && tk && tk !== 'РЫНОК') {
    try {
      ai = await ctx.AI.review(ctx.S || {}, { src: plain(ctx.src).slice(0, 2500), tk, name: ((N.ALIASES[tk] || [])[0] || '').trim(), ty: ctx.tyRu || ctx.ty,
        conclusion: d.sig > 0 ? 'лонг (рост)' : d.sig < 0 ? 'шорт (падение)' : meaning > 0 ? 'позитив для бумаги' : meaning < 0 ? 'негатив для бумаги' : 'нейтрально' });
      if (ai && ai.verdict === false) fails.push('ИИ-ревизор: ' + (ai.why || 'вывод не соответствует новости'));
    } catch (e) { notes.push('ИИ-ревизор недоступен: ' + e.message); }
  }
  const res = { ok: !fails.length, fails, notes, polarity: pol.s, ai: ai ? ai.verdict : null };
  logAudit({ agent: 'ревизор', tk, ty: ctx.ty, link: ctx.link, ok: res.ok, fails, notes: notes.slice(0, 3) });
  return res;
}

module.exports = { audit, tickerSense, polarity, postDirections, HOMO };
