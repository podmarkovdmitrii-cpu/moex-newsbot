# -*- coding: utf-8 -*-
"""Проверка №27: кривые капитала стратегий виртуального счёта (S1, F4, F2) и общего портфеля на 5-минутных свечах 2020–2026.

Сделки — как в №26 (S1: лимит на откате 50% хода, тейк +25% хода, до 5 дн.; F2: покупка сразу, 10 дн., модель-фильтр «верхние 30%»;
F4: покупка сразу, тейк +200% хода, до 20 дн., модель-фильтр с математикой «верхние 30%»; издержки 0,1/0,2%).
Портфель — как у бота: на сделку S1 и F4 — 10% капитала, F2 — 5% (половинный риск), не больше 10 открытых позиций (лишние сигналы
пропускаются), прибыль учитывается в день выхода, капитал реинвестируется. Печатает по годам, просадку и капитал на конец каждого месяца
(строки «КРИВАЯ …» — для графика).
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
import exp22 as E22  # noqa: E402
import exp26 as E26  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
W = {"S1": 0.10, "F4": 0.10, "F2": 0.05}


def trades_with_exit():
    rows = []
    orig = E22.trade5

    def tap(r, Dd, bars, side_long, ent, tp_kind, tp_val, hold, cost):
        x = orig(r, Dd, bars, side_long, ent, tp_kind, tp_val, hold, cost)
        if x:
            rows.append(x[1])
        return x
    E22.trade5 = tap
    T = E26.build_trades()
    E22.trade5 = orig
    # build_trades вызывает trade5 и для неотобранных сигналов только при True-ветках — порядок вызовов совпадает с порядком строк
    T["exit"] = pd.to_datetime(rows[:len(T)]) if len(rows) == len(T) else pd.NaT
    return T


def portfolio(T, keys, maxpos=10):
    g = T[T.strat.isin(keys)].sort_values("d")
    cap, open_, pnl, taken, skipped = 1.0, [], {}, [], 0
    for _, r in g.iterrows():
        still = []
        for x in open_:
            if x[0] < r.d:
                cap += x[1]
                pnl[x[0]] = pnl.get(x[0], 0) + x[1]
            else:
                still.append(x)
        open_ = still
        if len(open_) >= maxpos:
            skipped += 1
            continue
        p = W[r.strat] * cap * r.ret
        open_.append((r.exit, p))
        taken.append(r)
    for x in open_:
        cap += x[1]
        pnl[x[0]] = pnl.get(x[0], 0) + x[1]
    s = pd.Series(pnl).sort_index()
    eq = 1 + s.cumsum()
    me = eq.resample("ME").last().ffill()
    yrs = {}
    for y in sorted(set(s.index.year)):
        start = 1 + s[s.index.year < y].sum()
        yrs[int(y)] = float(s[s.index.year == y].sum() / start)
    dd = float((eq / eq.cummax() - 1).min())
    n_years = (s.index[-1] - s.index[0]).days / 365.25
    cagr = float(eq.iloc[-1] ** (1 / max(0.5, n_years)) - 1)
    rets = np.array([t.ret for t in taken])
    return dict(final=float(eq.iloc[-1]), cagr=cagr, dd=dd, years=yrs, n=len(taken), skipped=skipped,
                win=float((rets > 0).mean()) if len(rets) else None, mean=float(rets.mean()) if len(rets) else None,
                monthly={d.strftime("%Y-%m"): round(float(v), 4) for d, v in me.items()})


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    T = trades_with_exit()
    T = T.dropna(subset=["exit"])
    log(f"сделок: " + ", ".join(f"{k} {v}" for k, v in T.strat.value_counts().items()) + f"; {round(time.time() - t0)} с")
    out = {}
    log("===== КАПИТАЛ ПО СТРАТЕГИЯМ И ОБЩИЙ ПОРТФЕЛЬ (как у бота: S1/F4 — 10%, F2 — 5%, ≤10 позиций) =====")
    for nm, keys in (("S1", ["S1"]), ("F4", ["F4"]), ("F2", ["F2"]), ("Портфель S1+F4+F2", ["S1", "F4", "F2"]), ("Портфель S1+F4", ["S1", "F4"])):
        r = portfolio(T, keys)
        out[nm] = r
        log(f"  {nm}: сделок {r['n']} (пропущено {r['skipped']}), средняя {r['mean']:+.2%}, прибыльных {r['win']:.0%}; капитал ×{r['final']:.2f}, "
            f"в среднем {r['cagr']:+.1%} в год, макс. просадка {r['dd']:.0%}")
        log("     по годам: " + ", ".join(f"{y}: {v:+.1%}" for y, v in r["years"].items()))
        log(f"КРИВАЯ {nm} | " + " ".join(f"{k}={v}" for k, v in r["monthly"].items()))
    json.dump(out, open(os.path.join(TR.MODELS, "exp27_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №27 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
