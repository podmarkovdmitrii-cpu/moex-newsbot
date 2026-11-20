# -*- coding: utf-8 -*-
"""Проверка №28: что будет без лимита 10 позиций (S1, F4, F2 и общий портфель, 5-минутные свечи 2020–2026).

Сделки — те же, что в №26–27. В №27 стоял лимит 10 открытых позиций: при 10% на сделку это ровно 100% капитала, то есть без плеча;
лишние сигналы пропускались (в общем портфеле — 257 из 804). Здесь варианты:
  • лимит 10 / 15 / 20 позиций (при 10% на сделку 15–20 позиций = плечо до 1,5–2);
  • без лимита с плечом: каждая сделка — полный размер (10% / 5% капитала), сколько бы ни было открыто;
    плата за заём — 0% (идеал) и 20% годовых на занятую часть (примерно ставка маржинального кредита у брокера в 2024–2026);
  • без лимита и без плеча: сделка берётся, пока хватает свободных денег; если не хватает — на остаток;
  • без лимита, половинный размер (5% / 2,5%), плата 20%.
Считается капитал в день выхода (реинвест), просадка, годовые, максимум открытых позиций и загрузки капитала (>100% — плечо),
и сколько дней счёт был в плече. Свободные деньги ничего не приносят во всех вариантах (одинаково).
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
import exp27 as E27  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
W0 = {"S1": 0.10, "F4": 0.10, "F2": 0.05}


def run(T, keys, maxpos=None, mode="lev", rate=0.0, scale=1.0):
    g = T[T.strat.isin(keys)].sort_values("d")
    cap, open_, pnl, taken, skipped, cut = 1.0, [], {}, [], 0, 0
    book = []   # (вход, выход, сумма) — для загрузки и платы за заём
    for _, r in g.iterrows():
        still = []
        for x in open_:
            if x[0] < r.d:
                cap += x[1]
                pnl[x[0]] = pnl.get(x[0], 0) + x[1]
            else:
                still.append(x)
        open_ = still
        if maxpos is not None and len(open_) >= maxpos:
            skipped += 1
            continue
        size = W0[r.strat] * scale * cap
        if mode == "cash":
            free = cap - sum(x[2] for x in open_)
            if free < 0.01 * cap:
                skipped += 1
                continue
            if free < size:
                size, cut = free, cut + 1
        p = size * r.ret
        open_.append((r.exit, p, size))
        book.append((r.d, r.exit, size))
        taken.append(r)
    for x in open_:
        cap += x[1]
        pnl[x[0]] = pnl.get(x[0], 0) + x[1]
    s = pd.Series(pnl).sort_index()
    # загрузка капитала по дням
    days = pd.date_range(min(b[0] for b in book), max(b[1] for b in book), freq="D")
    expo = pd.Series(0.0, index=days)
    npos = pd.Series(0, index=days)
    for d0, d1, sz in book:
        expo[d0:d1] += sz
        npos[d0:d1] += 1
    capd = (1 + s.reindex(days).fillna(0).cumsum())
    load = expo / capd
    borrow = (expo - capd).clip(lower=0)
    if rate > 0:
        cost = borrow * rate / 365
        s = s.add(-cost[cost > 0], fill_value=0).sort_index()
    eq = 1 + s.cumsum()
    yrs = {}
    for y in sorted(set(s.index.year)):
        start = 1 + s[s.index.year < y].sum()
        yrs[int(y)] = float(s[s.index.year == y].sum() / start)
    dd = float((eq / eq.cummax() - 1).min())
    n_years = (s.index[-1] - s.index[0]).days / 365.25
    me = eq.resample("ME").last().ffill()
    return dict(final=float(eq.iloc[-1]), cagr=float(eq.iloc[-1] ** (1 / n_years) - 1), dd=dd, years=yrs, n=len(taken), skipped=skipped, cut=cut,
                maxpos=int(npos.max()), maxload=float(load.max()), lev_days=int((load > 1.0001).sum()), avgload=float(load.mean()),
                lev_cost=float((borrow * rate / 365).sum()) if rate > 0 else 0.0, monthly={d.strftime("%Y-%m"): round(float(v), 4) for d, v in me.items()})


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    T = E27.trades_with_exit().dropna(subset=["exit"])
    log(f"сделок: " + ", ".join(f"{k} {v}" for k, v in T.strat.value_counts().items()) + f"; {round(time.time() - t0)} с")
    V = [("лимит 10 (как сейчас)", dict(maxpos=10)), ("лимит 15", dict(maxpos=15)), ("лимит 20", dict(maxpos=20)),
         ("без лимита, плечо, заём 0%", dict(maxpos=None, mode="lev", rate=0.0)),
         ("без лимита, плечо, заём 20%", dict(maxpos=None, mode="lev", rate=0.20)),
         ("без лимита, без плеча (на свободные деньги)", dict(maxpos=None, mode="cash")),
         ("без лимита, половинный размер, заём 20%", dict(maxpos=None, mode="lev", rate=0.20, scale=0.5))]
    out = {}
    for nm, keys in (("Портфель S1+F4+F2", ["S1", "F4", "F2"]), ("Портфель S1+F4", ["S1", "F4"]), ("S1", ["S1"]), ("F4", ["F4"]), ("F2", ["F2"])):
        log(f"===== {nm} =====")
        out[nm] = {}
        for vn, kw in V:
            if nm in ("S1", "F4", "F2") and vn not in ("лимит 10 (как сейчас)", "без лимита, плечо, заём 20%", "без лимита, без плеча (на свободные деньги)"):
                continue
            r = run(T, keys, **kw)
            out[nm][vn] = r
            log(f"  {vn}: сделок {r['n']} (пропущено {r['skipped']}, урезано {r['cut']}); капитал ×{r['final']:.2f}, {r['cagr']:+.1%} в год, "
                f"просадка {r['dd']:.0%}; макс. позиций {r['maxpos']}, макс. загрузка {r['maxload']:.0%}, средняя {r['avgload']:.0%}, "
                f"дней в плече {r['lev_days']}, плата за заём {r['lev_cost']:.1%} капитала")
            log("     по годам: " + ", ".join(f"{y}: {v:+.1%}" for y, v in r["years"].items()))
            log(f"КРИВАЯ {nm} / {vn} | " + " ".join(f"{k}={v}" for k, v in r["monthly"].items()))
    json.dump(out, open(os.path.join(TR.MODELS, "exp28_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №28 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
