// ИИ-вступление к новостным постам паблика через шлюз Claude (формат Anthropic Messages API).
// Ключ — только из переменной окружения LLM_API_KEY (секрет GitHub), адрес — LLM_BASE_URL.
// Проверка: все числа и $ТИКЕРЫ во вступлении должны быть в исходном посте, иначе пост уходит шаблоном.
'use strict';
const BASE = String(process.env.LLM_BASE_URL || '').replace(/\/+$/, '');
const KEY = process.env.LLM_API_KEY || '';
const AUTH = (process.env.LLM_AUTH || 'bearer').toLowerCase();
const MODELS = [process.env.LLM_MODEL, 'claude-sonnet-4-5', 'claude-sonnet-4-20250514', 'claude-3-5-haiku-latest'].filter(Boolean);
const MAX = +(process.env.LLM_NEWS_PER_DAY || 30);
const ON = !!(BASE && KEY && process.env.LLM_OFF !== '1');
const SYSTEM = 'Ты редактор русскоязычного паблика о рынке акций Мосбиржи. Тебе дают готовый новостной пост с историей реакции рынка. ' +
  'Напиши вступление «Коротко»: 1–2 простых предложения — что произошло и чем это исторически было для бумаги или рынка. ' +
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
  if (/(покупайте|продавайте|рекомендуем|советуем|стоит купить|стоит продать)/i.test(lead)) { log('вступление отброшено: похоже на рекомендацию'); return false; }
  return lead.length >= 30 && lead.length <= 450;
}

async function call(model, text) {
  const headers = { 'content-type': 'application/json', 'anthropic-version': '2023-06-01' };
  if (AUTH === 'x-api-key') headers['x-api-key'] = KEY; else headers.authorization = 'Bearer ' + KEY;
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 45000);
  try {
    const r = await fetch(BASE + '/v1/messages', { method: 'POST', headers, signal: ctl.signal,
      body: JSON.stringify({ model, max_tokens: 300, temperature: 0.3, system: SYSTEM, messages: [{ role: 'user', content: text.slice(0, 5000) }] }) });
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

async function lead(html, S) {
  if (!ON) return null;
  const day = new Date().toISOString().slice(0, 10);
  const st = S.ai = S.ai || {};
  if (st.day !== day) { st.day = day; st.n = 0; }
  if (st.n >= MAX) return null;
  const src = plain(html);
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
    try { res = await call(m, src); } catch (e) { log('шлюз недоступен:', e.message); return null; }
    if (res.err) {
      log('модель', m, 'ошибка', res.err, res.msg);
      if ((res.err === 400 || res.err === 404) && /model/i.test(res.msg)) continue;
      return null;
    }
    st.model = m;
    const out = res.text.replace(/[*#]/g, '').replace(/\s+/g, ' ').trim().replace(/^[«"]|[»"]$/g, '');
    return check(out, src) ? out : null;
  }
  return null;
}

async function withLead(html, S, esc) {
  const ld = await lead(html, S).catch(e => { log(e.message); return null; });
  if (!ld) return html;
  const i = html.indexOf('\n');
  return i < 0 ? html + `\n\n🧠 <i>Коротко: ${esc(ld)}</i>` : html.slice(0, i) + `\n\n🧠 <i>Коротко: ${esc(ld)}</i>\n\n` + html.slice(i).replace(/^\n+/, '');
}

module.exports = { withLead, check, ON };
