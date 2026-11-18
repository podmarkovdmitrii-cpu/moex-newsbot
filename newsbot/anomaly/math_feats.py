# -*- coding: utf-8 -*-
"""Математические закономерности перед резкими движениями — признаки для модели аномалий (проверка №21).

Все значения — на закрытии дня D−1 (известны утром дня D), кроме календаря (известен заранее).
  Сжатие волатильности перед выходом («пружина»): ширина полос Боллинджера и её ранг за 120 дней, полосы внутри канала Кельтнера
    и сколько дней подряд, NR4/NR7 (самый узкий диапазон за 4/7 дней), внутренние бары подряд, ATR5/ATR60, ширина канала
    Дончиана и её ранг.
  Оценки волатильности по свечам: Паркинсон, Гарман–Класс, доля ночных гэпов в волатильности, EWMA-волатильность (λ = 0,94,
    как в RiskMetrics/GARCH) к 60-дневной, «волатильность волатильности».
  Форма распределения: асимметрия и эксцесс за 60 дней, максимальный и минимальный дневной ход за 20 дней (эффект MAX).
  Связь с рынком: бета и корреляция с индексом за 60 дней, идиосинкратическая волатильность, изменение беты (20 к 120 дням).
  Память ряда: автокорреляция доходностей, отношение дисперсий VR(5) (тренд > 1, возврат к среднему < 1),
    перестановочная энтропия (порядок 3, 60 дней; ниже — упорядоченнее), связь цены и оборота, наклон оборота.
  Круглые числа: расстояние до ближайшего «круглого» уровня в ATR.
  Календарь: дней до квартальной экспирации/ребалансировки индекса (третья пятница марта, июня, сентября, декабря),
    первые дни месяца, день месяца.
"""
import numpy as np
import pandas as pd

COLS = ["mx_bbw", "mx_bbw_rk", "mx_kc_sq", "mx_sq_len", "mx_nr4", "mx_nr7", "mx_inside_n", "mx_atr_ratio", "mx_don_w", "mx_don_rk",
        "mx_park20", "mx_gk20", "mx_on_share", "mx_ewma_ratio", "mx_volvol", "mx_skew60", "mx_kurt60", "mx_max20", "mx_min20",
        "mx_beta60", "mx_corr60", "mx_idio60", "mx_beta_ch", "mx_ac1", "mx_vr5", "mx_pe60", "mx_pv_corr20", "mx_vol_slope20",
        "mx_round", "mx_to_expiry", "mx_month_start", "mx_dom"]


def _rank(s, n):
    return s.rolling(n, min_periods=n // 2).rank(pct=True)


def ticker(df, ix_c):
    """df: d, o, h, l, c, v одной бумаги (по дате); ix_c: Series закрытий индекса (index = дата). → DataFrame признаков (со сдвигом)."""
    o, h, l, c, v = (df[k].astype(float).values for k in ("o", "h", "l", "c", "v"))
    o, h, l, c, v = (pd.Series(x, index=df.index) for x in (o, h, l, c, v))
    f = pd.DataFrame(index=df.index)
    r = np.log(c).diff()
    sma, sd = c.rolling(20).mean(), c.rolling(20).std()
    bbw = 4 * sd / sma
    f["mx_bbw"] = bbw
    f["mx_bbw_rk"] = _rank(bbw, 120)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr20 = tr.rolling(20).mean()
    sq = ((sma + 2 * sd) < (sma + 1.5 * atr20)) & ((sma - 2 * sd) > (sma - 1.5 * atr20))
    f["mx_kc_sq"] = sq.astype(float)
    f["mx_sq_len"] = sq.groupby((~sq).cumsum()).cumsum().astype(float)
    rng = h - l
    f["mx_nr4"] = (rng <= rng.rolling(4).min()).astype(float)
    f["mx_nr7"] = (rng <= rng.rolling(7).min()).astype(float)
    ins = (h <= h.shift()) & (l >= l.shift())
    f["mx_inside_n"] = ins.groupby((~ins).cumsum()).cumsum().astype(float)
    f["mx_atr_ratio"] = tr.rolling(5).mean() / tr.rolling(60).mean()
    don = (h.rolling(20).max() - l.rolling(20).min()) / c
    f["mx_don_w"] = don
    f["mx_don_rk"] = _rank(don, 120)
    hl = np.log(h / l)
    f["mx_park20"] = np.sqrt((hl ** 2).rolling(20).mean() / (4 * np.log(2)))
    gk = 0.5 * hl ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
    f["mx_gk20"] = np.sqrt(gk.clip(lower=0).rolling(20).mean())
    on = np.log(o / c.shift())
    idr = np.log(c / o)
    f["mx_on_share"] = (on ** 2).rolling(20).sum() / ((on ** 2).rolling(20).sum() + (idr ** 2).rolling(20).sum())
    ew = (r ** 2).ewm(alpha=0.06, adjust=False).mean()
    f["mx_ewma_ratio"] = np.sqrt(ew / (r ** 2).rolling(60).mean())
    v5 = r.rolling(5).std()
    f["mx_volvol"] = v5.rolling(60).std() / v5.rolling(60).mean()
    f["mx_skew60"] = r.rolling(60).skew()
    f["mx_kurt60"] = r.rolling(60).kurt()
    f["mx_max20"] = r.rolling(20).max()
    f["mx_min20"] = r.rolling(20).min()
    ixr = np.log(ix_c.reindex(df["d"].values).ffill()).diff()
    ixr.index = df.index
    cov60 = r.rolling(60).cov(ixr)
    var60 = ixr.rolling(60).var()
    beta60 = cov60 / var60
    f["mx_beta60"] = beta60
    f["mx_corr60"] = r.rolling(60).corr(ixr)
    f["mx_idio60"] = (r - beta60 * ixr).rolling(60).std()
    f["mx_beta_ch"] = r.rolling(20).cov(ixr) / ixr.rolling(20).var() - r.rolling(120).cov(ixr) / ixr.rolling(120).var()
    f["mx_ac1"] = r.rolling(60).corr(r.shift())
    r5 = r.rolling(5).sum()
    f["mx_vr5"] = r5.rolling(120).var() / (5 * r.rolling(120).var())
    # перестановочная энтропия порядка 3: 6 возможных «узоров» из трёх закрытий
    a, b, cc = c.shift(2), c.shift(1), c
    pat = np.select([(a < b) & (b < cc), (a < cc) & (cc <= b), (cc <= a) & (a < b), (b <= a) & (a < cc), (b < cc) & (cc <= a), (cc <= b) & (b <= a)],
                    [0, 1, 2, 3, 4, 5], -1)
    P = pd.DataFrame({k: (pat == k).astype(float) for k in range(6)}, index=df.index).rolling(60).mean()
    f["mx_pe60"] = -(P * np.log(P.where(P > 0))).sum(axis=1, min_count=1) / np.log(6)
    lv = np.log(v.where(v > 0))
    f["mx_pv_corr20"] = r.rolling(20).corr(lv.diff())
    t_ = pd.Series(np.arange(len(df), dtype=float), index=df.index)
    f["mx_vol_slope20"] = lv.rolling(20).cov(t_) / t_.rolling(20).var()
    step = 10 ** np.floor(np.log10(c.where(c > 0))) / 2
    f["mx_round"] = (c - (c / step).round() * step).abs() / atr20
    f = f.shift(1)
    # календарь (известен заранее)
    d = pd.to_datetime(df["d"].values)
    exp_days = []
    for y in range(d.min().year, d.max().year + 2):
        for m in (3, 6, 9, 12):
            first = pd.Timestamp(y, m, 1)
            fri = first + pd.Timedelta(days=(4 - first.weekday()) % 7) + pd.Timedelta(days=14)
            exp_days.append(fri)
    E = np.array(sorted(exp_days), dtype="datetime64[ns]")
    j = np.searchsorted(E, d.values.astype("datetime64[ns]"))
    f["mx_to_expiry"] = ((E[np.minimum(j, len(E) - 1)] - d.values.astype("datetime64[ns]")) / np.timedelta64(1, "D")).astype(float)
    dd = pd.Series(d)
    first_td = dd.groupby([dd.dt.year, dd.dt.month]).cumcount().values
    f["mx_month_start"] = (first_td < 3).astype(float)
    f["mx_dom"] = d.day.astype(float)
    return f.replace([np.inf, -np.inf], np.nan)


def build(Dd, IX, series):
    """Все бумаги: DataFrame d, t + COLS. series — функция «строки → DataFrame d,o,h,l,c,v» (core_daily.series)."""
    ix = series(IX)
    ix_c = ix.set_index("d")["c"]
    out = []
    for t, a in Dd.items():
        df = series(a)
        if len(df) < 130:
            continue
        F = ticker(df.reset_index(drop=True), ix_c)
        F["d"], F["t"] = df["d"].values, t
        out.append(F)
    return pd.concat(out, ignore_index=True)
