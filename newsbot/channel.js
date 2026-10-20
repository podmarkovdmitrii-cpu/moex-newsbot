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
const VK_GROUP = String(process.env.VK_GROUP_ID || '').replace(/^-/, '');
const PER_DAY = +(process.env.PUBLIC_PER_DAY || 40);
const DRY = process.argv.includes('--dry-run') || (!TG_CHAT && !VK_TOKEN);
const ON = !!(TG_CHAT || VK_TOKEN || process.argv.includes('--dry-run'));
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
};
const TRUSTED = new Set(Object.entries(SRC).filter(([, v]) => ['офиц', 'политик', 'агентство'].includes(v[1])).map(([k]) => k).concat(['rbc_news', 'vedomosti', 'kommersant']));
const POLIT = new Set(Object.entries(SRC).filter(([, v]) => v[1] === 'политик' || v[1] === 'офиц').map(([k]) => k));
const SEC_RU = { bank: 'банки', oil: 'нефтегаз', metal: 'металлурги', retail: 'ритейл', tech: 'IT и связь', power: 'энергетика', chem: 'химия', devel: 'девелоперы', transport: 'транспорт' };
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
const pc = (x, d = 1) => (x >= 0 ? '+' : '−') + Math.abs(x * 100).toFixed(d).replace('.', ',') + '%';
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
  return `$${tk} ${arrow} ${strength(v.a5)}: ${pc(v.a5, 2)} к индексу за 5 дн., ${pc(v.a1, 2)} за день; обгоняла индекс в ${Math.round(v.up5 * 100)}% случаев (${v.n} случаев${v.lvl !== 'бумага' ? ', оценка по ' + (v.lvl === 'сектор' ? 'сектору' : 'всем бумагам') : ''})` +
    (Math.abs(v.pre) >= 0.003 ? `; до публикации цена уже сдвигалась на ${pc(v.pre)}` : '');
}
function mktLines(ty, s) {
  const k = typeKey(ty, s), m = IMPACT.mkt[k];
  if (!m) return [];
  const secs = Object.entries(m.sec || {}).sort((a, b) => Math.abs(b[1].r5) - Math.abs(a[1].r5)).slice(0, 4);
  return [`Индекс МосБиржи ${m.r5 >= 0 ? '▲' : '▼'} ${strength(m.r5)}: ${pc(m.r5, 2)} за 5 дн., ${pc(m.r1, 2)} за день; рост в ${Math.round(m.up5 * 100)}% случаев (${m.n} случаев)`,
    'Сильнее всего реагируют: ' + secs.map(([sec, v]) => `${SEC_RU[sec] || sec} ${pc(v.r5, 2)}`).join(', ')];
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
async function vkPost(html) {
  if (DRY || !VK_TOKEN || !VK_GROUP) { if (VK_TOKEN || DRY) console.log('\n===== ПАБЛИК ВК =====\n' + vkText(html)); return null; }
  const p = new URLSearchParams({ owner_id: '-' + VK_GROUP, from_group: '1', message: vkText(html).slice(0, 15000), access_token: VK_TOKEN, v: '5.199' });
  const r = await fetch('https://api.vk.com/method/wall.post', { method: 'POST', body: p });
  const js = await r.json().catch(() => ({}));
  if (js.error) log('vk:', js.error.error_code, js.error.error_msg);
  await sleep(400);
  return js.response ? js.response.post_id : null;
}
async function publish(S, html, reply, useAi) {
  if (useAi) html = await AI.withLead(html, S, esc);
  const id = await tgSend(html + `\n\n<i>${DISCLAIMER}</i>`, reply);
  await vkPost(html + `\n\n${DISCLAIMER}`);
  S.pub.count++;
  return id;
}

// ---------- оформление ----------
function headline(c) {
  const pick = Object.entries(c.srcs).sort((a, b) => (TRUSTED.has(b[0]) - TRUSTED.has(a[0])) || (a[1].rumor - b[1].rumor) || a[1].ts - b[1].ts)[0];
  const tx = (pick && pick[1].tx) || c.first.tx;
  return tx.replace(/\s+/g, ' ').trim().slice(0, 360);
}
function sourcesLine(c) {
  const arr = Object.entries(c.srcs).sort((a, b) => a[1].ts - b[1].ts).slice(0, 7);
  return 'Источники: ' + arr.map(([ch, x]) => `<a href="${x.link}">${esc(src(ch))}</a>${x.rumor ? ' (со ссылкой на источники)' : ''}`).join(' · ');
}
function tags(tks) { return tks.length ? '\n' + tks.slice(0, 6).map(t => '#' + t).join(' ') : ''; }

// ---------- основной отбор ----------
async function run(S, clusters, items) {
  if (!ON) return;
  S.pub = S.pub || { day: '', count: 0, links: [], msg: {} };
  const today = new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10);
  if (S.pub.day !== today) { S.pub.day = today; S.pub.count = 0; }
  const done = new Set(S.pub.links);
  const now = Date.now() / 1000;
  let n = 0;
  const can = () => n < 6 && S.pub.count < PER_DAY;
  const mark = l => { done.add(l); S.pub.links.push(l); S.pub.links = S.pub.links.slice(-800); };
  // тексты источников (для заголовков)
  for (const it of items || []) for (const c of clusters) if (c.srcs[it.ch] && c.srcs[it.ch].link === it.link) c.srcs[it.ch].tx = it.tx;

  // 1) сделки инсайдеров и крупных акционеров (раскрытые)
  for (const it of items || []) {
    if (!can()) break;
    if (!it.tks.length || done.has(it.link) || now - it.ts > 3 * 3600 || (SRC[it.ch] || [])[1] === 'автор' || !INSIDER.test(it.tx)) continue;
    const buy = /купил|приобр|увеличил|нарастил/i.test(it.tx);
    mark(it.link); n++;
    await publish(S, `💼 <b>Сделка инсайдера / крупного акционера</b> — ${it.tks.map(t => '$' + t).join(' ')}\n${esc(it.tx.replace(/\s+/g, ' ').slice(0, 380))}\n\n` +
      `🔗 <a href="${it.link}">${esc(src(it.ch))}</a>\n` +
      `Как читать: ${buy ? 'покупки' : 'продажи'} менеджмента и крупных акционеров — публичная информация о том, как «свои» оценивают бумагу. ` +
      'Это не инсайдерская информация в смысле закона (224-ФЗ): сделка уже раскрыта.' + tags(it.tks));
  }

  const lastTs = c => Math.max(...Object.values(c.srcs).map(x => x.ts));
  const MKTY = /^(oil_|ruble_|cbr_|geo_)/;
  const fresh = clusters.filter(c => now - lastTs(c) < 3 * 3600)
    .sort((a, b) => (MKTY.test(a.key.split('|')[1]) && a.key[0] !== 'Р') - (MKTY.test(b.key.split('|')[1]) && b.key[0] !== 'Р') || Math.abs(b.score) - Math.abs(a.score));
  for (const c of fresh) {
    if (!can()) break;
    const [tk, ty] = c.key.split('|');
    const chs = Object.keys(c.srcs);
    const firstLink = c.first.link;
    c.pub = c.pub || {};
    const s = c.best && c.best.v ? Math.sign(c.best.v) : 0;
    const polit = chs.some(ch => (SRC[ch] || [])[1] === 'политик') || (chs.some(ch => POLIT.has(ch)) && tk === 'РЫНОК');
    const trustedFact = chs.some(ch => TRUSTED.has(ch) && !c.srcs[ch].rumor);
    const indepFacts = chs.filter(ch => !c.srcs[ch].rumor && (SRC[ch] || [])[1] !== 'автор').length;

    // 2) заявления официальных лиц и ведомств о рынке, экономике, геополитике
    if (polit && tk === 'РЫНОК' && IMPACT.mkt[typeKey(ty, s)] && !c.pub.done && !done.has(firstLink)) {
      c.pub.done = true; mark(firstLink); n++;
      const lines = mktLines(ty, s);
      c.pub.msg = await publish(S, `🏛 <b>Заявление</b> — ${esc(TYPE_RU[ty] || ty)}\n${esc(headline(c))}\n\n${sourcesLine(c)}\n\n<b>Как рынок реагировал на такие заявления (2017–2026):</b>\n${lines.map(esc).join('\n')}`, null, true);
      continue;
    }
    // 3) ранний сигнал (данные «источников») с оценкой вероятности подтверждения
    if (c.rumorOnly && tk !== 'РЫНОК' && ty !== 'tone' && !c.pub.rumor && STRONG_RUMOR.test(c.first.tx || '')) {
      const p = rumorProb(c);
      if (p >= 0.1 || chs.length >= 2) {
        c.pub.rumor = true; mark(firstLink); n++;
        c.pub.msg = await publish(S, `🕵️ <b>Ранний сигнал (не подтверждён)</b> — ${c.tks.map(t => '$' + t).join(' ')}: ${esc(TYPE_RU[ty] || ty)}\n${esc(headline(c))}\n\n${sourcesLine(c)}\n` +
          `Вероятность подтверждения: ~${Math.round(p * 100)}% — по тому, как часто подтверждались прошлые сообщения этих источников.\n\n<b>Если подтвердится, исторически:</b>\n${c.tks.slice(0, 4).map(t => esc(compLine(t, ty, s))).join('\n')}` + tags(c.tks));
      }
      continue;
    }
    // 4) подтверждение раннего сигнала
    if (c.pub.rumor && !c.pub.confirm && !c.rumorOnly) {
      c.pub.confirm = true; n++;
      const off = Object.entries(c.srcs).find(([, x]) => !x.rumor);
      await publish(S, `✔️ <b>Подтверждено</b> — ${c.tks.map(t => '$' + t).join(' ')}: ${esc(TYPE_RU[ty] || ty)}\n🔗 <a href="${off[1].link}">${esc(src(off[0]))}</a> · через ${Math.round((off[1].ts - c.ts) / 60)} мин после первого сообщения`, c.pub.msg);
      continue;
    }
    // 5) достоверная новость о компании или рынке
    if (c.pub.done || c.first.rumor && !trustedFact) continue;
    const reliable = trustedFact || indepFacts >= 2;
    const relevant = tk === 'РЫНОК' ? !!IMPACT.mkt[typeKey(ty, s)] && (c.official || trustedFact) : (ty !== 'tone' || Math.abs(c.score) >= 0.15);
    if (!reliable || !relevant || done.has(firstLink)) continue;
    if (tk !== 'РЫНОК' && ty !== 'tone' && Math.abs(c.score) < 0.05 && !c.official) continue; // типы без заметной исторической реакции — пропускаем
    c.pub.done = true; mark(firstLink); n++;
    const body = tk === 'РЫНОК' ? mktLines(ty, s).map(esc).join('\n') : c.tks.slice(0, 5).map(t => esc(compLine(t, ty, s))).join('\n');
    c.pub.msg = await publish(S, `${c.official ? '🏛' : '📰'} <b>${tk === 'РЫНОК' ? 'Рынок' : c.tks.map(t => '$' + t).join(' ')}</b> — ${esc(TYPE_RU[ty] || ty)}\n${esc(headline(c))}\n\n${sourcesLine(c)}\n\n<b>Влияние (история реакций 2017–2026):</b>\n${body}` + tags(c.tks), null, true);
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
