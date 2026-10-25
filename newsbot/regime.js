// ===================== RG: движок стратегий «по режиму рынка» (умеренный риск) =====================
// Один и тот же код используется в исследовании (Node) и в приложении (браузер).
// Правила: сигнал по закрытию дня i → сделка по открытию дня i+1; стоп-лосс фиксированный (по умолчанию 3%);
// размер позиции — от риск-бюджета: риск 1% капитала / расстояние до стопа; без плеча.
// Издержки — тариф Т-Инвестиций «Трейдер»: комиссия 0,05% за сторону + проскальзывание по ликвидности + 390 ₽ в месяц с операциями.
(function (root) {
  'use strict';
  const NaN_ = NaN;

  // ---------- индикаторы (массивы в календаре рынка; NaN — нет торгов) ----------
  function ema(x, n) {
    const r = new Float64Array(x.length).fill(NaN_), k = 2 / (n + 1); let e = NaN_, cnt = 0, s = 0;
    for (let i = 0; i < x.length; i++) {
      const v = x[i]; if (v !== v) { r[i] = e; continue; }
      if (cnt < n) { s += v; cnt++; if (cnt === n) e = s / n; r[i] = cnt === n ? e : NaN_; continue; }
      e = v * k + e * (1 - k); r[i] = e;
    }
    return r;
  }
  function sma(x, n) {
    const r = new Float64Array(x.length).fill(NaN_), q = []; let s = 0;
    for (let i = 0; i < x.length; i++) {
      const v = x[i]; if (v !== v) { r[i] = q.length === n ? s / n : NaN_; continue; }
      q.push(v); s += v; if (q.length > n) s -= q.shift();
      r[i] = q.length === n ? s / n : NaN_;
    }
    return r;
  }
  function std(x, n) {
    const r = new Float64Array(x.length).fill(NaN_), q = [];
    for (let i = 0; i < x.length; i++) {
      const v = x[i]; if (v === v) { q.push(v); if (q.length > n) q.shift(); }
      if (q.length === n) { let m = 0; for (const z of q) m += z; m /= n; let s = 0; for (const z of q) s += (z - m) * (z - m); r[i] = Math.sqrt(s / n); }
    }
    return r;
  }
  function atr(h, l, c, n) {
    const tr = new Float64Array(c.length).fill(NaN_); let pc = NaN_;
    for (let i = 0; i < c.length; i++) {
      if (c[i] !== c[i]) continue;
      tr[i] = pc === pc ? Math.max(h[i] - l[i], Math.abs(h[i] - pc), Math.abs(l[i] - pc)) : h[i] - l[i]; pc = c[i];
    }
    return ema(tr, n);
  }
  function rsi(c, n) {
    const r = new Float64Array(c.length).fill(NaN_); let ag = 0, al = 0, pc = NaN_, k = 0;
    for (let i = 0; i < c.length; i++) {
      const v = c[i]; if (v !== v) { continue; }
      if (pc === pc) {
        const d = v - pc, g = d > 0 ? d : 0, lo = d < 0 ? -d : 0; k++;
        if (k <= n) { ag += g / n; al += lo / n; } else { ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + lo) / n; }
        if (k >= n) r[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
      }
      pc = v;
    }
    return r;
  }
  function hhv(x, n) { // максимум за n торговых дней, включая текущий
    const r = new Float64Array(x.length).fill(NaN_), q = [];
    for (let i = 0; i < x.length; i++) { const v = x[i]; if (v === v) { q.push(v); if (q.length > n) q.shift(); } if (q.length === n) r[i] = Math.max.apply(null, q); }
    return r;
  }
  function llv(x, n) {
    const r = new Float64Array(x.length).fill(NaN_), q = [];
    for (let i = 0; i < x.length; i++) { const v = x[i]; if (v === v) { q.push(v); if (q.length > n) q.shift(); } if (q.length === n) r[i] = Math.min.apply(null, q); }
    return r;
  }
  function prevValid(x) { // значение на предыдущем торговом дне бумаги
    const r = new Float64Array(x.length).fill(NaN_); let p = NaN_;
    for (let i = 0; i < x.length; i++) { r[i] = p; if (x[i] === x[i]) p = x[i]; }
    return r;
  }
  function roc(c, n) { // доходность за n торговых дней бумаги
    const r = new Float64Array(c.length).fill(NaN_), q = [];
    for (let i = 0; i < c.length; i++) { const v = c[i]; if (v !== v) continue; q.push(v); if (q.length > n + 1) q.shift(); if (q.length === n + 1) r[i] = v / q[0] - 1; }
    return r;
  }

  // ---------- данные ----------
  // H: { days: ['YYYY-MM-DD',...], sec: { TICKER: [[d,o,h,l,c,value],...] }, imoex: [[d,o,h,l,c],...] }
  function prepare(H) {
    const days = H.days, N = days.length, pos = new Map(days.map((d, i) => [d, i]));
    const mk = rows => {
      const o = new Float64Array(N).fill(NaN_), h = new Float64Array(N).fill(NaN_), l = new Float64Array(N).fill(NaN_), c = new Float64Array(N).fill(NaN_), v = new Float64Array(N).fill(NaN_);
      for (const r of rows) { const i = pos.get(r[0]); if (i === undefined) continue; o[i] = r[1]; h[i] = r[2]; l[i] = r[3]; c[i] = r[4]; v[i] = r[5] || 0; }
      return { o, h, l, c, v, cache: {} };
    };
    const S = {}; for (const [t, rows] of Object.entries(H.sec)) S[t] = mk(rows);
    // внешние сигналы (новости, Пульс): оценка ожидаемой реакции на день сигнала (вход — на открытии следующего дня)
    if (H.sig) for (const [t, arr] of Object.entries(H.sig)) { const s = S[t]; if (!s) continue; s.sig = new Float64Array(N); s.sigWhy = {}; for (const [d, v, why] of arr) { const i = pos.get(d); if (i !== undefined) { s.sig[i] += v; if (why) s.sigWhy[i] = (s.sigWhy[i] ? s.sigWhy[i] + ' | ' : '') + why; } } }
    // дивиденды (цены без поправки на дивиденды): сумма на акцию в день отсечки (первый день без дивиденда)
    if (H.div) for (const [t, arr] of Object.entries(H.div)) { const s = S[t]; if (!s) continue; s.div = new Float64Array(N); for (const [d, v] of arr) { const i = pos.get(d); if (i !== undefined) s.div[i] += v; } }
    const X = mk(H.imoex);
    // контекст рынка на день входа (геополитика, ОФЗ, решение ЦБ): { 'YYYY-MM-DD': 'причина' } — до открытия этого дня
    const ctx = new Array(N).fill(null);
    if (H.ctx) for (const [d, why] of Object.entries(H.ctx)) { const i = pos.get(d); if (i !== undefined && why) ctx[i] = why; }
    return { days, N, S, X, ctx, tickers: Object.keys(S), cache: {}, divTaxOnly: !!H.divTaxOnly };
  }
  function ind(s, key, fn) { return s.cache[key] || (s.cache[key] = fn()); }
  const I = {
    ema: (s, n) => ind(s, 'ema' + n, () => ema(s.c, n)),
    sma: (s, n) => ind(s, 'sma' + n, () => sma(s.c, n)),
    atr: (s, n = 14) => ind(s, 'atr' + n, () => atr(s.h, s.l, s.c, n)),
    rsi: (s, n) => ind(s, 'rsi' + n, () => rsi(s.c, n)),
    hh: (s, n) => ind(s, 'hh' + n, () => prevValid(hhv(s.h, n))), // максимум предыдущих n дней (без текущего)
    ll: (s, n) => ind(s, 'll' + n, () => prevValid(llv(s.l, n))),
    hc: (s, n) => ind(s, 'hc' + n, () => hhv(s.c, n)),
    av: (s, n = 20) => ind(s, 'av' + n, () => prevValid(sma(s.v, n))), // средний оборот предыдущих n дней
    std: (s, n) => ind(s, 'std' + n, () => std(s.c, n)),
    roc: (s, n) => ind(s, 'roc' + n, () => roc(s.c, n)),
    pc: s => ind(s, 'pc', () => prevValid(s.c)),
    ph: s => ind(s, 'ph', () => prevValid(s.h)),
    pl: s => ind(s, 'pl', () => prevValid(s.l)),
  };

  // ---------- режим рынка по индексу МосБиржи ----------
  // +1 — рост, −1 — падение, 0 — боковик; vol — годовая волатильность индекса за 20 дней
  function regimeOf(D, R) {
    const key = 'rg' + JSON.stringify(R); if (D.cache[key]) return D.cache[key];
    const X = D.X, c = X.c, e = I.ema(X, R.ma || 50), lt = I.sma(X, R.lt || 200), N = D.N;
    const g = new Int8Array(N), vol = new Float64Array(N).fill(NaN_), slopeN = R.slope || 10;
    const lr = new Float64Array(N).fill(NaN_); let pcv = NaN_;
    for (let i = 0; i < N; i++) { if (c[i] === c[i]) { if (pcv === pcv) lr[i] = Math.log(c[i] / pcv); pcv = c[i]; } }
    const sd = std(lr, 20);
    for (let i = 0; i < N; i++) {
      vol[i] = sd[i] * Math.sqrt(252);
      const ei = e[i], ej = e[i - slopeN];
      if (!(ei === ei) || !(ej === ej) || !(c[i] === c[i])) { g[i] = 0; continue; }
      const up = c[i] > ei && ei > ej, dn = c[i] < ei && ei < ej;
      let r = up ? 1 : dn ? -1 : 0;
      if (R.ltConfirm && lt[i] === lt[i]) { if (r > 0 && c[i] < lt[i]) r = 0; if (r < 0 && c[i] > lt[i]) r = 0; }
      g[i] = r;
    }
    // ширина рынка: доля ликвидных акций выше SMA 200 (подтверждение режима индекса)
    if (R.breadth) {
      const br = new Float64Array(N).fill(NaN_);
      for (let i = 0; i < N; i++) {
        let up = 0, tot = 0;
        for (const t of D.tickers) { const s = D.S[t], c2 = s.c[i], m = I.sma(s, 200)[i]; if (c2 === c2 && m === m && I.av(s, 60)[i] >= 1e7) { tot++; if (c2 > m) up++; } }
        br[i] = tot >= 8 ? up / tot : NaN_;
      }
      for (let i = 0; i < N; i++) {
        const b = br[i]; if (!(b === b)) continue;
        if (g[i] > 0 && b < R.bUp) g[i] = 0;
        if (g[i] < 0 && b > R.bDn) g[i] = 0;
        if (R.bForce && g[i] === 0 && b <= R.bDn * 0.8) g[i] = -1;
      }
      D.cache['breadth'] = br;
    }
    // сглаживание: режим меняется только если новый держится confirm дней подряд
    const cf = R.confirm || 1, out = new Int8Array(N); let cur = 0, cand = 0, run = 0;
    for (let i = 0; i < N; i++) { if (g[i] === cand) run++; else { cand = g[i]; run = 1; } if (run >= cf) cur = cand; out[i] = cur; }
    return (D.cache[key] = { g: out, vol });
  }

  // ---------- сигналы семейств (по закрытию дня i) ----------
  // каждое семейство: entry(s, i, p) → score|0, exitSig(s, i, pos, p) → true, если выходим по открытию следующего дня
  const FAM = {
    // Пробой N-дневного максимума на объёме (t1a, s_break) — тренд
    brk: {
      side: 1,
      entry(s, i, p) {
        const c = s.c[i], hh = I.hh(s, p.n)[i], av = I.av(s)[i], e = I.ema(s, p.tf || 200)[i];
        if (!(c > hh) || !(s.v[i] > (p.vk || 1) * av)) return 0;
        if (p.tf && !(c > e)) return 0;
        const a = I.atr(s)[i]; return 1 + (c - hh) / a + Math.min(3, s.v[i] / av) * 0.2;
      },
      exitSig(s, i, P, p) { return p.x ? s.c[i] < I.ll(s, p.x)[i] : false; },
    },
    // Откат к EMA в восходящем тренде (s_swing, pullback)
    pull: {
      side: 1,
      entry(s, i, p) {
        const c = s.c[i], f = I.ema(s, p.f)[i], sl = I.ema(s, p.s)[i], lt = I.sma(s, 200)[i];
        if (!(f > sl) || !(c > lt) || !(c > f)) return 0;
        if (!(s.l[i] <= f * (1 + (p.tol || 0.01)))) return 0;
        if (!(c > s.o[i])) return 0;
        return 1 + (f / sl - 1) * 20;
      },
      exitSig(s, i, P, p) { return s.c[i] < I.ema(s, p.s)[i]; },
    },
    // Отбой от EMA 200 («Тыквенная»)
    pump: {
      side: 1,
      entry(s, i, p) {
        const c = s.c[i], e = I.ema(s, 200)[i], pc = I.pc(s)[i];
        if (!(c > e) || !(s.l[i] <= e * (1 + (p.tol || 0.015))) || !(c > s.o[i]) || !(c > pc)) return 0;
        const e50 = I.ema(s, 50)[i]; if (p.up && !(e50 > e)) return 0;
        return 1 + (c / e - 1) * 10;
      },
      exitSig(s, i, P, p) { return s.c[i] < I.ema(s, 200)[i] * (1 - (p.xb || 0.02)); },
    },
    // Возврат к среднему: перепроданность в долгосрочном росте (s_mean, dip, Alexey0611)
    mr: {
      side: 1,
      entry(s, i, p) {
        const c = s.c[i], lt = I.sma(s, p.lt || 200)[i], r = I.rsi(s, p.rn || 2)[i];
        if (p.lt !== 0 && !(c > lt)) return 0;
        if (!(r < p.rl)) return 0;
        return 1 + (p.rl - r) / p.rl;
      },
      exitSig(s, i, P, p) { return s.c[i] > I.sma(s, p.xm || 5)[i] || I.rsi(s, p.rn || 2)[i] > (p.rx || 70); },
    },
    // «Упрямый бык»: разворот после отката — закрытие выше вчерашнего максимума
    bull: {
      side: 1,
      entry(s, i, p) {
        const c = s.c[i], a = I.atr(s)[i], top = I.hc(s, 10)[i], ph = I.ph(s)[i];
        if (!(top - c >= (p.dd || 2) * a)) return 0;
        if (!(c > ph) || !(c > s.o[i])) return 0;
        if (p.tf && !(c > I.sma(s, 200)[i])) return 0;
        return 1 + (top - c) / a * 0.2;
      },
      exitSig() { return false; },
    },
    // Новостной сигнал: оценка ожидаемой реакции по типам новостей (веса обучены только на прошлых годах)
    nwsL: {
      side: 1,
      entry(s, i, p) { const v = s.sig ? s.sig[i] : 0; if (!(v >= p.th)) return 0; if (p.tf && !(s.c[i] > I.sma(s, 200)[i])) return 0; return 1 + v; },
      exitSig(s, i, P, p) { return p.rev ? (s.sig && s.sig[i] <= -p.th) : false; },
    },
    nwsS: {
      side: -1,
      entry(s, i, p) { const v = s.sig ? s.sig[i] : 0; if (!(v <= -p.th)) return 0; if (p.tf && !(s.c[i] < I.sma(s, 200)[i])) return 0; return 1 - v; },
      exitSig(s, i, P, p) { return p.rev ? (s.sig && s.sig[i] >= p.th) : false; },
    },
    // Импульс (ротация): лучшая доходность за lb дней среди бумаг выше SMA 200, пересмотр раз в неделю
    mom: {
      side: 1, rotation: true,
      entry(s, i, p) {
        const c = s.c[i], r = I.roc(s, p.lb)[i], rs = I.roc(s, 5)[i];
        if (!(c > I.sma(s, 200)[i]) || !(r > (p.min || 0.1))) return 0;
        return 1 + r - (rs || 0) * (p.skip ? 1 : 0);
      },
      exitSig(s, i, P, p) { return s.c[i] < I.ema(s, p.xe || 50)[i]; },
    },
    // Шорт пробоя минимума в падающем рынке
    sbrk: {
      side: -1,
      entry(s, i, p) {
        const c = s.c[i], ll = I.ll(s, p.n)[i], av = I.av(s)[i];
        if (!(c < ll) || !(s.v[i] > (p.vk || 1) * av) || !(c < I.ema(s, 200)[i])) return 0;
        return 1 + (ll - c) / I.atr(s)[i];
      },
      exitSig(s, i, P, p) { return p.x ? s.c[i] > I.hh(s, p.x)[i] : false; },
    },
    // Шорт отскока в падающем рынке (продажа перекупленности)
    smr: {
      side: -1,
      entry(s, i, p) {
        const c = s.c[i], r = I.rsi(s, 2)[i];
        if (!(c < I.sma(s, 200)[i]) || !(c < I.ema(s, 50)[i]) || !(r > p.rh)) return 0;
        return 1 + (r - p.rh) / (100 - p.rh);
      },
      exitSig(s, i, P, p) { return s.c[i] < I.sma(s, p.xm || 5)[i] || I.rsi(s, 2)[i] < (p.rx || 30); },
    },
    // Ложный пробой минимума (s_false): прокол N-дневного минимума и закрытие обратно выше него
    fb: {
      side: 1,
      entry(s, i, p) {
        const c = s.c[i], ll = I.ll(s, p.n)[i], rng = s.h[i] - s.l[i];
        if (!(s.l[i] < ll) || !(c > ll) || !(rng > 0)) return 0;
        if ((c - s.l[i]) / rng < (p.cl || 0.6)) return 0;
        if (p.tf && !(c > I.sma(s, 200)[i])) return 0;
        return 1 + (c - s.l[i]) / rng;
      },
      exitSig(s, i, P, p) { return p.x ? s.c[i] > I.hh(s, p.x)[i] : false; },
    },
    // Событийная (s_event): гэп вверх больше k ATR на объёме и закрытие выше открытия — продолжение движения
    gap: {
      side: 1,
      entry(s, i, p) {
        const o = s.o[i], c = s.c[i], ph = I.ph(s)[i], a = I.atr(s)[i], av = I.av(s)[i];
        if (!(o - ph > (p.g || 0.8) * a) || !(c > o) || !(s.v[i] > (p.vk || 2) * av)) return 0;
        if (p.tf && !(c > I.sma(s, 200)[i])) return 0;
        return 1 + (o - ph) / a;
      },
      exitSig(s, i, P, p) { return p.xe ? s.c[i] < I.ema(s, p.xe)[i] : false; },
    },
  };

  // ---------- объяснение сигнала (для приложения) ----------
  const REG_TXT = { 1: 'рынок растёт (IMOEX выше средней, средняя растёт)', 0: 'рынок без явного тренда', '-1': 'рынок падает (IMOEX ниже средней, средняя падает)' };
  const f1 = v => (Math.round(v * 10) / 10).toString().replace('.', ',');
  function whyOf(m, s, i, g) {
    const p = m.p, c = s.c[i], out = [];
    if (m.fam === 'mom') out.push(`Импульс: рост за ${p.lb} дней ${f1(I.roc(s, p.lb)[i] * 100)}% — среди лидеров рынка, цена выше SMA 200`);
    else if (m.fam === 'pull') out.push(`Откат к EMA ${p.f} в восходящем тренде (EMA ${p.f} выше EMA ${p.s}, цена выше SMA 200), закрытие выше открытия`);
    else if (m.fam === 'sbrk') out.push(`Шорт: закрытие ниже минимума ${p.n} дней на объёме ×${f1(s.v[i] / I.av(s)[i])}, цена ниже EMA 200`);
    else if (m.fam === 'brk') out.push(`Пробой максимума ${p.n} дней на объёме ×${f1(s.v[i] / I.av(s)[i])}`);
    else if (m.fam === 'pump') out.push(`Отбой от EMA 200 (${f1(I.ema(s, 200)[i])}) зелёной свечой`);
    else out.push('Сигнал модуля ' + m.fam);
    out.push('Режим: ' + REG_TXT[g]);
    out.push('Стоп-лосс 3% от входа, риск 1% капитала' + (m.take ? `, цель ${m.take * 3}% (риск к прибыли 1:${m.take})` : ', цель не ограничена (не меньше 1:3 — держим, пока жив импульс)') + (m.be ? `, после +${m.be}R стоп в безубыток` : '') + (m.trail ? `, трейлинг ${m.trail} ATR` : '') + (m.hold ? `, не дольше ${m.hold} дн.` : ''));
    return out;
  }

  // ---------- издержки ----------
  function slip(av, C) { if (!(av > 0)) return C.slipLow; return av >= 1e9 ? C.slipHi : av >= 2e8 ? C.slipMid : C.slipLow; }
  const COST_TRADER = { comm: 0.0005, slipHi: 0.0005, slipMid: 0.001, slipLow: 0.002, monthly: 390, shortDay: 0.0005 };

  // ---------- прогон стратегии ----------
  // P: { capital, riskPct, stopPct, maxPos, maxPosPct, minValue, topN, regime:{...}, modules:[{fam, on:[1,0,-1], p:{...}, take, trail, be, hold}], cost }
  function run(D, P, opt = {}) {
    const N = D.N, from = opt.from || 0, to = opt.to === undefined ? N - 1 : opt.to, C = Object.assign({}, COST_TRADER, P.cost || {});
    const RG = regimeOf(D, P.regime || {}), reg = RG.g;
    const stopPct = P.stopPct || 0.03, riskPct = P.riskPct || 0.01, maxPos = P.maxPos || 3, maxPosPct = P.maxPosPct || 0.34;
    const mods = P.modules.map((m, k) => Object.assign({ k, F: FAM[m.fam] }, m));
    // собственный режим модуля (m.reg) и собственный новостной фильтр (m.ng: объект или false)
    for (const m of mods) { m.RGm = m.reg ? regimeOf(D, m.reg) : RG; m.NGm = m.ng === undefined ? P.newsGate : m.ng; }
    const secs = D.tickers.map(t => [t, D.S[t]]);
    let cash = P.capital || 300000, equity = cash, peak = cash, pauseUntil = -1, lossRun = 0;
    const open = [], trades = [], daily = [];
    let monthFee = '', feesPaid = 0, commPaid = 0;
    const lastRot = {}, sigTo = opt.signalTo === undefined ? to : opt.signalTo;
    const canSig = i => opt.live ? i <= sigTo : i < to;
    const NG = P.newsGate;
    const newsSum = (s, i, k) => { if (!s.sig) return 0; let v = 0; for (let j = Math.max(0, i - k + 1); j <= i; j++) v += s.sig[j]; return v; };
    const newsOk = (s, i, side, ng = NG) => !ng || side * newsSum(s, i, ng.days || 3) > -(ng.th || 0.3);
    const newsBoost = (s, i, side, ng = NG) => ng && ng.boost ? Math.max(0, side * newsSum(s, i, ng.days || 3)) * ng.boost : 0;
    // дополнительные фильтры входа модуля (по итогам разбора убытков): ATR бумаги, растяжение над EMA 20
    const entryOk = (m, s, i) => {
      if (m.atrMax && !(I.atr(s)[i] / s.c[i] <= m.atrMax)) return false;
      if (m.extMax && !(m.F.side * (s.c[i] / I.ema(s, 20)[i] - 1) <= m.extMax)) return false;
      return true;
    };
    const pend = [], pendEntries = []; // заявки на открытие следующего дня (выходы и входы)
    const feeSet = opt.feeMonths; // общий для нескольких «книг» одного счёта: абонплата берётся один раз в месяц
    const touchMonth = d => { const m = d.slice(0, 7); if (m !== monthFee) { monthFee = m; if (feeSet && feeSet.has(m)) return; if (feeSet) feeSet.add(m); cash -= C.monthly; feesPaid += C.monthly; } };
    const liquid = i => { // ликвидные бумаги на день i: средний оборот ≥ minValue, топ-N по обороту
      const arr = [];
      for (const [t, s] of secs) { const av = I.av(s, 60)[i]; if (av >= (P.minValue || 5e7) && s.c[i] === s.c[i]) arr.push([t, s, av]); }
      arr.sort((a, b) => b[2] - a[2]); return P.topN ? arr.slice(0, P.topN) : arr;
    };
    for (let i = from; i <= to; i++) {
      const d = D.days[i];
      // 0) дивиденды: держатели на закрытии накануне получают дивиденд (минус НДФЛ 13%), шорт выплачивает компенсацию;
      //    стоп, цель и трейлинг сдвигаются на размер дивиденда — гэп отсечки не считается движением цены
      for (const ps of open) {
        const s = D.S[ps.t]; if (!s.div || !s.div[i]) continue; const dv = s.div[i];
        if (D.divTaxOnly) { if (ps.side > 0) { const tx = ps.qty * dv * (C.divTax ?? 0.13); cash -= tx; ps.divs = (ps.divs || 0) - tx; } continue; } // цены уже с поправкой: удерживаем только НДФЛ
        const cashDv = ps.side > 0 ? ps.qty * dv * (1 - (C.divTax ?? 0.13)) : -ps.qty * dv;
        cash += cashDv; ps.divs = (ps.divs || 0) + cashDv;
        ps.stop -= dv; if (ps.take) ps.take -= dv; ps.best -= dv; ps.entryAdj = (ps.entryAdj || ps.entry) - dv;
      }
      // 1) исполнение заявок по открытию
      const exitsToday = new Set();
      for (const ord of pend.splice(0)) {
        const s = D.S[ord.t]; const o = s.o[i];
        if (!(o === o)) { if (ord.type === 'exit') pend.push(ord); continue; } // нет торгов — переносим выход
        if (ord.type === 'exit') {
          const ps = ord.pos; if (!open.includes(ps)) continue;
          closePos(ps, o * (1 - ps.side * ps.slip), i, ord.reason, true); exitsToday.add(ord.t);
        }
      }
      for (const ord of pendEntries.splice(0)) {
        const s = D.S[ord.t]; const o = s.o[i];
        if (!(o === o) || open.length >= maxPos || open.some(x => x.t === ord.t)) continue;
        if (ord.m.gapMax && ord.side * (o / I.pc(s)[i] - 1) < -ord.m.gapMax) continue;
        const sl = ord.slip, px = o * (1 + ord.side * sl);
        const cx = ord.side > 0 && P.ctxRisk && D.ctx ? D.ctx[i] : null; // рискованный фон для лонга → риск сделки уменьшается
        const riskRub = equity * riskPct * (cx ? P.ctxRisk : 1), stopDist = px * stopPct;
        let notional = riskRub / stopDist * px;
        notional = Math.min(notional, equity * maxPosPct);
        const gross = open.reduce((a, x) => a + x.qty * x.last, 0);
        notional = Math.min(notional, Math.max(0, equity * (P.maxGross || 1) - gross));
        if (ord.side > 0) notional = Math.min(notional, cash * 0.999);
        if (notional < equity * 0.05) continue;
        let qty = notional / px;
        const lot = P.lots && P.lots[ord.t]; if (lot) { qty = Math.floor(qty / lot) * lot; if (qty < lot) continue; notional = qty * px; }
        const fee = notional * C.comm;
        touchMonth(d);
        if (ord.side > 0) cash -= notional + fee; else cash += notional - fee;
        commPaid += fee;
        const m = ord.m;
        const ps = { t: ord.t, side: ord.side, qty, entry: px, i0: i, d0: d, stop: px * (1 - ord.side * stopPct), take: m.take ? px * (1 + ord.side * m.take * stopPct) : null,
          best: px, last: px, slip: sl, m, fam: m.fam, reg: ord.reg, score: ord.score, risk: qty * px * stopPct, fees: fee, carry: 0, why: cx ? (ord.why || []).concat(['Фон рынка: ' + cx + ' — риск сделки уменьшен до ' + Math.round(riskPct * P.ctxRisk * 1000) / 10 + '% счёта']) : (ord.why || []), stop0: px * (1 - ord.side * stopPct) };
        open.push(ps);
      }
      // 2) внутридневные стоп и тейк
      for (const ps of open.slice()) {
        const s = D.S[ps.t]; const o = s.o[i], h = s.h[i], l = s.l[i];
        if (!(o === o)) continue;
        if (ps.side > 0) {
          if (l <= ps.stop) { closePos(ps, Math.min(o, ps.stop) * (1 - ps.slip), i, ps.stop >= (ps.entryAdj || ps.entry) ? 'Безубыток/трейлинг' : (ps.stop > (ps.entryAdj || ps.entry) * (1 - stopPct) * 1.0001 ? 'Трейлинг-стоп' : 'Стоп 3%'), false); continue; }
          if (ps.take && h >= ps.take) { closePos(ps, Math.max(o, ps.take), i, 'Цель', false); continue; }
        } else {
          if (h >= ps.stop) { closePos(ps, Math.max(o, ps.stop) * (1 + ps.slip), i, ps.stop <= (ps.entryAdj || ps.entry) ? 'Безубыток/трейлинг' : (ps.stop < (ps.entryAdj || ps.entry) * (1 + stopPct) * 0.9999 ? 'Трейлинг-стоп' : 'Стоп 3%'), false); continue; }
          if (ps.take && l <= ps.take) { closePos(ps, Math.min(o, ps.take), i, 'Цель', false); continue; }
        }
      }
      // 3) переоценка по закрытию, перенос шортов, трейлинг
      let mv = 0;
      for (const ps of open) {
        const s = D.S[ps.t]; const c = s.c[i];
        if (c === c) { ps.last = c; ps.best = ps.side > 0 ? Math.max(ps.best, s.h[i]) : Math.min(ps.best, s.l[i]); }
        if (ps.side < 0) { const cr = ps.qty * ps.last * C.shortDay; cash -= cr; ps.carry += cr; }
        const m = ps.m;
        if (m.be) { const ea = ps.entryAdj || ps.entry; if ((ps.side * (ps.best / ea - 1)) >= m.be * stopPct) ps.stop = ps.side > 0 ? Math.max(ps.stop, ea) : Math.min(ps.stop, ea); }
        if (m.trail && c === c) { // трейлинг-стоп по ATR от лучшей цены
          const a = I.atr(s)[i]; const ts = ps.best - ps.side * m.trail * a;
          ps.stop = ps.side > 0 ? Math.max(ps.stop, ts) : Math.min(ps.stop, ts);
        }
        mv += ps.side * ps.qty * ps.last;
      }
      equity = cash + mv;
      const g = reg[i];
      // 4) сигналы выхода по закрытию → выход по открытию завтра
      if (canSig(i)) {
        for (const ps of open) {
          if (pend.some(o => o.pos === ps)) continue;
          const s = D.S[ps.t]; if (!(s.c[i] === s.c[i])) continue;
          const m = ps.m, held = i - ps.i0 + 1;
          let why = null;
          if (m.hold && held >= m.hold) why = 'Время (' + m.hold + ' дн.)';
          else if (m.F.exitSig(s, i, ps, m.p)) why = 'Сигнал выхода';
          else if (m.regExit && !m.on.includes(m.RGm.g[i])) why = 'Смена режима';
          else if (m.NGm && m.NGm.exit && ps.side * newsSum(s, i, 1) <= -m.NGm.exit) why = 'Новость против позиции';
          else if (m.F.rotation && isRotDay(i) && !rotKeep(m, ps.t, i)) why = 'Выпала из лидеров';
          if (why) pend.push({ type: 'exit', t: ps.t, pos: ps, reason: why, day: i });
        }
      }
      // 5) сигналы входа
      peak = Math.max(peak, equity);
      const ddNow = 1 - equity / peak;
      let mp = P.ddCut && ddNow >= P.ddCut.at ? P.ddCut.maxPos : maxPos;
      if (P.maxPosFlat !== undefined && reg[i] === 0) mp = Math.min(mp, P.maxPosFlat);
      if (P.pause && i < pauseUntil) mp = 0;
      if (canSig(i) && open.length - pend.filter(o => o.type === 'exit').length < mp) {
        const cands = [];
        const L = liquid(i);
        const held = new Set(open.map(x => x.t));
        for (const m of mods) {
          const gm = m.RGm.g[i];
          if (!m.on.includes(gm)) continue;
          if (m.vmax && m.RGm.vol[i] > m.vmax) continue;
          if (m.maxOpen && open.filter(x => x.m.k === m.k).length >= m.maxOpen) continue;
          if (m.F.rotation) {
            if (!isRotDay(i)) continue;
            const rk = rotRank(m, i, L).slice(0, m.p.k || 3);
            for (const [t, s, sc] of rk) if (!held.has(t) && entryOk(m, s, i) && newsOk(s, i, m.F.side, m.NGm)) cands.push({ t, s, sc: sc + (m.prio || 0) * 10 + newsBoost(s, i, m.F.side, m.NGm), m, g: gm });
            continue;
          }
          for (const [t, s] of L) {
            if (held.has(t) || !entryOk(m, s, i)) continue;
            const sc = m.F.entry(s, i, m.p); if (sc > 0 && newsOk(s, i, m.F.side, m.NGm)) cands.push({ t, s, sc: sc + (m.prio || 0) * 10 + newsBoost(s, i, m.F.side, m.NGm), m, g: gm });
          }
        }
        cands.sort((a, b) => b.sc - a.sc);
        const used = new Set();
        let free = mp - open.length + pend.filter(o => o.type === 'exit').length;
        for (const c of cands) {
          if (free <= 0) break; if (used.has(c.t)) continue; used.add(c.t);
          pendEntries.push({ t: c.t, side: c.m.F.side, m: c.m, reg: c.g, score: c.sc, slip: slip(I.av(c.s, 60)[i], C), day: i, why: whyOf(c.m, c.s, i, c.g) });
          free--;
        }
      }
      daily.push({ i, d, equity, cash, n: open.length, expo: open.reduce((a, x) => a + x.qty * x.last, 0) / Math.max(1, equity), g, unr: open.reduce((a, x) => a + x.side * (x.last - x.entry) * x.qty, 0) });
      if (opt.onDay) opt.onDay(i, open);
    }
    if (opt.live) return { trades, daily, feesPaid, commPaid, P, open, pendEntries, pendExits: pend.filter(o => o.type === 'exit'), regime: reg, vol: RG.vol };
    // закрываем остаток по последнему закрытию (для статистики)
    for (const ps of open.slice()) closePos(ps, ps.last * (1 - ps.side * ps.slip), to, 'Конец периода', true, true);
    if (daily.length) daily[daily.length - 1].equity = cash;
    return { trades, daily, feesPaid, commPaid, P };

    function closePos(ps, px, i, reason, mkt, final) {
      const notional = ps.qty * px, fee = notional * C.comm;
      if (!final) touchMonth(D.days[i]);
      if (ps.side > 0) cash += notional - fee; else cash -= notional + fee;
      commPaid += fee;
      const pnl = ps.side * (px - ps.entry) * ps.qty - ps.fees - fee - ps.carry + (ps.divs || 0);
      trades.push({ t: ps.t, side: ps.side, fam: ps.fam, mod: ps.m.k, reg: ps.reg, d0: ps.d0, d1: D.days[i], i0: ps.i0, i1: i, entry: ps.entry, exit: px, qty: ps.qty,
        pnl, r: pnl / ps.risk, ret: ps.side * (px / ps.entry - 1), reason, days: i - ps.i0 + 1, open: !!final, why: ps.why, stop0: ps.stop0, take0: ps.take, divs: ps.divs || 0 });
      if (P.pause && !final) { if (pnl < 0) { lossRun++; if (lossRun >= P.pause.k) { pauseUntil = i + P.pause.days; lossRun = 0; } } else lossRun = 0; }
      open.splice(open.indexOf(ps), 1);
    }
    function isRotDay(i) { const a = new Date(D.days[i]).getUTCDay(), b = i + 1 < N ? new Date(D.days[i + 1]).getUTCDay() : 1; return b <= a || i + 1 >= N; } // последний торговый день недели
    function rotRank(m, i, L) {
      const k = m.k + ':' + i; if (lastRot[k]) return lastRot[k];
      const arr = []; for (const [t, s] of L) { const sc = m.F.entry(s, i, m.p); if (sc > 0) arr.push([t, s, sc]); }
      arr.sort((a, b) => b[2] - a[2]); return (lastRot[k] = arr);
    }
    function rotKeep(m, t, i) { const rk = rotRank(m, i, liquid(i)); const idx = rk.findIndex(x => x[0] === t); return idx >= 0 && idx < (m.p.keep || 6); }
  }

  // ---------- метрики ----------
  function metrics(res, a, b, capital0) {
    // a, b — индексы дней (включительно); метрики считаются по дневной кривой капитала и по сделкам, закрытым в периоде
    const dd = res.daily.filter(x => x.i >= a && x.i <= b); if (dd.length < 2) return null;
    const i0 = res.daily.indexOf(dd[0]); const e0 = i0 > 0 ? res.daily[i0 - 1].equity : (capital0 || res.P.capital || 300000);
    const eq = dd.map(x => x.equity), rets = []; let prev = e0;
    for (const e of eq) { rets.push(e / prev - 1); prev = e; }
    const yrs = dd.length / 252, total = eq[eq.length - 1] / e0 - 1, cagr = Math.pow(Math.max(1e-9, 1 + total), 1 / yrs) - 1;
    let pk = e0, mdd = 0, uw = 0, maxUw = 0, ulc = 0;
    for (const e of eq) { if (e >= pk) { pk = e; uw = 0; } else uw++; maxUw = Math.max(maxUw, uw); const ddp = (pk - e) / pk; mdd = Math.max(mdd, ddp); ulc += ddp * ddp * 1e4; }
    const mean = rets.reduce((s, x) => s + x, 0) / rets.length, sd = Math.sqrt(rets.reduce((s, x) => s + (x - mean) ** 2, 0) / rets.length);
    const dn = Math.sqrt(rets.reduce((s, x) => s + Math.min(0, x) ** 2, 0) / rets.length);
    const sorted = rets.slice().sort((x, y) => x - y), k5 = Math.max(1, Math.floor(rets.length * 0.05));
    const var95 = -sorted[k5 - 1], cvar95 = -sorted.slice(0, k5).reduce((s, x) => s + x, 0) / k5;
    const tr = res.trades.filter(t => t.i1 >= a && t.i1 <= b);
    const wins = tr.filter(t => t.pnl > 0), loss = tr.filter(t => t.pnl <= 0);
    const gp = wins.reduce((s, t) => s + t.pnl, 0), gl = -loss.reduce((s, t) => s + t.pnl, 0);
    let streak = 0, maxStreak = 0; for (const t of tr) { if (t.pnl <= 0) { streak++; maxStreak = Math.max(maxStreak, streak); } else streak = 0; }
    const rs = tr.map(t => t.r), mr = rs.length ? rs.reduce((s, x) => s + x, 0) / rs.length : 0, sr = rs.length > 1 ? Math.sqrt(rs.reduce((s, x) => s + (x - mr) ** 2, 0) / (rs.length - 1)) : 0;
    const expo = dd.reduce((s, x) => s + x.expo, 0) / dd.length;
    return {
      days: dd.length, years: +yrs.toFixed(2), total, cagr, mdd, maxUw, ulcer: Math.sqrt(ulc / eq.length),
      sharpe: sd ? mean / sd * Math.sqrt(252) : 0, sortino: dn ? mean / dn * Math.sqrt(252) : 0, calmar: mdd ? cagr / mdd : 0,
      vol: sd * Math.sqrt(252), var95, cvar95, n: tr.length, perYear: tr.length / yrs, win: tr.length ? wins.length / tr.length : 0,
      pf: gl ? gp / gl : (gp ? 99 : 0), avgWinR: wins.length ? wins.reduce((s, t) => s + t.r, 0) / wins.length : 0, avgLossR: loss.length ? loss.reduce((s, t) => s + t.r, 0) / loss.length : 0,
      expR: mr, tstat: sr ? mr / sr * Math.sqrt(rs.length) : 0, maxLossStreak: maxStreak, worstR: rs.length ? Math.min(...rs) : 0, bestR: rs.length ? Math.max(...rs) : 0,
      avgDays: tr.length ? tr.reduce((s, t) => s + t.days, 0) / tr.length : 0, expo,
    };
  }

  const API = { prepare, run, metrics, regimeOf, FAM, I, COST_TRADER, ind: { ema, sma, atr, rsi, hhv, llv } };
  if (typeof module !== 'undefined' && module.exports) module.exports = API; else root.RG = API;
})(typeof window !== 'undefined' ? window : globalThis);
