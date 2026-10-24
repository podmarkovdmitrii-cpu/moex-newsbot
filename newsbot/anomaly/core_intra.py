# -*- coding: utf-8 -*-
"""Внутридневной прогноз аномалии: вероятность, что бумага, ещё не ставшая аномалией к часу c, станет ею до конца дня.
Одни и те же признаки считаются по часовым свечам (обучение) и по текущим котировкам + снимкам оборота (в работе бота)."""
import datetime as dt

import numpy as np
import pandas as pd

CHK = [10, 11, 12, 13, 14, 15, 16, 17]  # с 10 часов: утренняя сессия даёт много ложных тревог (проверка на 2021–2026: AUC 0,917 → 0,923)
DCOLS = ["tier", "sd60", "sd20", "an20", "an250", "since_an", "vr1", "nw_n", "nw_w", "nw_wmax", "ix_r1", "ix_sd20", "lval", "ema20d", "dhi20", "dlo20", "month", "dow", "to_ex"]
FEAT = ["c", "cx", "acx", "cvol", "cvol_v", "cvol_typ", "lastv", "rng", "ixr", "p_pre", "nw_day", "nw_lasth"] + DCOLS
MSK = dt.timedelta(hours=3)


def thresholds(tier, sd60):
    thr = np.maximum(np.select([tier == 1, tier == 2], [0.04, 0.06], 0.08), 3 * sd60)
    vth = np.where(tier == 3, 6, 4)
    return thr, vth


def pivot(a):
    df = pd.DataFrame([r[:6] for r in a], columns=["k", "o", "h", "l", "c", "v"])
    df["d"] = pd.to_datetime(df["k"].str[:10]); df["hr"] = df["k"].str[11:13].astype(int)
    df = df[df.d.dt.weekday < 5]
    C = df.pivot_table(index="d", columns="hr", values="c", aggfunc="last")
    V = df.pivot_table(index="d", columns="hr", values="v", aggfunc="sum").fillna(0)
    Hh = df.pivot_table(index="d", columns="hr", values="h", aggfunc="max")
    L = df.pivot_table(index="d", columns="hr", values="l", aggfunc="min")
    return C, V, Hh, L


def news_hours(events):
    nh = {}
    for e in events:
        w = dt.datetime.utcfromtimestamp(e["ts"]) + MSK
        if w.weekday() < 5:
            nh.setdefault((e["tk"], pd.Timestamp(w.date())), []).append(w.hour + w.minute / 60)
    return nh


def typical_share(V):
    """Средняя доля дневного оборота, набранная к концу часа c (последние 60 дней)."""
    cum = V.cumsum(axis=1)
    sh = cum.div(V.sum(axis=1).replace(0, np.nan), axis=0)
    return sh.rolling(60, min_periods=20).mean().shift(1)


def build_train(HH, IXH, daily, events, pre):
    ixC, _, _, _ = pivot(IXH)
    ixf_all = ixC.ffill(axis=1)
    ixl_all = ixf_all.iloc[:, -1].shift(1)
    nh = news_hours(events)
    out, typ_last = [], {}
    for t, a in HH.items():
        dd = daily[daily.t == t].set_index("d")
        if len(dd) < 100 or not a:
            continue
        C, V, Hh, L = pivot(a)
        days = C.index.intersection(dd.index)
        if len(days) < 100:
            continue
        typ = typical_share(V)
        typ_last[t] = {int(c): float(v) for c, v in V.loc[V.index[-60:]].cumsum(axis=1).div(V.loc[V.index[-60:]].sum(axis=1).replace(0, np.nan), axis=0).mean().items() if pd.notna(v)}
        C, V, Hh, L, typ = C.loc[days], V.loc[days], Hh.loc[days], L.loc[days], typ.loc[days]
        Cf = C.ffill(axis=1)
        pc_ = C.ffill(axis=1).iloc[:, -1].shift(1)
        med = 10 ** dd.loc[days, "lval"]
        tier, sd60 = dd.loc[days, "tier"].values, dd.loc[days, "sd60"].values
        thr, vth = thresholds(tier, sd60)
        cumV = V.cumsum(axis=1)
        ixf = ixf_all.reindex(days)
        ixl = ixl_all.reindex(days)
        for c in CHK:
            if c not in C.columns:
                continue
            last = Cf[c]
            ixr = (ixf[c] / ixl - 1) if c in ixf.columns else pd.Series(np.nan, index=days)
            cx = (last / pc_ - 1) - ixr.fillna(0)
            cv = cumV[c]
            cvol = cv / med
            reached = (cx.abs().values >= thr) | ((cvol.values >= vth) & (cv.values >= 30e6))
            hi = Hh[[h for h in Hh.columns if h <= c]].max(axis=1); lo = L[[h for h in L.columns if h <= c]].min(axis=1)
            prev_v = cumV[max([h for h in cumV.columns if h < c], default=c)] if any(h < c for h in cumV.columns) else 0
            f = pd.DataFrame({"d": days, "t": t, "c": c, "cx": cx.values, "acx": np.abs(cx.values) / thr, "cvol": cvol.values, "cvol_v": cvol.values / vth,
                              "cvol_typ": (cvol / typ[c].replace(0, np.nan)).values if c in typ.columns else np.nan,
                              "lastv": ((cv - prev_v) / med).values, "rng": ((hi - lo) / pc_).values / sd60, "ixr": ixr.values,
                              "reached": reached, "y": dd.loc[days, "y"].values, "y_px": dd.loc[days, "y_px"].values})
            for k in DCOLS:
                f[k] = dd.loc[days, k].values
            f = f.merge(pre, on=["d", "t"], how="left")
            f["nw_day"] = [sum(1 for h in nh.get((t, d), []) if 9.9 <= h < c + 1) for d in days]
            f["nw_lasth"] = [sum(1 for h in nh.get((t, d), []) if c <= h < c + 1) for d in days]
            out.append(f[~f.reached])
    X = pd.concat(out, ignore_index=True)
    return X, typ_last


def build_live(md, ix_chg, hour, today_daily, p_pre, typ, prev_val, news_hours_today):
    """md — котировки (SECID, LAST, VALTODAY, HIGH, LOW, LASTTOPREVPRICE, PREVPRICE); today_daily — признаки дня по бумагам (индекс t);
    prev_val — оборот бумаги час назад; news_hours_today — {тикер: [час новости,...]}. Возвращает таблицу признаков бумаг, ещё не ставших аномалией."""
    rows = []
    c = hour
    for r in md:
        t = r["SECID"]
        if t not in today_daily.index or not r.get("LAST"):
            continue
        dd = today_daily.loc[t]
        tier, sd60 = np.array([dd["tier"]]), np.array([dd["sd60"]])
        thr, vth = thresholds(tier, sd60)
        thr, vth = float(thr[0]), float(vth[0])
        cx = (r.get("LASTTOPREVPRICE") or 0) / 100 - ix_chg
        val = r.get("VALTODAY") or 0
        med = 10 ** dd["lval"]
        cvol = val / med
        if abs(cx) >= thr or (cvol >= vth and val >= 30e6):
            continue
        prev = r.get("PREVPRICE") or (r["LAST"] / (1 + (r.get("LASTTOPREVPRICE") or 0) / 100))
        ts = typ.get(t, {}).get(str(c)) or typ.get(t, {}).get(c)
        pv = prev_val.get(t)
        f = dict(t=t, c=c, cx=cx, acx=abs(cx) / thr, cvol=cvol, cvol_v=cvol / vth, cvol_typ=(cvol / ts) if ts else np.nan,
                 lastv=((val - pv) / med) if pv is not None else np.nan,
                 rng=((r.get("HIGH") or r["LAST"]) - (r.get("LOW") or r["LAST"])) / prev / sd60[0] if prev else np.nan, ixr=ix_chg,
                 p_pre=p_pre.get(t, np.nan), nw_day=sum(1 for h in news_hours_today.get(t, []) if 9.9 <= h < c + 1),
                 nw_lasth=sum(1 for h in news_hours_today.get(t, []) if c <= h < c + 1), thr=thr)
        for k in DCOLS:
            f[k] = dd[k]
        rows.append(f)
    return pd.DataFrame(rows)
