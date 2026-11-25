# -*- coding: utf-8 -*-
"""Проверка №41: «линейка шума» для сравнения правил портфеля (продолжение №39/40).

Те же сделки и портфель, что в №40. Насколько устойчива разница «как в исследованиях − одна позиция» (R − V1) и «S1+F2 − одна позиция» (V2 − V1):
  1) подвыборки сделок: 300 раз случайно убираем 10% сделок-кандидатов (и отдельно — ровно 8, как разница данных №39/№40), пересчитываем оба варианта;
  2) бутстрэп по годам: разница годовых результатов, годы с возвращением (5000 раз), средняя в год;
  3) без отдельных лет: результат без 2020 и без 2022.
Выход: медиана и 90%/95% интервалы разницы в п.п./год, доля прогонов с разницей > 0 — это мерка, против которой сравнивать будущие проверки.
Не является индивидуальной инвестиционной рекомендацией.
"""
import json
import os
import random
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import exp35 as E35  # noqa: E402
import exp40 as E40  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
N_SUB = int(os.environ.get("N_SUB", "300"))


def cagr(trades, CL, days, rule):
    s = E40.port(trades, CL, days, rule)[0]
    m = E35.metr(s)
    return m["cagr"], m["dd"], m["years"]


def q(a, p):
    return float(np.percentile(a, p))


def ruler(name, trades, CL, days):
    log(f"===== {name}: сделок-кандидатов {len(trades)} =====")
    out = {}
    base = {r: cagr(trades, CL, days, r) for r in ("R", "V1", "V2")}
    for r, (c, d, _) in base.items():
        log(f"  {r}: {c:+.2%} в год, просадка {d:.0%}")
    ys = sorted(base["R"][2])
    # 3) без отдельных лет
    for drop in ({"2020"}, {"2022"}, {"2020", "2022"}):
        v = {r: E40.ex_years(base[r][2], drop) for r in base}
        log(f"  без {', '.join(sorted(drop))}: R {v['R']:+.1%}, V1 {v['V1']:+.1%}, V2 {v['V2']:+.1%}; R−V1 {v['R'] - v['V1']:+.1%}, V2−V1 {v['V2'] - v['V1']:+.1%}")
        out["без " + "+".join(sorted(drop))] = v
    # 2) бутстрэп по годам
    rng = np.random.default_rng(41)
    for a, b in (("R", "V1"), ("V2", "V1")):
        dif = np.array([base[a][2][y] - base[b][2][y] for y in ys])
        bs = np.array([dif[rng.integers(0, len(dif), len(dif))].mean() for _ in range(5000)])
        log(f"  бутстрэп по годам {a}−{b}: средняя разница {dif.mean():+.2%} в год, 90% [{q(bs, 5):+.2%}; {q(bs, 95):+.2%}], 95% [{q(bs, 2.5):+.2%}; {q(bs, 97.5):+.2%}], "
            f"доля > 0: {(bs > 0).mean():.0%}")
        out[f"годы {a}-{b}"] = dict(mean=float(dif.mean()), lo90=q(bs, 5), hi90=q(bs, 95), lo95=q(bs, 2.5), hi95=q(bs, 97.5), pos=float((bs > 0).mean()))
    # 1) подвыборки сделок
    random.seed(41)
    for label, k in (("минус 10% сделок", max(1, len(trades) // 10)), ("минус 8 сделок", 8)):
        res = {"R-V1": [], "V2-V1": [], "V1": []}
        t0 = time.time()
        for i in range(N_SUB):
            drop = set(random.sample(range(len(trades)), k))
            tr = [z for j, z in enumerate(trades) if j not in drop]
            c = {r: cagr(tr, CL, days, r)[0] for r in ("R", "V1", "V2")}
            res["R-V1"].append(c["R"] - c["V1"])
            res["V2-V1"].append(c["V2"] - c["V1"])
            res["V1"].append(c["V1"])
        o = {}
        for key, a in res.items():
            a = np.array(a)
            o[key] = dict(med=float(np.median(a)), lo90=q(a, 5), hi90=q(a, 95), lo95=q(a, 2.5), hi95=q(a, 97.5), pos=float((a > 0).mean()), sd=float(a.std()))
            log(f"  {label}, {key}: медиана {o[key]['med']:+.2%}, 90% [{o[key]['lo90']:+.2%}; {o[key]['hi90']:+.2%}], 95% [{o[key]['lo95']:+.2%}; {o[key]['hi95']:+.2%}], "
                f"разброс (ст. откл.) {o[key]['sd']:.2%}" + (f", доля > 0: {o[key]['pos']:.0%}" if key != "V1" else ""))
        log(f"     ({N_SUB} прогонов, {round(time.time() - t0)} с)")
        out[label] = o
    return out


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    rep = {}
    import exp9 as E9
    X, Dd, IX, Hh, IXH, ev, Wn = E9.load()
    days = sorted({r[0][:10] for r in IX if pd.Timestamp(r[0][:10]).weekday() < 5})
    CL = {}
    for t in Dd:
        D = CD.series(Dd[t])
        CL[t] = {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(D.d, D.c)}
    import exp29 as E29
    import exp30 as E30
    E, R, FE, Dd2, bars, cost = E29.load_all()
    F = E29.Filt(E, FE)
    rows = E30.build(E, R, F, Dd2, bars, cost)
    V = E30.variants(rows)
    cur = V[("нет", "как сейчас", "свой")]
    trB = [dict(strat=x["strat"], t=x["t"], d_in=x["d_in"], d_out=v[2], ret=float(v[0]), e=x["e"]) for v, x in zip(cur, rows) if v is not None]
    dB = sorted(set(d for d in days if d >= "2020-01-01") | {z["d_in"] for z in trB if z["d_in"] >= "2020-01-01"})
    rep["B"] = ruler("Б) 2020–2026, 5-мин, правила бота", trB, CL, dB)
    log(f"{round(time.time() - t0)} с")
    import exp31 as E31
    got = {}
    orig = E31.portfolio

    def spy(TT, CL_, days_all, w, cap):
        if "fwd" not in got:
            got["fwd"] = TT
        return orig(TT, CL_, days_all, w, cap)
    E31.portfolio = spy
    E31.main()
    E31.portfolio = orig
    T = got["fwd"]
    trA = [dict(strat=r.strat, t=r.t, d_in=r.d_in, d_out=r.d_out, ret=float(r.ret), e=CL.get(r.t, {}).get(r.d_in)) for r in T.itertuples()]
    rep["A"] = ruler("А) 2011–2026, дневные аналоги «вперёд»", trA, CL, [d for d in days if d >= "2011-01-01"])
    json.dump(rep, open(os.path.join(TR.MODELS, "exp41_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №41 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
