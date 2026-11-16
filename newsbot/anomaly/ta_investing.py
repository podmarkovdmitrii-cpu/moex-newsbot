# -*- coding: utf-8 -*-
"""Технический анализ как в Investing.com (раздел «Технический анализ»): 12 индикаторов, 12 скользящих средних, точки разворота,
свечные модели и итоговая сводка «Активно продавать … Активно покупать».

Индикаторы (правила «покупать / продавать / нейтрально» — как в таблице Investing.com):
  RSI(14) >50 покупать, <50 продавать (>70 перекупленность, <30 перепроданность);  STOCH(9,6);  STOCHRSI(14);  MACD(12,26) >0;
  ADX(14) >25 — сила тренда, сторона по +DI/−DI;  Williams %R;  CCI(14) >100 / <−100;  ATR(14) — нейтрально (волатильность);
  Highs/Lows(14);  Ultimate Oscillator(7,14,28);  ROC(12) >0;  Bull/Bear Power(13).
Скользящие средние: MA5, 10, 20, 50, 100, 200 — простые и экспоненциальные: цена выше — покупать, ниже — продавать.
Сводка: (покупать − продавать) / число сигналов по индикаторам и по средним; итог — их среднее, метка от −2 (активно продавать)
до +2 (активно покупать). Перекупленность/перепроданность в сводке нейтральны, но сохраняются отдельными признаками.
Точки разворота (классические, Фибоначчи, Камарилья) — по максимуму, минимуму и закрытию прошлого бара.
Свечные модели: доджи, молот, падающая звезда, поглощения, харами, просвет в облаках / завеса, утренняя/вечерняя звезда,
три белых солдата / три чёрные вороны: +1 бычья, −1 медвежья.
Все значения — на закрытии бара (без сдвига): для прогноза «на завтра» их нужно сдвигать на 1 бар.
"""
import numpy as np
import pandas as pd

IND = ["rsi", "stoch", "stochrsi", "macd", "adx", "willr", "cci", "hl", "uo", "roc", "bbp"]
MAS = [5, 10, 20, 50, 100, 200]


def _wilder(x, n):
    return x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def indicators(df):
    """df: o, h, l, c (по порядку времени) → DataFrame признаков на закрытии каждого бара."""
    o, h, l, c = (df[k].astype(float) for k in ("o", "h", "l", "c"))
    f = pd.DataFrame(index=df.index)
    d = c.diff()
    rs = _wilder(d.clip(lower=0), 14) / _wilder((-d).clip(lower=0), 14).replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    f["rsi"] = rsi
    ll9, hh9 = l.rolling(9).min(), h.rolling(9).max()
    k = 100 * (c - ll9) / (hh9 - ll9).replace(0, np.nan)
    f["stoch"] = k.rolling(6).mean()
    rmin, rmax = rsi.rolling(14).min(), rsi.rolling(14).max()
    f["stochrsi"] = 100 * (rsi - rmin) / (rmax - rmin).replace(0, np.nan)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    f["macd"] = macd / c
    f["macd_h"] = (macd - macd.ewm(span=9, adjust=False).mean()) / c
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = _wilder(tr, 14)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    pdi, mdi = 100 * _wilder(pdm, 14) / atr, 100 * _wilder(mdm, 14) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    f["adx"] = _wilder(dx, 14)
    f["di"] = pdi - mdi
    hh14, ll14 = h.rolling(14).max(), l.rolling(14).min()
    f["willr"] = -100 * (hh14 - c) / (hh14 - ll14).replace(0, np.nan)
    tp = (h + l + c) / 3
    md = tp.rolling(14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    f["cci"] = (tp - tp.rolling(14).mean()) / (0.015 * md.replace(0, np.nan))
    f["atr"] = atr / c
    f["hl"] = ((h - h.shift()).clip(lower=0).rolling(14).sum() - (l.shift() - l).clip(lower=0).rolling(14).sum()) / c  # прирост максимумов против минимумов за 14
    bp = c - pd.concat([l, c.shift()], axis=1).min(axis=1)
    trr = pd.concat([h, c.shift()], axis=1).max(axis=1) - pd.concat([l, c.shift()], axis=1).min(axis=1)
    a7, a14, a28 = (bp.rolling(n).sum() / trr.rolling(n).sum().replace(0, np.nan) for n in (7, 14, 28))
    f["uo"] = 100 * (4 * a7 + 2 * a14 + a28) / 7
    f["roc"] = c / c.shift(12) - 1
    e13 = c.ewm(span=13, adjust=False).mean()
    f["bbp"] = ((h - e13) + (l - e13)) / c
    for n in MAS:
        f[f"sma{n}"] = c / c.rolling(n).mean() - 1
        f[f"ema{n}"] = c / c.ewm(span=n, adjust=False, min_periods=n).mean() - 1
    # сигналы по правилам Investing.com: +1 покупать, −1 продавать, 0 нейтрально
    sig = pd.DataFrame(index=df.index)
    sig["rsi"] = np.select([rsi > 70, rsi < 30, rsi > 50, rsi < 50], [0, 0, 1, -1], 0)
    sig["stoch"] = np.select([f.stoch > 80, f.stoch < 20, f.stoch > 50, f.stoch < 50], [0, 0, 1, -1], 0)
    sig["stochrsi"] = np.select([f.stochrsi > 80, f.stochrsi < 20, f.stochrsi > 50, f.stochrsi < 50], [0, 0, 1, -1], 0)
    sig["macd"] = np.sign(macd).fillna(0)
    sig["adx"] = np.where(f.adx > 25, np.sign(f.di), 0)
    sig["willr"] = np.select([f.willr > -20, f.willr < -80, f.willr > -50, f.willr < -50], [0, 0, 1, -1], 0)
    sig["cci"] = np.select([f.cci > 100, f.cci < -100], [1, -1], 0)
    sig["hl"] = np.sign(f.hl).fillna(0)
    sig["uo"] = np.select([f.uo > 70, f.uo < 30, f.uo > 50, f.uo < 50], [0, 0, 1, -1], 0)
    sig["roc"] = np.sign(f.roc).fillna(0)
    sig["bbp"] = np.sign(f.bbp).fillna(0)
    f["ob"] = ((rsi > 70).astype(int) + (f.stoch > 80) + (f.willr > -20) + (f.uo > 70) + (f.stochrsi > 80)).astype(float)   # сколько «перекуплено»
    f["os"] = ((rsi < 30).astype(int) + (f.stoch < 20) + (f.willr < -80) + (f.uo < 30) + (f.stochrsi < 20)).astype(float)   # «перепродано»
    f["ind_score"] = sig[IND].sum(axis=1) / len(IND)
    mas = np.sign(pd.concat([f[f"sma{n}"] for n in MAS] + [f[f"ema{n}"] for n in MAS], axis=1)).fillna(0)
    f["ma_score"] = mas.sum(axis=1) / mas.shape[1]
    f["score"] = (f.ind_score + f.ma_score) / 2
    f["label"] = np.select([f.score >= 0.5, f.score >= 0.1, f.score <= -0.5, f.score <= -0.1], [2, 1, -2, -1], 0)
    # точки разворота по прошлому бару
    ph, pl, pcl = h.shift(), l.shift(), c.shift()
    P = (ph + pl + pcl) / 3
    rng = (ph - pl)
    a = atr.replace(0, np.nan)
    f["pv_p"] = (c - P) / a
    f["pv_r1"] = (c - (2 * P - pl)) / a
    f["pv_s1"] = (c - (2 * P - ph)) / a
    f["pv_r2"] = (c - (P + rng)) / a
    f["pv_s2"] = (c - (P - rng)) / a
    f["pv_fr1"] = (c - (P + 0.382 * rng)) / a
    f["pv_fs1"] = (c - (P - 0.382 * rng)) / a
    f["pv_cr3"] = (c - (pcl + rng * 1.1 / 4)) / a
    f["pv_cs3"] = (c - (pcl - rng * 1.1 / 4)) / a
    # свечные модели
    body, rngc = (c - o), (h - l).replace(0, np.nan)
    ab = body.abs()
    lo_sh = pd.concat([o, c], axis=1).min(axis=1) - l
    hi_sh = h - pd.concat([o, c], axis=1).max(axis=1)
    pb, po, pc_ = body.shift(), o.shift(), c.shift()
    cd = pd.DataFrame(index=df.index)
    f["cdl_doji"] = (ab / rngc < 0.1).astype(float)
    cd["hammer"] = ((lo_sh > 2 * ab) & (hi_sh < ab) & (c.shift() < c.shift(5))).astype(float)
    cd["star"] = -((hi_sh > 2 * ab) & (lo_sh < ab) & (c.shift() > c.shift(5))).astype(float)
    cd["engulf"] = np.select([(pb < 0) & (body > 0) & (c >= po) & (o <= pc_), (pb > 0) & (body < 0) & (c <= po) & (o >= pc_)], [1, -1], 0)
    cd["harami"] = np.select([(pb < 0) & (body > 0) & (c < po) & (o > pc_) & (ab < pb.abs()), (pb > 0) & (body < 0) & (c > po) & (o < pc_) & (ab < pb.abs())], [1, -1], 0)
    mid = (po + pc_) / 2
    cd["pierce"] = np.select([(pb < 0) & (body > 0) & (o < pc_) & (c > mid) & (c < po), (pb > 0) & (body < 0) & (o > pc_) & (c < mid) & (c > po)], [1, -1], 0)
    small = body.shift().abs() < 0.3 * rngc.shift()
    cd["mstar"] = np.select([(body.shift(2) < 0) & small & (body > 0) & (c > (o.shift(2) + c.shift(2)) / 2),
                             (body.shift(2) > 0) & small & (body < 0) & (c < (o.shift(2) + c.shift(2)) / 2)], [1, -1], 0)
    three = (body > 0) & (body.shift() > 0) & (body.shift(2) > 0) & (c > c.shift()) & (c.shift() > c.shift(2))
    threeb = (body < 0) & (body.shift() < 0) & (body.shift(2) < 0) & (c < c.shift()) & (c.shift() < c.shift(2))
    cd["three"] = np.select([three, threeb], [1, -1], 0)
    f["cdl"] = cd.sum(axis=1)
    for k_ in ("hammer", "star", "engulf", "harami", "pierce", "mstar", "three"):
        f["cdl_" + k_] = cd[k_]
    return f.replace([np.inf, -np.inf], np.nan)


SIGNED = ["rsi", "stoch", "stochrsi", "macd", "macd_h", "di", "willr", "cci", "hl", "uo", "roc", "bbp", "ind_score", "ma_score", "score", "label",
          "cdl", "cdl_hammer", "cdl_star", "cdl_engulf", "cdl_harami", "cdl_pierce", "cdl_mstar", "cdl_three"] + [f"{a}{n}" for a in ("sma", "ema") for n in MAS] \
    + ["pv_p", "pv_r1", "pv_s1", "pv_r2", "pv_s2", "pv_fr1", "pv_fs1", "pv_cr3", "pv_cs3"]
CENTER = {"rsi": 50, "stoch": 50, "stochrsi": 50, "willr": -50, "uo": 50}


def oriented(F, s):
    """Признаки «в сторону сигнала»: для падения (s = −1) знак меняется, у осцилляторов — относительно середины шкалы;
    перекупленность/перепроданность меняются местами."""
    G = F.copy()
    for k in SIGNED:
        if k in G.columns:
            G[k] = (G[k] - CENTER.get(k, 0)) * s
    if "ob" in G.columns and "os" in G.columns:
        ob, os_ = G["ob"].copy(), G["os"].copy()
        G["ob"] = np.where(s > 0, ob, os_)
        G["os"] = np.where(s > 0, os_, ob)
    return G
