# -*- coding: utf-8 -*-
"""Проверка №39 (аудит B1-1): насколько результат исследований зависел от второй позиции по той же бумаге.

В проверках №30/31/35 портфель брал сигналы S1, F4 и F2 по одной бумаге одновременно (S1 + F2 на одном росте, F4 поверх открытой S1).
С 25.11.2026 бот так не делает: одна позиция на бумагу (правило риск-менеджмента). Здесь — те же сделки и те же правила портфеля бота
(S1/F4 5%, F2 2,5%, потолок 150%, F2 только при загрузке ≤ 100% и ≤ 2 открытых, приоритет S1/F4, размер от капитала по закрытым,
переоценка по дневным закрытиям) в двух вариантах: «как в исследованиях» и «одна позиция на бумагу».
  Б) 2020–2026 — 5-минутные свечи, сделки №30 (точные правила бота);
  А) 2011–2026 — дневные аналоги «вперёд», сделки №31.
Выход: в год, просадка, сделок/пропущено, сколько сделок отсечено правилом и их средний результат, вклад отсечённых по годам.
Не является индивидуальной инвестиционной рекомендацией.
"""
import json
import os
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import exp35 as E35  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
W, CAP, F2_LOAD, F2_OPEN = E35.W, E35.CAP, E35.F2_LOAD, E35.F2_OPEN


def port(trades, CL, days, dedup):
    by = {}
    for z in sorted(trades, key=lambda z: (z["d_in"], 0 if z["strat"] != "F2" else 1, z["t"])):
        by.setdefault(z["d_in"], []).append(z)
    cash, op, eq, taken, skipped, dup = 0.0, [], [], 0, 0, []
    for d in days:
        keep = []
        for z in op:
            if z["d_out"] < d:
                cash += z["size"] * z["ret"]
            else:
                keep.append(z)
        op = keep
        capn = 1 + cash
        for z in by.get(d, []):
            if dedup and any(o["t"] == z["t"] for o in op):
                dup.append(z)
                continue
            used = sum(o["size"] for o in op) / capn
            w = W[z["strat"]]
            if used + w > CAP + 1e-9:
                skipped += 1
                continue
            if z["strat"] == "F2" and (used + w > F2_LOAD + 1e-9 or sum(1 for o in op if o["strat"] == "F2") >= F2_OPEN):
                skipped += 1
                continue
            e = z.get("e") or CL.get(z["t"], {}).get(d)
            if not e:
                skipped += 1
                continue
            op.append(dict(z, size=w * capn, e=e))
            taken += 1
        mtm = 0.0
        for z in op:
            c = CL.get(z["t"], {}).get(d)
            if c and d >= z["d_in"]:
                mtm += z["size"] * (c / z["e"] - 1)
        eq.append(1 + cash + mtm)
    return pd.Series(eq, index=pd.to_datetime(days)), taken, skipped, dup


def compare(name, trades, CL, days):
    out = {}
    log(f"===== {name}: сделок-кандидатов {len(trades)} =====")
    # сколько пар «та же бумага, пересекающиеся сроки» в исходном списке
    by_t = {}
    for z in trades:
        by_t.setdefault(z["t"], []).append(z)
    over = sum(1 for lst in by_t.values() for i, a in enumerate(lst) for b in lst[i + 1:] if a["d_in"] <= b["d_out"] and b["d_in"] <= a["d_out"])
    log(f"  пар сделок по одной бумаге с пересекающимися сроками (до правил портфеля): {over}")
    for dedup in (False, True):
        s, tk, sk, dup = port(trades, CL, days, dedup)
        m = E35.metr(s)
        key = "одна позиция на бумагу" if dedup else "как в исследованиях"
        out[key] = dict(m, taken=tk, skipped=sk, dup=len(dup))
        log(f"  {key}: {m['cagr']:+.1%} в год, просадка {m['dd']:.0%}, доходность/просадка {m['calmar']:.2f}, худший год {m['worst']:+.1%}, "
            f"в плюсе {m['pos']} из {m['ny']} лет; сделок {tk}, пропущено потолком {sk}" + (f", отсечено правилом {len(dup)}" if dedup else ""))
        log("     по годам: " + ", ".join(f"{y}: {v:+.1%}" for y, v in m["years"].items()))
        if dedup and dup:
            g = pd.DataFrame(dup)
            log(f"  отсечённые сделки: {len(g)} — " + ", ".join(f"{k} {v}" for k, v in g.strat.value_counts().items())
                + f"; средний результат {g.ret.mean():+.2%}, медиана {g.ret.median():+.2%}, прибыльных {(g.ret > 0).mean():.0%}")
            g["y"] = g.d_in.str[:4]
            log("     по годам (число / средний результат): " + ", ".join(f"{y}: {len(x)}/{x.ret.mean():+.1%}" for y, x in g.groupby("y")))
            out["отсечённые"] = dict(n=int(len(g)), mean=float(g.ret.mean()), median=float(g.ret.median()), win=float((g.ret > 0).mean()),
                                     by=g.strat.value_counts().to_dict())
    a, b = out["как в исследованиях"], out["одна позиция на бумагу"]
    log(f"  РАЗНИЦА (одна позиция − как в исследованиях): {b['cagr'] - a['cagr']:+.1%} в год, просадка {b['dd'] - a['dd']:+.0%} п.")
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
    # ---------- Б: 5-мин, сделки №30 ----------
    import exp29 as E29
    import exp30 as E30
    E, R, FE, Dd2, bars, cost = E29.load_all()
    F = E29.Filt(E, FE)
    rows = E30.build(E, R, F, Dd2, bars, cost)
    V = E30.variants(rows)
    cur = V[("нет", "как сейчас", "свой")]
    trB = [dict(strat=x["strat"], t=x["t"], d_in=x["d_in"], d_out=v[2], ret=float(v[0]), e=x["e"]) for v, x in zip(cur, rows) if v is not None]
    dB = sorted(set(d for d in days if d >= "2020-01-01") | {z["d_in"] for z in trB if z["d_in"] >= "2020-01-01"})
    rep["B"] = compare("Б) 2020–2026, 5-мин, правила бота", trB, CL, dB)
    log(f"{round(time.time() - t0)} с")
    # ---------- А: дневные аналоги «вперёд», сделки №31 ----------
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
    rep["A"] = compare("А) 2011–2026, дневные аналоги «вперёд»", trA, CL, [d for d in days if d >= "2011-01-01"])
    json.dump(rep, open(os.path.join(TR.MODELS, "exp39_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №39 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
