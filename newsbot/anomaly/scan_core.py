# -*- coding: utf-8 -*-
"""Сканер сделок по дневным свечам (проверка №24, боевой сканер бота идей) — одни и те же признаки и уровни для обучения и для бота.

Сделка: вход по закрытию дня; стоп «за уровнем» (ближайший сильный уровень разворотов в 1,2–3,2 ATR от входа + 0,3 ATR, иначе 2,5 ATR —
как в проверке №23 и кнопке «🤖» бота); цель 5R; выход по сроку — через 60 торговых дней. Модель (градиентный бустинг) оценивает
результат сделки в R по признакам состояния бумаги и рынка на закрытии дня; сторона сделки (лонг/шорт) — тоже признак, направленные
признаки умножаются на сторону. Все признаки считаются только по свечам до дня входа включительно.
"""
import numpy as np
import pandas as pd

FEATS = ["side", "tr_s", "sl_s", "mom5_s", "mom20_s", "mom60_s", "pos60_s", "rsi_s", "d50_s", "d200_s", "atrp", "atr_pct", "vr5", "val_l",
         "gap_s", "ix_tr_s", "ix_mom20_s", "rs20_s", "stop_atr", "brk_s", "inside", "dow"]
TARGET_R = 5.0
HOLD = 60


def atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    return pd.Series(tr).rolling(n).mean().values


def strong_levels(h, l, i, A):
    """Уровни разворотов (3 бара с каждой стороны) за 250 дней до i (без последних 3 дней), склейка ближе 0,35 ATR, сильный — ≥2 касания."""
    from numpy.lib.stride_tricks import sliding_window_view
    a, b = max(3, i - 250), i - 3
    if b - a < 20:
        return np.array([])
    H, L = h[a - 3:b + 3], l[a - 3:b + 3]
    wh = sliding_window_view(H, 7).max(1)
    wl = sliding_window_view(L, 7).min(1)
    pts = np.r_[H[3:-3][H[3:-3] == wh], L[3:-3][L[3:-3] == wl]]
    if not len(pts):
        return np.array([])
    pts.sort()
    grp = np.r_[0, np.cumsum(np.diff(pts) > 0.35 * A)]
    cnt = np.bincount(grp)
    return (np.bincount(grp, weights=pts) / cnt)[cnt >= 2]


def level_stop(h, l, c, i, A, side):
    """Расстояние до стопа (в цене) для входа по закрытию бара i."""
    lv = strong_levels(h, l, i, A)
    dist = (c[i] - lv) * side
    ok = (dist >= 1.2 * A) & (dist <= 3.2 * A)
    return (float(dist[ok].min() + 0.3 * A), True) if ok.any() else (2.5 * A, False)


def base_arrays(df, ix=None):
    """Признаки на каждом баре одной бумаги (без стороны). df: d, o, h, l, c, v. ix: Series закрытий индекса (index = дата)."""
    o, h, l, c, v = (df[k].to_numpy(float) for k in ("o", "h", "l", "c", "v"))
    A = atr(h, l, c)
    s = pd.Series(c)
    m50, m200 = s.rolling(50).mean().values, s.rolling(200).mean().values
    sl = np.r_[np.full(10, np.nan), m50[10:] - m50[:-10]]
    hi60, lo60 = pd.Series(h).rolling(60).max().values, pd.Series(l).rolling(60).min().values
    d = s.diff()
    up, dn = d.clip(lower=0).rolling(14).mean(), (-d.clip(upper=0)).rolling(14).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).values
    sh = lambda x, k: np.r_[np.full(k, np.nan), x[:-k]]  # noqa: E731
    out = dict(A=A, c=c, h=h, l=l,
               tr=np.sign(c - m50), sl=np.sign(sl),
               mom5=(c - sh(c, 5)) / A, mom20=(c - sh(c, 20)) / A, mom60=(c - sh(c, 60)) / A,
               pos60=(c - lo60) / np.where(hi60 - lo60 > 0, hi60 - lo60, np.nan) - 0.5,
               rsi=(rsi - 50) / 50, d50=(c - m50) / A, d200=(c - m200) / A,
               atrp=pd.Series(A / c).rolling(250, min_periods=120).rank(pct=True).values, atr_pct=A / c,
               vr5=pd.Series(v).rolling(5).mean().values / pd.Series(v).rolling(60).mean().values,
               val_l=np.log10(pd.Series(v).rolling(60, min_periods=20).median().values + 1),
               gap=(o - sh(c, 1)) / A,
               brk=np.where(c >= sh(pd.Series(h).rolling(20).max().values, 1), 1.0, np.where(c <= sh(pd.Series(l).rolling(20).min().values, 1), -1.0, 0.0)),
               inside=((h <= sh(h, 1)) & (l >= sh(l, 1))).astype(float),
               dow=pd.to_datetime(df["d"]).dt.weekday.to_numpy(float),
               val_med=pd.Series(v).rolling(60, min_periods=20).median().values)
    if ix is not None:
        ixc = ix.reindex(pd.to_datetime(df["d"]).values).ffill().to_numpy(float)
        ism = pd.Series(ixc)
        im50 = ism.rolling(50).mean().values
        isl = np.r_[np.full(10, np.nan), im50[10:] - im50[:-10]]
        out["ix_tr"] = np.sign(ixc - im50) + np.sign(isl)
        out["ix_mom20"] = ixc / sh(ixc, 20) - 1
        out["rs20"] = (c / sh(c, 20) - 1) - out["ix_mom20"]
    else:
        out["ix_tr"] = out["ix_mom20"] = out["rs20"] = np.full(len(c), np.nan)
    return out


def row(B, i, side, stop_dist):
    """Вектор признаков для входа по закрытию бара i со стороной side (+1 лонг, −1 шорт)."""
    s = float(side)
    return [s, B["tr"][i] * s, B["sl"][i] * s, B["mom5"][i] * s, B["mom20"][i] * s, B["mom60"][i] * s, B["pos60"][i] * s, B["rsi"][i] * s,
            B["d50"][i] * s, B["d200"][i] * s, B["atrp"][i], B["atr_pct"][i], B["vr5"][i], B["val_l"][i], B["gap"][i] * s,
            B["ix_tr"][i] * s, B["ix_mom20"][i] * s, B["rs20"][i] * s, stop_dist / B["A"][i], B["brk"][i] * s, B["inside"][i], B["dow"][i]]


def plan(B, i, side):
    """Уровни сделки: вход (закрытие), стоп, цель 5R, признак «стоп за уровнем»."""
    A, c = B["A"][i], B["c"][i]
    dist, lvl = level_stop(B["h"], B["l"], B["c"], i, A, side)
    return dict(entry=float(c), stop=float(c - side * dist), target=float(c + side * TARGET_R * dist), risk=float(dist), level=lvl, atr=float(A))
