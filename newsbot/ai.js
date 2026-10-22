// ИИ-вступление к новостным постам паблика через шлюз Claude (формат Anthropic Messages API).
// Ключ — только из переменной окружения LLM_API_KEY (секрет GitHub), адрес — LLM_BASE_URL.
// Проверка: все числа и $ТИКЕРЫ во вступлении должны быть в исходном посте, иначе пост уходит шаблоном.
'use strict';
const BASE = String(process.env.LLM_BASE_URL || '').replace(/\/+$/, '');
const KEY = process.env.LLM_API_KEY || '';
const AUTH = (process.env.LLM_AUTH || 'bearer').toLowerCase();
const MODELS = [process.env.LLM_MODEL, 'claude-sonnet-4-5', 'claude-sonnet-4-20250514', 'claude-3-5-haiku-latest'].filter(Boolean);
const MAX = +(process.env.LLM_NEWS_PER_DAY || 45);
const ON = !!(BASE && KEY && process.env.LLM_OFF !== '1');
const SYSTEM = 'Ты редактор русскоязычного паблика о рынке акций Мосбиржи. Тебе дают готовый новостной пост с историей реакции рынка. ' +
  'Напиши вступление «Коротко»: 1–2 простых предложения (не пересказывай пост целиком: одна-две главные мысли, не больше трёх чисел) — что произошло и чем это исторически было для бумаги или рынка. ' +
  'Строго: только факты и числа из поста, ничего не добавляй; тикеры как в посте ($SBER); без призывов купить или продать, без прогнозов цены, ' +
  'без эмодзи, хештегов, HTML и Markdown. Не длиннее 300 знаков. Ответь только текстом вступления.';
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), '[ИИ]', ...a);
const plain = h => String(h).replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const nums = s => new Set((s.replace(/[  ]/g, '').match(/\d+(?:[.,]\d+)?/g) || []).map(x => x.replace(',', '.').replace(/^0+(?=\d)/, '')));
const tks = s => new Set(s.match(/\$[A-Z0-9]{2,8}/g) || []);

function check(lead, src) {
  const sn = nums(src), st = tks(src);
  const badN = [...nums(lead)].filter(x => !sn.has(x)), badT = [...tks(lead)].filter(x => !st.has(x));
  if (badN.length || badT.length) { log('вступление отброшено: лишние числа', badN.slice(0, 5), 'тикеры', badT.slice(0, 5)); return false; }
  const cyr = (lead.match(/[А-Яа-яЁё]/g) || []).length, let_ = (lead.match(/[A-Za-zА-Яа-яЁё]/g) || []).length;
  if (cyr < 0.5 * let_ || /^\s*(the user|i need|let me|okay|here)/i.test(lead)) { log('вступление отброшено: ответ не по-русски / рассуждения модели'); return false; }
  if (/(покупайте|продавайте|рекомендуем|советуем|стоит купить|стоит продать)/i.test(lead)) { log('вступление отброшено: похоже на рекомендацию'); return false; }
  return lead.length >= 30 && lead.length <= 450;
}

async function call(model, text, system = SYSTEM, maxTokens = 300) {
  const headers = { 'content-type': 'application/json', 'anthropic-version': '2023-06-01' };
  if (AUTH === 'x-api-key') headers['x-api-key'] = KEY; else headers.authorization = 'Bearer ' + KEY;
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), +(process.env.LLM_TIMEOUT_MS || 100000));
  try {
    const r = await fetch(BASE + '/v1/messages', { method: 'POST', headers, signal: ctl.signal,
      body: JSON.stringify({ model, max_tokens: maxTokens, temperature: 0.4, system, messages: [{ role: 'user', content: text.slice(0, 5000) }] }) });
    const body = await r.text();
    if (!r.ok) return { err: r.status, msg: body.slice(0, 200) };
    const js = JSON.parse(body);
    return { text: (js.content || []).filter(b => b.type === 'text').map(b => b.text).join('').trim() };
  } finally { clearTimeout(t); }
}

async function listModels() {
  const headers = { 'anthropic-version': '2023-06-01' };
  if (AUTH === 'x-api-key') headers['x-api-key'] = KEY; else headers.authorization = 'Bearer ' + KEY;
  try {
    const r = await fetch(BASE + '/v1/models?limit=100', { headers });
    if (!r.ok) return [];
    const js = await r.json();
    return (js.data || js.models || []).map(m => m && m.id).filter(Boolean);
  } catch (e) { log('список моделей недоступен:', e.message); return []; }
}
function pick(ms) {
  for (const p of ['sonnet', 'haiku', 'opus']) { const c = ms.filter(m => m.toLowerCase().includes(p)).sort().reverse(); if (c.length) return c; }
  return ms;
}

// общий запрос к шлюзу: выбор модели, дневной лимит, перебор моделей при «Model not found»
let DOWN = false; let RUN_N = 0; const RUN_MAX = +(process.env.LLM_PER_RUN || 3); // шлюз не ответил в этом запуске — дальше публикуем без ИИ, чтобы не тормозить ленту
async function ask(S, text, system, maxTokens) {
  if (!ON || DOWN || RUN_N >= RUN_MAX) return null;
  RUN_N++;
  const day = new Date().toISOString().slice(0, 10);
  const st = S.ai = S.ai || {};
  if (st.day !== day) { st.day = day; st.n = 0; }
  if (st.n >= MAX) return null;
  if (process.env.LLM_MODEL && st.model !== process.env.LLM_MODEL) { delete st.model; delete st.cands; }
  if (!st.model && !process.env.LLM_MODEL && !st.listed) {
    st.listed = 1;
    const found = pick(await listModels());
    log('модели шлюза:', found.slice(0, 8).join(', ') || '—');
    if (found.length) { st.model = found[0]; st.cands = found.slice(0, 4); }
  }
  const models = (st.model ? [st.model] : []).concat((st.cands || []).concat(MODELS).filter(m => m !== st.model));
  for (const m of models) {
    st.n++;
    let res;
    const t0 = Date.now();
    try { res = await call(m, text, system, maxTokens); } catch (e) { DOWN = true; log('шлюз не ответил за', Math.round((Date.now() - t0) / 1000), 'с:', e.message, '— до конца запуска без ИИ'); return null; }
    log('ответ модели', m, 'за', Math.round((Date.now() - t0) / 1000), 'с');
    if (res.err) {
      log('модель', m, 'ошибка', res.err, res.msg);
      if ((res.err === 400 || res.err === 404) && /model/i.test(res.msg)) continue;
      return null;
    }
    st.model = m;
    return res.text;
  }
  return null;
}

async function lead(html, S) {
  const src = plain(html);
  const txt = await ask(S, src, SYSTEM, 300);
  if (!txt) return null;
  const out = txt.replace(/[*#]/g, '').replace(/\s+/g, ' ').trim().replace(/^[«"]|[»"]$/g, '').replace(/^(🧠\s*)?(Коротко|Кратко|Вступление)\s*[:—-]\s*/i, '');
  return check(out, src) ? out : null;
}

// Новость целиком своими словами + подпись-мем для картинки + короткий факт для картинки.
const NEWS_SYSTEM = 'Ты редактор русскоязычного паблика о рынке акций Мосбиржи. Тебе дают исходный текст новости из Telegram-канала и контекст (тикеры, тип события). ' +
  'Верни строго JSON без пояснений: {"relevant": true|false, "tickers": ["..."], "retell": "...", "fact": "...", "caption": "..."}. ' +
  'relevant — влияет ли новость на российский фондовый рынок (акции Мосбиржи, ОФЗ, рубль, сырьё, ставка Банка России); новости о зарубежных компаниях и иностранных центробанках без связи с Россией, награды, протокольные встречи, культура, спорт — false. ' +
  'tickers — только те тикеры из списка кандидатов, о компаниях которых новость НАПРЯМУЮ (компания — участник события); случайные упоминания, тёзки (самолёт как техника, «пик», «полюс» как слово), списки в конце поста — не включай. ' +
  'retell — новость ЦЕЛИКОМ своими словами: все ключевые факты, цифры, даты, кто что сказал или сделал, 2–5 предложений, до 700 знаков; не копируй фразы источника дословно; ' +
  'без эмодзи, хештегов, ссылок, HTML, Markdown и рекламы канала-источника. ' +
  'fact — суть одной фразой до 160 знаков. ' +
  'caption — короткая остроумная подпись-мем к картинке (до 90 знаков) про ситуацию глазами частного инвестора, без оскорблений, без имён реальных людей и без призывов купить или продать; ' +
  'если новость о гибели людей, катастрофах, атаках, болезнях или другой трагедии — caption нейтральная и серьёзная, без шуток. ' +
  'Только факты из исходного текста: не добавляй числа, даты и тикеры, которых там нет.';

async function newsPack(S, srcText, ctx, cands) {
  const src = plain(srcText);
  const txt = await ask(S, `Контекст: ${ctx}\n\nИсходный текст новости:\n${src}`, NEWS_SYSTEM, 900);
  if (!txt) return null;
  let js;
  try { js = JSON.parse(txt.slice(txt.indexOf('{'), txt.lastIndexOf('}') + 1)); } catch (e) { log('ответ не JSON'); return null; }
  const clean = v => String(v || '').replace(/[*#]/g, '').replace(/\s+/g, ' ').trim();
  const out = { retell: clean(js.retell), fact: clean(js.fact), caption: clean(js.caption).replace(/^[«"]|[»"]$/g, ''),
    relevant: js.relevant !== false, tickers: Array.isArray(js.tickers) ? js.tickers.map(t => String(t).replace(/^\$/, '').toUpperCase()).filter(t => !cands || cands.includes(t)) : null };
  const base = src + ' ' + ctx;
  const ok = v => { const cy = (v.match(/[А-Яа-яЁё]/g) || []).length, lt = (v.match(/[A-Za-zА-Яа-яЁё]/g) || []).length; if (cy < 0.5 * lt) return false; const sn = nums(base), st = tks(base); return ![...nums(v)].some(x => !sn.has(x)) && ![...tks(v)].some(x => !st.has(x)) && !/(покупайте|продавайте|рекомендуем|советуем|стоит купить|стоит продать)/i.test(v); };
  for (const k of ['retell', 'fact', 'caption']) if (out[k] && !ok(out[k])) { log('отброшено', k, ': числа/тикеры не из новости или похоже на совет'); out[k] = ''; }
  if (out.retell.length < 40) out.retell = '';
  if (out.caption.length > 120) out.caption = '';
  log('новость:', out.relevant ? 'по делу' : 'НЕ влияет на рынок', '| тикеры:', (out.tickers || []).join(',') || '—', '| пересказ:', out.retell ? 'да' : 'нет', '| подпись:', out.caption || '—');
  return out;
}

async function withLead(html, S, esc) {
  const ld = await lead(html, S).catch(e => { log(e.message); return null; });
  if (!ld) return html;
  const i = html.indexOf('\n');
  return i < 0 ? html + `\n\n🧠 <i>Коротко: ${esc(ld)}</i>` : html.slice(0, i) + `\n\n🧠 <i>Коротко: ${esc(ld)}</i>\n\n` + html.slice(i).replace(/^\n+/, '');
}

module.exports = { withLead, newsPack, check, ON };
