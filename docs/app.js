/* Денежный пульс — деньги в акциях Мосбиржи в течение дня.
   Источник: открытый API Мосбиржи (ISS). Бесплатный доступ отдаёт котировки с задержкой ~15 минут,
   позиции физ. и юр. лиц по фьючерсам — с задержкой 14 дней. Всё считается в браузере, сервер не нужен. */
'use strict';
const ISS = 'https://iss.moex.com/iss/';
const SEC = {"LKOH":"oil","ROSN":"oil","TATN":"oil","TATNP":"oil","SNGS":"oil","SNGSP":"oil","SIBN":"oil","BANEP":"oil","NVTK":"oil","GAZP":"oil","TRNFP":"oil","RNFT":"oil","GMKN":"metal","CHMF":"metal","NLMK":"metal","MAGN":"metal","RUAL":"metal","ALRS":"metal","PLZL":"metal","UGLD":"metal","MTLR":"metal","RASP":"metal","SELG":"metal","ENPG":"metal","SBER":"bank","SBERP":"bank","VTBR":"bank","T":"bank","SVCB":"bank","BSPB":"bank","CBOM":"bank","MOEX":"bank","DOMRF":"bank","SMLT":"devel","PIKK":"devel","LSRG":"devel","MGNT":"retail","X5":"retail","OZON":"retail","LENT":"retail","MVID":"retail","BELU":"retail","GCHE":"retail","YDEX":"tech","VKCO":"tech","POSI":"tech","HEAD":"tech","ASTR":"tech","MTSS":"tech","RTKM":"tech","IRAO":"power","HYDR":"power","FEES":"power","UPRO":"power","MSNG":"power","MSRS":"power","MRKP":"power","MRKC":"power","PHOR":"chem","AKRN":"chem","AFLT":"transport","FLOT":"transport","NMTP":"transport","MBNK":"bank","LEAS":"bank","RENI":"bank","SFIN":"bank","CARM":"bank","ZAYM":"bank","MGKL":"bank","SPBE":"bank","ETLN":"devel","GLRX":"devel","FIXR":"retail","OKEY":"retail","APTK":"retail","SVET":"retail","CNRU":"tech","DIAS":"tech","DELI":"tech","IVAT":"tech","SOFL":"tech","DATA":"tech","WUSH":"tech","ELMT":"tech","ELFV":"power","LSNG":"power","MRKU":"power","MRKV":"power","MRKZ":"power","MRKS":"power","MRKY":"power","OGKB":"power","TGKA":"power","RTSB":"power","TGKN":"power","PMSB":"power","VRSB":"power","MRSB":"power","TORS":"power","KZOS":"chem","NKNC":"chem","KAZT":"chem","VSMO":"metal","CHMK":"metal","TRMK":"metal","ROLO":"metal","YAKG":"oil","KRKNP":"oil","FESH":"transport","UWGN":"transport","EUTR":"transport","GTRK":"transport","ABIO":"consumer","AQUA":"consumer","GEMC":"consumer","HNFG":"consumer","MDMG":"consumer","PRMD":"consumer","RAGR":"consumer","NKHP":"consumer","KROT":"consumer","OZPH":"consumer","RKKE":"industry","SVAV":"industry","UNAC":"industry","IRKT":"industry","NSVZ":"industry","MSTT":"industry","BAZA":"tech","VSEH":"retail","BTBR":"tech","ABRD":"consumer","EVRZ":"metal","INCB":"industry","URKZ":"metal","USBN":"bank","MAGE":"power","KMAZ":"industry","MTLRP":"metal","LSNGP":"power","RTKMP":"tech","NKNCP":"chem","BANE":"oil","SVETP":"retail","KROTP":"consumer","PMSBP":"power","BSPBP":"bank"};
const SEC_RU = { oil: 'Нефтегаз', metal: 'Металлы и добыча', bank: 'Финансы', devel: 'Девелоперы', retail: 'Ритейл', tech: 'IT и связь', power: 'Энергетика', chem: 'Химия', transport: 'Транспорт', consumer: 'Потребсектор', industry: 'Машиностроение', other: 'Прочие' };

// ---------- утилиты ----------
const $ = s => document.querySelector(s);
const nf = (x, d = 0) => x == null || !isFinite(x) ? '—' : x.toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }).replace(/ /g, ' ').replace('-', '−');
const rub = x => { if (x == null || !isFinite(x)) return '—'; const a = Math.abs(x), s = x < 0 ? '−' : ''; return a >= 1e12 ? s + nf(a / 1e12, 2) + ' трлн ₽' : a >= 1e9 ? s + nf(a / 1e9, 1) + ' млрд ₽' : a >= 1e6 ? s + nf(a / 1e6, 0) + ' млн ₽' : s + nf(a / 1e3, 0) + ' тыс ₽'; };
const rubS = x => (x > 0 ? '+' : '') + rub(x);
const pct = (x, d = 1) => x == null || !isFinite(x) ? '—' : (x > 0 ? '+' : x < 0 ? '−' : '') + nf(Math.abs(x * 100), d) + '%';
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } }, set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* хранилище недоступно */ } } };
function msk(d = new Date()) {
  const p = Object.fromEntries(new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).formatToParts(d).map(x => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, min: +p.hour * 60 + +p.minute, hm: `${p.hour}:${p.minute}`, hms: `${p.hour}:${p.minute}:${p.second}`, dow: new Date(`${p.year}-${p.month}-${p.day}T12:00:00Z`).getUTCDay() };
}
const ddmm = d => d ? `${d.slice(8, 10)}.${d.slice(5, 7)}` : '—';
function addDays(d, n) { const t = new Date(d + 'T12:00:00Z'); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); }
async function iss(path, params = {}, tries = 3) {
  const u = new URL(ISS + path);
  u.searchParams.set('iss.meta', 'off');
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(u, { cache: 'no-store', signal: AbortSignal.timeout ? AbortSignal.timeout(20000) : undefined });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } catch (e) { if (i === tries - 1) throw e; await sleep(600 * (i + 1)); }
  }
}
const rows = (js, name) => { const b = js && js[name]; return b ? b.data.map(r => Object.fromEntries(b.columns.map((c, i) => [c, r[i]]))) : []; };
async function pool(items, n, fn) { const out = new Array(items.length); let i = 0; await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const k = i++; try { out[k] = await fn(items[k], k); } catch (e) { out[k] = null; } } })); return out; }

// Доля дневного оборота, которая обычно проходит к этому времени (утро 6:50–9:50 ≈3%, основная сессия ≈90%, вечер ≈7%)
function dayFrac(min) {
  if (min < 410) return 0.01;
  if (min < 590) return 0.01 + 0.02 * (min - 410) / 180;
  if (min < 600) return 0.03;
  if (min < 1120) return 0.03 + 0.90 * (min - 600) / 520;
  if (min < 1145) return 0.93;
  if (min < 1430) return 0.93 + 0.07 * (min - 1145) / 285;
  return 1;
}
const BAR_EXP = 0.9 / 52; // доля дня в одной 10-минутной свече основной сессии

// ---------- состояние ----------
const S = { shares: [], byT: {}, avg: {}, avgDays: 0, snaps: store.get('snaps', { date: '', list: [] }), bars: {}, sort: { k: 'val', dir: -1 }, showAll: false, q: '', live: true, view: 'money', fy: null, fyGroup: 'all', loading: false };

// ---------- данные: акции ----------
async function loadShares() {
  const js = await iss('engines/stock/markets/shares/boards/TQBR/securities.json', {
    'iss.only': 'securities,marketdata', 'securities.columns': 'SECID,SHORTNAME,SECNAME,INSTRID',
    'marketdata.columns': 'SECID,LAST,LASTTOPREVPRICE,VALTODAY,NUMTRADES,UPDATETIME,SYSTIME'
  });
  const meta = {};
  for (const r of rows(js, 'securities')) if ((r.INSTRID || 'EQIN') === 'EQIN') meta[r.SECID] = r;
  const out = [];
  let sys = '';
  for (const r of rows(js, 'marketdata')) {
    const m = meta[r.SECID]; if (!m) continue;
    sys = r.SYSTIME || sys;
    out.push({ t: r.SECID, name: m.SHORTNAME || m.SECNAME || r.SECID, last: r.LAST, chg: (r.LASTTOPREVPRICE || 0) / 100, val: r.VALTODAY || 0, num: r.NUMTRADES || 0, time: r.UPDATETIME, sec: SEC[r.SECID] || 'other' });
  }
  S.shares = out; S.byT = Object.fromEntries(out.map(x => [x.t, x])); S.sys = sys;
}
// средний оборот за 15 торговых дней (история по датам, кэш в браузере — прошлые дни не меняются)
async function loadAvg(onProgress) {
  const today = msk().date, need = 15, cache = store.get('hist', {});
  const got = [];
  let d = addDays(today, -1), guard = 0;
  while (got.length < need && guard++ < 35) {
    const dow = new Date(d + 'T12:00:00Z').getUTCDay();
    if (dow !== 0 && dow !== 6) {
      if (!cache[d]) {
        const map = {}; let start = 0, total = 1;
        while (start < total) {
          const js = await iss('history/engines/stock/markets/shares/boards/TQBR/securities.json', { 'iss.only': 'history,history.cursor', 'history.columns': 'SECID,VALUE', date: d, start });
          for (const r of rows(js, 'history')) if (r.VALUE) map[r.SECID] = r.VALUE;
          const cur = rows(js, 'history.cursor')[0] || { TOTAL: 0, PAGESIZE: 100 };
          total = cur.TOTAL; start += cur.PAGESIZE || 100;
        }
        cache[d] = map;
      }
      if (Object.keys(cache[d]).length > 10) got.push(d);
      onProgress && onProgress(got.length, need);
    }
    d = addDays(d, -1);
  }
  for (const k of Object.keys(cache)) if (!got.includes(k) && k < addDays(today, -60)) delete cache[k];
  store.set('hist', cache);
  const avg = {};
  for (const day of got) for (const [t, v] of Object.entries(cache[day])) avg[t] = (avg[t] || 0) + v / got.length;
  S.avg = avg; S.avgDays = got.length;
}
// 10-минутные свечи сегодня по самым торгуемым бумагам: темп денег сейчас и «пульс» рынка
async function loadBars(list) {
  const today = msk().date;
  const res = await pool(list, 6, async t => {
    const js = await iss(`engines/stock/markets/shares/boards/TQBR/securities/${t}/candles.json`, { interval: 10, from: today, 'candles.columns': 'begin,value,close' });
    return rows(js, 'candles').filter(b => b.begin.slice(0, 10) === today);
  });
  list.forEach((t, i) => { if (res[i]) S.bars[t] = res[i]; });
}

// ---------- расчёты ----------
function enrich() {
  const m = msk(), f = dayFrac(m.min);
  const snaps = S.snaps.list;
  const now = Date.now();
  let base = null;
  for (const s of snaps) if (now - s.ts >= 14 * 60e3) base = s;
  if (!base && snaps.length && now - snaps[0].ts >= 60e3) base = snaps[0];
  const dMin = base ? Math.max(1, Math.round((now - base.ts) / 60e3)) : 0;
  for (const x of S.shares) {
    const a = S.avg[x.t];
    x.avg = a || 0;
    x.x = a ? x.val / (a * f) : null;
    x.d15 = base && base.v[x.t] != null ? Math.max(0, x.val - base.v[x.t]) : null;
    const b = S.bars[x.t];
    if (b && b.length && a) {
      const done = b.length >= 4 ? b.slice(-4, -1) : b.slice(0, -1); // три последние законченные свечи
      const v30 = done.reduce((s, z) => s + (z.value || 0), 0);
      x.v30 = v30; x.r30 = done.length ? v30 / (a * BAR_EXP * done.length) : null;
    } else { x.v30 = null; x.r30 = null; }
  }
  S.dMin = dMin; S.frac = f;
}
function snapshot() {
  const m = msk();
  if (S.snaps.date !== m.date) S.snaps = { date: m.date, list: [] };
  const v = {}; let tot = 0;
  for (const x of S.shares) { v[x.t] = x.val; tot += x.val; }
  const list = S.snaps.list;
  if (!list.length || tot !== list[list.length - 1].tot) list.push({ ts: Date.now(), hm: m.hm, tot, v });
  while (list.length > 150) list.shift();
  store.set('snaps', S.snaps);
}

// ---------- отрисовка: деньги ----------
function renderMoney() {
  const sh = S.shares, m = msk();
  const tot = sh.reduce((s, x) => s + x.val, 0);
  const avgTot = sh.reduce((s, x) => s + (x.avg || 0), 0);
  $('#totalVal').textContent = rub(tot);
  $('#totalSub').textContent = `${sh.filter(x => x.val > 0).length} акций · данные на ${S.sys ? S.sys.slice(11, 16) : m.hm} МСК`;
  const x = avgTot ? tot / (avgTot * S.frac) : null;
  $('#normX').innerHTML = x ? `<span class="${x >= 1.3 ? 'am' : ''}">×${nf(x, 2)}</span>` : '—';
  $('#normH').textContent = S.avgDays ? `обычно к ${m.hm} проходит ${rub(avgTot * S.frac)}` : 'считаю средний оборот…';
  const list = S.snaps.list;
  if (list.length >= 2) {
    const last = list[list.length - 1]; let base = list[0];
    for (const s of list) if (last.ts - s.ts >= 14 * 60e3) base = s;
    const dm = Math.max(1, Math.round((last.ts - base.ts) / 60e3));
    $('#flow15').textContent = rub(last.tot - base.tot);
    $('#flow15h').textContent = `за ${dm} мин · ${rub((last.tot - base.tot) / dm)} в минуту`;
  }
  const upN = sh.filter(x => x.chg > 0).length, dnN = sh.filter(x => x.chg < 0).length;
  $('#breadth').innerHTML = `<span class="up">${upN}</span> / <span class="dn">${dnN}</span>`;
  // секторы
  const secs = {};
  for (const x of sh) secs[x.sec] = (secs[x.sec] || 0) + x.val;
  const sl = Object.entries(secs).sort((a, b) => b[1] - a[1]);
  const mx = sl.length ? sl[0][1] : 1;
  $('#sectors').innerHTML = sl.map(([k, v]) => `<div class="srow"><span>${SEC_RU[k] || k}</span><span class="sbar"><i style="width:${(v / mx * 100).toFixed(1)}%"></i></span><span class="mono">${rub(v)}</span></div>`).join('');
  renderTable();
  drawPulse();
}
function renderTable() {
  const q = S.q.trim().toLowerCase();
  let list = S.shares.filter(x => x.val > 0 && (!q || x.t.toLowerCase().includes(q) || x.name.toLowerCase().includes(q)));
  const { k, dir } = S.sort;
  list.sort((a, b) => { const va = a[k], vb = b[k]; if (k === 't') return dir * va.localeCompare(vb); return dir * ((va ?? -Infinity) - (vb ?? -Infinity)); });
  const n = S.showAll || q ? list.length : 30;
  $('#more').hidden = S.showAll || !!q || list.length <= 30;
  document.querySelectorAll('#tbl th').forEach(th => th.classList.toggle('sorted', th.dataset.sort === k));
  $('#tbl tbody').innerHTML = list.slice(0, n).map(x => {
    const xb = x.x == null ? '—' : `<span class="xb ${x.x >= 2.5 ? 'hot' : x.x <= 0.4 ? 'cold' : ''}">${x.x >= 2.5 ? '▲' : x.x <= 0.4 ? '▼' : ''}×${nf(x.x, x.x >= 10 ? 0 : 1)}</span>`;
    return `<tr tabindex="0" data-t="${x.t}"><td class="l"><span class="tk">${x.t}</span><span class="nm">${esc(x.name)}</span></td><td>${rub(x.val)}</td><td>${xb}</td><td class="hide-s">${x.d15 == null ? '—' : rub(x.d15)}</td><td class="${x.chg > 0 ? 'up' : x.chg < 0 ? 'dn' : ''}">${pct(x.chg)}</td></tr>`;
  }).join('') || '<tr><td class="l" colspan="5">Ничего не найдено</td></tr>';
}
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
function prepCanvas(cv) { const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight; cv.width = w * dpr; cv.height = h * dpr; const c = cv.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, w, h); return { c, w, h }; }
// Пульс: 10-минутный оборот по самым торгуемым акциям (≈90% денег рынка) и средний темп
function drawPulse() {
  const cv = $('#pulse'); const { c, w, h } = prepCanvas(cv);
  const agg = {};
  for (const b of Object.values(S.bars)) for (const z of b) { const k = z.begin.slice(11, 16); agg[k] = (agg[k] || 0) + (z.value || 0); }
  const keys = Object.keys(agg).sort();
  if (!keys.length) { c.fillStyle = css('--muted'); c.font = '13px Onest, sans-serif'; c.fillText('Торгов сегодня ещё не было или данные загружаются', 12, h / 2); return; }
  const tracked = Object.keys(S.bars).reduce((s, t) => s + (S.avg[t] || 0), 0);
  const exp = tracked * BAR_EXP;
  const vals = keys.map(k => agg[k]); const mx = Math.max(exp * 1.2, ...vals);
  const pad = 22, bw = Math.max(2, (w - 8) / Math.max(keys.length, 30) - 2);
  c.strokeStyle = css('--line'); c.lineWidth = 1;
  c.beginPath(); c.moveTo(0, h - pad + .5); c.lineTo(w, h - pad + .5); c.stroke();
  keys.forEach((k, i) => {
    const v = agg[k], bh = (h - pad - 8) * v / mx, x = 4 + i * (bw + 2);
    c.fillStyle = exp && v >= exp * 2 ? css('--amber') : css('--accent');
    c.globalAlpha = i === keys.length - 1 ? .45 : 1;
    c.fillRect(x, h - pad - bh, bw, bh);
    c.globalAlpha = 1;
    if (k.endsWith(':00') && i % 6 === 0) { c.fillStyle = css('--muted'); c.font = '11px "JetBrains Mono", monospace'; c.fillText(k, x, h - 6); }
  });
  if (exp) { const y = h - pad - (h - pad - 8) * exp / mx; c.setLineDash([4, 4]); c.strokeStyle = css('--muted'); c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); c.setLineDash([]); c.fillStyle = css('--muted'); c.font = '11px Onest, sans-serif'; c.fillText('обычный темп', w - 92, y - 4); }
  $('#pulseHint').textContent = `10-минутные свечи, ${Object.keys(S.bars).length} самых торгуемых акций; последняя свеча ещё идёт`;
}

// ---------- поведение денег: что может стоять за движением ----------
// Цена против открытого интереса фьючерса на акцию (классическая матрица), перевес сделок против цены, оборот без движения цены.
const FUT2STOCK = { SBRF: 'SBER', SBPR: 'SBERP', GAZR: 'GAZP', SNGR: 'SNGS', SNGP: 'SNGSP', MTSI: 'MTSS', NOTKM: 'NVTK', TRNF: 'TRNFP', TATP: 'TATNP' };
async function loadOI() {
  const js = await iss('engines/futures/markets/forts/securities.json', { 'iss.only': 'securities,marketdata', 'securities.columns': 'SECID,ASSETCODE,PREVOPENPOSITION', 'marketdata.columns': 'SECID,OPENPOSITION' });
  const now = Object.fromEntries(rows(js, 'marketdata').map(r => [r.SECID, r.OPENPOSITION]));
  const agg = {};
  for (const r of rows(js, 'securities')) {
    const cur = now[r.SECID], prev = r.PREVOPENPOSITION; if (!r.ASSETCODE || cur == null || !prev) continue;
    const t = FUT2STOCK[r.ASSETCODE] || r.ASSETCODE; const g = agg[t] = agg[t] || [0, 0]; g[0] += cur; g[1] += prev;
  }
  S.oi = Object.fromEntries(Object.entries(agg).filter(([, [, p]]) => p >= 1000).map(([t, [c, p]]) => [t, c / p - 1]));
  S.oiAt = Date.now();
}
function behavior(x, flow) {
  const out = [], chg = x.chg || 0, oi = S.oi ? S.oi[x.t] : null;
  if (oi != null && Math.abs(oi) >= 0.03 && Math.abs(chg) >= 0.005) {
    const o = pct(oi, 0);
    if (chg > 0 && oi < 0) out.push(`похоже на закрытие шортов: цена растёт, а открытый интерес по фьючерсу ${o} — рост во многом за счёт откупа коротких позиций и может быстро выдохнуться`);
    else if (chg > 0) out.push(`новые покупки: растут и цена, и открытый интерес по фьючерсу (${o}) — в бумагу заходят новые деньги`);
    else if (oi > 0) out.push(`новые шорты: цена падает, а открытый интерес по фьючерсу ${o} — продавцы наращивают короткие позиции`);
    else out.push(`закрытие лонгов: снижаются и цена, и открытый интерес (${o}) — фиксация прибыли, давление может ослабнуть`);
  }
  if (flow != null) {
    if (flow >= 0.2 && chg <= -0.005) out.push('покупки по рынку не поднимают цену — крупный продавец стоит лимитными заявками (поглощение)');
    else if (flow <= -0.2 && chg >= 0.005) out.push('продажи по рынку не роняют цену — кто-то крупный выкупает предложение лимитками');
  }
  const low = x.x != null && x.x <= 0.35;
  const xx = low ? 0 : Math.max(x.x || 0, x.r30 || 0);
  if (xx >= 2.5) {
    if (chg <= -0.04 && (flow == null || flow < 0)) out.push('обвал на большом объёме — возможны вынужденные продажи (маржин-коллы)');
    else if (Math.abs(chg) < 0.01 && out.length < 2) out.push('большой оборот почти без движения цены — похоже на перекладку крупного пакета или борьбу за уровень');
    else if (chg >= 0.05 && out.length < 2) out.push('сильный импульс на большом объёме — проверьте новости: без повода такие движения часто частично откатываются');
  }
  if (x.x != null && x.x <= 0.35 && out.length < 2) out.push('интерес к бумаге пропал — крупные участники ждут, движение цены на малом объёме ненадёжно');
  return out.slice(0, 2);
}

// ---------- всплески ----------
function renderSpikes() {
  const up = [], dn = [];
  for (const x of S.shares) {
    if (!x.avg || x.avg < 20e6) continue;
    const why = [];
    if (x.r30 != null && x.r30 >= 3 && x.v30 >= 20e6) why.push(`сейчас ×${nf(x.r30, 1)}: ${rub(x.v30)} за 30 мин`);
    if (x.x != null && x.x >= 2.5 && x.val >= 30e6) why.push(`за день ×${nf(x.x, 1)}: ${rub(x.val)}`);
    if (why.length) up.push({ x, score: Math.max(x.r30 || 0, x.x || 0), why, bh: behavior(x) });
    if (x.x != null && x.x <= 0.35 && x.avg >= 150e6 && S.frac > 0.15) dn.push({ x, score: x.x, why: [`за день ×${nf(x.x, 2)}: ${rub(x.val)} при обычных ${rub(x.avg * S.frac)} к этому времени`], bh: behavior(x) });
  }
  up.sort((a, b) => b.score - a.score); dn.sort((a, b) => a.score - b.score);
  const li = (o, cls, arrow) => `<li tabindex="0" data-t="${o.x.t}"><span class="x ${cls}">${arrow}×${nf(o.score, o.score >= 10 ? 0 : o.score < 1 ? 2 : 1)}</span><span><span class="tk">${o.x.t}</span> <span class="hint">${esc(o.x.name)}</span></span><span class="${o.x.chg > 0 ? 'up' : o.x.chg < 0 ? 'dn' : ''} mono">${pct(o.x.chg)}</span><span class="why">${o.why.join(' · ')}</span>${o.bh.length ? `<span class="bh">🔎 ${esc(o.bh.join('; '))}</span>` : ''}</li>`;
  $('#spUp').innerHTML = up.slice(0, 25).map(o => li(o, 'up', '▲')).join('') || '<li class="empty">Сейчас резких всплесков нет</li>';
  $('#spDn').innerHTML = dn.slice(0, 15).map(o => li(o, 'dn', '▼')).join('') || '<li class="empty">Нет ликвидных бумаг с провалом оборота</li>';
  $('#spikeCount').textContent = up.length ? String(up.length) : '';
  $('#upHint').textContent = S.avgDays ? `норма — средний оборот за ${S.avgDays} дней` : '';
  notifySpikes(up);
}
// уведомления о новых всплесках (если пользователь разрешил)
function notifySpikes(up) {
  const seen = store.get('seenSpikes', { date: '', t: [] }), m = msk();
  if (seen.date !== m.date) { seen.date = m.date; seen.t = []; }
  const fresh = up.filter(o => o.x.r30 >= 3 && !seen.t.includes(o.x.t));
  if (fresh.length && 'Notification' in window && Notification.permission === 'granted' && document.hidden) {
    try { new Notification('Всплеск объёма', { body: fresh.slice(0, 3).map(o => `${o.x.t} ${o.why[0]}`).join('\n'), icon: 'icon-192.png', tag: 'spike' }); } catch (e) { /* без уведомлений */ }
  }
  seen.t.push(...fresh.map(o => o.x.t)); store.set('seenSpikes', seen);
}

// ---------- карточка бумаги ----------
async function openShare(t) {
  const x = S.byT[t]; if (!x) return;
  const dlg = $('#sheet');
  $('#shTitle').textContent = `${t} · ${x.name}`;
  $('#shSub').textContent = `${SEC_RU[x.sec] || 'Прочие'} · цена ${nf(x.last, x.last >= 100 ? 1 : 2)} ₽ (${pct(x.chg)})`;
  $('#shBody').innerHTML = `<div class="kv"><div>Оборот за день<b>${rub(x.val)}</b></div><div>К норме на это время<b>${x.x ? '×' + nf(x.x, 2) : '—'}</b></div><div>За последние 30 мин<b>${x.v30 != null ? rub(x.v30) : '—'}</b></div><div>Сделок<b>${nf(x.num)}</b></div></div>
    ${(b => b.length ? `<p class="bh">🔎 ${esc(b.join('; '))}</p>` : '')(behavior(x))}
    <div class="label">Оборот по 10 минут сегодня, пунктир — обычный темп</div><canvas id="shChart" height="180"></canvas>
    <div id="bs"><button class="btn" id="bsBtn" type="button">Посчитать покупки и продажи за день</button><p class="hint">Разберу все сделки дня по ${t}: кто был инициатором — покупатель или продавец. Для самых ликвидных бумаг это до минуты загрузки.</p></div>`;
  if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
  if (!S.bars[t]) { try { await loadBars([t]); enrich(); } catch (e) { /* без свечей */ } }
  drawShare(t);
  $('#bsBtn').onclick = () => buySell(t);
}
function drawShare(t) {
  const cv = $('#shChart'); if (!cv) return;
  const { c, w, h } = prepCanvas(cv);
  const b = S.bars[t] || [], exp = (S.avg[t] || 0) * BAR_EXP;
  if (!b.length) { c.fillStyle = css('--muted'); c.font = '13px Onest, sans-serif'; c.fillText('Свечей за сегодня нет', 10, h / 2); return; }
  const mx = Math.max(exp * 1.2, ...b.map(z => z.value || 0)), pad = 20, bw = Math.max(2, (w - 8) / Math.max(b.length, 30) - 2);
  b.forEach((z, i) => { const bh = (h - pad - 6) * (z.value || 0) / mx, x0 = 4 + i * (bw + 2); c.fillStyle = exp && (z.value || 0) >= exp * 3 ? css('--amber') : css('--accent'); c.fillRect(x0, h - pad - bh, bw, bh); if (i % 6 === 0) { c.fillStyle = css('--muted'); c.font = '11px "JetBrains Mono", monospace'; c.fillText(z.begin.slice(11, 16), x0, h - 5); } });
  if (exp) { const y = h - pad - (h - pad - 6) * exp / mx; c.setLineDash([4, 4]); c.strokeStyle = css('--muted'); c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); c.setLineDash([]); }
}
async function buySell(t) {
  const btn = $('#bsBtn'); btn.disabled = true;
  let b = 0, s = 0, n = 0, start = 0, big = [];
  try {
    for (let page = 0; page < 80; page++) {
      btn.textContent = `Загружаю сделки… ${nf(n)}`;
      const js = await iss(`engines/stock/markets/shares/boards/TQBR/securities/${t}/trades.json`, { 'iss.only': 'trades', 'trades.columns': 'TRADETIME,VALUE,BUYSELL', start, limit: 5000 });
      const r = rows(js, 'trades');
      for (const z of r) { if (z.BUYSELL === 'B') b += z.VALUE; else if (z.BUYSELL === 'S') s += z.VALUE; if (z.VALUE >= 50e6) big.push(z); }
      n += r.length; start += r.length;
      if (r.length < 5000) break;
    }
    const tot = b + s, net = b - s;
    const bh = behavior(S.byT[t], tot ? net / tot : null);
    big.sort((a, c) => c.VALUE - a.VALUE);
    $('#bs').innerHTML = `<div class="kv"><div>Покупки по рынку<b class="up">${rub(b)}</b></div><div>Продажи по рынку<b class="dn">${rub(s)}</b></div><div>Чистый поток<b class="${net >= 0 ? 'up' : 'dn'}">${rubS(net)}</b></div><div>Доля покупок<b>${tot ? nf(b / tot * 100, 0) + '%' : '—'}</b></div></div>
      ${bh.length ? `<p class="bh">🔎 ${esc(bh.join('; '))}</p>` : ''}
      <p class="hint">${nf(n)} сделок. ${big.length ? 'Крупнейшие (от 50 млн ₽): ' + big.slice(0, 5).map(z => `${z.TRADETIME.slice(0, 5)} ${z.BUYSELL === 'B' ? 'покупка' : 'продажа'} ${rub(z.VALUE)}`).join(' · ') : 'Сделок от 50 млн ₽ не было.'}</p>`;
  } catch (e) { btn.disabled = false; btn.textContent = 'Не удалось загрузить сделки — повторить'; }
}

// ---------- физ. и юр. лица (позиции по фьючерсам) ----------
const FX = new Set(['USDRUBTOM', 'CNYRUBTOM', 'EURRUBTOM', 'Si', 'CNY', 'Eu', 'ED', 'UCNY', 'AUDU', 'USDRUBF', 'CNYRUBF', 'EURRUBF', 'GBPU', 'UJPY', 'UCHF', 'UTRY', 'KZT', 'TRY', 'HKD', 'AED', 'INR', 'BYN']);
const IDX = new Set(['IMOEX', 'MIX', 'MXI', 'RTS', 'RTSM', 'RVI', 'IMOEXF', 'RGBI', 'SPYF', 'NASD', 'DAX', 'HANG', 'MOEXCNY', 'NIKKEI', 'STOXX', 'R2000', 'DJ30', 'EM']);
const COMM = new Set(['GLDRUBTOM', 'BR', 'NG', 'GOLD', 'SILV', 'PLT', 'PLD', 'COPPER', 'WHEAT', 'COFFEE', 'COCOA', 'TTF', 'GL', 'GLDRUBF', 'BRM', 'NGM', 'SILVM', 'ALUM', 'NICKEL', 'ZINC', 'SUGAR', 'IBIT', 'BTC', 'ETH', 'ORANGE']);
const ANAME = { USDRUBTOM: 'Доллар/рубль, вечный', CNYRUBTOM: 'Юань/рубль, вечный', EURRUBTOM: 'Евро/рубль, вечный', GLDRUBTOM: 'Золото/рубль, вечный', IMOEX: 'Индекс МосБиржи, вечный', Si: 'Доллар/рубль', CNY: 'Юань/рубль', Eu: 'Евро/рубль', ED: 'Евро/доллар', UCNY: 'Доллар/юань', USDRUBF: 'Доллар/рубль, вечный', CNYRUBF: 'Юань/рубль, вечный', EURRUBF: 'Евро/рубль, вечный', GLDRUBF: 'Золото/рубль, вечный', MIX: 'Индекс МосБиржи', MXI: 'Индекс МосБиржи, мини', IMOEXF: 'Индекс МосБиржи, вечный', RTS: 'Индекс РТС', RTSM: 'Индекс РТС, мини', RVI: 'Волатильность RVI', RGBI: 'Индекс ОФЗ', BR: 'Нефть Brent', BRM: 'Нефть Brent, мини', NG: 'Газ Henry Hub', NGM: 'Газ, мини', GOLD: 'Золото', SILV: 'Серебро', PLT: 'Платина', PLD: 'Палладий', COPPER: 'Медь', WHEAT: 'Пшеница', TTF: 'Газ TTF', SPYF: 'S&P 500', NASD: 'Nasdaq 100', AUDU: 'Австр. доллар/доллар', SBRF: 'Сбербанк', SBPR: 'Сбербанк, преф.', GAZR: 'Газпром', SNGR: 'Сургутнефтегаз', MTSI: 'МТС', NOTKM: 'Новатэк', TRNF: 'Транснефть' };
const fyGroup = a => FX.has(a) ? 'fx' : IDX.has(a) ? 'index' : COMM.has(a) ? 'comm' : 'share';
async function loadFutures() {
  const js = await iss('engines/futures/markets/forts/securities.json', { 'iss.only': 'securities,marketdata', 'securities.columns': 'SECID,ASSETCODE,SHORTNAME,MINSTEP,STEPPRICE,PREVSETTLEPRICE', 'marketdata.columns': 'SECID,LAST,VALTODAY,OPENPOSITION' });
  const md = Object.fromEntries(rows(js, 'marketdata').map(r => [r.SECID, r]));
  const byPrefix = {}, best = {};
  for (const r of rows(js, 'securities')) {
    const m = md[r.SECID] || {};
    const key = r.SECID.length <= 4 ? r.SECID.slice(0, 2) : r.SECID; // SiZ6 → Si, вечные (USDRUBF) — целиком
    byPrefix[key] = r.ASSETCODE;
    const price = m.LAST || r.PREVSETTLEPRICE;
    const cv = price && r.MINSTEP ? price * (r.STEPPRICE || 0) / r.MINSTEP : null;
    const oi = m.OPENPOSITION || 0;
    if (cv && (!best[r.ASSETCODE] || oi > best[r.ASSETCODE].oi)) best[r.ASSETCODE] = { cv, oi, name: r.SHORTNAME };
  }
  return { byPrefix, best };
}
async function lastFutoiDate() {
  const today = msk().date;
  const js = await iss('analyticalproducts/futoi/securities.json', { date: today });
  const msg = JSON.stringify(js);
  const m = msg.match(/(\d{4}-\d{2}-\d{2})\)/);
  return m ? m[1] : today;
}
async function loadFizYur() {
  if (S.fy && S.fy.loadedAt && Date.now() - S.fy.loadedAt < 30 * 60e3) return renderFizYur();
  $('#fyTbl tbody').innerHTML = '<tr><td class="l" colspan="5">Загружаю позиции…</td></tr>';
  try {
    const [fut, lim] = await Promise.all([loadFutures(), lastFutoiDate()]);
    let d = lim, all = [];
    for (let k = 0; k < 7 && !all.length; k++, d = addDays(d, -1)) {
      for (let start = 0; start < 20000; start += 1000) {
        const js = await iss('analyticalproducts/futoi/securities.json', { date: d, start });
        const r = rows(js, 'futoi').filter(x => x.ticker);
        all = all.concat(r);
        if (r.length < 1000) break;
      }
      if (all.length) break;
    }
    const last = {};
    for (const r of all) { const k = r.ticker + '|' + r.clgroup; if (!last[k] || r.systime > last[k].systime) last[k] = r; }
    const items = {};
    for (const r of Object.values(last)) {
      const it = items[r.ticker] = items[r.ticker] || { code: r.ticker };
      it[r.clgroup === 'FIZ' ? 'fiz' : 'yur'] = r;
      it.date = r.tradedate; it.time = r.tradetime;
    }
    for (const it of Object.values(items)) {
      const asset = fut.byPrefix[it.code] || it.code;
      it.asset = asset; it.group = fyGroup(asset);
      it.name = ANAME[asset] || (S.byT[asset] ? S.byT[asset].name : asset);
      it.cv = (fut.best[asset] || {}).cv || null;
    }
    S.fy = { items: Object.values(items).filter(it => it.fiz && it.yur), date: Object.values(items)[0]?.date, loadedAt: Date.now() };
  } catch (e) {
    $('#fyTbl tbody').innerHTML = `<tr><td class="l" colspan="5">Не удалось загрузить позиции с Мосбиржи. Проверьте интернет и нажмите «Обновить».</td></tr>`;
    return;
  }
  renderFizYur();
}
function renderFizYur() {
  if (!S.fy) return;
  const { items, date } = S.fy;
  const val = (it, g, k) => it.cv && it[g] ? it[g][k] * it.cv : null;
  let fizNet = 0, yurNet = 0, fizL = 0, fizS = 0;
  for (const it of items) { if (!it.cv) continue; fizNet += val(it, 'fiz', 'pos'); yurNet += val(it, 'yur', 'pos'); fizL += val(it, 'fiz', 'pos_long'); fizS += -val(it, 'fiz', 'pos_short'); }
  $('#fyFiz').innerHTML = `<span class="${fizNet >= 0 ? 'up' : 'dn'}">${rubS(fizNet)}</span>`;
  $('#fyFizH').textContent = `лонг ${rub(fizL)} · шорт ${rub(fizS)}`;
  $('#fyYur').innerHTML = `<span class="${yurNet >= 0 ? 'up' : 'dn'}">${rubS(yurNet)}</span>`;
  $('#fyYurH').textContent = 'по всем фьючерсам Мосбиржи';
  $('#fyDate').textContent = ddmm(date);
  $('#fyNote').textContent = `Мосбиржа бесплатно раскрывает позиции физических и юридических лиц только по фьючерсам и с задержкой 14 дней: сейчас доступны данные на ${ddmm(date)}. По самим акциям такой разбивки в открытом доступе нет; онлайн-данные — по платной подписке Мосбиржи.`;
  const g = S.fyGroup;
  const list = items.filter(it => g === 'all' || it.group === g).sort((a, b) => Math.abs(val(b, 'fiz', 'pos_long') || 0) + Math.abs(val(b, 'fiz', 'pos_short') || 0) - Math.abs(val(a, 'fiz', 'pos_long') || 0) - Math.abs(val(a, 'fiz', 'pos_short') || 0));
  $('#fyTbl tbody').innerHTML = list.map(it => {
    const L = val(it, 'fiz', 'pos_long'), Sh = -val(it, 'fiz', 'pos_short'), fn = val(it, 'fiz', 'pos'), yn = val(it, 'yur', 'pos');
    const share = L != null && L + Sh > 0 ? L / (L + Sh) : null;
    return `<tr tabindex="0" data-code="${esc(it.code)}"><td class="l"><span class="tk">${esc(it.asset)}</span><span class="nm">${esc(it.name)}</span></td>
      <td><span class="ls">${share == null ? '' : `<span class="lsbar"><i style="width:${(share * 100).toFixed(0)}%"></i></span>`}<span class="mono">${rub(L)} / ${rub(Sh)}</span></span></td>
      <td class="${fn >= 0 ? 'up' : 'dn'}">${rubS(fn)}</td><td class="${yn >= 0 ? 'up' : 'dn'}">${rubS(yn)}</td>
      <td class="mono">${nf(it.fiz.pos_long_num)} / ${nf(it.fiz.pos_short_num)}</td></tr>`;
  }).join('') || '<tr><td class="l" colspan="5">Нет данных</td></tr>';
}
async function openFut(code) {
  const it = S.fy && S.fy.items.find(x => x.code === code); if (!it) return;
  const dlg = $('#sheet');
  $('#shTitle').textContent = `${it.asset} · ${it.name}`;
  $('#shSub').textContent = `Позиции физ. и юр. лиц, данные на ${ddmm(it.date)} ${it.time ? it.time.slice(0, 5) : ''}`;
  const cv = it.cv;
  $('#shBody').innerHTML = `<div class="kv"><div>Физлица лонг<b class="up">${rub(it.fiz.pos_long * cv)}</b></div><div>Физлица шорт<b class="dn">${rub(-it.fiz.pos_short * cv)}</b></div><div>Юрлица лонг<b class="up">${rub(it.yur.pos_long * cv)}</b></div><div>Юрлица шорт<b class="dn">${rub(-it.yur.pos_short * cv)}</b></div></div>
    <div class="label">Нетто-позиция за месяц: физлица — малахит, юрлица — янтарь</div><canvas id="shChart" height="180"></canvas><p class="hint" id="futHint">Загружаю историю…</p>`;
  if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
  try {
    const till = it.date, from = addDays(till, -30); let all = [];
    for (let start = 0; start < 30000; start += 1000) {
      const js = await iss(`analyticalproducts/futoi/securities/${code.toLowerCase()}.json`, { from, till, start });
      const r = rows(js, 'futoi').filter(x => x.ticker); all = all.concat(r);
      if (r.length < 1000) break;
    }
    const day = {};
    for (const r of all) { const k = r.tradedate; day[k] = day[k] || {}; const g = r.clgroup === 'FIZ' ? 'f' : 'y'; if (!day[k][g] || r.systime > day[k][g].systime) day[k][g] = r; }
    const ds = Object.keys(day).sort().filter(k => day[k].f && day[k].y);
    drawFut(ds.map(k => [k, day[k].f.pos * cv, day[k].y.pos * cv]));
    $('#futHint').textContent = ds.length ? `${ddmm(ds[0])}–${ddmm(ds[ds.length - 1])}, на конец каждого дня; деньги — по текущей стоимости контракта` : 'Истории нет';
  } catch (e) { $('#futHint').textContent = 'Не удалось загрузить историю позиций.'; }
}
function drawFut(pts) {
  const cv = $('#shChart'); if (!cv || !pts.length) return;
  const { c, w, h } = prepCanvas(cv);
  const all = pts.flatMap(p => [p[1], p[2]]); const mx = Math.max(...all, 0), mn = Math.min(...all, 0), pad = 20;
  const Y = v => 6 + (h - pad - 12) * (mx - v) / ((mx - mn) || 1), X = i => 8 + (w - 16) * i / Math.max(1, pts.length - 1);
  c.strokeStyle = css('--line'); c.beginPath(); c.moveTo(0, Y(0)); c.lineTo(w, Y(0)); c.stroke();
  for (const [k, col] of [[1, '--accent'], [2, '--amber']]) { c.strokeStyle = css(col); c.lineWidth = 2; c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(i), Y(p[k])) : c.moveTo(X(i), Y(p[k]))); c.stroke(); }
  c.fillStyle = css('--muted'); c.font = '11px "JetBrains Mono", monospace';
  [0, Math.floor(pts.length / 2), pts.length - 1].forEach(i => c.fillText(ddmm(pts[i][0]), Math.min(X(i), w - 40), h - 4));
  c.fillText(rub(mx), 6, 14); c.fillText(rub(mn), 6, h - pad - 2);
}

// ---------- цикл обновления ----------
async function refresh(first) {
  if (S.loading) return; S.loading = true;
  const st = $('#status');
  try {
    await loadShares();
    if (first || !S.avgDays) {
      st.textContent = 'Считаю средний оборот за 15 дней…';
      loadAvg((k, n) => { st.textContent = `Средний оборот: ${k} из ${n} дней`; }).then(() => { enrich(); render(); setStatus(); }).catch(() => { st.textContent = 'Не удалось загрузить историю оборотов — «×норма» временно недоступна'; });
    }
    const top = [...S.shares].sort((a, b) => b.val - a.val).slice(0, 40).map(x => x.t);
    if (first || !S.lastBars || Date.now() - S.lastBars > 4 * 60e3) { loadBars(top).then(() => { S.lastBars = Date.now(); enrich(); render(); }).catch(() => {}); }
    if (!S.oiAt || Date.now() - S.oiAt > 5 * 60e3) loadOI().then(render).catch(() => {});
    snapshot(); enrich(); render(); setStatus();
  } catch (e) {
    st.textContent = 'Нет связи с Мосбиржей — пробую снова через минуту';
  } finally { S.loading = false; }
}
function setStatus() {
  const m = msk(), wk = m.dow === 0 || m.dow === 6;
  $('#status').textContent = `Обновлено ${m.hms} МСК · котировки с задержкой ~15 мин` + (wk ? ' · выходной: торгуется только сессия выходного дня' : '');
}
function render() {
  if (S.view === 'money') renderMoney();
  renderSpikes(); // и вкладка, и счётчик всплесков на ней
}

// ---------- события ----------
document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => show(b.dataset.view)));
function show(v) {
  S.view = v;
  document.querySelectorAll('.tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.view === v)));
  document.querySelectorAll('.view').forEach(s => { s.hidden = s.id !== 'view-' + v; });
  if (location.hash.slice(1) !== v) history.replaceState(null, '', '#' + v);
  if (v === 'fizyur') loadFizYur(); else render();
}
window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (['money', 'spikes', 'fizyur'].includes(v) && v !== S.view) show(v); });
document.querySelectorAll('#tbl th').forEach(th => th.addEventListener('click', () => { const k = th.dataset.sort; S.sort = { k, dir: S.sort.k === k ? -S.sort.dir : (k === 't' ? 1 : -1) }; renderTable(); }));
$('#search').addEventListener('input', e => { S.q = e.target.value; renderTable(); });
$('#more').addEventListener('click', () => { S.showAll = true; renderTable(); });
const rowOpen = e => { const tr = e.target.closest('[data-t]'); if (tr) openShare(tr.dataset.t); };
$('#tbl tbody').addEventListener('click', rowOpen);
$('#spUp').addEventListener('click', rowOpen); $('#spDn').addEventListener('click', rowOpen);
document.addEventListener('keydown', e => { if (e.key === 'Enter') { const r = e.target.closest && (e.target.closest('[data-t]') || e.target.closest('[data-code]')); if (r) r.click(); } });
$('#fyTbl tbody').addEventListener('click', e => { const tr = e.target.closest('[data-code]'); if (tr) openFut(tr.dataset.code); });
document.querySelectorAll('.seg .chip').forEach(b => b.addEventListener('click', () => { S.fyGroup = b.dataset.g; document.querySelectorAll('.seg .chip').forEach(x => x.classList.toggle('on', x === b)); renderFizYur(); }));
$('#shClose').addEventListener('click', () => $('#sheet').close ? $('#sheet').close() : $('#sheet').removeAttribute('open'));
$('#sheet').addEventListener('click', e => { if (e.target === $('#sheet')) $('#sheet').close(); });
$('#btnRefresh').addEventListener('click', () => { if (S.view === 'fizyur') { S.fy = null; loadFizYur(); } refresh(false); });
$('#btnLive').addEventListener('click', () => {
  S.live = !S.live; const b = $('#btnLive'); b.classList.toggle('on', S.live); b.setAttribute('aria-pressed', String(S.live)); b.textContent = S.live ? '● Онлайн' : '○ Пауза';
  if (S.live && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
});
window.addEventListener('resize', () => { if (S.view === 'money') drawPulse(); });
setInterval(() => { if (S.live && !document.hidden) refresh(false); }, 60e3);
document.addEventListener('visibilitychange', () => { if (!document.hidden && S.live) refresh(false); });
if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('sw.js').catch(() => {});

const startView = location.hash.slice(1);
if (['spikes', 'fizyur'].includes(startView)) show(startView);
refresh(true);
