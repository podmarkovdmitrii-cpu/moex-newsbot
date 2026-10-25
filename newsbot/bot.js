// =====================================================================================================
// Новостной бот МОEX: виртуальный счёт по лучшей стратегии + новости из Telegram-каналов агентств и ведомств.
// Запускается GitHub Actions каждые 5 минут в часы торгов. Состояние — state/newsbot.json (кэш Actions).
//   • Открытие/закрытие сделки → сообщение: бумага, цена, стоп, риск в ₽ и % счёта, правило стратегии,
//     новостной фон по бумаге (новости с источниками, подтверждением и исторической реакцией рынка).
//   • Ранние (непроверенные) новости и слухи → отдельные сообщения со ссылкой и бумагами, на которые влияют;
//     при подтверждении другими каналами — сообщение «подтверждено».
//   • Важные официальные новости (ЦБ, Минфин, Правительство, Мосбиржа, компании) → сообщение с оценкой влияния.
// Виртуальный счёт, не инвестиционная рекомендация. Цены ISS Мосбиржи без подписки — с задержкой до 15 минут.
// =====================================================================================================
'use strict';
const fs = require('fs');
const path = require('path');
const RG = require('./regime.js');
const N = require('./nlp.js');
const AI = require('./ai.js');

const HERE = __dirname;
const STATE_DIR = process.env.STATE_DIR || path.join(HERE, 'state');
const STATE_FILE = path.join(STATE_DIR, 'newsbot.json');
const DRY = process.argv.includes('--dry-run') || !process.env.TG_TOKEN;
const FIXTURE = process.env.BOT_FIXTURE || null; // для проверки без сети: файл с сохранёнными ответами
const CAPITAL = +(process.env.BOT_CAPITAL || 100000);
const ISS = 'https://iss.moex.com/iss/';
const STRAT = JSON.parse(fs.readFileSync(path.join(HERE, 'strategy.json'), 'utf8'));
const WEIGHTS = JSON.parse(fs.readFileSync(path.join(HERE, 'weights.json'), 'utf8'));
const RELIAB = fs.existsSync(path.join(HERE, 'reliability.json')) ? JSON.parse(fs.readFileSync(path.join(HERE, 'reliability.json'), 'utf8')) : {};
const DIVS = JSON.parse(fs.readFileSync(path.join(HERE, 'divhist.json'), 'utf8'));
const UNIVERSE = 'SBER SBERP GAZP LKOH ROSN NVTK GMKN YDEX T PLZL TATN TATNP SNGS SNGSP VTBR MGNT CHMF PHOR NLMK ALRS MOEX MTSS X5 IRAO RUAL AFLT OZON VKCO SMLT AFKS MAGN HEAD POSI SVCB BSPB UGLD ENPG SIBN TRNFP PIKK FLOT SGZH BELU MTLR HYDR FEES RTKM CBOM AGRO LENT MVID LSRG AKRN NMTP UPRO MSNG RASP KMAZ SELG BANEP GCHE'.split(' ');
const KNOWN = new Set(UNIVERSE.concat(['IMOEX']));
const KNOWN_NEWS = new Set(UNIVERSE.concat(Object.keys(N.ALIASES))); // новости: и 2–3 эшелон

// ---------- каналы: тип источника и как часто проверять ----------
const CHANNELS = {
  // официальные
  centralbank_russia: 'офиц', minfin: 'офиц', government_rus: 'офиц', duma_gov_ru: 'офиц', mid_russia: 'офиц', moscowexchangeofficial: 'офиц',
  // агентства и деловые СМИ
  interfaxonline: 'агентство', ifax_go: 'агентство', prime1: 'агентство', tass_agency: 'агентство', rbc_news: 'СМИ', vedomosti: 'СМИ', kommersant: 'СМИ',
  frank_media: 'СМИ', banksta: 'СМИ', economica: 'СМИ', cbonds: 'СМИ',
  // рыночные ленты
  markettwits: 'лента', headlines_for_traders: 'лента', cbrstocks: 'лента', finamalert: 'лента', smartlabnews: 'лента', AK47pfl: 'лента', bcs_express: 'лента',
  tb_invest_official: 'лента', russianmacro: 'лента', if_stocks: 'лента',
  // авторские каналы инвесторов
  bitkogan: 'автор', harmfulinvestor: 'автор', mozgovikresearch: 'автор', pravdainvest: 'автор', invest_or_lost: 'автор', investheroes: 'автор',
  // ведомства и официальные лица (заявления о экономике, санкциях, переговорах) — для паблика
  minec_russia: 'офиц', minenergo_official: 'офиц', minpromtorg_ru: 'офиц', fnsru: 'офиц', rosstat_official: 'офиц', news_kremlin: 'политик',
  MariaVladimirovnaZakharova: 'политик', medvedev_telegram: 'политик', vv_volodin: 'политик', kadmitriev: 'политик',
  // ещё СМИ, брокеры и официальные каналы компаний (22.10.2026)
  forbesrussia: 'СМИ', thebell_io: 'СМИ', bloomberg_ru: 'СМИ', expert_ra: 'СМИ',
  sberinvestments: 'лента', alfa_investments: 'лента', omyinvestments: 'лента', bcs_world_of_investments: 'лента', alorbroker: 'лента', investfuture: 'лента', dohod: 'лента',
  if_market_news: 'лента', oilfly: 'лента',
  nornickel_official: 'офиц', sberbank: 'офиц', aeroflot_official: 'офиц', magnit_retail: 'офиц', yandex: 'офиц', gazprom: 'офиц',
};
const PRIORITY = ['markettwits', 'interfaxonline', 'ifax_go', 'prime1', 'tass_agency', 'rbc_news', 'cbrstocks', 'centralbank_russia', 'minfin', 'headlines_for_traders', 'moscowexchangeofficial', 'government_rus', 'news_kremlin', 'kadmitriev', 'if_market_news', 'bloomberg_ru'];
const CH = require('./channel.js');
const TYPE_RU = {
  div_rec: 'дивиденды', div_cut: 'отказ от дивидендов', buyback: 'обратный выкуп', spo: 'допэмиссия/SPO', ipo: 'IPO', results_up: 'отчётность: рост', results_down: 'отчётность: снижение',
  guidance_up: 'прогноз повышен', guidance_down: 'прогноз понижен', analyst_up: 'аналитики повысили', analyst_down: 'аналитики понизили', sanctions: 'санкции', legal: 'суды, штрафы, обыски',
  tax: 'налоги', production_up: 'операционные: рост', production_down: 'операционные: снижение', mna: 'сделка M&A', delist_redom: 'делистинг/редомициляция', management: 'смена руководства',
  cbr_cut: 'ЦБ снизил ставку', cbr_hike: 'ЦБ повысил ставку', cbr_hold: 'ЦБ сохранил ставку', geo_peace: 'переговоры/мир', geo_escal: 'эскалация', oil_up: 'нефть дорожает', oil_down: 'нефть дешевеет',
  ruble_weak: 'рубль слабеет', ruble_strong: 'рубль крепнет', tone: 'тон новости',
};

const STRONG_RUMOR = /источник|по данным|по информации|сообща[ею]т|инсайд|слух|утечк|неофициальн/i;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const nowMsk = () => new Date(Date.now() + 3 * 3600e3);
const dayStrMsk = (d = nowMsk()) => d.toISOString().slice(0, 10);
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const fmt = (x, d = 2) => x == null ? '—' : Number(x).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
const rub = x => (x >= 0 ? '+' : '−') + Math.abs(Math.round(x)).toLocaleString('ru-RU') + ' ₽';
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// ---------- сеть ----------
let FIX = null;
async function http(url, asJson = true) {
  if (FIXTURE) { FIX = FIX || JSON.parse(fs.readFileSync(FIXTURE, 'utf8')); const k = Object.keys(FIX).find(k => url.startsWith(k)); if (!k) throw new Error('нет в фикстуре: ' + url); return asJson ? FIX[k] : FIX[k]; }
  for (let k = 0; k < 3; k++) {
    try {
      const r = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0 (moex-newsbot)' }, signal: AbortSignal.timeout(20000) });
      if (r.status === 429) { await sleep(3000 * (k + 1)); continue; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return asJson ? await r.json() : await r.text();
    } catch (e) { if (k === 2) throw e; await sleep(1500 * (k + 1)); }
  }
}
const table = (js, name) => { const t = js[name] || {}; return (t.data || []).map(r => Object.fromEntries((t.columns || []).map((c, i) => [c, r[i]]))); };

async function tg(text, news = false) {
  if (DRY) { console.log('\n----- TELEGRAM -----\n' + text.replace(/<[^>]+>/g, '')); return; }
  const r = await fetch(`https://api.telegram.org/bot${process.env.TG_TOKEN}/sendMessage`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id: (news && process.env.TG_NEWS_CHAT_ID) || process.env.TG_CHAT_ID, text: text.slice(0, 4000), parse_mode: 'HTML', disable_web_page_preview: true }) });
  if (!r.ok) log('telegram:', r.status, (await r.text()).slice(0, 200));
  await sleep(400);
}

// ---------- состояние ----------
function loadState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch (e) { return {}; }
}
function saveState(S) { fs.mkdirSync(STATE_DIR, { recursive: true }); fs.writeFileSync(STATE_FILE, JSON.stringify(S)); }

// ---------- Telegram-каналы ----------
function parseChannel(ch, html) {
  const out = [];
  const blocks = html.split('<div class="tgme_widget_message_wrap');
  for (const b of blocks) {
    const pm = b.match(/data-post="([^"/]+)\/(\d+)"/); const tm = b.match(/<time[^>]*datetime="([^"]+)"/);
    const xm = b.match(/<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)<\/div>/);
    if (!pm || !tm || !xm) continue;
    const text = xm[1].replace(/<br\s*\/?>/g, '\n').replace(/<[^>]+>/g, '').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&nbsp;/g, ' ').replace(/&#(\d+);/g, (m, n) => String.fromCodePoint(+n)).replace(/&#x([0-9a-f]+);/gi, (m, n) => String.fromCodePoint(parseInt(n, 16))).trim();
    out.push({ ch, id: +pm[2], ts: Math.floor(Date.parse(tm[1]) / 1000), tx: text.slice(0, 1500), link: `https://t.me/${pm[1]}/${pm[2]}` });
  }
  return out;
}
function weightOf(type, rumor) {
  const w = WEIGHTS[type + (rumor ? '|слух' : '|факт')] || WEIGHTS[type];
  return w || null;
}
async function collectNews(S) {
  const chans = Object.keys(CHANNELS);
  const rot = chans.filter(c => !PRIORITY.includes(c));
  S.rot = ((S.rot || 0) + 1) % 3;
  const todo = PRIORITY.concat(rot.filter((c, i) => i % 3 === S.rot));
  const items = [];
  S.seen = S.seen || {};
  for (const ch of todo) {
    try {
      const html = await http('https://t.me/s/' + ch, false);
      const posts = parseChannel(ch, html);
      const last = S.seen[ch] || 0;
      const fresh = posts.filter(p => p.id > last && Date.now() / 1000 - p.ts < 6 * 3600);
      if (posts.length) S.seen[ch] = Math.max(last, ...posts.map(p => p.id));
      if (!last) continue; // первый запуск: только запоминаем последние id, без рассылки старого
      items.push(...fresh);
    } catch (e) { log('канал', ch, e.message); }
    await sleep(250);
  }
  // разметка
  return items.map(p => {
    const c = N.classify(p.tx);
    let tks = N.subjectTickers(p.tx, KNOWN_NEWS).filter(t => t !== 'IMOEX');
    const types = c.types.length ? c.types : (tks.length && c.s ? [['tone', 0]] : []);
    let score = 0, best = null;
    for (const [ty, d0] of types) {
      const d = d0 || Math.sign(c.s); const w = weightOf(ty, c.rumor);
      if (!w || !d) continue;
      const v = w.w * d; if (!best || Math.abs(v) > Math.abs(best.v)) best = { ty, v, mean: w.mean, n: w.n }; score += v;
    }
    return Object.assign(p, { tks, types: types.map(t => t[0]), s: c.s, rumor: c.rumor, official: c.official || CHANNELS[p.ch] === 'офиц' || CHANNELS[p.ch] === 'политик', score, best });
  });
}


// ---------- региональные события: инцидент + регион активов компаний (anomaly/regions.json) → журнал для моделей и постов ----------
let REGX = null;
function regInit() {
  if (REGX) return REGX;
  try {
    const R = JSON.parse(fs.readFileSync(path.join(HERE, 'anomaly', 'regions.json'), 'utf8'));
    REGX = { inc: new RegExp(R.incident, 'i'), ind: new RegExp(R.industrial, 'i'), dig: new RegExp(R.digest, 'i'),
      reg: Object.entries(R.regions).map(([k, v]) => [k, new RegExp('(?<![а-яёa-z])(' + v + ')', 'i')]) };
  } catch (e) { REGX = { reg: [] }; }
  return REGX;
}
function logRegional(S, items) {
  const X = regInit();
  if (!X.inc) return;
  S.regLast = S.regLast || {};
  for (const p of items || []) {
    const t = String(p.tx || '').slice(0, 600);
    if (X.dig.test(t.slice(0, 120)) || !X.inc.test(t)) continue;
    const regs = X.reg.filter(([k, rx]) => rx.test(t)).map(([k]) => k).filter(g => p.ts - (S.regLast[g] || 0) >= 3 * 3600);
    if (!regs.length) continue;
    for (const g of regs) S.regLast[g] = p.ts;
    try { fs.appendFileSync(path.join(STATE_DIR, 'news_log.jsonl'), JSON.stringify({ tk: '@REG', ts: p.ts, regs, ind: X.ind.test(t) ? 1 : 0, ch: p.ch, tx: t.slice(0, 300) }) + '\n'); } catch (e) { /* не критично */ }
  }
}
// ---------- сценарии сделки (модель anomaly/scenario.py через jobs.py trade_prob) ----------
function tradeScen(t, side, entry, stop, take, hz) {
  try {
    const out = require('child_process').execFileSync(process.env.PYTHON || 'python3', [path.join(HERE, 'channel', 'jobs.py'), 'trade_prob', t, String(side), String(entry), String(stop), take ? String(take) : '0', String(hz || 10)],
      { timeout: 90e3, encoding: 'utf8', env: Object.assign({}, process.env, { MODE: 'trade_prob', CHANNEL_STATE: path.join(STATE_DIR, 'channel'), NEWS_STATE: STATE_DIR }) });
    const line = out.split('\n').find(l => l.startsWith('SCEN_JSON '));
    return line ? JSON.parse(line.slice(10)) : null;
  } catch (e) { log('сценарии сделки: ошибка', e.message.slice(0, 200)); return null; }
}
// кластеры: одна новость в разных каналах (бумага + тип в окне 12 часов) — так считаем подтверждение
function cluster(S, items) {
  S.cl = S.cl || {};
  const now = Date.now() / 1000;
  for (const k of Object.keys(S.cl)) if (now - S.cl[k].ts > 5 * 86400) delete S.cl[k];
  const touched = [];
  for (const p of items) {
    const keys = (p.tks.length ? p.tks : (p.types.length ? ['РЫНОК'] : [])).flatMap(t => (p.types.length ? p.types : ['tone']).map(ty => t + '|' + ty));
    for (const key of keys) {
      let c = S.cl[key];
      if (c && p.ts - c.ts > 12 * 3600) c = null;
      if (!c) {
        c = S.cl[key] = { key, ts: p.ts, first: { ch: p.ch, link: p.link, tx: p.tx.slice(0, 1200), rumor: p.rumor }, srcs: {}, rumorOnly: true, sent: {}, score: p.score, best: p.best, tks: p.tks, official: p.official };
        // для новостного фильтра стратегии: одна новость = одно событие (как в исследовании)
        const tk = key.split('|')[0];
        // журнал событий для модели аномалий (продолжение исторического ряда новостей)
        try {
          fs.appendFileSync(path.join(STATE_DIR, 'news_log.jsonl'), JSON.stringify({ tk: tk === 'РЫНОК' ? 'IMOEX' : tk, ts: p.ts, ty: key.split('|')[1], s: Math.sign(p.score || 0), rum: p.rumor ? 1 : 0, off: p.official ? 1 : 0 }) + '\n');
        } catch (e) { /* журнал не критичен */ }
        if (tk !== 'РЫНОК' && p.best && !(S.newsEv || []).some(e => e.t === tk && e.ts === p.ts)) { S.newsEv = (S.newsEv || []).filter(e => p.ts - e.ts < 20 * 86400); S.newsEv.push({ t: tk, ts: p.ts, v: +(p.best.v).toFixed(3), ty: p.best.ty }); }
      }
      if (!c.srcs[p.ch]) c.srcs[p.ch] = { ts: p.ts, link: p.link, rumor: p.rumor, tx: p.tx.slice(0, 1200) };
      if (!p.rumor) c.rumorOnly = false;
      if (p.official) c.official = true;
      if (Math.abs(p.score) > Math.abs(c.score)) { c.score = p.score; c.best = p.best; }
      touched.push(c);
    }
  }
  return [...new Set(touched)];
}

function srcLine(c) {
  const n = Object.keys(c.srcs).length;
  const kinds = [...new Set(Object.keys(c.srcs).map(ch => CHANNELS[ch] || 'канал'))].join(', ');
  return `${n} ${n === 1 ? 'источник' : n < 5 ? 'источника' : 'источников'} (${kinds})`;
}
function effectLine(c) {
  if (!c.best) return 'историческая реакция: нет данных';
  const b = c.best;
  if (c.key.startsWith('РЫНОК|')) { const m = WEIGHTS['MKT:' + b.ty]; return `тип: ${TYPE_RU[b.ty] || b.ty}` + (m ? `; исторически индекс МосБиржи за 5 дней после таких новостей в среднем ${m.mean >= 0 ? 'рос' : 'снижался'} на ${fmt(Math.abs(m.mean), 2)}% (${m.n} случаев)` : ''); }
  return `тип: ${TYPE_RU[b.ty] || b.ty}; после таких новостей бумага в среднем ${b.mean >= 0 ? 'продолжала движение в сторону новости' : 'двигалась против новости (рынок учёл её заранее)'}: ${fmt(Math.abs(b.mean), 2)}% относительно индекса за 5 дней (${b.n} случаев, 2017–2026)`;
}
function relLine(ch) {
  const r = RELIAB[ch]; if (!r) return '';
  return `\nИсточник ${esc(ch)}: первым сообщал в ${fmt(r.firstShare * 100, 0)}% совпавших новостей` + (r.confirm != null ? `, слухи подтверждались в ${fmt(r.confirm * 100, 0)}% случаев` : '') + '.';
}

async function newsMessages(S, clusters) {
  let sent = 0;
  S.sentLinks = (S.sentLinks || []).slice(-500);
  const done = new Set(S.sentLinks);
  const today = dayStrMsk(); if (S.newsDay !== today) { S.newsDay = today; S.newsCount = 0; }
  const cap = +(process.env.NEWS_PER_DAY || 30);
  for (const c of clusters.sort((a, b) => Math.abs(b.score) - Math.abs(a.score))) {
    if (sent >= 8 || S.newsCount + sent >= cap) break;
    const firstLinkSent = done.has(c.first.link);
    const [tk, ty] = c.key.split('|');
    const n = Object.keys(c.srcs).length;
    const tickers = tk === 'РЫНОК' ? 'весь рынок' : c.tks.map(t => '$' + t).join(' ');
    // 1) ранняя новость / слух: показываем сразу, отдельно, со ссылкой
    if (c.first.rumor && !c.sent.rumor && tk !== 'РЫНОК' && ty !== 'tone' && STRONG_RUMOR.test(c.first.tx)) {
      c.sent.rumor = true; if (firstLinkSent) continue; done.add(c.first.link); S.sentLinks.push(c.first.link); sent++;
      await tg(`⚡️ <b>Ранняя новость (не подтверждена)</b> — ${tickers}\n${esc(c.first.tx.slice(0, 350))}\n\n🔗 <a href="${c.first.link}">${esc(c.first.ch)}</a> · ${srcLine(c)}\n${effectLine(c)}${relLine(c.first.ch)}\n<i>Слух или данные «источников»: может не подтвердиться. Не рекомендация.</i>`, true);
      continue;
    }
    // 2) подтверждение ранней новости
    if (c.sent.rumor && !c.sent.confirm && (n >= 3 || !c.rumorOnly)) {
      c.sent.confirm = true; sent++;
      const offLink = Object.entries(c.srcs).find(([ch, x]) => !x.rumor);
      await tg(`✔️ <b>Подтверждено</b> — ${tickers}: ${esc(TYPE_RU[ty] || ty)}\n${srcLine(c)}${offLink ? `\n🔗 <a href="${offLink[1].link}">${esc(offLink[0])}</a>` : ''}\nОт первого сообщения прошло ${Math.round((Date.now() / 1000 - c.ts) / 60)} мин.`, true);
      continue;
    }
    // 3) важная новость (официальная или с заметной исторической реакцией)
    const important = (Math.abs(c.score) >= 0.25 && n >= 1) || (c.official && (tk !== 'РЫНОК' || /^cbr_|^geo_|^tax|^sanctions/.test(ty)));
    if (important && !c.sent.imp && !c.first.rumor) {
      c.sent.imp = true; if (firstLinkSent) continue; done.add(c.first.link); S.sentLinks.push(c.first.link); sent++;
      await tg(`${c.official ? '🏛' : '📰'} <b>${c.official ? 'Официально' : 'Важная новость'}</b> — ${tickers}\n${esc(c.first.tx.slice(0, 400))}\n\n🔗 <a href="${c.first.link}">${esc(c.first.ch)}</a> · ${srcLine(c)}\n${effectLine(c)}`, true);
    }
  }
  S.newsCount += sent;
}

// ---------- котировки и стратегия ----------
async function dailyCandles(S, today) {
  if (S.candles && S.candles.date === today && S.candles.data) return S.candles.data;
  const start = new Date(Date.parse(S.start) - 420 * 86400e3).toISOString().slice(0, 10);
  const data = { sec: {}, imoex: [], lot: {} };
  const get = async (p) => { let out = [], st = 0; for (;;) { const js = await http(`${ISS}${p}?interval=24&from=${start}&start=${st}&iss.meta=off&candles.columns=open,close,high,low,value,begin`); const rows = table(js, 'candles'); out = out.concat(rows); if (rows.length < 500) break; st += rows.length; } return out; };
  for (const t of UNIVERSE) {
    try { data.sec[t] = (await get(`engines/stock/markets/shares/boards/TQBR/securities/${t}/candles.json`)).filter(r => r.begin.slice(0, 10) < today).map(r => [r.begin.slice(0, 10), r.open, r.high, r.low, r.close, r.value]); } catch (e) { log('свечи', t, e.message); }
    await sleep(120);
  }
  data.imoex = (await get('engines/stock/markets/index/securities/IMOEX/candles.json')).filter(r => r.begin.slice(0, 10) < today).map(r => [r.begin.slice(0, 10), r.open, r.high, r.low, r.close, 0]);
  const js = await http(`${ISS}engines/stock/markets/shares/boards/TQBR/securities.json?iss.meta=off&iss.only=securities&securities.columns=SECID,LOTSIZE,SHORTNAME,DECIMALS`);
  for (const r of table(js, 'securities')) data.lot[r.SECID] = { lot: r.LOTSIZE || 1, name: r.SHORTNAME, dec: r.DECIMALS ?? 2 };
  S.candles = { date: today, data };
  return data;
}
async function todayBars(today) {
  const out = { sec: {}, imoex: null };
  try {
    const js = await http(`${ISS}engines/stock/markets/shares/boards/TQBR/securities.json?iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,OPEN,HIGH,LOW,LAST,VALTODAY,SYSTIME`);
    for (const r of table(js, 'marketdata')) if (r.OPEN && r.LAST && String(r.SYSTIME || '').startsWith(today)) out.sec[r.SECID] = [today, r.OPEN, r.HIGH, r.LOW, r.LAST, r.VALTODAY || 0];
    const ix = await http(`${ISS}engines/stock/markets/index/securities/IMOEX.json?iss.meta=off&iss.only=marketdata&marketdata.columns=OPEN,HIGH,LOW,CURRENTVALUE,SYSTIME`);
    const m = table(ix, 'marketdata')[0];
    if (m && m.OPEN && String(m.SYSTIME || '').startsWith(today)) out.imoex = [today, m.OPEN, m.HIGH, m.LOW, m.CURRENTVALUE, 0];
  } catch (e) { log('котировки дня:', e.message); }
  return out;
}
function divAdjust(t, rows) { // множитель поправки на дивиденды (как в приложении): индикаторы без «гэпов отсечек»
  const ev = [], idx = new Map(rows.map((r, i) => [r[0], i]));
  for (const [d, v] of DIVS[t] || []) { const i = idx.get(d); if (i > 0 && v / rows[i - 1][4] > 0 && v / rows[i - 1][4] < 0.25) ev.push([i, v]); }
  const f = new Array(rows.length).fill(1); let k = 1, e = ev.length - 1;
  for (let i = rows.length - 1; i >= 0; i--) { f[i] = k; while (e >= 0 && ev[e][0] === i) { k *= 1 - ev[e][1] / rows[i - 1][4]; e--; } }
  return { f, div: ev.map(([i, v]) => [rows[i][0], v * f[i - 1]]) };
}
function runStrategy(S, data, live, today) {
  const sec = {}, div = {}, fac = {}, lots = {};
  for (const [t, rows0] of Object.entries(data.sec)) {
    const rows = rows0.slice(); if (live.sec[t]) rows.push(live.sec[t]);
    if (rows.length < 60) continue;
    const A = divAdjust(t, rows);
    sec[t] = rows.map((r, i) => [r[0], r[1] * A.f[i], r[2] * A.f[i], r[3] * A.f[i], r[4] * A.f[i], r[5]]);
    div[t] = A.div; fac[t] = new Map(rows.map((r, i) => [r[0], A.f[i]])); lots[t] = (data.lot[t] || {}).lot || 1;
  }
  const X = data.imoex.slice(); if (live.imoex) X.push(live.imoex);
  const days = X.map(r => r[0]);
  // новостные события → сигнальный день (вход на открытии следующего дня после публикации, как в исследовании)
  const sig = {};
  for (const e of S.newsEv || []) {
    const m = new Date((e.ts + 3 * 3600) * 1000), d = m.toISOString().slice(0, 10), hm = m.toISOString().slice(11, 16);
    let j = days.findIndex(x => x >= d); if (j < 0) j = days.length; if (days[j] === d && hm >= '09:55') j++;
    const sd = days[j - 1] || (j - 1 >= days.length ? days[days.length - 1] : null); if (!sd) continue;
    (sig[e.t] = sig[e.t] || []).push([sd, e.v]);
  }
  const D = RG.prepare({ days, sec, imoex: X, div, divTaxOnly: 1, sig });
  let from = days.findIndex(d => d >= S.start); if (from < 0) from = days.length - 1;
  const to = days.length - 1, sigTo = days[to] >= today ? to - 1 : to;
  const P = Object.assign({}, STRAT, { capital: CAPITAL, lots });
  const r = RG.run(D, P, { from, to, live: true, signalTo: sigTo });
  const raw = (t, d, px) => { const f = fac[t] && fac[t].get(d); return f ? px / f : px; };
  return { r, D, raw, days, to, regime: r.daily.length ? r.daily[r.daily.length - 1].g : 0 };
}

const REG_RU = { 1: 'рост (покупки: импульс и откат)', 0: 'боковик (новых сделок нет)', '-1': 'падение (только шорт пробоев)' };
function newsContext(S, t) {
  const now = Date.now() / 1000;
  const cl = Object.values(S.cl || {}).filter(c => c.tks.includes(t) && now - c.ts < 3 * 86400).sort((a, b) => Math.abs(b.score) - Math.abs(a.score) || b.ts - a.ts).slice(0, 3);
  if (!cl.length) return 'Новостной фон: значимых новостей по бумаге за 3 дня нет — решение принято по технической модели.';
  return 'Новостной фон (3 дня):\n' + cl.map(c => `• ${esc(c.first.tx.slice(0, 160))}\n  🔗 <a href="${c.first.link}">${esc(c.first.ch)}</a> · ${srcLine(c)}${c.first.rumor ? ' · слух' : ''}\n  ${effectLine(c)}`).join('\n');
}
function filterLine(p) {
  const ng = p.m && p.m.NGm;
  if (ng) return `🛡 Новостной фильтр пройден: весомых плохих новостей по бумаге за ${ng.days} дн. нет (вход запрещён при сумме весов ≤ −${fmt(ng.th, 1)}).\n`;
  if (p.side < 0) return '⚠️ Шорт: на реальном счёте нужен доступ к маржинальной торговле; перенос позиции платный, возможен резкий гэп вверх.\n';
  return 'Импульс входит без новостного фильтра (по исследованию он здесь отсекает лучшие тренды); проверено, что цена не выше EMA 20 больше чем на 10% — вход не на рывке.\n';
}
async function tradeMessages(S, res, data) {
  const { r, raw } = res;
  const name = t => ((data.lot[t] || {}).name || t);
  const key = p => p.t + '@' + p.d0;
  const openNow = new Map(r.open.map(p => [key(p), p]));
  S.open = S.open || {};
  // новые позиции
  for (const [k, p] of openNow) {
    if (S.open[k]) continue;
    const lot = (data.lot[p.t] || {}).lot || 1, entry = raw(p.t, p.d0, p.entry), stop = entry * (1 - p.side * STRAT.stopPct);
    const eq = r.daily.length ? r.daily[r.daily.length - 1].equity : CAPITAL, risk = Math.abs(entry - stop) * p.qty;
    await tg(`${p.side > 0 ? '🟢 <b>Открыта сделка: ПОКУПКА</b>' : '🔴 <b>Открыта сделка: ШОРТ</b>'} $${p.t} (${esc(name(p.t))})\n` +
      `Вход: <b>${fmt(entry, 2)}</b> · ${Math.round(p.qty / lot)} лот. (${Math.round(p.qty)} шт.) на ${fmt(entry * p.qty, 0)} ₽\n` +
      `Стоп-лосс: <b>${fmt(stop, 2)}</b> (−${fmt(STRAT.stopPct * 100, 0)}%)${p.take ? ` · цель ${fmt(raw(p.t, p.d0, p.take), 2)}` : ''}\n` +
      `⚠️ Риск сделки: ${fmt(risk, 0)} ₽ = ${fmt(risk / eq * 100, 2)}% счёта (${fmt(eq, 0)} ₽)\n` +
      `Почему: ${esc((p.why || []).join('; '))}\nРежим рынка: ${REG_RU[res.regime]}\n${filterLine(p)}\n${newsContext(S, p.t)}\n\n<i>Виртуальный счёт «${esc(STRAT.name)}». Не инвестиционная рекомендация.</i>`);
    const sc = tradeScen(p.t, p.side, entry, stop, p.take ? raw(p.t, p.d0, p.take) : null, p.fam === 'mom' ? 20 : 10);
    S.open[k] = { t: p.t, d0: p.d0, entry, qty: p.qty, side: p.side, sc: sc ? { p_take: sc.p_take, p_stop: sc.p_stop, p_time: sc.p_time, hz: sc.hz } : null };
    await CH.trade(S, `${p.side > 0 ? '🟢 <b>Модельный портфель: ПОКУПКА</b>' : '🔴 <b>Модельный портфель: ШОРТ</b>'} $${p.t} (${esc(name(p.t))})\n` +
      `Вход ≈ <b>${fmt(entry, 2)}</b> · стоп ${fmt(stop, 2)} (−${fmt(STRAT.stopPct * 100, 0)}%)${p.take ? ` · цель ${fmt(raw(p.t, p.d0, p.take), 2)}` : ''}\n` +
      `Риск сделки — ${fmt(risk / eq * 100, 1)}% модельного счёта.\nПочему: ${esc((p.why || [])[0] || '')}\nРежим рынка: ${REG_RU[res.regime]}\n${filterLine(p)}\n${sc && sc.text ? sc.text + '\n' : ''}Стратегия «${esc(STRAT.name)}» — виртуальный счёт, сделки публикуются для прозрачной статистики.\n#${p.t} #модельный_портфель`);
  }
  // закрытые позиции
  for (const k of Object.keys(S.open)) {
    if (openNow.has(k)) continue;
    const o = S.open[k];
    const tr = r.trades.filter(t => t.t === o.t && t.d0 === o.d0).pop();
    if (tr) {
      const exit = raw(tr.t, tr.d1, tr.exit);
      await tg(`${tr.pnl >= 0 ? '✅' : '⛔️'} <b>Закрыта сделка</b> $${tr.t} (${esc(name(tr.t))})\nВыход: ${fmt(exit, 2)} (вход ${fmt(o.entry, 2)}) · причина: ${esc(tr.reason)}\nРезультат: <b>${rub(tr.pnl)}</b> (${fmt(tr.r, 2)}R, ${fmt(tr.ret * 100, 2)}%), держали ${tr.days} дн.`);
      const scl = o.sc ? `\n🎲 При входе модель давала: цель раньше стопа ${Math.round(o.sc.p_take * 100)}%, стоп раньше цели ${Math.round(o.sc.p_stop * 100)}%, выход по времени ${Math.round(o.sc.p_time * 100)}%.` : '';
      await CH.trade(S, `${tr.pnl >= 0 ? '✅' : '⛔️'} <b>Модельный портфель: закрыта сделка</b> $${tr.t}\nВыход ${fmt(exit, 2)} (вход ${fmt(o.entry, 2)}) · ${esc(tr.reason)}\nРезультат: <b>${fmt(tr.ret * 100, 2)}%</b> по цене, ${fmt(tr.r, 2)}R (с комиссиями), ${tr.days} дн.${scl}\n#${tr.t} #модельный_портфель`);
    }
    delete S.open[k];
  }
  // вечером — сигналы на завтра (один раз в день)
  const hm = nowMsk().toISOString().slice(11, 16), today = dayStrMsk();
  if (hm >= '19:00' && S.sigDay !== today) {
    S.sigDay = today;
    const pend = r.pendEntries || [];
    if (pend.length) await tg('🔔 <b>Сигналы стратегии на завтра</b> (вход на открытии):\n' + pend.map(q => `• $${q.t} ${q.side > 0 ? 'покупка' : 'шорт'} — ${esc((q.why || [])[0] || '')}`).join('\n') + `\nРежим рынка: ${REG_RU[res.regime]}`);
  }
}

// ---------- сигналы и список наблюдения для утреннего плана паблика (state/signals.json) ----------
function writeSignals(S, res, data, today) {
  const { r, D, raw, to } = res;
  const I = RG.I, d = D.days[to];
  const name = t => ((data.lot[t] || {}).name || t);
  const rr = (t, px) => raw(t, d, px);
  const regS = RG.regimeOf(D, (STRAT.modules.find(m => m.fam === 'sbrk') || {}).reg || STRAT.regime).g[to];
  const liquid = D.tickers.filter(t => { const s = D.S[t]; return s.c[to] === s.c[to] && I.av(s, 60)[to] >= (STRAT.minValue || 1e7); })
    .sort((a, b) => I.av(D.S[b], 60)[to] - I.av(D.S[a], 60)[to]).slice(0, STRAT.topN || 25);
  const mom = liquid.map(t => { const s = D.S[t]; return { t, roc: I.roc(s, 126)[to], ext: s.c[to] / I.ema(s, 20)[to] - 1, ok: s.c[to] > I.sma(s, 200)[to] }; })
    .filter(x => x.ok && x.roc >= 0.05).sort((a, b) => b.roc - a.roc).slice(0, 6);
  const watch = [], held = new Set(r.open.map(p => p.t));
  mom.filter(x => !held.has(x.t)).forEach((x, k) => { const s = D.S[x.t]; const c = s.c[to]; watch.push({ t: x.t, name: name(x.t), kind: 'mom', side: 1, rank: k + 1, roc: x.roc, ext: x.ext, entry: rr(x.t, c), stop: rr(x.t, c * 0.97), take: null,
    note: `лидер импульса №${k + 1}: ${fmt(x.roc * 100, 0)}% за 126 дней` + (x.ext > 0.10 ? `, но цена выше EMA 20 на ${fmt(x.ext * 100, 1)}% — вход отложен до паузы` : '; ротация по пятницам') }); });
  for (const t of liquid) {
    const s = D.S[t], c = s.c[to], e20 = I.ema(s, 20)[to], e100 = I.ema(s, 100)[to], s200 = I.sma(s, 200)[to];
    if (held.has(t)) continue;
    if (e20 > e100 && c > s200 && c >= e20 && (c - e20) / e20 <= 0.03 && !watch.some(w => w.t === t))
      watch.push({ t, name: name(t), kind: 'pull', side: 1, entry: rr(t, e20), stop: rr(t, e20 * 0.97), take: rr(t, e20 * 1.09), dist: (c - e20) / e20,
        note: `восходящий тренд, цена в ${fmt((c - e20) / e20 * 100, 1)}% над EMA 20: покупка при откате к ${fmt(rr(t, e20), 2)} и закрытии выше открытия` });
    const ll = I.ll(s, 40)[to], e200 = I.ema(s, 200)[to];
    if (regS === -1 && c < e200 && c <= ll * 1.03 && c > ll)
      watch.push({ t, name: name(t), kind: 'sbrk', side: -1, entry: rr(t, ll), stop: rr(t, ll * 1.03), take: rr(t, ll * 0.91), note: `падающий рынок, цена в ${fmt((c / ll - 1) * 100, 1)}% над минимумом 40 дней: шорт при закрытии ниже ${fmt(rr(t, ll), 2)} на объёме` });
  }
  const last = r.daily[r.daily.length - 1] || {};
  const tr = r.trades.filter(x => !x.open);
  const out = {
    made: new Date().toISOString(), day: d, today, regime: res.regime, regimeShort: regS, regimeText: REG_RU[res.regime], strategy: STRAT.name, start: S.start,
    equity: last.equity || CAPITAL, capital: CAPITAL, trades: tr.length, wins: tr.filter(x => x.pnl > 0).length,
    open: r.open.map(p => ({ t: p.t, name: name(p.t), side: p.side, fam: p.fam, d0: p.d0, entry: raw(p.t, p.d0, p.entry), stop: rr(p.t, p.stop), take: p.take ? rr(p.t, p.take) : null, last: rr(p.t, p.last), pnl: p.side * (p.last / p.entry - 1) })),
    pend: (r.pendEntries || []).map(q => { const c = D.S[q.t].c[to]; return { t: q.t, name: name(q.t), side: q.side, fam: q.m.fam, why: q.why, entry: rr(q.t, c), stop: rr(q.t, c * (1 - q.side * STRAT.stopPct)), take: q.m.take ? rr(q.t, c * (1 + q.side * q.m.take * STRAT.stopPct)) : null }; }),
    watch: watch.slice(0, 14),
    recent: tr.slice(-10).map(x => ({ t: x.t, side: x.side, d0: x.d0, d1: x.d1, ret: x.ret, r: x.r, reason: x.reason })),
  };
  fs.mkdirSync(STATE_DIR, { recursive: true });
  fs.writeFileSync(path.join(STATE_DIR, 'signals.json'), JSON.stringify(out));
}

// ---------- главный цикл ----------
(async () => {
  const S = loadState();
  const today = dayStrMsk();
  S.start = S.start || process.env.BOT_START || today;
  S.runs = (S.runs || 0) + 1;
  log('запуск', S.runs, 'старт счёта', S.start, DRY ? '(без отправки)' : '');
  // 1) новости
  try {
    const items = await collectNews(S);
    logRegional(S, items);
    const cl = cluster(S, items);
    log('новых постов', items.length, 'кластеров', cl.length);
    await newsMessages(S, Object.values(S.cl || {}).filter(c => Date.now() / 1000 - c.ts < 2 * 86400));
    try { await CH.run(S, Object.values(S.cl || {}).filter(c => Date.now() / 1000 - c.ts < 2 * 86400), items); } catch (e) { log('паблик: ошибка', e.stack || e.message); }
  } catch (e) { log('новости: ошибка', e.message); }
  // 2) стратегия: дневные свечи (раз в день) + текущий день из marketdata
  try {
    const data = await dailyCandles(S, today);
    const live = await todayBars(today);
    const res = runStrategy(S, data, live, today);
    const eq = res.r.daily.length ? res.r.daily[res.r.daily.length - 1].equity : CAPITAL;
    log('позиций', res.r.open.length, 'сделок', res.r.trades.length, 'капитал', Math.round(eq));
    if (S.firstRun === undefined) { S.firstRun = false; S.open = Object.fromEntries(res.r.open.map(p => [p.t + '@' + p.d0, { t: p.t, d0: p.d0, entry: res.raw(p.t, p.d0, p.entry), qty: p.qty, side: p.side }])); await tg(`🤖 Новостной бот запущен. Виртуальный счёт ${fmt(CAPITAL, 0)} ₽, стратегия «${esc(STRAT.name)}», риск 1% на сделку, стоп 3%. Каналов: ${Object.keys(CHANNELS).length}. Режим рынка: ${REG_RU[res.regime]}.`); }
    else await tradeMessages(S, res, data);
    S.equity = Math.round(eq);
    try { writeSignals(S, res, data, today); } catch (e) { log('signals.json: ошибка', e.message); }
  } catch (e) { log('стратегия: ошибка', e.stack || e.message); }
  saveState(S);
  // 3) режим «почти реального времени»: запуск по расписанию держится NEWS_LOOP_MIN минут и проверяет каналы каждые NEWS_LOOP_SEC секунд,
  //    чтобы весомые новости уходили в паблик сразу, а не раз в 5–30 минут (расписание GitHub часто запаздывает)
  const LOOP_MIN = +(process.env.NEWS_LOOP_MIN || 0), LOOP_SEC = +(process.env.NEWS_LOOP_SEC || 75);
  const t0 = Date.now();
  let lastAn = 0, lastPulse = 0;
  while (LOOP_MIN && Date.now() - t0 < LOOP_MIN * 60e3) {
    // аномалии цен/объёмов — каждые ~5 минут
    if (process.env.ANOMALY_EVERY_MIN && Date.now() - lastAn >= +process.env.ANOMALY_EVERY_MIN * 60e3) {
      lastAn = Date.now();
      try {
        require('child_process').execFileSync(process.env.PYTHON || 'python3', [path.join(HERE, 'channel', 'jobs.py'), 'anomaly'],
          { timeout: 240e3, stdio: 'inherit', env: Object.assign({}, process.env, { MODE: 'anomaly', CHANNEL_STATE: path.join(STATE_DIR, 'channel'), NEWS_STATE: STATE_DIR }) });
      } catch (e) { log('аномалии: ошибка', e.message); }
    }
    // прогноз аномалий на день — один раз утром до открытия (06:00–09:50 МСК, будни)
    const msk = new Date(Date.now() + 3 * 3600e3);
    const mday = msk.toISOString().slice(0, 10), mh = msk.getUTCHours(), mwd = msk.getUTCDay();
    if (mwd >= 1 && mwd <= 5 && mh >= 6 && mh <= 9 && S.anomPreDay !== mday) {
      S.anomPreDay = mday;
      try {
        require('child_process').execFileSync(process.env.PYTHON || 'python3', [path.join(HERE, 'channel', 'jobs.py'), 'anom_pre'],
          { timeout: 600e3, stdio: 'inherit', env: Object.assign({}, process.env, { MODE: 'anom_pre', CHANNEL_STATE: path.join(STATE_DIR, 'channel'), NEWS_STATE: STATE_DIR }) });
      } catch (e) { log('прогноз аномалий: ошибка', e.message); }
    }
    // вечером — прогноз аномалий на следующий торговый день (нужен ночью для сценариев сделок по новостям)
    if (mwd >= 1 && mwd <= 5 && mh >= 19 && mh <= 22 && S.anomNextDay !== mday) {
      S.anomNextDay = mday;
      try {
        require('child_process').execFileSync(process.env.PYTHON || 'python3', [path.join(HERE, 'channel', 'jobs.py'), 'anom_next'],
          { timeout: 600e3, stdio: 'inherit', env: Object.assign({}, process.env, { MODE: 'anom_next', CHANNEL_STATE: path.join(STATE_DIR, 'channel'), NEWS_STATE: STATE_DIR }) });
      } catch (e) { log('прогноз на завтра: ошибка', e.message); }
    }
    // Пульс: сделки сильных трейдеров + лента постов для подтверждения новостей
    if (process.env.PULSE_EVERY_MIN && Date.now() - lastPulse >= +process.env.PULSE_EVERY_MIN * 60e3) {
      lastPulse = Date.now();
      try {
        require('child_process').execFileSync(process.env.PYTHON || 'python3', [path.join(HERE, 'channel', 'jobs.py'), 'pulse_deals'],
          { timeout: 240e3, stdio: 'inherit', env: Object.assign({}, process.env, { MODE: 'pulse_deals', CHANNEL_STATE: path.join(STATE_DIR, 'channel'), NEWS_STATE: STATE_DIR }) });
      } catch (e) { log('Пульс: ошибка', e.message); }
    }
    await sleep(LOOP_SEC * 1000);
    try {
      AI.newCycle();
      const items = await collectNews(S);
      logRegional(S, items);
      const cl = cluster(S, items);
      if (items.length) log('новых постов', items.length, 'кластеров', cl.length);
      const recent = Object.values(S.cl || {}).filter(c => Date.now() / 1000 - c.ts < 2 * 86400);
      await newsMessages(S, recent);
      try { await CH.run(S, recent, items); } catch (e) { log('паблик: ошибка', e.stack || e.message); }
      saveState(S);
    } catch (e) { log('цикл новостей: ошибка', e.message); }
  }
})();
