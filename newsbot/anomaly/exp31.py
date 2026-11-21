# -*- coding: utf-8 -*-
"""Проверка №31: сборная стратегия на дневных свечах 2008–2026 — обучение и проверка по очереди (скользящий выбор по годам).

5-минутных свечей до 2020 г. и часовых до 2012 г. нет, поэтому здесь дневные аналоги стратегий бота (сигнал — дневная ценовая аномалия
y_px, известна на закрытии; только ликвидные, оборот ≥ 10 млн ₽/день; только покупки):
  Д-S1 — аномальный рост: лимит на следующий день на откате 50% хода, тейк +25% хода, до 5 дней;
  Д-F2 — аномальный рост: покупка по закрытию дня сигнала, 10 дней;
  Д-F4 — аномальное падение: покупка по закрытию, тейк +200% хода, до 20 дней.
Издержки 0,1/0,2% (как в №15), гэп через стоп — по открытию, стоп и тейк в одном баре — стоп.

Для каждого года проверки Y = 2011…2026 ВСЁ выбирается только по годам 2008…Y−1:
  • выход: стоп (нет / 2 ATR / 3 ATR / −15%) × тейк (как сейчас / нет / 3R / 5R) × срок (свой / 20 дн.) — лучший по среднему за прошлые годы;
  • модель-фильтр для Д-F2 и Д-F4 (HGB по признакам дневной модели аномалий): модель года Y учится на сигналах до 1 декабря Y−1,
    порог «верхние 30%» — по оценкам Y−3…Y−1; брать ли фильтр — по результатам вне выборки за Y−4…Y−1 (лучше «всех» на ≥ 0,2 п.п. в ≥ 3 из 4 лет);
  • фильтры из журнала («по тренду», «рынок не против») — так же, по прошлым 4 годам.
Портфель — с ежедневной переоценкой открытых позиций: доля на сделку 2,5 / 5 / 10% × потолок загрузки 100 / 150% / без потолка.
Плюс счёт сигналов по годам (проверка «мало сделок в 2025»).
Не является индивидуальной инвестиционной рекомендацией.
"""
import bisect
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import exp9 as E9  # noqa: E402
import exp15 as E15  # noqa: E402
import exp22 as E22  # noqa: E402
import scan_core as SC  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
ND = 20
OWN = {"S1": 5, "F2": 10, "F4": 20}
STOPS = ["нет", "2 ATR", "3 ATR", "−15%"]
TAKES = ["как сейчас", "нет", "3R", "5R"]
HOLDS = ["свой", "20 дн."]
Y0 = 2011


def build(X, Dd, IX):
    """Сигналы и пути (20 дней) по дневным свечам, признаки дневной модели на день сигнала."""
    S = X[(X.y_px == 1) & X.x.notna() & (X.d >= pd.Timestamp(TR.FROM))].copy()
    feats = CD.feature_cols(X)
    ixs = pd.Series({pd.Timestamp(r[0][:10]): float(r[4]) for r in IX}).sort_index()
    ixr = ixs.pct_change()
    rows = []
    for t, g in S.groupby("t"):
        if t not in Dd:
            continue
        D = CD.series(Dd[t]).reset_index(drop=True)
        if len(D) < 80:
            continue
        pos = {d: i for i, d in enumerate(D.d)}
        o, h, l, c = (D[k].to_numpy(float) for k in ("o", "h", "l", "c"))
        A = SC.atr(h, l, c)
        m50 = pd.Series(c).rolling(50).mean().values
        sl50 = np.r_[np.full(10, np.nan), m50[10:] - m50[:-10]]
        ds = [d.strftime("%Y-%m-%d") for d in D.d]
        for r in g.itertuples():
            j = pos.get(r.d)
            if j is None or j < 60 or j + ND >= len(D) or not c[j - 1] or not A[j] > 0:
                continue
            lv = float(r.lval) if r.lval == r.lval else 6.0
            if lv < 7:
                continue
            if E15.bad_path(c[j], h[j + 1:j + 1 + ND], l[j + 1:j + 1 + ND], c[j + 1:j + 1 + ND]) or abs(c[j] / c[j - 1] - 1) > 0.8:
                continue
            s = 1.0 if r.x > 0 else -1.0
            rows.append(dict(t=t, d=r.d, y=r.d.year, s=s, c0=c[j], prev=c[j - 1], mv=abs(c[j] / c[j - 1] - 1), A=float(A[j]), lval=lv,
                             o=o[j + 1:j + 1 + ND], h=h[j + 1:j + 1 + ND], l=l[j + 1:j + 1 + ND], c=c[j + 1:j + 1 + ND], dts=ds[j + 1:j + 1 + ND], d0=ds[j],
                             trend=bool(c[j] > m50[j] and sl50[j] > 0), mkt=float(ixr.get(r.d, 0) or 0), cost=float(E15.cost_of(np.array([lv]))[0])))
    B = pd.DataFrame(rows)
    F = X.drop_duplicates(["t", "d"]).set_index(["t", "d"])[[f for f in feats if f not in B.columns]]
    B = B.join(F, on=["t", "d"])
    return B, feats


def trade(x, strat, stop, take, hold):
    """Одна сделка → (доходность, индекс дня выхода, дата входа) или None (лимит не исполнен)."""
    n = OWN[strat] if hold == "свой" else 20
    o, h, l, c = x["o"][:n], x["h"][:n], x["l"][:n], x["c"][:n]
    mv = x["mv"]
    if strat == "S1":
        e = x["c0"] - 0.5 * (x["c0"] - x["prev"])
        if not l[0] <= e:
            return None
        j0, d_in = 1, x["dts"][0]           # день исполнения лимита — тейк/стоп с следующего дня
        if o[0] < e:
            e = o[0]                        # открылись ниже лимита — покупка по открытию
    else:
        e, j0, d_in = x["c0"], 0, x["d0"]
    S = {"нет": None, "2 ATR": e - 2 * x["A"], "3 ATR": e - 3 * x["A"], "−15%": e * 0.85}[stop]
    if take == "как сейчас":
        T = e * (1 + 0.25 * mv) if strat == "S1" else (e * (1 + 2.0 * mv) if strat == "F4" else None)
    elif take == "нет":
        T = None
    else:
        if S is None:
            return "skip"
        T = e + float(take[0]) * (e - S)
    for k in range(j0, n):
        if S is not None and l[k] <= S:
            return min(o[k], S) / e - 1 - x["cost"] - 0.0005, k, d_in, (e - S) / e
        if T is not None and h[k] >= T:
            return max(o[k], T) / e - 1 - x["cost"], k, d_in, ((e - S) / e if S else None)
    return c[n - 1] / e - 1 - x["cost"], n - 1, d_in, ((e - S) / e if S else None)


def year_mean(vals, years, Y0_, Y1_):
    ms = []
    for y in range(Y0_, Y1_):
        r = [v for v, yy in zip(vals, years) if yy == y and v is not None]
        if len(r) >= 8:
            ms.append(np.mean(r))
    return (float(np.mean(ms)), len(ms)) if ms else (np.nan, 0)


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    log("строк дневной модели:", len(X), "с", X.d.min().date(), "по", X.d.max().date())
    B, feats = build(X, Dd, IX)
    log(f"ликвидных сигналов {len(B)} ({round(time.time() - t0)} с); по годам (рост/падение): "
        + ", ".join(f"{y}: {int(((B.y == y) & (B.s > 0)).sum())}/{int(((B.y == y) & (B.s < 0)).sum())}" for y in sorted(B.y.unique())))
    allp = X[(X.y_px == 1)]
    log("все ценовые аномалии (любой оборот) по годам: " + ", ".join(f"{y}: {n}" for y, n in allp.d.dt.year.value_counts().sort_index().items()))
    recs = B.to_dict("records")
    years = B.y.values
    side = {"S1": 1.0, "F2": 1.0, "F4": -1.0}
    # все варианты выхода
    V = {}
    for strat in OWN:
        for st in STOPS:
            for tk in TAKES:
                for hd in HOLDS:
                    if strat == "F4" and hd == "20 дн." :
                        continue           # у F4 свой срок и есть 20 дн.
                    out = []
                    skip = False
                    for x in recs:
                        if x["s"] != side[strat]:
                            out.append(None)
                            continue
                        z = trade(x, strat, st, tk, hd)
                        if z == "skip":
                            skip = True
                            break
                        out.append(z)
                    if not skip:
                        V[(strat, st, tk, hd)] = out
    log(f"вариантов выхода: {len(V)}; {round(time.time() - t0)} с")
    YM = {}
    for k, out_ in V.items():
        d_ = {}
        for z, yy in zip(out_, years):
            if z:
                d_.setdefault(int(yy), []).append(z[0])
        YM[k] = {yy: float(np.mean(v)) for yy, v in d_.items() if len(v) >= 8}
    # модель-фильтр: оценки вне выборки по годам
    def scores(strat, y, R):
        cut = pd.Timestamp(f"{y}-01-01") - pd.Timedelta(days=30)
        sg = side[strat]
        tr = np.array([r is not None for r in R]) & (B.d.values < cut.to_datetime64()) & (B.s.values == sg)
        te = (B.y.values == y) & (B.s.values == sg)
        p = np.full(len(B), np.nan)
        if tr.sum() >= 300 and te.sum():
            yv = np.array([r if r is not None else np.nan for r in R])[tr]
            clip = np.clip(yv, np.nanquantile(yv, 0.02), np.nanquantile(yv, 0.98))
            p[te] = E22.hgb().fit(B.loc[tr, feats], clip).predict(B.loc[te, feats])
        return p
    cacheP = {}

    def P(strat, key, y):
        k = (strat, key, y)
        if k not in cacheP:
            R = [z[0] if z else None for z in V[key]]
            cacheP[k] = scores(strat, y, R)
        return cacheP[k]
    flt = {"по тренду": lambda x: x["trend"], "рынок не против": lambda x: x["mkt"] > -0.01}
    picked = []        # сделки, отобранные «вперёд»
    fixed = []         # текущие правила без выбора, все сигналы (для сравнения)
    for strat in OWN:                          # текущие правила без выбора — с 2008 г. (для сравнения и для 2008–2010)
        cur = (strat, "нет", "как сейчас", "свой")
        for i, x in enumerate(recs):
            z = V[cur][i]
            if z:
                fixed.append(dict(strat=strat, t=x["t"], d_in=z[2], d_out=x["dts"][z[1]], ret=z[0], y=x["y"]))
    log("===== ВЫБОР ПО ГОДАМ (только по прошлым годам) =====")
    for Y in range(Y0, 2027):
        for strat in OWN:
            keys = [k for k in V if k[0] == strat]
            best = None
            for k in keys:
                ms = [v for yy, v in YM[k].items() if yy < Y]
                if len(ms) >= 3 and (best is None or np.mean(ms) > best[0]):
                    best = (float(np.mean(ms)), k)
            if best is None or not best[0] > 0:
                log(f"  {Y} {strat}: выключен (лучший по прошлым годам {best[0] if best else float('nan'):+.2%})")
                continue
            k = best[1]
            vals = [z[0] if z else None for z in V[k]]
            # модель-фильтр (только F2/F4)
            use_f, thr, p = False, None, None
            if strat in ("F2", "F4"):
                ps = {y: P(strat, k, y) for y in range(Y - 4, Y)}
                pool = np.concatenate([q[np.isfinite(q)] for q in ps.values()])
                if len(pool) >= 30:
                    th4 = np.quantile(pool, 0.7)
                    wins, d_all, d_top = 0, [], []
                    for y, q in ps.items():
                        a_ = [v for v, yy in zip(vals, years) if yy == y and v is not None]
                        t_ = [v for v, yy, qq in zip(vals, years, q) if yy == y and v is not None and qq == qq and qq >= th4]
                        if a_ and t_:
                            d_all.append(np.mean(a_))
                            d_top.append(np.mean(t_))
                            wins += np.mean(t_) > np.mean(a_)
                    if d_top and np.mean(d_top) - np.mean(d_all) >= 0.002 and wins >= 3:
                        use_f = True
                        ph = np.concatenate([P(strat, k, y) for y in range(Y - 3, Y)])
                        thr = float(np.quantile(ph[np.isfinite(ph)], 0.7))
                        p = P(strat, k, Y)
            # фильтры журнала
            use_j = []
            for fn, f in flt.items():
                wins, gain = 0, []
                for y in range(Y - 4, Y):
                    a_ = [v for v, x in zip(vals, recs) if x["y"] == y and v is not None]
                    t_ = [v for v, x in zip(vals, recs) if x["y"] == y and v is not None and f(x)]
                    if len(a_) >= 5 and len(t_) >= 3:
                        gain.append(np.mean(t_) - np.mean(a_))
                        wins += np.mean(t_) > np.mean(a_)
                if gain and np.mean(gain) >= 0.002 and wins >= 3:
                    use_j.append(fn)
            n = 0
            for i, x in enumerate(recs):
                z = V[k][i]
                if x["y"] != Y or not z:
                    continue
                if use_f and not (p[i] == p[i] and p[i] >= thr):
                    continue
                if any(not flt[fn](x) for fn in use_j):
                    continue
                picked.append(dict(strat=strat, t=x["t"], d_in=z[2], d_out=x["dts"][z[1]], ret=z[0], y=Y))
                n += 1
            log(f"  {Y} {strat}: стоп {k[1]}, тейк {k[2]}, срок {k[3]} (прошлые годы {best[0]:+.2%}); модель-фильтр {'да' if use_f else 'нет'}; "
                f"фильтры журнала: {', '.join(use_j) or 'нет'}; сделок {n}")
    T = pd.DataFrame(picked)
    T0 = pd.DataFrame(fixed)
    # итоги по стратегиям
    log("===== СДЕЛКИ «ВПЕРЁД» ПО СТРАТЕГИЯМ И ГОДАМ (средняя, число) =====")
    for nm, TT in (("вперёд", T), ("текущие правила, все сигналы", T0)):
        for strat in OWN:
            g = TT[TT.strat == strat] if len(TT) else TT
            if not len(g):
                continue
            by = g.groupby("y").ret.agg(["mean", "count"])
            log(f"  {nm} {strat}: всего {len(g)}, средняя {g.ret.mean():+.2%}, прибыльных {(g.ret > 0).mean():.0%}, лет в плюсе {(by['mean'] > 0).sum()}/{len(by)}; "
                + ", ".join(f"{y}: {r['mean']:+.1%} ({int(r['count'])})" for y, r in by.iterrows()))
    # портфель с переоценкой
    CL = {}
    for t in set(T.t) | set(T0.t):
        D = CD.series(Dd[t])
        CL[t] = {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(D.d, D.c)}
    days_full = sorted(set(d for v in CL.values() for d in v if d >= "2008-06-01"))
    ent = {}
    for TT in (T, T0):
        for r in TT.itertuples():
            ent[(r.t, r.d_in)] = CL[r.t].get(r.d_in)
    out = {}
    log("===== ПОРТФЕЛЬ С ПЕРЕОЦЕНКОЙ: «вперёд» 2011–2026, текущие правила 2008–2026 (в год; 2011–15 / 2016–20 / 2021–26; макс. просадка) =====")
    for nm, TT in (("вперёд", T), ("текущие правила, все сигналы", T0)):
        for w in (0.025, 0.05, 0.10):
            for cap in (1.0, 1.5, None):
                start = f"{Y0}-01-01" if nm == "вперёд" else "2008-06-01"
                r = portfolio(TT, CL, [d for d in days_full if d >= start], w, cap)
                key = f"{nm} | доля {w:.1%} | потолок {'нет' if cap is None else format(cap, '.0%')}"
                out[key] = r
                log(f"  {key}: ×{r['final']:.2f}, {r['cagr']:+.1%} в год ({r['p1']:+.1%} / {r['p2']:+.1%} / {r['p3']:+.1%}), просадка {r['dd']:.0%}; "
                    f"сделок {r['taken']}, пропущено {r['skipped']}; загрузка макс. {r['maxload']:.0%}")
                log("     по годам: " + ", ".join(f"{y}: {v:+.1%}" for y, v in r["years"].items()))
                log(f"КРИВАЯ {key} | " + " ".join(f"{k}={v}" for k, v in r["monthly"].items()))
    json.dump(out, open(os.path.join(TR.MODELS, "exp31_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №31 =====", round(time.time() - t0), "с")


def portfolio(TT, CL, days_all, w, cap):
    by_day = {}
    for r in TT.sort_values(["d_in", "t"]).itertuples():
        by_day.setdefault(r.d_in, []).append(r)
    cash, open_, eq, loads, taken, skipped = 0.0, [], [], [], 0, 0
    for d in days_all:
        still = []
        for z in open_:
            if z["d_out"] < d:
                cash += z["size"] * z["ret"]
            else:
                still.append(z)
        open_ = still
        capn = 1 + cash
        for r in by_day.get(d, []):
            size = w * capn
            used = sum(z["size"] for z in open_)
            if cap is not None and used + size > cap * capn:
                free = cap * capn - used
                if free < size / 3:
                    skipped += 1
                    continue
                size = free
            e = CL[r.t].get(d)
            open_.append(dict(t=r.t, d_in=d, d_out=r.d_out, ret=r.ret, size=size, e=e))
            taken += 1
        mtm = 0.0
        for z in open_:
            c = CL[z["t"]].get(d)
            if c and z["e"]:
                mtm += z["size"] * (c / z["e"] - 1)
        eq.append(1 + cash + mtm)
        loads.append(sum(z["size"] for z in open_) / max(1e-9, capn))
    for z in open_:
        cash += z["size"] * z["ret"]
    s = pd.Series(eq, index=pd.to_datetime(days_all))
    s.iloc[-1] = 1 + cash
    dd = float((s / s.cummax() - 1).min())
    yrs = {}
    prev = 1.0
    for y, g in s.groupby(s.index.year):
        yrs[int(y)] = float(g.iloc[-1] / prev - 1)
        prev = float(g.iloc[-1])

    def per(a, b):
        g = s[(s.index.year >= a) & (s.index.year <= b)]
        base = float(s[s.index.year < a].iloc[-1]) if (s.index.year < a).any() else 1.0
        return float((g.iloc[-1] / base) ** (1 / (b - a + 1)) - 1) if len(g) else float("nan")
    n_years = (s.index[-1] - s.index[0]).days / 365.25
    return dict(final=float(s.iloc[-1]), cagr=float(max(s.iloc[-1], 1e-9) ** (1 / n_years) - 1), dd=dd, years=yrs, taken=taken, skipped=skipped,
                maxload=float(max(loads)), p1=per(2011, 2015), p2=per(2016, 2020), p3=per(2021, 2026),
                monthly={k.strftime("%Y-%m"): round(float(v), 4) for k, v in s.resample("ME").last().items()})


if __name__ == "__main__":
    main()
