# -*- coding: utf-8 -*-
"""Проверка №40 (аудит B1-1, продолжение №39): какое правило «позиций по одной бумаге» выбрать.

Те же сделки и правила портфеля, что в №39 (S1/F4 5%, F2 2,5%, потолок 150%, F2 при загрузке ≤ 100% и ≤ 2 открытых,
приоритет S1/F4, размер от капитала по закрытым, переоценка по дневным закрытиям). Четыре варианта:
  R  — как в исследованиях (без ограничений);
  V1 — одна позиция на бумагу (бот сейчас);
  V2 — S1 и F2 могут стоять на одной бумаге; F4 не берётся, если по бумаге есть S1/F2, и наоборот;
       та же стратегия по той же бумаге дважды не берётся;
  V3 — без запрета дублей, но по бумаге не больше 7,5% капитала суммарно.
Плюс: отсечённые сделки по годам, результат без 2022 года, разница вариантов по годам.
  Б) 2020–2026 — 5-минутные свечи, сделки №30;  А) 2011–2026 — дневные аналоги «вперёд», сделки №31.
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
TICKER_CAP = 0.075
CRISIS = {"2022"}


def allowed(rule, z, op, capn):
    same = [o for o in op if o["t"] == z["t"]]
    if rule == "R" or not same:
        return True
    if rule == "V1":
        return False
    if rule == "V2":
        if any(o["strat"] == z["strat"] for o in same):
            return False
        if z["strat"] == "F4" or any(o["strat"] == "F4" for o in same):
            return False
        return True
    if rule == "V3":
        return (sum(o["size"] for o in same) / capn + W[z["strat"]]) <= TICKER_CAP + 1e-9
    raise ValueError(rule)


def port(trades, CL, days, rule):
    by = {}
    for z in sorted(trades, key=lambda z: (z["d_in"], 0 if z["strat"] != "F2" else 1, z["t"])):
        by.setdefault(z["d_in"], []).append(z)
    cash, op, eq, taken, skipped, cut, kept_dup = 0.0, [], [], 0, 0, [], 0
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
            if not allowed(rule, z, op, capn):
                cut.append(z)
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
            if any(o["t"] == z["t"] for o in op):
                kept_dup += 1
            op.append(dict(z, size=w * capn, e=e))
            taken += 1
        mtm = 0.0
        for z in op:
            c = CL.get(z["t"], {}).get(d)
            if c and d >= z["d_in"]:
                mtm += z["size"] * (c / z["e"] - 1)
        eq.append(1 + cash + mtm)
    return pd.Series(eq, index=pd.to_datetime(days)), taken, skipped, cut, kept_dup


def ex_years(years, drop):
    v = 1.0
    n = 0
    for y, r in years.items():
        if str(y) in drop:
            continue
        v *= 1 + r
        n += 1
    return v ** (1 / n) - 1 if n else 0.0


NAMES = {"R": "как в исследованиях", "V1": "одна позиция (бот сейчас)", "V2": "S1+F2 можно, F4 отдельно", "V3": "лимит 7,5% на бумагу"}


def compare(name, trades, CL, days):
    out = {}
    log(f"===== {name}: сделок-кандидатов {len(trades)} =====")
    for rule in ("R", "V1", "V2", "V3"):
        s, tk, sk, cut, kd = port(trades, CL, days, rule)
        m = E35.metr(s)
        m["ex2022"] = ex_years(m["years"], CRISIS)
        r = dict(m, taken=tk, skipped=sk, cut=len(cut), kept_dup=kd)
        log(f"  {rule} {NAMES[rule]}: {m['cagr']:+.1%} в год, просадка {m['dd']:.0%}, доходность/просадка {m['calmar']:.2f}, худший год {m['worst']:+.1%}; "
            f"без 2022: {m['ex2022']:+.1%} в год; сделок {tk} (из них вторых по бумаге {kd}), пропущено потолком {sk}, отсечено правилом {len(cut)}")
        if cut:
            g = pd.DataFrame(cut)
            g["y"] = g.d_in.str[:4]
            calm = g[~g.y.isin(CRISIS)]
            r["cut_stats"] = dict(n=int(len(g)), mean=float(g.ret.mean()), win=float((g.ret > 0).mean()),
                                  by=g.strat.value_counts().to_dict(),
                                  n_ex2022=int(len(calm)), mean_ex2022=float(calm.ret.mean()) if len(calm) else None,
                                  win_ex2022=float((calm.ret > 0).mean()) if len(calm) else None,
                                  years={y: [int(len(x)), float(x.ret.mean())] for y, x in g.groupby("y")})
            cs = r["cut_stats"]
            log(f"     отсечённые: {cs['n']} (" + ", ".join(f"{k} {v}" for k, v in cs["by"].items()) + f"), средний {cs['mean']:+.2%}, прибыльных {cs['win']:.0%}; "
                f"без 2022: {cs['n_ex2022']} шт., средний " + (f"{cs['mean_ex2022']:+.2%}, прибыльных {cs['win_ex2022']:.0%}" if cs["n_ex2022"] else "—"))
            log("     по годам (число / средний): " + ", ".join(f"{y}: {v[0]}/{v[1]:+.1%}" for y, v in cs["years"].items()))
        out[rule] = r
    ys = sorted(out["R"]["years"])
    log("  по годам, в год:      " + " | ".join(f"{y}" for y in ys))
    for rule in ("R", "V1", "V2", "V3"):
        log(f"   {rule:3s}                 " + " | ".join(f"{out[rule]['years'].get(y, 0):+.1%}" for y in ys))
    for rule in ("R", "V2", "V3"):
        dif = {y: out[rule]["years"].get(y, 0) - out["V1"]["years"].get(y, 0) for y in ys}
        better = sum(v > 0.0005 for v in dif.values())
        worse = sum(v < -0.0005 for v in dif.values())
        log(f"   {rule} − V1 по годам: " + ", ".join(f"{y}: {v:+.1%}" for y, v in dif.items()) + f"  → лучше в {better}, хуже в {worse} из {len(ys)} лет")
        out[rule]["vs_V1"] = dict(better=better, worse=worse, diff=dif)
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
    rep["B"] = compare("Б) 2020–2026, 5-мин, правила бота", trB, CL, dB)
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
    rep["A"] = compare("А) 2011–2026, дневные аналоги «вперёд»", trA, CL, [d for d in days if d >= "2011-01-01"])
    json.dump(rep, open(os.path.join(TR.MODELS, "exp40_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №40 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
