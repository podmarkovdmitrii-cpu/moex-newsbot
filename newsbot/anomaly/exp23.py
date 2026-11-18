# -*- coding: utf-8 -*-
"""Проверка №23: как лучше ставить стоп и тейк для торговой идеи — по истории ликвидных акций Мосбиржи 2008–2026.

Идеи пользователя заранее неизвестны, поэтому входы — систематические: каждый 3-й день по каждой ликвидной бумаге
(медианный оборот за 60 дней ≥ 10 млн ₽), вход по закрытию дня, лонг и шорт. Три набора входов:
  все — лонг и шорт подряд; по тренду — лонг выше растущей SMA50, шорт ниже падающей; против тренда — наоборот.
Стопы: k·ATR14 (1; 1,5; 2; 2,5; 3; 4), фиксированный % (3; 5; 8), «за уровнем» (как в боте: ближайший уровень
разворотов в 1,2–3,2 ATR + 0,3 ATR, иначе 2,5 ATR). Тейки: один на 1…5R, без тейка (выход по сроку), «50% на 1R → стоп в
безубыток, остаток 3R», «по трети 1R/2R/3R, после Т1 безубыток, после Т2 стоп на Т1». Сроки 20 и 60 торговых дней.
Исполнение по дневным свечам: гэп за стоп/тейк — по цене открытия; стоп и тейк в одной свече — стоп; издержки 0,1% на круг
+ 0,05% проскальзывания на стопе. Результат — в R (доля риска; при риске 1% счёта на сделку R = % счёта).
Выбор лучшего — по 2008–2019, проверка — 2020–2026 (и по годам).
Отдельно: нынешний выбор цели в боте («наибольшее ожидание по истории бумаги») — только по данным до дня входа —
против лучшей фиксированной цели на тех же входах.
"""
import datetime as dt
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
import train as TR  # noqa: E402

log = TR.log
STEP = int(os.environ.get("EXP_STEP", "3"))
SPLIT = int(os.environ.get("EXP_SPLIT", "2020"))
COST, SLIP = 0.001, 0.0005
HMAX = 60


def load():
    tick = TR.universe()
    Dd = TR.load_series("d", tick + ["IMOEX"], "2012-01-01", index=("IMOEX",))
    IX = Dd.pop("IMOEX")
    Dd = TR.stitch_renamed(Dd)
    Dd, IX = TR.backfill(Dd, IX)
    out = {}
    for t, rows in Dd.items():
        df = CD.series(rows).dropna(subset=["o", "h", "l", "c"])
        df = df[(df.c > 0) & (df.h >= df.l)].reset_index(drop=True)
        if len(df) >= 400:
            out[t] = df
    return out


def atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    return pd.Series(tr).rolling(n).mean().values


def strong_levels(h, l, i, A):
    """Уровни для стопа: развороты (3 бара с каждой стороны) за 250 дней до i, склейка ближе 0,35 ATR, сильный — ≥2 касания."""
    a, b = max(3, i - 250), i - 3
    if b - a < 20:
        return np.array([])
    H, L = h[a - 3:b + 3], l[a - 3:b + 3]
    from numpy.lib.stride_tricks import sliding_window_view
    wh = sliding_window_view(H, 7).max(1)
    wl = sliding_window_view(L, 7).min(1)
    pts = np.r_[H[3:-3][H[3:-3] == wh], L[3:-3][L[3:-3] == wl]]
    if not len(pts):
        return np.array([])
    pts.sort()
    grp = np.r_[0, np.cumsum(np.diff(pts) > 0.35 * A)]
    cnt = np.bincount(grp)
    mean = np.bincount(grp, weights=pts) / cnt
    return mean[cnt >= 2]


def entries(D):
    """Таблица входов и окна следующих HMAX свечей (o, h, l, c)."""
    rows, W = [], []
    for t, df in D.items():
        o, h, l, c, v = (df[k].to_numpy(float) for k in ("o", "h", "l", "c", "v"))
        n = len(c)
        A = atr(h, l, c)
        m50 = pd.Series(c).rolling(50).mean().values
        sl = np.r_[np.full(10, np.nan), m50[10:] - m50[:-10]]
        val = pd.Series(v).rolling(60).median().values
        r1 = np.abs(np.diff(np.log(c)))
        bad = np.r_[False, (r1 > 0.45)] | (h / np.maximum(l, 1e-9) > 1.8)
        badc = np.cumsum(bad)
        from numpy.lib.stride_tricks import sliding_window_view
        pad = np.full(HMAX, np.nan)
        win = [sliding_window_view(np.r_[x[1:], pad], HMAX) for x in (o, h, l, c)]
        for i in range(260, n - 2, STEP):
            if not (A[i] > 0) or not (val[i] >= 1e7) or m50[i] != m50[i]:
                continue
            if badc[min(n - 1, i + HMAX)] - badc[i - 1] > 0:   # сплиты/сбои в окне
                continue
            strong = strong_levels(h, l, i, A[i])
            ls = {}
            for side in (1, -1):
                dist = (c[i] - strong) * side
                ok = (dist >= 1.2 * A[i]) & (dist <= 3.2 * A[i])
                ls[side] = float(dist[ok].min() + 0.3 * A[i]) if ok.any() else 2.5 * A[i]
            rows.append((t, df.d.iloc[i], c[i], A[i], np.sign(c[i] - m50[i]), np.sign(sl[i]), ls[1], ls[-1], i))
            W.append(np.stack([w[i] for w in win], 0))
    E = pd.DataFrame(rows, columns=["t", "d", "E", "A", "tr", "slope", "lsL", "lsS", "i"])
    return E, np.asarray(W, dtype=np.float32)   # W: N × 4 × HMAX


def sim(E0, side, W, S, takes, H, be):
    """Векторная симуляция. S — расстояние до стопа (в цене), takes — [(кратность R, доля)], be — 0 нет, 1 безубыток после Т1,
    2 — ещё и стоп на Т1 после Т2. → (R, дней в позиции)."""
    N = len(E0)
    o, h, l, c = (W[:, k, :H].astype(float) for k in range(4))
    stop = E0 - side * S
    T = [E0 + side * m * S for m, _ in takes]
    sh = [w for _, w in takes]
    rem = np.ones(N)
    pnl = np.zeros(N)
    hit = [np.zeros(N, bool) for _ in takes]
    days = np.full(N, H, float)
    alive = np.ones(N, bool)
    for j in range(H):
        oj, hj, lj, cj = o[:, j], h[:, j], l[:, j], c[:, j]
        nan = ~np.isfinite(cj)
        # гэп через стоп
        g = alive & ~nan & ((oj - stop) * side <= 0)
        pnl[g] += rem[g] * ((oj[g] - E0[g]) * side - SLIP * E0[g])
        rem[g] = 0
        alive &= ~g
        days[g] = j + 1
        # стоп внутри свечи (раньше тейков)
        if side == 1:
            s_ = alive & ~nan & (lj <= stop)
        else:
            s_ = alive & ~nan & (hj >= stop)
        pnl[s_] += rem[s_] * ((stop[s_] - E0[s_]) * side - SLIP * E0[s_])
        rem[s_] = 0
        alive &= ~s_
        days[s_] = j + 1
        newstop = stop.copy()
        for k in range(len(takes)):
            px_o = (oj - T[k]) * side >= 0
            px_i = ((hj if side == 1 else lj) - T[k]) * side >= 0
            m = alive & ~nan & ~hit[k] & (px_o | px_i)
            fill = np.where(px_o, oj, T[k])
            pnl[m] += sh[k] * (fill[m] - E0[m]) * side
            rem[m] -= sh[k]
            hit[k] |= m
            if be and k == 0:
                newstop = np.where(m, E0, newstop)
            if be == 2 and k == 1:
                newstop = np.where(m, T[0], newstop)
        done = alive & (rem <= 1e-9)
        days[done] = j + 1
        alive &= ~done
        stop = np.where(alive, np.where(((newstop - stop) * side) > 0, newstop, stop), stop)
    # выход по сроку — закрытие последней доступной свечи
    if alive.any():
        nf = np.isfinite(c).sum(1)
        lastc = np.where(nf > 0, c[np.arange(N), np.maximum(nf - 1, 0)], E0)
        pnl[alive] += rem[alive] * (lastc[alive] - E0[alive]) * side
    R = (pnl - COST * E0) / S
    return R, days


STOPS = [(f"{k:g} ATR", "atr", k) for k in (1, 1.5, 2, 2.5, 3, 4)] + [(f"{p}%", "pct", p / 100) for p in (3, 5, 8)] + [("за уровнем", "lvl", None)]
TAKES = [(f"тейк {m:g}R", [(m, 1.0)], 0) for m in (1, 1.5, 2, 3, 4, 5)] + [("без тейка (по сроку)", [], 0),
         ("50% на 1R → б/у, остаток 3R", [(1, 0.5), (3, 0.5)], 1), ("по трети 1R/2R/3R + б/у", [(1, 1 / 3), (2, 1 / 3), (3, 1 / 3)], 2)]


def stop_dist(E, side, kind, k):
    if kind == "atr":
        return k * E.A.values
    if kind == "pct":
        return k * E.E.values
    return (E.lsL if side == 1 else E.lsS).values


def summarize(R, days, yrs, mask):
    out = {}
    for nm, m in (("2008–2019", mask & (yrs < SPLIT)), (f"{SPLIT}–2026", mask & (yrs >= SPLIT))):
        r = R[m]
        out[nm] = dict(n=int(m.sum()), R=float(r.mean()) if len(r) else None, win=float((r > 0).mean()) if len(r) else None,
                       days=float(np.median(days[m])) if len(r) else None, R100=float(r.sum() / max(1, days[m].sum()) * 100) if len(r) else None)
    by = {}
    for y in np.unique(yrs[mask & (yrs >= SPLIT)]):
        by[int(y)] = float(R[mask & (yrs == y)].mean())
    out["years"] = by
    return out


def ev_choice(D, E, side, W, sub=5):
    """Нынешний выбор цели в боте, но только по данным до входа: стоп «за уровнем», цель 1,5…5R с наибольшим ожиданием."""
    import idea_judge as J
    cand = (1.5, 2, 2.5, 3, 4, 5)
    pick, fixed = [], {m: [] for m in cand}
    idx = np.arange(len(E))[::sub]
    idx = idx[pd.to_datetime(E.d.values[idx]).year >= 2012]
    S_all = stop_dist(E, side, "lvl", None)
    res = {m: sim(E.E.values[idx], side, W[idx], S_all[idx], [(m, 1.0)], HMAX, 0)[0] for m in cand}
    chosen = np.zeros(len(idx))
    for q, ii in enumerate(idx):
        r = E.iloc[ii]
        df = D[r.t]
        a = {k: df[k].to_numpy(float)[:int(r.i) + 1] for k in ("o", "h", "l", "c", "v")}
        at = J.atr_series(a)
        tr, sl, pos = J.state(a)
        sim_m = (tr == tr[-1]) & (sl == sl[-1]) & (np.abs(pos - pos[-1]) <= 0.2)
        a_s = S_all[ii] / r.A
        best, bev = cand[0], -9
        for m in cand:
            P = J.prob(a, side, a_s, m * a_s, J.H_D, step=2, similar=sim_m)
            p, pt = P["p"], P.get("p_timeout") or 0
            if p != p:
                continue
            ev = p * m - max(0, 1 - p - pt) - 0.002 / (S_all[ii] / r.E)
            if ev > bev:
                best, bev = m, ev
        chosen[q] = res[best][q]
        pick.append(best)
    yrs = pd.to_datetime(E.d.values[idx]).year
    return dict(n=len(idx), chosen=chosen, fixed=res, yrs=yrs, pick=pick)


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    D = load()
    log(f"бумаг с историей: {len(D)}; {round(time.time() - t0)} с")
    E, W = entries(D)
    yrs = pd.to_datetime(E.d).dt.year.values
    log(f"входов {len(E)} (каждый {STEP}-й день, оборот ≥10 млн ₽), {E.t.nunique()} бумаг, {yrs.min()}–{yrs.max()}; {round(time.time() - t0)} с")
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), n=len(E), rules=[])
    sets = {}
    for side in (1, -1):
        trend = (E.tr.values == side) & (E.slope.values == side)
        counter = (E.tr.values == -side) & (E.slope.values == -side)
        sets[side] = {"все": np.ones(len(E), bool), "по тренду": trend, "против тренда": counter}
    for H in (20, 60):
        for snm, kind, k in STOPS:
            for tnm, tk, be in TAKES:
                res = {}
                for side in (1, -1):
                    S = stop_dist(E, side, kind, k)
                    res[side] = sim(E.E.values, side, W, S, tk, H, be)
                for setn in ("все", "по тренду", "против тренда"):
                    R = np.r_[res[1][0][sets[1][setn]], res[-1][0][sets[-1][setn]]]
                    dd = np.r_[res[1][1][sets[1][setn]], res[-1][1][sets[-1][setn]]]
                    yy = np.r_[yrs[sets[1][setn]], yrs[sets[-1][setn]]]
                    sm = summarize(R, dd, yy, np.ones(len(R), bool))
                    out["rules"].append(dict(set=setn, stop=snm, take=tnm, H=H, **sm))
        log(f"срок {H}: готово; {round(time.time() - t0)} с")
        json.dump(out, open(os.path.join(TR.MODELS, "exp23_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    df = pd.DataFrame([dict(set=r["set"], stop=r["stop"], take=r["take"], H=r["H"], R_a=r["2008–2019"]["R"], R_b=r[f"{SPLIT}–2026"]["R"],
                            win_b=r[f"{SPLIT}–2026"]["win"], d_b=r[f"{SPLIT}–2026"]["days"], yb=sum(1 for v in r["years"].values() if v > 0),
                            ny=len(r["years"])) for r in out["rules"]])
    for setn in ("все", "по тренду", "против тренда"):
        q = df[df.set == setn].sort_values("R_a", ascending=False)
        log(f"===== {setn.upper()}: лучшие по 2008–2019 (R на сделку; при риске 1% счёта — % счёта) и их проверка {SPLIT}–2026 =====")
        for _, r in q.head(10).iterrows():
            log(f"  стоп {r.stop}, {r['take']}, срок {r.H}: 2008–19 {r.R_a:+.3f}R → {SPLIT}–26 {r.R_b:+.3f}R, прибыльных {r.win_b:.0%}, "
                f"медиана {r.d_b:.0f} дн., лет в плюсе {r.yb}/{r.ny}")
        rk = q.R_a.rank().corr(q.R_b.rank())
        log(f"  связь рангов 2008–19 → {SPLIT}–26: {rk:+.2f}")
        for col, nm in (("stop", "СТОП"), ("take", "ТЕЙК")):
            g = df[df.set == setn].groupby(col)[["R_a", "R_b"]].mean().sort_values("R_a", ascending=False)
            log(f"  {nm} в среднем по остальным параметрам: " + "; ".join(f"{i} {a:+.3f}/{b:+.3f}" for i, (a, b) in g.iterrows()))
    # нынешний выбор цели ботом против фиксированной цели
    log("===== ВЫБОР ЦЕЛИ «ПО ОЖИДАНИЮ НА ИСТОРИИ БУМАГИ» (как в боте, только прошлое) против фиксированной; стоп за уровнем, срок 60 =====")
    out["ev"] = {}
    for side in (1, -1):
        sub = int(os.environ.get("EXP_EV_SUB", "6"))
        r = ev_choice(D, E, side, W, sub=sub)
        late = r["yrs"] >= SPLIT
        line = {"выбор по ожиданию": (float(r["chosen"].mean()), float(r["chosen"][late].mean()))}
        for m, v in r["fixed"].items():
            line[f"всегда {m:g}R"] = (float(v.mean()), float(v[late].mean()))
        out["ev"]["лонг" if side > 0 else "шорт"] = dict(n=r["n"], res=line, picks={str(k): int(v) for k, v in zip(*np.unique(r["pick"], return_counts=True))})
        log(f"  {'лонг' if side > 0 else 'шорт'} ({r['n']} входов): " + "; ".join(f"{k} {a:+.3f}/{b:+.3f}" for k, (a, b) in line.items())
            + " | выбирались: " + ", ".join(f"{k}R×{v}" for k, v in out["ev"]["лонг" if side > 0 else "шорт"]["picks"].items()))
        json.dump(out, open(os.path.join(TR.MODELS, "exp23_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №23 =====", round(time.time() - t0), "с")
    for setn in ("все", "по тренду", "против тренда"):
        q = df[df.set == setn].sort_values("R_a", ascending=False).iloc[0]
        log(f"  {setn}: лучший по 2008–19 — стоп {q.stop}, {q['take']}, срок {q.H}: {q.R_a:+.3f}R → {SPLIT}–26 {q.R_b:+.3f}R, лет в плюсе {q.yb}/{q.ny}")
    json.dump(out, open(os.path.join(TR.MODELS, "exp23_report.json"), "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
