// =====================================================================================================
// Паблик (Telegram-канал и сообщество ВК): отбор достоверных новостей, заявления официальных лиц, ранние сигналы,
// сделки инсайдеров и крупных акционеров — с оценкой влияния на бумаги и сектора по истории 2017–2026.
// Вызывается из bot.js на каждом запуске (каждые 5 минут). Без TG_PUBLIC_CHAT и VK_GROUP_TOKEN ничего не публикует.
// =====================================================================================================
'use strict';
const fs = require('fs');
const path = require('path');
const N = require('./nlp.js');
const AI = require('./ai.js');

const IMPACT = JSON.parse(fs.readFileSync(path.join(__dirname, 'impact.json'), 'utf8'));
const RELIAB = fs.existsSync(path.join(__dirname, 'reliability.json')) ? JSON.parse(fs.readFileSync(path.join(__dirname, 'reliability.json'), 'utf8')) : {};
const TG_TOKEN = process.env.TG_PUBLIC_TOKEN || process.env.TG_TOKEN || '';
const TG_CHAT = process.env.TG_PUBLIC_CHAT || '';
const VK_TOKEN = process.env.VK_GROUP_TOKEN || '';
const VK_USER = process.env.VK_USER_TOKEN || '';
const VK_GROUP = String(process.env.VK_GROUP_ID || '').replace(/^-/, '');
const PER_DAY = +(process.env.PUBLIC_PER_DAY || 60);
const DRY = process.argv.includes('--dry-run') || (!TG_CHAT && !VK_TOKEN && !process.env.VK_USER_TOKEN);
const ON = !!(TG_CHAT || VK_TOKEN || process.env.VK_USER_TOKEN || process.argv.includes('--dry-run'));
const DISCLAIMER = 'Не является индивидуальной инвестиционной рекомендацией.';

// Источники: кто считается достоверным, как называть в постах
const SRC = {
  centralbank_russia: ['Банк России', 'офиц'], minfin: ['Минфин', 'офиц'], government_rus: ['Правительство РФ', 'офиц'], duma_gov_ru: ['Госдума', 'офиц'],
  mid_russia: ['МИД России', 'офиц'], moscowexchangeofficial: ['Мосбиржа', 'офиц'], minec_russia: ['Минэкономразвития', 'офиц'], minenergo_official: ['Минэнерго', 'офиц'],
  minpromtorg_ru: ['Минпромторг', 'офиц'], fnsru: ['ФНС', 'офиц'], rosstat_official: ['Росстат', 'офиц'], news_kremlin: ['Кремль. Новости', 'политик'],
  MariaVladimirovnaZakharova: ['Мария Захарова (МИД)', 'политик'], medvedev_telegram: ['Дмитрий Медведев', 'политик'], vv_volodin: ['Вячеслав Володин', 'политик'],
  kadmitriev: ['Кирилл Дмитриев (РФПИ)', 'политик'],
  interfaxonline: ['Интерфакс', 'агентство'], ifax_go: ['Интерфакс', 'агентство'], prime1: ['ПРАЙМ', 'агентство'], tass_agency: ['ТАСС', 'агентство'],
  rbc_news: ['РБК', 'СМИ'], vedomosti: ['Ведомости', 'СМИ'], kommersant: ['Коммерсантъ', 'СМИ'], frank_media: ['Frank Media', 'СМИ'], banksta: ['Банкста', 'СМИ'],
  economica: ['The Экономист', 'СМИ'], cbonds: ['Cbonds', 'СМИ'], markettwits: ['MarketTwits', 'лента'], headlines_for_traders: ['Headlines for traders', 'лента'],
  cbrstocks: ['ЦБ-акции', 'лента'], finamalert: ['Финам', 'лента'], smartlabnews: ['Смартлаб', 'лента'], AK47pfl: ['AK47pfl', 'лента'], bcs_express: ['БКС Экспресс', 'лента'],
  tb_invest_official: ['Т-Инвестиции', 'лента'], russianmacro: ['Russian Macro', 'лента'], if_stocks: ['ИФ Акции', 'лента'],
  forbesrussia: ['Forbes Russia', 'СМИ'], thebell_io: ['The Bell', 'СМИ'], bloomberg_ru: ['Bloomberg на русском', 'СМИ'], expert_ra: ['Эксперт РА', 'СМИ'],
  sberinvestments: ['СберИнвестиции', 'лента'], alfa_investments: ['Альфа-Инвестиции', 'лента'], omyinvestments: ['ВТБ Моя Аналитика', 'лента'],
  bcs_world_of_investments: ['БКС Мир инвестиций', 'лента'], alorbroker: ['АЛОР', 'лента'], investfuture: ['InvestFuture', 'лента'], dohod: ['ДОХОДЪ', 'лента'],
  if_market_news: ['IF News', 'лента'], oilfly: ['Oilfly', 'лента'],
  nornickel_official: ['Норникель', 'компания'], sberbank: ['Сбер', 'компания'], aeroflot_official: ['Аэрофлот', 'компания'], magnit_retail: ['Магнит', 'компания'],
  yandex: ['Яндекс', 'компания'], gazprom: ['Газпром', 'компания'],
};
const TRUSTED = new Set(Object.entries(SRC).filter(([, v]) => ['офиц', 'политик', 'агентство', 'компания'].includes(v[1])).map(([k]) => k).concat(['rbc_news', 'vedomosti', 'kommersant', 'forbesrussia', 'thebell_io', 'bloomberg_ru']));
const POLIT = new Set(Object.entries(SRC).filter(([, v]) => v[1] === 'политик' || v[1] === 'офиц').map(([k]) => k));
const SEC_RU = { consumer: 'потребсектор', industry: 'машиностроение', bank: 'банки', oil: 'нефтегаз', metal: 'металлурги', retail: 'ритейл', tech: 'IT и связь', power: 'энергетика', chem: 'химия', devel: 'девелоперы', transport: 'транспорт' };
const TYPE_RU = {
  div_rec: 'дивиденды', div_cut: 'отказ от дивидендов', buyback: 'обратный выкуп', spo: 'допэмиссия/SPO', ipo: 'IPO', results_up: 'рост прибыли/выручки', results_down: 'снижение прибыли',
  guidance_up: 'прогноз повышен', guidance_down: 'прогноз понижен', analyst_up: 'аналитики повысили оценку', analyst_down: 'аналитики понизили оценку', sanctions: 'санкции', legal: 'суды, штрафы, обыски',
  tax: 'налоги', production_up: 'рост добычи/продаж', production_down: 'снижение добычи/продаж', mna: 'сделка M&A', delist_redom: 'делистинг/редомициляция', management: 'смена руководства',
  cbr_cut: 'ЦБ снизил ставку', cbr_hike: 'ЦБ повысил ставку', cbr_hold: 'ЦБ сохранил ставку', geo_peace: 'переговоры, мир', geo_escal: 'эскалация', oil_up: 'нефть дорожает', oil_down: 'нефть дешевеет',
  ruble_weak: 'рубль слабеет', ruble_strong: 'рубль крепнет', tone: 'тон новости',
};
const STRONG_RUMOR = /источник|по данным|по информации|сообща[ею]т|инсайд|слух|утечк|неофициальн|рассматрива|может (быть|стать|выплатить|провести|рекомендовать|получить|продать|купить|объявить)/i;
// раскрытые сделки инсайдеров и крупных акционеров (по сообщениям компаний и СМИ)
const INSIDER = /(член[а-я]* совета директоров|председател[а-я]* совета директоров|гендиректор|генеральн[а-я]* директор|глав[а-я]* компании|президент компании|председател[а-я]* правления|топ-менеджер|менеджмент|инсайдер|мажоритар|крупн[а-я]* акционер|основател[а-я]*|владел[а-я]*|акционер[а-я]*)[^.]{0,80}(купил|приобр[её]л|продал|увеличил[а-я]* (свою )?долю|снизил[а-я]* (свою )?долю|сократил[а-я]* (свою )?долю|нарастил|реализовал)/i;

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const plural = (n, a, b, c) => { const m = n % 100, k = n % 10; return (m >= 11 && m <= 14) ? c : k === 1 ? a : (k >= 2 && k <= 4) ? b : c; };
const cases = n => `${n} ${plural(n, 'случай', 'случая', 'случаев')}`;
const fmt = (x, d = 2) => x == null ? '—' : Number(x).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }).replace(/\u00a0/g, ' ');
const pc = (x, d = 1) => { const v = Math.abs(x * 100).toFixed(d); return (Number(v) === 0 ? '' : x >= 0 ? '+' : '−') + v.replace('.', ',') + '%'; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), '[паблик]', ...a);
const src = ch => (SRC[ch] || [ch])[0];
const BASE_DIR = Object.fromEntries(N.TYPES.map(t => [t[0], t[2]]));
const typeKey = (ty, s) => (ty === 'tone' || !BASE_DIR[ty]) ? ty + (s > 0 ? '+' : s < 0 ? '-' : '0') : ty;

// ---------- оценка влияния ----------
function strength(x) { const a = Math.abs(x); return a < 0.0025 ? 'слабое' : a < 0.0075 ? 'умеренное' : 'сильное'; }
function compImpact(tk, ty, s) {
  const k = typeKey(ty, s), sec = IMPACT.secOf[tk];
  const v = IMPACT.comp[tk + '|' + k] || IMPACT.sec[sec + '|' + k] || IMPACT.ty[k];
  if (!v) return null;
  return Object.assign({ lvl: IMPACT.comp[tk + '|' + k] ? 'бумага' : IMPACT.sec[sec + '|' + k] ? 'сектор' : 'все бумаги' }, v);
}
function compLine(tk, ty, s) {
  const v = compImpact(tk, ty, s);
  if (!v) return `$${tk} — истории по такому типу новостей мало`;
  const arrow = v.a5 >= 0 ? '▲' : '▼';
  return `$${tk} ${arrow} ${strength(v.a5)}: ${pc(v.a5, 2)} к индексу за 5 дн., ${pc(v.a1, 2)} за день; обгоняла индекс в ${Math.round(v.up5 * 100)}% случаев (${cases(v.n)}${v.lvl !== 'бумага' ? ', оценка по ' + (v.lvl === 'сектор' ? 'сектору' : 'всем бумагам') : ''})` +
    (Math.abs(v.pre) >= 0.003 ? `; до публикации цена уже сдвигалась на ${pc(v.pre)}` : '');
}
function mktLines(ty, s) {
  const k = typeKey(ty, s), m = IMPACT.mkt[k];
  if (!m) return [];
  const secs = Object.entries(m.sec || {}).sort((a, b) => Math.abs(b[1].r5) - Math.abs(a[1].r5)).slice(0, 4);
  return [`Индекс МосБиржи ${m.r5 >= 0 ? '▲' : '▼'} ${strength(m.r5)}: ${pc(m.r5, 2)} за 5 дн., ${pc(m.r1, 2)} за день; рост в ${Math.round(m.up5 * 100)}% случаев (${cases(m.n)})`,
    'Сильнее всего реагируют: ' + secs.map(([sec, v]) => `${SEC_RU[sec] || sec} ${pc(v.r5, 2)}`).join(', ')];
}
// ---------- вес новости по истории 135 тыс. новостей 2017–2026 и реакции рынка на них ----------
// W = |ожидаемый эффект за 5 дней| × уверенность (z-оценка устойчивости знака, до 1) × вероятность, что новость правдива.
// Процентиль W среди всех исторических пар «бумага × тип новости» (или «рынок × тип») — понятная шкала силы.
const zConf = (up5, n) => Math.abs(up5 - 0.5) / Math.sqrt(0.25 / Math.max(1, n));
const wOf = (a, up5, n) => Math.abs(a) * Math.min(1, zConf(up5, n) / 2);
const PCT = {
  comp: Object.values(IMPACT.comp).map(v => wOf(v.a5, v.up5, v.n)).sort((a, b) => a - b),
  mkt: Object.values(IMPACT.mkt).map(v => wOf(v.r5, v.up5, v.n)).sort((a, b) => a - b),
};
const pctOf = (arr, x) => { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < x) lo = m + 1; else hi = m; } return arr.length ? lo / arr.length : 0; };
function newsWeight(c) {
  const [tk, ty] = c.key.split('|');
  const s = c.best && c.best.v ? Math.sign(c.best.v) : 0;
  const p = c.rumorOnly ? rumorProb(c) : 1;
  if (tk === 'РЫНОК') {
    const m = IMPACT.mkt[typeKey(ty, s)];
    if (!m) return null;
    const E = Math.abs(m.r5);
    const W0 = E * Math.min(1, zConf(m.up5, m.n) / 2);
    return { W: W0 * p, E, n: m.n, p, pct: pctOf(PCT.mkt, W0), dir: Math.sign(m.r5), lvl: 'рынок' };
  }
  const v = compImpact(tk, ty, s);
  if (!v) return null;
  const W0 = wOf(v.a5, v.up5, v.n);
  return { W: W0 * p, E: Math.abs(v.a5), n: v.n, p, pct: pctOf(PCT.comp, W0), dir: Math.sign(v.a5), lvl: v.lvl };
}
function weightLine(w) {
  if (!w) return '';
  const lab = w.pct >= 0.9 ? 'высокий' : w.pct >= 0.75 ? 'повышенный' : 'средний';
  return `⚖️ <b>Вес новости: ${lab}</b> — сильнее ${Math.round(w.pct * 100)}% ${w.lvl === 'рынок' ? 'типов рыночных' : ''} новостей 2017–2026. Ожидаемое движение ${w.lvl === 'рынок' ? 'индекса МосБиржи' : 'бумаги относительно индекса'} за 5 дней ≈ ${pc(w.E * (w.dir || 1), 2)} (выборка: ${cases(w.n)}${w.lvl !== 'бумага' && w.lvl !== 'рынок' ? ', оценка по ' + (w.lvl === 'сектор' ? 'сектору' : 'всем бумагам') : ''})` + (w.p < 1 ? `; с поправкой на вероятность подтверждения ~${Math.round(w.p * 100)}%` : '') + '.';
}
function rumorProb(c) {
  let q = 1;
  for (const [ch, x] of Object.entries(c.srcs)) { if (!x.rumor) return 1; const r = RELIAB[ch]; q *= 1 - Math.min(0.5, Math.max(0.04, r && r.confirm != null ? r.confirm : 0.08)); }
  return 1 - q;
}

// ---------- отправка ----------
async function tgSend(text, reply) {
  if (DRY || !TG_CHAT) { console.log('\n===== ПАБЛИК TG =====\n' + text.replace(/<[^>]+>/g, '')); return null; }
  const body = { chat_id: TG_CHAT, text: text.slice(0, 4000), parse_mode: 'HTML', disable_web_page_preview: true };
  if (reply) body.reply_parameters = { message_id: reply, allow_sending_without_reply: true };
  const r = await fetch(`https://api.telegram.org/bot${TG_TOKEN}/sendMessage`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const js = await r.json().catch(() => ({}));
  if (!js.ok) { log('telegram:', r.status, JSON.stringify(js).slice(0, 200)); return null; }
  await sleep(1100); // лимит Telegram для каналов ~20 сообщений в минуту
  return js.result.message_id;
}
function vkText(html) {
  return html.replace(/<a href="([^"]+)">([^<]*)<\/a>/g, '$2: $1').replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
}
async function vkPost(html, photo, tgLink) {
  if (DRY || !(VK_TOKEN || VK_USER) || !VK_GROUP) { if (VK_TOKEN || VK_USER || DRY) console.log('\n===== ПАБЛИК ВК =====\n' + vkText(html)); return null; }
  const ph = photo && fs.existsSync(photo) ? await vkPhoto(photo) : null;
  const post = async tok => {
    const msg = vkText(html) + (!ph && tgLink ? `\n\nКартинка: ${tgLink}` : '');
    const p = new URLSearchParams({ owner_id: '-' + VK_GROUP, from_group: '1', message: msg.slice(0, 15000), access_token: tok, v: '5.199' });
    if (ph) p.set('attachments', ph.att);
    const r = await fetch('https://api.vk.com/method/wall.post', { method: 'POST', body: p });
    return r.json().catch(() => ({}));
  };
  const first = ph ? ph.tok : (VK_TOKEN || VK_USER);
  let js = await post(first);
  if (js.error && first === VK_TOKEN && VK_USER) { log('vk: ключ сообщества не подошёл (', js.error.error_code, js.error.error_msg, ') — публикую ключом администратора'); js = await post(VK_USER); }
  if (js.error) log('vk:', js.error.error_code, js.error.error_msg);
  await sleep(400);
  return js.response ? js.response.post_id : null;
}
// Фото + подпись: подпись в Telegram ограничена 1024 знаками — остаток уходит ответом под фото.
function splitCaption(html, lim = 1000) {
  const blocks = html.split('\n\n');
  let cap = '';
  let i = 0;
  for (; i < blocks.length; i++) {
    const nx = cap ? cap + '\n\n' + blocks[i] : blocks[i];
    if (nx.replace(/<[^>]+>/g, '').length > lim) break;
    cap = nx;
  }
  if (!cap) { // первый блок слишком длинный — режем по предложению (без тегов внутри обрезки)
    const plainB = blocks[0];
    cap = plainB.slice(0, plainB.lastIndexOf('. ', lim - 20) + 1 || lim - 20).replace(/<[^>]*$/, '');
    return [cap, [plainB.slice(cap.length).trim(), ...blocks.slice(1)].filter(Boolean).join('\n\n')];
  }
  return [cap, blocks.slice(i).join('\n\n')];
}
async function tgPhoto(photo, caption, reply) {
  const fd = new FormData();
  fd.append('chat_id', TG_CHAT);
  fd.append('caption', caption);
  fd.append('parse_mode', 'HTML');
  if (reply) fd.append('reply_parameters', JSON.stringify({ message_id: reply, allow_sending_without_reply: true }));
  fd.append('photo', new Blob([fs.readFileSync(photo)], { type: 'image/png' }), path.basename(photo));
  const r = await fetch(`https://api.telegram.org/bot${TG_TOKEN}/sendPhoto`, { method: 'POST', body: fd });
  const js = await r.json().catch(() => ({}));
  if (!js.ok) { log('telegram фото:', r.status, JSON.stringify(js).slice(0, 200)); return null; }
  await sleep(1100);
  return js.result.message_id;
}
let VK_GROUP_PHOTO_OFF = false; // ВК не даёт ключу сообщества загружать фото на стену (ошибка 27) — нужен VK_USER_TOKEN
async function vkPhoto(photo) {
  // загрузка картинки на стену: сначала ключом сообщества, при отказе — ключом администратора
  for (const tok of [VK_TOKEN, VK_USER].filter(Boolean)) {
    if (tok === VK_TOKEN && VK_GROUP_PHOTO_OFF) continue;
    try {
      const q = p => new URLSearchParams({ ...p, access_token: tok, v: '5.199' });
      const srv = await (await fetch('https://api.vk.com/method/photos.getWallUploadServer', { method: 'POST', body: q({ group_id: VK_GROUP }) })).json();
      if (srv.error) { if (tok === VK_TOKEN && srv.error.error_code === 27) VK_GROUP_PHOTO_OFF = true; else log('vk фото (сервер):', srv.error.error_code, srv.error.error_msg); continue; }
      const fd = new FormData();
      fd.append('photo', new Blob([fs.readFileSync(photo)], { type: 'image/png' }), path.basename(photo));
      const up = await (await fetch(srv.response.upload_url, { method: 'POST', body: fd })).json();
      const sv = await (await fetch('https://api.vk.com/method/photos.saveWallPhoto', { method: 'POST', body: q({ group_id: VK_GROUP, server: up.server, photo: up.photo, hash: up.hash }) })).json();
      if (sv.error) { log('vk фото (сохранение):', sv.error.error_code, sv.error.error_msg); continue; }
      const p = sv.response[0];
      return { att: `photo${p.owner_id}_${p.id}`, tok };
    } catch (e) { log('vk фото:', e.message); }
  }
  return null;
}
async function publish(S, html, reply, useAi, photo) {
  if (useAi) html = await AI.withLead(html, S, esc);
  const full = html + `\n\n<i>${DISCLAIMER}</i>`;
  let id = null;
  if (photo && fs.existsSync(photo) && !DRY && TG_CHAT) {
    const disc = `\n\n<i>${DISCLAIMER}</i>`;
    let [cap, rest] = splitCaption(html, 1000 - DISCLAIMER.length - 2);
    cap += disc;
    if (rest) rest += disc;
    id = await tgPhoto(photo, cap, reply);
    if (id && rest) await tgSend(rest); // продолжение отдельным сообщением (не ответом — без цитаты-дубля)
    if (!id) id = await tgSend(full, reply);
  } else {
    if (photo) console.log('[картинка] ' + photo);
    id = await tgSend(full, reply);
  }
  // ссылка на пост с картинкой в Telegram: ВК покажет её карточкой с этой картинкой, если фото не загрузить ключом сообщества
  const tgUser = TG_CHAT.startsWith('@') ? TG_CHAT.slice(1) : '';
  const tgLink = photo && id && tgUser ? `https://t.me/${tgUser}/${id}` : '';
  await vkPost(html + `\n\n${DISCLAIMER}`, photo, tgLink);
  S.pub.count++;
  return id;
}


// Пульс только как подтверждение новости: посты авторов о той же бумаге за 12 часов (лента пишется jobs.py pulse_deals)
function pulseLine(tks, dir) {
  let feed = [];
  try { feed = JSON.parse(fs.readFileSync(path.join(process.env.STATE_DIR || path.join(__dirname, 'state'), 'channel', 'pulse_feed.json'), 'utf8')); } catch (e) { return ''; }
  const now = Date.now() / 1000;
  const xs = feed.filter(x => now - x.ts < 12 * 3600 && x.st && (x.tks || []).some(t => tks.includes(t)));
  if (!xs.length) return '';
  const b = xs.filter(x => x.st > 0).length, s = xs.length - b;
  const strong = [...new Set(xs.filter(x => x.top).map(x => `${x.nick} — ${x.st > 0 ? 'покупка' : 'продажа'}`))].slice(0, 3);
  const agree = dir ? (dir > 0 ? b > s : s > b) : null;
  return `👥 <b>Пульс за 12 ч</b> по ${tks.map(t => '$' + t).join(' ')}: ${b} о покупке, ${s} о продаже` + (strong.length ? `; сильные трейдеры: ${esc(strong.join(', '))}` : '') +
    (agree === null ? '.' : agree ? ' — настроение совпадает с новостью.' : ' — настроение против новости.');
}
// ---------- оформление ----------
function srcPick(c) {
  return Object.entries(c.srcs).filter(([, x]) => x.tx).sort((a, b) => (TRUSTED.has(b[0]) - TRUSTED.has(a[0])) || (a[1].rumor - b[1].rumor) || (b[1].tx.length - a[1].tx.length))[0];
}
// Чистка текста источника: хештеги рубрик, упоминания каналов, реклама «подписывайтесь/канал в Максе»
// HTML-сущности из источника (&#036; → $, &amp;quot; → ") — иногда приходят дважды закодированными
function unent(t) {
  for (let i = 0; i < 3 && /&(#\d+|#x[0-9a-f]+|amp|quot|lt|gt|nbsp|apos|#39);/i.test(t); i++)
    t = t.replace(/&amp;/g, '&').replace(/&#(\d+);/g, (m, n) => String.fromCodePoint(+n)).replace(/&#x([0-9a-f]+);/gi, (m, n) => String.fromCodePoint(parseInt(n, 16)))
      .replace(/&quot;/g, '"').replace(/&apos;|&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ');
  return t;
}
function cleanSrc(tx) {
  return unent(String(tx || ''))
    .replace(/[▪️•]*\s*(Канал|Читайте|Подписывайтесь|Подписаться|Наш канал|Мы в)[^.!?\n]{0,80}[«"]?(Максе|MAX|Telegram|ВК|VK|канал[а-я]*)[»"]?[.!]?/gi, ' ')
    .replace(/(^|\s)#[\wА-Яа-яЁё_]+/g, ' ')
    .replace(/(^|\s)@[\w_]{4,}/g, ' ')
    .replace(/^[\s\u2600-\u27BF\uD83C-\uDBFF\uDC00-\uDFFF\uFE0F]+/, '')
    .replace(/\s+/g, ' ').trim();
}
function srcFull(c) {
  const p = srcPick(c);
  return cleanSrc((p && p[1].tx) || c.first.tx || '');
}
// Дайджесты и сводки (много тем в одном посте) в паблик не идут — тип события в них определяется плохо
const DIGEST = /(сводк[аи]|главн(ые|ое) (новост|к этому|за|на)|что произошло|дайджест|утренн(ий|яя) (обзор|сводка)|вечерн(ий|яя) (обзор|сводка)|итоги (дня|недели)|коротко о главном|новости к этому часу|как закрыл(ась|ся|ись)|лидеры (роста|падения)|итоги торгов|на закрытие|рынок на \d\d:\d\d|на \d\d:\d\d мск)/i;
const isDigest = c => DIGEST.test(srcFull(c).slice(0, 200)) || (c.tks || []).length > 3;
// Цитата без обрыва на полуслове: целиком до 700 знаков, иначе до конца предложения + «читать полностью»
function quoteFull(tx, link) {
  tx = tx.replace(/\s+/g, ' ').trim();
  if (tx.length <= 700) return { html: esc(tx), cut: false };
  let k = Math.max(tx.lastIndexOf('. ', 700), tx.lastIndexOf('! ', 700), tx.lastIndexOf('? ', 700));
  if (k < 250) k = tx.lastIndexOf(' ', 700);
  return { html: esc(tx.slice(0, k + 1).trim()) + (link ? ` … <a href="${link}">читать полностью</a>` : ' …'), cut: true };
}
function headline(c) { return srcFull(c).slice(0, 360); }
// Текст новости для поста: пересказ целиком (ИИ) или цитата без обрыва; плюс картинка
// Российский рынок: новости «рынка» без связи с Россией (Netflix, IPO Anthropic, ЦБ Турции…) в паблик не идут
const RU_REL = /росси|рф\b|рубл|мосбирж|imoex|ммвб|офз|банк росси|\bцб\b|минфин|набиуллин|силуанов|кремл|путин|песков|госдум|правительств|санкц|нефт|brent|urals|газ|опек|экспорт|импорт|украин|переговор|уиткофф|бюджет|ндпи|налог|инфляц|ключев/i;
function marketRelevant(text) { return RU_REL.test(text) && !/^(?=.*(netflix|apple|tesla|nvidia|anthropic|openai|microsoft|google|amazon|meta\b))(?!.*(росси|рубл|мосбирж))/is.test(text); }

// Сигнал по новости: направление — по истории реакций бумаги на такие новости, уровни — по дневному ATR (card.py)
function newsSide(tk, ty, s) {
  const v = compImpact(tk, ty, s);
  if (!v || v.n < 15) return { side: 0, v, why: 'истории реакций по такому типу новостей мало' };
  const edge = v.up5 - 0.5;
  if (Math.abs(v.a5) < 0.0025 || Math.abs(edge) < 0.04 || Math.sign(v.a5) !== Math.sign(edge)) return { side: 0, v, why: 'историческая реакция слабая или неустойчивая' };
  return { side: Math.sign(v.a5), v };
}
function sigBlock(tk, ns, card, rumor) {
  const v = ns.v;
  if (!ns.side) return `🎯 <b>Сигнал по $${tk}:</b> нет — ${ns.why}.`;
  const dir = ns.side > 0 ? 'ЛОНГ' : 'ШОРТ';
  let t = `🎯 <b>Сигнал: ${dir} $${tk}</b>${rumor ? ' — только если новость подтвердится' : ''}\n`;
  if (card && card.entry) t += `Вход ≈ ${fmt(card.entry, 2)} · стоп ${fmt(card.stop, 2)} (${pc(-card.risk, 1)}) · тейк ${fmt(card.take, 2)} (${pc(2 * card.risk, 1)}) · горизонт до 5 дней\n`;
  t += `Основание: после таких новостей $${tk} ${v.a5 >= 0 ? 'обгоняла' : 'отставала от'} индекс${v.a5 >= 0 ? '' : 'а'} в среднем на ${pc(Math.abs(v.a5), 2).replace(/^[+−]/, '')} за 5 дней, в ${Math.round((v.a5 >= 0 ? v.up5 : 1 - v.up5) * 100)}% случаев (выборка ${v.n}${v.lvl !== 'бумага' ? ', оценка по ' + (v.lvl === 'сектор' ? 'сектору' : 'всем бумагам') : ''}).`;
  if (Math.sign(v.pre) === ns.side && Math.abs(v.pre) >= Math.abs(v.a5)) t += '\n⚠️ Обычно цена успевает сдвинуться ещё до публикации — часть реакции может быть уже отыграна.';
  if (ns.side < 0) t += '\n⚠️ Шорт требует маржинальной торговли; перенос позиции платный.';
  if (card && card.entry) {
    const sc = scenCall(tk, ns.side, card.entry, card.stop, card.take, 5);
    if (sc && sc.text) t += '\n' + sc.text;
  }
  return t;
}
// Сценарии сделки по модели (channel/jobs.py trade_prob): вероятности тейка, стопа и выхода по времени
function scenCall(t, side, entry, stop, take, hz) {
  try {
    const sd = process.env.STATE_DIR || path.join(__dirname, 'state');
    const out = require('child_process').execFileSync(process.env.PYTHON || 'python3', [path.join(__dirname, 'channel', 'jobs.py'), 'trade_prob', t, String(side), String(entry), String(stop), String(take || 0), String(hz)],
      { timeout: 90e3, encoding: 'utf8', env: Object.assign({}, process.env, { MODE: 'trade_prob', CHANNEL_STATE: path.join(sd, 'channel'), NEWS_STATE: sd }) });
    const line = out.split('\n').find(l => l.startsWith('SCEN_JSON '));
    return line ? JSON.parse(line.slice(10)) : null;
  } catch (e) { log('сценарии сделки:', e.message.slice(0, 160)); return null; }
}
// Вероятная реакция рынка: сектор бумаги и индекс на такой тип новостей
function reactBlock(tk, ty, s) {
  const k = typeKey(ty, s), out = [];
  const sec = tk && tk !== 'РЫНОК' ? (IMPACT.secOf[tk] || N.SEC_OF[tk]) : null;
  const sv = sec && IMPACT.sec[sec + '|' + k];
  if (sv && sv.n >= 30) out.push(`Сектор «${SEC_RU[sec] || sec}» ${sv.a5 >= 0 ? '▲' : '▼'} ${strength(sv.a5)}: ${pc(sv.a5, 2)} к индексу за 5 дн. (${cases(sv.n)})`);
  out.push(...mktLines(ty, s));
  return out.length ? '<b>Вероятная реакция рынка (история 2017–2026):</b>\n' + out.map(esc).join('\n') : '';
}

async function newsBody(S, c, label, tk, s, ty, rumor) {
  const p = srcPick(c);
  const link = p ? p[1].link : c.first.link;
  const full = srcFull(c);
  if (tk === 'РЫНОК' && !marketRelevant(full)) return { skip: 'нет связи с российским рынком' };
  const cands = (c.tks || []).filter(t => t !== 'РЫНОК');
  const pack = AI.ON ? await AI.newsPack(S, full, `тикеры-кандидаты: ${cands.map(t => '$' + t + ' (' + ((N.ALIASES[t] || [])[0] || '').trim() + ')').join(', ') || 'нет — новость о рынке в целом'}; событие: ${label}`, cands) : null;
  if (pack && pack.relevant === false) return { skip: 'ИИ: не влияет на российский рынок' };
  let tks = cands;
  if (pack && Array.isArray(pack.tickers) && cands.length) {
    tks = cands.filter(t => pack.tickers.includes(t));
    if (!tks.length || (tk !== 'РЫНОК' && !tks.includes(tk))) return { skip: `ИИ: новость не о $${tk}` };
  }
  const main = tk === 'РЫНОК' ? 'РЫНОК' : tk;
  tks = [main].concat(tks.filter(t => t !== main));
  const body = pack && pack.retell ? { html: esc(pack.retell) } : quoteFull(full, link);
  const ns = main !== 'РЫНОК' && ty ? newsSide(main, ty, s) : { side: 0 };
  const card = await makeCard({ t: main, kind: main === 'РЫНОК' ? 'index' : 'share', tf: '1h', label, caption: (pack && pack.caption) || label, fact: (pack && pack.fact) || '', sign: s, side: rumor ? 0 : ns.side });
  const sig = main !== 'РЫНОК' && ty ? sigBlock(main, ns, card, rumor) : '';
  return { html: body.html, photo: card && card.png, tks, main, sig, react: ty ? reactBlock(main, ty, s) : '' };
}
// Иллюстрация: своя карточка (свечи + подпись-мем + уровни) — newsbot/channel/card.py (matplotlib)
async function makeCard(d) {
  if (process.env.NEWS_CARDS === '0') return null;
  try {
    const { execFileSync } = require('child_process');
    const dir = path.join(process.env.STATE_DIR || __dirname, 'cards');
    fs.mkdirSync(dir, { recursive: true });
    for (const f of fs.readdirSync(dir)) if (Date.now() - fs.statSync(path.join(dir, f)).mtimeMs > 3600e3) fs.unlinkSync(path.join(dir, f));
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const js = path.join(dir, id + '.json'), png = path.join(dir, id + '.png');
    fs.writeFileSync(js, JSON.stringify(d));
    const py = process.env.PYTHON || 'python3';
    const out = execFileSync(py, [path.join(__dirname, 'channel', 'card.py'), js, png].concat(process.argv.includes('--demo') ? ['--demo'] : []), { timeout: 90000, stdio: ['ignore', 'pipe', 'pipe'], env: process.env }).toString();
    fs.unlinkSync(js);
    const line = out.trim().split('\n').filter(l => l.startsWith('{')).pop();
    const res = line ? JSON.parse(line) : {};
    return fs.existsSync(png) ? Object.assign(res, { png }) : null;
  } catch (e) { log('картинка не сделана:', String(e.stderr || e.message).slice(-300)); return null; }
}
function sourcesLine(c) {
  const arr = Object.entries(c.srcs).sort((a, b) => a[1].ts - b[1].ts).slice(0, 7);
  return 'Источники: ' + arr.map(([ch, x]) => `<a href="${x.link}">${esc(src(ch))}</a>${x.rumor ? ' (со ссылкой на источники)' : ''}`).join(' · ');
}
function tags(tks) { return tks.length ? '\n' + tks.slice(0, 6).map(t => '#' + t).join(' ') : ''; }

// ---------- основной отбор ----------
async function run(S, clusters, items) {
  // вес каждой новости сохраняется в кластере — по нему утренний план ранжирует ночные новости, вечерний — новости дня
  for (const c of clusters) {
    try {
      const w = newsWeight(c);
      if (w) c.wt = { W: +w.W.toFixed(5), pct: +w.pct.toFixed(3), E: +w.E.toFixed(4), dir: w.dir, n: w.n, p: +w.p.toFixed(2), lvl: w.lvl };
    } catch (e) { /* вес не критичен */ }
  }
  if (!ON) return;
  S.pub = S.pub || { day: '', count: 0, links: [], msg: {} };
  const today = new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10);
  if (S.pub.day !== today) { S.pub.day = today; S.pub.count = 0; }
  const done = new Set(S.pub.links);
  const now = Date.now() / 1000;
  let n = 0;
  const can = () => n < 4 && S.pub.count < PER_DAY;
  const mark = l => { done.add(l); S.pub.links.push(l); S.pub.links = S.pub.links.slice(-800); };
  // тексты источников (для заголовков)
  for (const it of items || []) for (const c of clusters) if (c.srcs[it.ch] && c.srcs[it.ch].link === it.link) c.srcs[it.ch].tx = it.tx;

  // 1) сделки инсайдеров и крупных акционеров (раскрытые)
  for (const it of items || []) {
    if (!can()) break;
    if (!it.tks.length || done.has(it.link) || now - it.ts > 3 * 3600 || (SRC[it.ch] || [])[1] === 'автор' || !INSIDER.test(it.tx)) continue;
    const buy = /купил|приобр|увеличил|нарастил/i.test(it.tx);
    mark(it.link); n++;
    const pseudo = { srcs: { [it.ch]: { tx: it.tx, link: it.link, ts: it.ts } }, first: { tx: it.tx, link: it.link }, tks: it.tks };
    const nb = await newsBody(S, pseudo, buy ? 'Покупка инсайдера' : 'Продажа инсайдера', it.tks[0], buy ? 1 : -1);
    await publish(S, `💼 <b>Сделка инсайдера / крупного акционера</b> — ${it.tks.map(t => '$' + t).join(' ')}\n${nb.html}\n\n` +
      `🔗 <a href="${it.link}">${esc(src(it.ch))}</a>\n` +
      `Как читать: ${buy ? 'покупки' : 'продажи'} менеджмента и крупных акционеров — публичная информация о том, как «свои» оценивают бумагу. ` +
      'Это не инсайдерская информация в смысле закона (224-ФЗ): сделка уже раскрыта.' + tags(it.tks), null, false, nb.photo);
  }

  const lastTs = c => Math.max(...Object.values(c.srcs).map(x => x.ts));
  const TH_COMP = +(process.env.NEWS_PCT_COMP || 0.75), TH_MKT = +(process.env.NEWS_PCT_MKT || 0.8);
  // сначала самые весомые: по исторической реакции рынка на такие новости
  const fresh = clusters.filter(c => now - lastTs(c) < 2 * 3600).map(c => ({ c, w: newsWeight(c) })).filter(x => x.w)
    .sort((a, b) => b.w.W - a.w.W);
  for (const { c, w } of fresh) {
    if (!can()) break;
    const [tk, ty] = c.key.split('|');
    if (!(c.pub && (c.pub.rumor || c.pub.done)) && isDigest(c)) continue;
    const chs = Object.keys(c.srcs);
    const firstLink = c.first.link;
    c.pub = c.pub || {};
    const s = c.best && c.best.v ? Math.sign(c.best.v) : 0;
    const polit = chs.some(ch => (SRC[ch] || [])[1] === 'политик') || (chs.some(ch => POLIT.has(ch)) && tk === 'РЫНОК');
    const trustedFact = chs.some(ch => TRUSTED.has(ch) && !c.srcs[ch].rumor);
    const indepFacts = chs.filter(ch => !c.srcs[ch].rumor && (SRC[ch] || [])[1] !== 'автор').length;
    const th = tk === 'РЫНОК' ? TH_MKT : TH_COMP;

    // 4) подтверждение раннего сигнала
    if (c.pub.rumor && !c.pub.confirm && !c.rumorOnly) {
      c.pub.confirm = true; n++;
      const off = Object.entries(c.srcs).find(([, x]) => !x.rumor);
      await publish(S, `✔️ <b>Подтверждено</b> — ${c.tks.map(t => '$' + t).join(' ')}: ${esc(TYPE_RU[ty] || ty)}\n🔗 <a href="${off[1].link}">${esc(src(off[0]))}</a> · через ${Math.round((off[1].ts - c.ts) / 60)} мин после первого сообщения`, c.pub.msg);
      continue;
    }
    if (c.pub.done || c.pub.rumor || done.has(firstLink)) continue;
    // 2) заявления официальных лиц и ведомств — сразу, если по истории такие заявления двигали рынок
    if (polit && tk === 'РЫНОК' && w.pct >= th - 0.2) {
      c.pub.done = true; mark(firstLink); n++;
      const nb = await newsBody(S, c, TYPE_RU[ty] || ty, tk, s, ty);
      if (nb.skip) { log('пропуск:', nb.skip); n--; continue; }
      c.pub.msg = await publish(S, `🏛 <b>Заявление</b> — ${esc(TYPE_RU[ty] || ty)}\n${nb.html}\n\n${sourcesLine(c)}\n\n${weightLine(w)}\n\n${nb.react}`, null, false, nb.photo);
      continue;
    }
    // 3) ранний сигнал (данные «источников») — сразу, если весомый; с вероятностью подтверждения
    if (c.rumorOnly) {
      if (tk === 'РЫНОК' || ty === 'tone' || !STRONG_RUMOR.test(c.first.tx || '') || w.p < 0.1 || w.pct < th) continue;
      c.pub.rumor = true; mark(firstLink); n++;
      const nb = await newsBody(S, c, (TYPE_RU[ty] || ty) + ' (слух)', tk, s, ty, true);
      if (nb.skip) { log('пропуск:', nb.skip); n--; continue; }
      c.pub.msg = await publish(S, `🕵️ <b>Ранний сигнал (не подтверждён)</b> — ${nb.tks.map(t => '$' + t).join(' ')}: ${esc(TYPE_RU[ty] || ty)}\n${nb.html}\n\n${sourcesLine(c)}\n` +
        `Вероятность подтверждения: ~${Math.round(w.p * 100)}% — по тому, как часто подтверждались прошлые сообщения этих источников.\n\n${weightLine(w)}\n\n${nb.sig}\n\n${nb.react}` + ((pl => pl ? `\n\n${pl}` : '')(pulseLine(nb.tks || [], w.dir))) + tags(nb.tks), null, false, nb.photo);
      continue;
    }
    // 5) факт — публикуем сразу по первому источнику, если вес высокий; иначе ждём второго источника или надёжного СМИ
    if (c.first.rumor && !trustedFact) continue;
    if (ty === 'tone') continue; // «тон новости» — нет конкретного события: в паблик не идёт
    if (w.pct < th) continue;
    const single = !(trustedFact || indepFacts >= 2);
    if (single && !chs.some(ch => (SRC[ch] || [])[1] && (SRC[ch] || [])[1] !== 'автор' && !c.srcs[ch].rumor)) continue; // авторские каналы в одиночку — не источник
    if (tk === 'РЫНОК' && !(c.official || trustedFact || w.pct >= 0.9)) continue;
    c.pub.done = true; mark(firstLink); n++;
    const nb = await newsBody(S, c, TYPE_RU[ty] || ty, tk, s, ty);
    if (nb.skip) { log('пропуск:', nb.skip); n--; continue; }
    const head = tk === 'РЫНОК' ? 'Рынок' : '$' + nb.main + (nb.tks.length > 1 ? ' (также ' + nb.tks.slice(1).map(t => '$' + t).join(' ') + ')' : '');
    c.pub.msg = await publish(S, `${w.pct >= 0.9 ? '⚡️' : c.official ? '🏛' : '📰'} <b>${head}</b> — ${esc(TYPE_RU[ty] || ty)}\n${nb.html}\n\n${sourcesLine(c)}` + (single ? ' · <i>пока один источник</i>' : '') +
      `\n\n${weightLine(w)}` + (nb.sig ? `\n\n${nb.sig}` : '') + (nb.react ? `\n\n${nb.react}` : '') + ((pl => pl ? `\n\n${pl}` : '')(pulseLine(nb.tks || [], w.dir))) + tags(nb.tks), null, false, nb.photo);
  }
  if (n) log('опубликовано', n, 'за день', S.pub.count);
}

// ---------- сделки модельного портфеля (прозрачный трек-рекорд) ----------
async function trade(S, html) {
  if (!ON || process.env.PUBLIC_TRADES === '0') return;
  S.pub = S.pub || { day: '', count: 0, links: [], msg: {} };
  await publish(S, html);
}

module.exports = { run, trade, compLine, mktLines, SRC };
