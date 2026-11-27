# -*- coding: utf-8 -*-
"""Проверка №46: (1) лимит числа новых F4 в день — дозирование риска в дни рыночных обвалов; (2) затухание по годам — режим или эрозия.

Данные — как в №45: 5-мин 2020–2026, точные правила бота, полный состав (ушедшие с биржи), обвалы в расчёте (exp15.bad_path теперь «real»).
(1) Сколько дней с > 2/3/5 сигналами F4 (событий и взятых сделок); портфель (одна позиция, 5/5/2,5%, потолок 150%) с лимитом новых F4 в день:
    нет / 5 / 3 / 2 / 1; бутстрэп по годам «лимит − нет»; худшие 10 дней портфеля без лимита.
(2) По годам: число событий роста/падения (ликвидных), сколько прошло в сделки по стратегиям, средняя сделка, доля прибыльных, вклад в год.
    Если событий столько же, а средняя падает — эрозия; если событий меньше — режим рынка.
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
import exp35 as E35  # noqa: E402
import exp40 as E40  # noqa: E402
import exp45 as E45  # noqa: E402
import train as TR  # noqa: E402

log = TR.log


def limit_f4(T, k):
    """Не больше k новых F4 в день (порядок внутри дня — как в портфеле: по тикеру)."""
    if k is None:
        return list(T)
    cnt, out = {}, []
    for z in sorted(T, key=lambda z: (z["d_in"], z["t"])):
        if z["strat"] == "F4":
            n = cnt.get(z["d_in"], 0)
            if n >= k:
                continue
            cnt[z["d_in"]] = n + 1
        out.append(z)
    return out


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    rep = {}
    S = E45.load_full(rep)
    T, CL, days, E = S["T"], S["CL"], S["days"], S["E"]
    log(f"сделок-кандидатов {len(T)}; {round(time.time() - t0)} с")
    # ---------- (1) лимит F4 в день
    f4 = pd.Series([z["d_in"] for z in T if z["strat"] == "F4"]).value_counts()
    ev_dn = E[(E.d >= pd.Timestamp("2020-01-01")) & (E.s < 0)].d.dt.strftime("%Y-%m-%d").value_counts()
    log("===== (1) Лимит новых F4 в день =====")
    log(f"  событий падения (ликвидных) по дням: дней с > 2: {int((ev_dn > 2).sum())}, > 3: {int((ev_dn > 3).sum())}, > 5: {int((ev_dn > 5).sum())}; "
        f"максимум {int(ev_dn.max())} ({ev_dn.idxmax()})")
    log(f"  сделок-кандидатов F4 (после модель-фильтра) по дням: дней с > 1: {int((f4 > 1).sum())}, > 2: {int((f4 > 2).sum())}, > 3: {int((f4 > 3).sum())}, "
        f"> 5: {int((f4 > 5).sum())}; самые плотные: " + ", ".join(f"{d}: {n}" for d, n in f4.head(8).items()))
    res = {}
    for k in (None, 5, 3, 2, 1):
        L = limit_f4(T, k)
        s, tk, sk, cut, kd = E40.port(L, CL, days, "V1")
        m = E35.metr(s)
        nm = "без лимита" if k is None else f"≤ {k} F4 в день"
        res[nm] = dict(cagr=m["cagr"], dd=m["dd"], worst=m["worst"], years=m["years"], taken=tk, f4=sum(z["strat"] == "F4" for z in L))
        log(f"  {nm}: {m['cagr']:+.2%} в год, просадка {m['dd']:.0%}, доходность/просадка {m['calmar']:.2f}, худший год {m['worst']:+.1%}, "
            f"сделок {tk} (кандидатов F4 {res[nm]['f4']})")
        if k is None:
            dd_ = s.pct_change().dropna().sort_values().head(10)
            log("     худшие дни портфеля: " + ", ".join(f"{d.date()}: {v:+.1%}" for d, v in dd_.items()))
            run = s / s.cummax() - 1
            log(f"     самая глубокая просадка: дно {run.idxmin().date()}, начало {s[:run.idxmin()].idxmax().date()}")
    base = res["без лимита"]
    rng = np.random.default_rng(46)
    ys = sorted(base["years"])
    for nm, r in res.items():
        if nm == "без лимита":
            continue
        dif = np.array([r["years"].get(y, 0) - base["years"].get(y, 0) for y in ys])
        bs = np.array([dif[rng.integers(0, len(dif), len(dif))].mean() for _ in range(5000)])
        log(f"  «{nm}» − «без лимита»: {dif.mean():+.2%}/год, 90% [{np.percentile(bs, 5):+.2%}; {np.percentile(bs, 95):+.2%}], просадка {r['dd'] - base['dd']:+.1%} п.; "
            + ", ".join(f"{y}: {d:+.1%}" for y, d in zip(ys, dif)))
        r["vs"] = dict(mean=float(dif.mean()), lo90=float(np.percentile(bs, 5)), hi90=float(np.percentile(bs, 95)))
    rep["лимит F4"] = res
    # ---------- (2) по годам
    log("===== (2) По годам: события, сделки, средняя =====")
    Ey = E[E.d >= pd.Timestamp("2020-01-01")]
    td = pd.DataFrame(T)
    td["y"] = td.d_in.str[:4]
    yrs = {}
    for y in sorted(td.y.unique()):
        e = Ey[Ey.d.dt.year == int(y)]
        row = dict(ev_up=int((e.s > 0).sum()), ev_dn=int((e.s < 0).sum()), port=base["years"].get(int(y)))
        parts = []
        for st in ("S1", "F2", "F4"):
            g = td[(td.y == y) & (td.strat == st)]
            row[st] = dict(n=int(len(g)), mean=float(g.ret.mean()) if len(g) else None, win=float((g.ret > 0).mean()) if len(g) else None)
            parts.append(f"{st} {len(g)} сд." + (f" {g.ret.mean():+.1%} ({(g.ret > 0).mean():.0%})" if len(g) else ""))
        yrs[y] = row
        log(f"  {y}: событий рост/падение {row['ev_up']}/{row['ev_dn']}; " + "; ".join(parts) + (f"; портфель {row['port']:+.1%}" if row["port"] is not None else ""))
    rep["по годам"] = yrs
    # грубая оценка: меньше событий или хуже средняя
    for a, b in (("2020", "2023"), ("2024", "2026")):
        g = td[(td.y >= a) & (td.y <= b)]
        e = Ey[(Ey.d.dt.year >= int(a)) & (Ey.d.dt.year <= int(b))]
        n_y = int(b) - int(a) + 1 if b != "2026" else 2.9
        log(f"  {a}–{b}: событий в год {len(e) / n_y:.0f}, сделок в год {len(g) / n_y:.0f}, средняя сделка {g.ret.mean():+.2%} "
            + "; ".join(f"{st} {g[g.strat == st].ret.mean():+.2%}" for st in ("S1", "F2", "F4") if (g.strat == st).any()))
    json.dump(rep, open(os.path.join(TR.MODELS, "exp46_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №46 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
