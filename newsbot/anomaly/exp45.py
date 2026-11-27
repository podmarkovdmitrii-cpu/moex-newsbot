# -*- coding: utf-8 -*-
"""Проверка №45: 5-мин 2020–2026 на ПОЛНОМ составе (ушедшие с биржи, №43) и с днями обвалов (№42) — точные правила бота (как №30/40/44).

Состав: текущий список TQBR + ушедшие с биржи акции из истории ISS (без фондов и переименованных); часовые свечи — для текущего топа оборота
(как раньше) и для ушедших, торговавших в 2020–2026; 5-мин бары — ALGOPACK (докачка в кэш a18). Фильтр сбоев exp15.bad_path ослаблен:
отбрасываются только «сбой» (цена вернулась за ≤ 3 дня), «дробление?» и «нет цен» — реальные обвалы остаются.
Сделки помечаются: по ушедшей бумаге / по пути, который раньше отбрасывался. Портфель (одна позиция, потолок 150%, 5/5/2,5%):
  выжившие без обвалов (≈ как в исследованиях) → + ушедшие → + обвалы → всё вместе; и всё вместе без F2.
По F2 отдельно — средняя сделка на выживших и на ушедших (тест жизнеспособности F2 бота с модель-фильтром).
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
import exp15 as E15  # noqa: E402
import exp35 as E35  # noqa: E402
import exp40 as E40  # noqa: E402
import exp42 as E42  # noqa: E402
import exp43 as E43  # noqa: E402
import train as TR  # noqa: E402

log = TR.log


def load_full(rep):
    """Полный состав + обвалы, точные правила бота (5-мин 2020–2026) → сделки T и всё нужное для портфеля."""
    import linkcheck as LC
    cur = TR.universe()
    H = E43.tqbr_history()
    cut = (dt.date.today() - dt.timedelta(days=45)).isoformat()
    fund = re.compile(r"ETF|БПИФ|ПИФ|фонд", re.I)
    gone = sorted(s for s, (a, b, nm) in H.items() if b and "2008-01-01" <= b < cut and s not in LC.RENAME and not fund.search(nm or ""))
    gone20 = [s for s in gone if H[s][1] >= "2020-01-01"]
    log(f"ушедших акций {len(gone)}, из них торговавших в 2020–2026: {len(gone20)}: {', '.join(gone20)}")
    gset = set(gone)
    # ---- подмена состава и фильтра сбоев
    orig_u, orig_ls, orig_bp = TR.universe, TR.load_series, E15.bad_path
    strict = E15.bad_path_strict
    n_h = int(os.environ.get("ANOM_HOURLY_N", "160"))
    os.environ["ANOM_HOURLY_N"] = str(n_h + len(gone))
    TR.universe = lambda: list(cur) + [s for s in gone if s not in cur]

    def ls(kind, tickers, start, index=()):
        if kind == "h":
            tickers = [t for t in tickers if t in cur and t not in index][:n_h] + gone20 + [t for t in tickers if t in index]
        return orig_ls(kind, tickers, start, index=index)
    was_bad = set()

    def bp(c0, nh, nl, nc):
        if not strict(c0, nh, nl, nc):
            return False
        cls = E42.classify(c0, nc)
        if cls in ("сбой", "дробление?", "нет цен"):
            return True
        was_bad.add(round(float(c0), 6))
        return False
    TR.load_series, E15.bad_path = ls, bp
    import exp29 as E29
    import exp30 as E30
    try:
        E, R, FE, Dd, bars, cost = E29.load_all()
    finally:
        TR.universe, TR.load_series, E15.bad_path = orig_u, orig_ls, orig_bp
        os.environ["ANOM_HOURLY_N"] = str(n_h)
    v = E43.version(Dd)
    v["gone"], v["gone_2020"] = len(gone), len(gone20)
    rep["версия данных"] = v
    log("ВЕРСИЯ ДАННЫХ:", v)
    eg = E[E.t.isin(gset) & (E.d >= pd.Timestamp("2020-01-01"))]
    log(f"события 2020–2026: всего {int((E.d >= pd.Timestamp('2020-01-01')).sum())}, по ушедшим {len(eg)} ({eg.t.nunique()} бумаг); "
        f"путей, которые раньше отбрасывались как сбой: {len(was_bad)}")
    eb = E[(E.d >= pd.Timestamp("2020-01-01")) & E.close.round(6).isin(was_bad)]
    crash_keys = {(r.t, r.d.strftime("%Y-%m-%d")) for r in eb.itertuples()}
    log("  бывшие «сбои» (теперь в расчёте): " + ", ".join(sorted(f"{t} {d}" for t, d in crash_keys))[:2000])
    F = E29.Filt(E, FE)
    rows = E30.build(E, R, F, Dd, bars, cost)
    V = E30.variants(rows)
    curv = V[("нет", "как сейчас", "свой")]
    T = []
    for v_, x in zip(curv, rows):
        if v_ is None:
            continue
        d0 = x["d"].strftime("%Y-%m-%d")
        T.append(dict(strat=x["strat"], t=x["t"], d_in=x["d_in"], d_out=v_[2], ret=float(v_[0]), e=x["e"],
                      gone=x["t"] in gset, crash=(x["t"], d0) in crash_keys))
    sig_g = {(r.t, r.d.strftime("%Y-%m-%d")) for r in eg.itertuples()}
    got_g = {(z["t"], z["d_in"]) for z in T if z["gone"]}
    log(f"сделок {len(T)}: по ушедшим {sum(z['gone'] for z in T)}, по бывшим «сбоям» {sum(z['crash'] for z in T)}; "
        f"событий ушедших без сделки (нет 5-мин баров на срок, фильтр, нет исполнения): ≈{max(0, len(sig_g) - len(got_g))}")
    CL = {}
    for t in Dd:
        D = CD.series(Dd[t])
        CL[t] = {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(D.d, D.c)}
    allday = sorted({d for t in CL for d in CL[t] if d >= "2020-01-01"})
    days = sorted(set(allday) | {z["d_in"] for z in T if z["d_in"] >= "2020-01-01"})
    return dict(T=T, Dd=Dd, CL=CL, days=days, E=E, gset=gset, gone=gone)


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    rep = {}
    S = load_full(rep)
    T, Dd, CL, days = S["T"], S["Dd"], S["CL"], S["days"]
    log("===== Средняя сделка по стратегиям =====")
    for s in ("S1", "F2", "F4"):
        a = [z["ret"] for z in T if z["strat"] == s and not z["gone"] and not z["crash"]]
        b = [z["ret"] for z in T if z["strat"] == s and z["gone"]]
        c = [z["ret"] for z in T if z["strat"] == s and z["crash"]]
        al = [z["ret"] for z in T if z["strat"] == s]
        log(f"  {s}: выжившие {len(a)} сд., {np.mean(a):+.2%}; ушедшие {len(b)} сд." + (f", {np.mean(b):+.2%} (медиана {np.median(b):+.2%})" if b else "")
            + f"; бывшие «сбои» {len(c)} сд." + (f", {np.mean(c):+.2%}" if c else "") + f"; всё вместе {np.mean(al):+.2%} (сдвиг {np.mean(al) - np.mean(a):+.2%})")
        rep[s] = dict(n_surv=len(a), mean_surv=float(np.mean(a)), n_gone=len(b), mean_gone=float(np.mean(b)) if b else None,
                      n_crash=len(c), mean_crash=float(np.mean(c)) if c else None, mean_all=float(np.mean(al)))
    sets = {"выжившие без обвалов (≈ исследования)": [z for z in T if not z["gone"] and not z["crash"]],
            "+ ушедшие": [z for z in T if not z["crash"]], "+ обвалы": [z for z in T if not z["gone"]], "всё вместе": T,
            "всё вместе без F2": [z for z in T if z["strat"] != "F2"]}
    log("===== Портфель 5-мин 2020–2026 (одна позиция, 5/5/2,5%, потолок 150%) =====")
    res = {}
    for k, L in sets.items():
        s, tk, sk, cutn, kd = E40.port(L, CL, days, "V1")
        m = E35.metr(s)
        res[k] = dict(cagr=m["cagr"], dd=m["dd"], worst=m["worst"], years=m["years"], taken=tk)
        log(f"  {k}: {m['cagr']:+.2%} в год, просадка {m['dd']:.0%}, худший год {m['worst']:+.1%}, сделок {tk}; по годам "
            + ", ".join(f"{y}: {r_:+.1%}" for y, r_ in m["years"].items()))
    rep["портфель"] = res
    rng = np.random.default_rng(45)
    for a_, b_ in (("выжившие без обвалов (≈ исследования)", "всё вместе"), ("всё вместе", "всё вместе без F2")):
        ys = sorted(res[a_]["years"])
        dif = np.array([res[b_]["years"].get(y, 0) - res[a_]["years"].get(y, 0) for y in ys])
        bs = np.array([dif[rng.integers(0, len(dif), len(dif))].mean() for _ in range(5000)])
        log(f"  «{b_}» − «{a_}»: {dif.mean():+.2%}/год, 90% [{np.percentile(bs, 5):+.2%}; {np.percentile(bs, 95):+.2%}], лучше в {int((dif > 0.0005).sum())} из {len(ys)} лет")
        rep[f"{b_} − {a_}"] = dict(mean=float(dif.mean()), lo90=float(np.percentile(bs, 5)), hi90=float(np.percentile(bs, 95)))
    json.dump(rep, open(os.path.join(TR.MODELS, "exp45_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №45 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
