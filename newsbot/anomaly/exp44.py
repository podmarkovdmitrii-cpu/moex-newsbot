# -*- coding: utf-8 -*-
"""Проверка №44: портфель без F2 (S1 + F4) — улучшается ли он, если F2 не занимает капитал.

  А) дневные 2011–2026, ПОЛНЫЙ состав (ушедшие с биржи, №43) и обвалы (№42), текущие правила выхода;
  Б) 5-мин 2020–2026, сделки №30/40 (только выжившие — 5-мин свечей по ушедшим нет).
Варианты (одна позиция на бумагу, потолок 150%, размер от капитала по закрытым):
  все (S1 5%, F4 5%, F2 2,5%) / без F2 / без F2, но S1 и F4 по 6% / F2 только при загрузке ≤ 50%.
Плюс бутстрэп по годам для разницы «без F2 − все» и версия данных (с числом ушедших и ликвидных ушедших).
Не является индивидуальной инвестиционной рекомендацией.
"""
import datetime as dt
import json
import os
import re
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
import exp42 as E42  # noqa: E402
import exp43 as E43  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
W0 = dict(E40.W)


def run(trades, CL, days, w=None, f2_load=None):
    E40.W.clear()
    E40.W.update(w or W0)
    old = E40.F2_LOAD
    if f2_load is not None:
        E40.F2_LOAD = f2_load
    try:
        s, tk, sk, cut, kd = E40.port(trades, CL, days, "V1")
    finally:
        E40.F2_LOAD = old
        E40.W.clear()
        E40.W.update(W0)
    m = E35.metr(s)
    return m, tk, sk


def compare(name, trades, CL, days):
    log(f"===== {name}: сделок-кандидатов {len(trades)} (S1 {sum(z['strat'] == 'S1' for z in trades)}, F4 {sum(z['strat'] == 'F4' for z in trades)}, "
        f"F2 {sum(z['strat'] == 'F2' for z in trades)}) =====")
    nf2 = [z for z in trades if z["strat"] != "F2"]
    V = {"все (5/5/2,5%)": (trades, None, None), "без F2": (nf2, None, None),
         "без F2, S1 и F4 по 6%": (nf2, dict(W0, S1=0.06, F4=0.06), None), "F2 только при загрузке ≤ 50%": (trades, None, 0.5)}
    out = {}
    for k, (L, w, fl) in V.items():
        m, tk, sk = run(L, CL, days, w, fl)
        out[k] = dict(cagr=m["cagr"], dd=m["dd"], calmar=m["calmar"], worst=m["worst"], years=m["years"], taken=tk, skipped=sk)
        log(f"  {k}: {m['cagr']:+.2%} в год, просадка {m['dd']:.0%}, доходность/просадка {m['calmar']:.2f}, худший год {m['worst']:+.1%}, сделок {tk}, пропущено потолком {sk}")
    a, b = out["все (5/5/2,5%)"], out["без F2"]
    ys = sorted(a["years"])
    dif = np.array([b["years"].get(y, 0) - a["years"].get(y, 0) for y in ys])
    rng = np.random.default_rng(44)
    bs = np.array([dif[rng.integers(0, len(dif), len(dif))].mean() for _ in range(5000)])
    log(f"  «без F2 − все» по годам: средняя {dif.mean():+.2%}/год, 90% [{np.percentile(bs, 5):+.2%}; {np.percentile(bs, 95):+.2%}], лучше в {int((dif > 0.0005).sum())} из {len(ys)} лет; "
        + ", ".join(f"{y}: {d:+.1%}" for y, d in zip(ys, dif)))
    out["разница"] = dict(mean=float(dif.mean()), lo90=float(np.percentile(bs, 5)), hi90=float(np.percentile(bs, 95)))
    f2 = [z["ret"] for z in trades if z["strat"] == "F2"]
    if f2:
        log(f"  F2 отдельно: {len(f2)} сд., средняя {np.mean(f2):+.2%}, медиана {np.median(f2):+.2%}, прибыльных {np.mean(np.array(f2) > 0):.0%}")
    return out


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    rep = {}
    import linkcheck as LC
    cur = TR.universe()
    H = E43.tqbr_history()
    cut = (dt.date.today() - dt.timedelta(days=45)).isoformat()
    fund = re.compile(r"ETF|БПИФ|ПИФ|фонд", re.I)
    gone = sorted(s for s, (a, b, nm) in H.items() if b and "2008-01-01" <= b < cut and s not in LC.RENAME and not fund.search(nm or ""))
    orig_u, orig_ls = TR.universe, TR.load_series
    n_h = int(os.environ.get("ANOM_HOURLY_N", "160"))
    os.environ["ANOM_HOURLY_N"] = str(n_h + len(gone))
    TR.universe = lambda: list(cur) + [s for s in gone if s not in cur]

    def ls(kind, tickers, start, index=()):
        if kind == "h":
            tickers = [t for t in tickers if t in cur and t not in index][:n_h] + [t for t in tickers if t in index]
        return orig_ls(kind, tickers, start, index=index)
    TR.load_series = ls
    import exp9 as E9
    X, Dd, IX, Hh, IXH, ev, Wn = E9.load()
    TR.universe, TR.load_series = orig_u, orig_ls
    os.environ["ANOM_HOURLY_N"] = str(n_h)
    v = E43.version(Dd)
    v["gone"] = len(gone)
    v["gone_liquid"] = sum(1 for s in gone if s in Dd and E43.liquid_years(Dd[s]) >= 1)
    rep["версия данных"] = v
    log("ВЕРСИЯ ДАННЫХ:", v)
    ok, drop, stopped = E42.daily_part(X, Dd, IX)
    real = [x for x in drop if x["why"] != "конец данных" and not x["why"].endswith("сбой") and not x["why"].endswith("дробление?")]
    T = E42.trades_of(ok) + E42.trades_of(real)
    CL = {}
    for t in Dd:
        D = CD.series(Dd[t])
        CL[t] = {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(D.d, D.c)}
    for z in T:
        z["e"] = CL.get(z["t"], {}).get(z["d_in"])
    days = sorted({r[0][:10] for r in IX if pd.Timestamp(r[0][:10]).weekday() < 5 and r[0][:10] >= "2011-01-01"})
    rep["A"] = compare("А) дневные 2011–2026, полный состав + обвалы", [z for z in T if z["d_in"] >= "2011-01-01" and z["e"]], CL, days)
    log(f"{round(time.time() - t0)} с")
    import exp29 as E29
    import exp30 as E30
    E, R, FE, Dd2, bars, cost = E29.load_all()
    F = E29.Filt(E, FE)
    rows = E30.build(E, R, F, Dd2, bars, cost)
    V = E30.variants(rows)
    curv = V[("нет", "как сейчас", "свой")]
    trB = [dict(strat=x["strat"], t=x["t"], d_in=x["d_in"], d_out=v_[2], ret=float(v_[0]), e=x["e"]) for v_, x in zip(curv, rows) if v_ is not None]
    allday = sorted({r[0][:10] for r in IX if pd.Timestamp(r[0][:10]).weekday() < 5})
    dB = sorted(set(d for d in allday if d >= "2020-01-01") | {z["d_in"] for z in trB if z["d_in"] >= "2020-01-01"})
    rep["B"] = compare("Б) 5-мин 2020–2026 (выжившие)", trB, CL, dB)
    json.dump(rep, open(os.path.join(TR.MODELS, "exp44_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №44 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
