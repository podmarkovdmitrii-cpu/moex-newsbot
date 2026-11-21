# -*- coding: utf-8 -*-
"""Проверка №34: глубина отката для лимита S1 — 20 / 30 / 40 / 50 (сейчас) / 60% хода. Меньше откат — чаще исполнение, но вход дороже.

A) Дневные свечи 2008–2026 (как №31: дневная ценовая аномалия y_px роста, оборот ≥ 10 млн ₽, лимит на следующий день,
   тейк +25% хода от входа или «к закрытию дня сигнала», до 5 дней, без стопа, издержки как в №15).
B) 5-минутные свечи 2020–2026 (как бот: сигнал внутри дня, лимит до конца дня, тейк +25% хода, до 5 торговых дней).
Для каждого варианта по годам: исполнено, средняя на сделку, прибыльных, «вклад на сигнал» (= доля исполненных × средняя).
Выбор глубины «вперёд»: для года Y — только по годам до Y (дневные — с 2011, 5-мин — с 2022), по вкладу на сигнал и по средней на сделку.
Портфель S1 с переоценкой (дневные, 5% на сделку, потолок 150%) по каждой глубине.
Не является индивидуальной инвестиционной рекомендацией.
"""
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
import exp18 as E18  # noqa: E402
import exp31 as E31  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
DEPTHS = (0.2, 0.3, 0.4, 0.5, 0.6)
TAKES = ("+25% хода", "к закрытию сигнала")


def trade_d(x, k, take, n=5):
    o, h, l, c = x["o"][:n], x["h"][:n], x["l"][:n], x["c"][:n]
    e = x["c0"] - k * (x["c0"] - x["prev"])
    if not l[0] <= e:
        return None
    if o[0] < e:
        e = o[0]
    T = e * (1 + 0.25 * x["mv"]) if take == "+25% хода" else max(x["c0"], e * 1.001)
    for j in range(1, n):
        if h[j] >= T:
            return max(o[j], T) / e - 1 - x["cost"], j, x["dts"][0]
    return c[n - 1] / e - 1 - x["cost"], n - 1, x["dts"][0]


def seq5(r, Dd, bars, hold, k):
    """Как exp20.build_seq с лимитом, но глубина отката k."""
    d0 = r.d.strftime("%Y-%m-%d")
    days = E18.trading_days(Dd, r.t, d0, hold)
    if len(days) < hold:
        return None
    band = {dd: (lo, hi) for dd, lo, hi in days}
    for x in Dd.get(r.t, []):
        if x[0][:10] == d0:
            band[d0] = (float(x[3]), float(x[2]))
            break
    seq, miss = [], 0
    for dd in [d0] + [x[0] for x in days]:
        b = bars.get((r.t, dd))
        if not b:
            miss += dd != d0
            continue
        lo, hi = band.get(dd, (0, 1e18))
        seq += [(dd, x[0], x[1], x[2], x[3], x[4]) for x in b if x[3] >= 0.7 * lo and x[2] <= 1.3 * hi]
    if miss > hold // 4 or not seq:
        return "nodata"
    t0 = f"{int(r.c) + 1:02d}:05"
    e = r.p0 - k * (r.p0 - r.prev)
    k0 = next((i for i, x in enumerate(seq) if x[0] == d0 and x[1] >= t0 and x[4] <= e), None)
    if k0 is None:
        return None
    S = [x for x in seq[k0 + 1:] if x[0] <= days[-1][0]]
    if not S:
        return None
    return e, np.array([[x[2], x[3], x[4], x[5]] for x in S], float)


def trade5(r, Dd, bars, k, cost):
    z = seq5(r, Dd, bars, 5, k)
    if z is None or isinstance(z, str):
        return z
    e, a = z
    mv = abs(r.p0 / r.prev - 1)
    tp = e * (1 + 0.25 * mv)
    hit = np.nonzero(a[:, 1] >= tp)[0]
    return (tp if len(hit) else a[-1, 3]) / e - 1 - cost


def table(name, res, years):
    """res[k] — список результатов (None = не исполнен) по сигналам; years — год сигнала."""
    out = {}
    for k, vals in res.items():
        by = {}
        for v, y in zip(vals, years):
            if v == "nodata":
                continue
            b = by.setdefault(int(y), [0, []])
            b[0] += 1
            if v is not None:
                b[1].append(v)
        out[k] = by
        alln = sum(b[0] for b in by.values())
        allv = [v for b in by.values() for v in b[1]]
        log(f"  {name} откат {k:.0%}: сигналов {alln}, исполнено {len(allv)} ({len(allv) / max(1, alln):.0%}), средняя {np.mean(allv):+.2%}, "
            f"прибыльных {np.mean(np.array(allv) > 0):.0%}, вклад на сигнал {np.sum(allv) / max(1, alln):+.2%}, "
            f"лет в плюсе {sum(1 for b in by.values() if b[1] and np.mean(b[1]) > 0)}/{sum(1 for b in by.values() if b[1])}")
        log("     по годам (исполнено% / средняя / вклад на сигнал): " + ", ".join(
            f"{y}: {len(b[1]) / max(1, b[0]):.0%}/{(np.mean(b[1]) if b[1] else 0):+.1%}/{np.sum(b[1]) / max(1, b[0]):+.2%}" for y, b in sorted(by.items())))
    return out


def forward(name, T, Y0, Ylast):
    """Выбор глубины на год Y по годам < Y: по среднему вкладу на сигнал и по средней на сделку."""
    for crit in ("вклад на сигнал", "средняя на сделку"):
        tot_n, tot_v, picks = 0, [], []
        for Y in range(Y0, Ylast + 1):
            best = None
            for k, by in T.items():
                ms = []
                for y, b in by.items():
                    if y < Y and b[1] and b[0] >= 10:
                        ms.append(np.sum(b[1]) / b[0] if crit == "вклад на сигнал" else np.mean(b[1]))
                if len(ms) >= 2 and (best is None or np.mean(ms) > best[0]):
                    best = (float(np.mean(ms)), k)
            if not best or Y not in T[best[1]]:
                continue
            b = T[best[1]][Y]
            tot_n += b[0]
            tot_v += b[1]
            picks.append(f"{Y}: {best[1]:.0%}")
        cur = T[0.5]
        n5 = sum(cur[y][0] for y in range(Y0, Ylast + 1) if y in cur)
        v5 = [v for y in range(Y0, Ylast + 1) if y in cur for v in cur[y][1]]
        log(f"  {name} «вперёд» по критерию «{crit}»: выбор {', '.join(picks)}; сделок {len(tot_v)}, средняя {np.mean(tot_v):+.2%}, "
            f"вклад на сигнал {np.sum(tot_v) / max(1, tot_n):+.2%} | сейчас (50%) за те же годы: сделок {len(v5)}, средняя {np.mean(v5):+.2%}, "
            f"вклад на сигнал {np.sum(v5) / max(1, n5):+.2%}")


def main():
    t0 = time.time()
    rep = {}
    # ---------- A: дневные 2008–2026 ----------
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    B, _ = E31.build(X, Dd, IX)
    U = B[B.s > 0].reset_index(drop=True)
    recs = U.to_dict("records")
    log(f"A) дневные сигналы роста (≥10 млн): {len(U)}, {U.y.min()}–{U.y.max()}; {round(time.time() - t0)} с")
    CL = {}
    for t in set(U.t):
        D = CD.series(Dd[t])
        CL[t] = {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(D.d, D.c)}
    days_full = sorted(set(d for v in CL.values() for d in v if d >= "2008-06-01"))
    for take in TAKES:
        log(f"===== A) ДНЕВНЫЕ 2008–2026, тейк {take}, до 5 дней =====")
        res, trades = {}, {}
        for k in DEPTHS:
            z = [trade_d(x, k, take) for x in recs]
            res[k] = [v[0] if v else None for v in z]
            trades[k] = pd.DataFrame([dict(strat="S1", t=x["t"], d_in=v[2], d_out=x["dts"][v[1]], ret=v[0], y=x["y"]) for x, v in zip(recs, z) if v])
        T = table("дневные", res, U.y.values)
        forward("дневные", T, 2011, 2026)
        for k in DEPTHS:
            r = E31.portfolio(trades[k], CL, days_full, 0.05, 1.5)
            rep[f"A {take} {k}"] = dict(final=r["final"], cagr=r["cagr"], dd=r["dd"], years=r["years"])
            log(f"  портфель S1 откат {k:.0%} (5%, потолок 150%): ×{r['final']:.2f}, {r['cagr']:+.1%} в год, просадка {r['dd']:.0%}, "
                f"сделок {r['taken']}, пропущено {r['skipped']}, загрузка макс. {r['maxload']:.0%}")
    log(f"A готово, {round(time.time() - t0)} с")
    # ---------- B: 5-минутные 2020–2026 ----------
    import exp29 as E29
    E, R, FE, Dd2, bars, cost = E29.load_all()
    up = np.where(E.s.values > 0)[0]
    yrs = E.d.dt.year.values[up]
    log(f"B) сигналы роста внутри дня: {len(up)}; {round(time.time() - t0)} с")
    res = {}
    for k in DEPTHS:
        res[k] = [trade5(E.iloc[i], Dd2, bars, k, cost[i]) for i in up]
    log("===== B) 5-МИНУТНЫЕ 2020–2026 (как бот), тейк +25% хода, до 5 дней =====")
    T5 = table("5-мин", res, yrs)
    forward("5-мин", T5, 2022, 2026)
    rep["B"] = {str(k): {str(y): [b[0], len(b[1]), float(np.mean(b[1])) if b[1] else None] for y, b in v.items()} for k, v in T5.items()}
    os.makedirs(TR.MODELS, exist_ok=True)
    json.dump(rep, open(os.path.join(TR.MODELS, "exp34_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №34 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
