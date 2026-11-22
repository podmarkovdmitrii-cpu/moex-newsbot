# -*- coding: utf-8 -*-
"""Проверка №35: какую прежнюю стратегию добавить к сборной (S1 + F4 + F2) и как делить капитал.

Кандидат — v3 «Режимная: импульс + откат + шорт пробоя» (newsbot/strategy.json, движок regime.js; держит импульс неделями и месяцами,
риск 1% на сделку, стоп 3%, до 3 позиций, без плеча). Прочие прежние варианты слабее (№25 трендовый сканер +1…7%/год при просадке 20–45%;
«трейдер ≤ 15 дней» 5%/год) — в расчёт не берутся.

Сборная стратегия — дневные кривые с переоценкой открытых позиций, правила бота: S1/F4 5%, F2 2,5% (F2 — только при загрузке ≤ 100% и ≤ 2 открытых),
потолок загрузки 150%:
  А) 2011–2026 — дневные аналоги, выбор «вперёд» по прошлым годам (сделки №31);
  Б) 2020–2026 — 5-минутные свечи, точные правила бота (сделки №30).
v3 — дневная кривая 2009–2026 (без новостных сигналов: их истории до 2017 нет).
Сочетания: доля капитала во второй стратегии 0…100% с ребалансировкой раз в месяц; «надстройка» — v3 на те же деньги поверх сборной
(сборная в среднем загружает лишь часть капитала) с суммарной загрузкой. Метрики: в год, макс. просадка, доходность/просадка, худший год,
корреляция дневных и месячных доходностей, поведение v3 в худшие месяцы сборной.
Не является индивидуальной инвестиционной рекомендацией.
"""
import json
import os
import subprocess
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
W = {"S1": 0.05, "F4": 0.05, "F2": 0.025}
CAP, F2_LOAD, F2_OPEN = 1.5, 1.0, 2


def port(trades, CL, days):
    """Дневная кривая с переоценкой, правила бота. trades: dict(strat, t, d_in, d_out, ret, e). → (Series капитала, Series загрузки, taken, skipped)."""
    by = {}
    for z in sorted(trades, key=lambda z: (z["d_in"], 0 if z["strat"] != "F2" else 1, z["t"])):
        by.setdefault(z["d_in"], []).append(z)
    cash, op, eq, ld, taken, skipped = 0.0, [], [], [], 0, 0
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
        ld.append(sum(o["size"] for o in op) / max(1e-9, 1 + cash))
    idx = pd.to_datetime(days)
    return pd.Series(eq, index=idx), pd.Series(ld, index=idx), taken, skipped


def metr(s):
    s = s.dropna()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1
    dd = float((s / s.cummax() - 1).min())
    ys = {}
    prev = s.iloc[0]
    for y, g in s.groupby(s.index.year):
        ys[int(y)] = float(g.iloc[-1] / prev - 1)
        prev = g.iloc[-1]
    return dict(cagr=float(cagr), dd=dd, calmar=float(cagr / abs(dd)) if dd else 0.0, worst=min(ys.values()), pos=sum(v > 0 for v in ys.values()), ny=len(ys), years=ys)


def mix_split(rA, rB, s):
    """Доля s капитала во второй стратегии, ребалансировка в первый день месяца."""
    va, vb, out, m0 = 1 - s, s, [], None
    for d, a, b in zip(rA.index, rA.values, rB.values):
        if m0 is not None and d.month != m0:
            v = va + vb
            va, vb = v * (1 - s), v * s
        m0 = d.month
        va *= 1 + a
        vb *= 1 + b
        out.append(va + vb)
    return pd.Series(out, index=rA.index)


def analyse(name, A, LA, B, EB):
    log(f"===== {name} =====")
    idx = A.index.intersection(B.index)
    A, LA, B, EB = A.reindex(idx), LA.reindex(idx), B.reindex(idx), EB.reindex(idx)
    rA, rB = A.pct_change().fillna(0), B.pct_change().fillna(0)
    mA, mB = (1 + rA).resample("ME").prod() - 1, (1 + rB).resample("ME").prod() - 1
    worst = mA.nsmallest(max(3, len(mA) // 10))
    log(f"  период {idx[0].date()} – {idx[-1].date()}, дней {len(idx)}; корреляция дневных {rA.corr(rB):+.2f}, месячных {mA.corr(mB):+.2f}; "
        f"в худших 10% месяцев сборной (в среднем {worst.mean():+.1%}) v3 в среднем {mB[worst.index].mean():+.1%}, в плюсе {int((mB[worst.index] > 0).sum())} из {len(worst)}")
    log(f"  загрузка сборной: средняя {LA.mean():.0%}, макс. {LA.max():.0%}; v3: средняя {EB.mean():.0%}, макс. {EB.max():.0%}")
    res = {}
    for s in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
        m = metr(mix_split(rA, rB, s))
        res[f"доля v3 {s:.0%}"] = m
        log(f"  доля v3 {s:>4.0%}: {m['cagr']:+.1%} в год, просадка {m['dd']:.0%}, доходность/просадка {m['calmar']:.2f}, худший год {m['worst']:+.0%}, "
            f"лет в плюсе {m['pos']}/{m['ny']}")
    for k in (0.5, 1.0):
        s = (1 + rA + k * rB).cumprod()
        m = metr(s)
        load = LA + k * EB
        res[f"надстройка v3 ×{k}"] = dict(m, maxload=float(load.max()), over150=float((load > 1.5).mean()), over200=float((load > 2.0).mean()))
        log(f"  надстройка: сборная 100% + v3 ×{k}: {m['cagr']:+.1%} в год, просадка {m['dd']:.0%}, доходность/просадка {m['calmar']:.2f}, худший год {m['worst']:+.0%}, "
            f"лет в плюсе {m['pos']}/{m['ny']}; суммарная загрузка макс. {load.max():.0%}, дней > 150%: {(load > 1.5).mean():.0%}, > 200%: {(load > 2.0).mean():.0%}")
    yrs = sorted(res["доля v3 0%"]["years"])
    log("  по годам (сборная / v3 / 50 на 50 / надстройка ×1): " + "; ".join(
        f"{y}: {res['доля v3 0%']['years'][y]:+.0%} / {res['доля v3 100%']['years'][y]:+.0%} / {res['доля v3 50%']['years'][y]:+.0%} / "
        f"{res['надстройка v3 ×1.0']['years'][y]:+.0%}" for y in yrs))
    for nm, s in (("сборная", A), ("v3", B), ("50 на 50", mix_split(rA, rB, 0.5)), ("надстройка ×1", (1 + rA + rB).cumprod())):
        log(f"КРИВАЯ {name} | {nm} | " + " ".join(f"{k.strftime('%Y-%m')}={v / s.iloc[0]:.4f}" for k, v in s.resample("ME").last().items()))
    return dict(corr_d=float(rA.corr(rB)), corr_m=float(mA.corr(mB)), mixes=res)


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    rep = {}
    # ---------- v3 ----------
    import exp9 as E9
    X, Dd, IX, Hh, IXH, ev, Wn = E9.load()
    days = sorted({r[0][:10] for r in IX if pd.Timestamp(r[0][:10]).weekday() < 5})
    sec = {}
    for t, rows in Dd.items():
        rr = [[r[0][:10], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)] for r in rows
              if r[4] and pd.Timestamp(r[0][:10]).weekday() < 5]
        if len(rr) >= 60:
            sec[t] = rr
    imo = [[r[0][:10], float(r[1]), float(r[2]), float(r[3]), float(r[4]), 0] for r in IX if pd.Timestamp(r[0][:10]).weekday() < 5]
    p_in, p_out = os.path.join(TR.MODELS, "v3_in.json"), os.path.join(TR.MODELS, "v3_out.json")
    json.dump(dict(days=days, sec=sec, imoex=imo), open(p_in, "w"))
    log(f"v3: бумаг {len(sec)}, дней {len(days)} ({days[0]} – {days[-1]}); {round(time.time() - t0)} с")
    subprocess.run(["node", os.path.join(HERE, "v3run.js"), p_in, p_out, "2009-01-01", "1000000"], check=True)
    V3 = json.load(open(p_out))
    log("v3 по периодам: " + "; ".join(f"{k}: {v['cagr']:+.1%} в год, просадка {v['mdd']:.0%}, PF {v['pf']:.2f}, сделок {v['n']}, загрузка {v['expo']:.0%}"
                                       for k, v in V3["per"].items()))
    B = pd.Series({pd.Timestamp(d): e for d, e, x in V3["daily"]})
    EB = pd.Series({pd.Timestamp(d): x for d, e, x in V3["daily"]})
    rep["v3"] = V3["per"]
    CL = {}
    for t in Dd:
        D = CD.series(Dd[t])
        CL[t] = {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(D.d, D.c)}
    # ---------- А: дневные аналоги «вперёд» (сделки №31) ----------
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
    dA = [d for d in days if d >= "2011-01-01"]
    A, LA, tk, sk = port(trA, CL, dA)
    mA = metr(A)
    log(f"А) сборная, дневные аналоги «вперёд», правила бота (5/5/2,5%, потолок 150%, приоритет S1/F4): {mA['cagr']:+.1%} в год, просадка {mA['dd']:.0%}; "
        f"сделок {tk}, пропущено {sk}; {round(time.time() - t0)} с")
    rep["A"] = analyse("А) 2011–2026: сборная (дневные аналоги) + v3", A, LA, B, EB)
    # ---------- Б: 5-мин, точные правила бота (сделки №30) ----------
    import exp29 as E29
    import exp30 as E30
    E, R, FE, Dd2, bars, cost = E29.load_all()
    F = E29.Filt(E, FE)
    rows = E30.build(E, R, F, Dd2, bars, cost)
    V = E30.variants(rows)
    cur = V[("нет", "как сейчас", "свой")]
    trB = [dict(strat=x["strat"], t=x["t"], d_in=x["d_in"], d_out=v[2], ret=float(v[0]), e=x["e"]) for v, x in zip(cur, rows) if v is not None]
    dB = [d for d in days if "2020-01-01" <= d]
    dB = sorted(set(dB) | {z["d_in"] for z in trB if z["d_in"] >= "2020-01-01"})
    A5, LA5, tk, sk = port(trB, CL, dB)
    m5 = metr(A5)
    log(f"Б) сборная, 5-мин, правила бота: {m5['cagr']:+.1%} в год, просадка {m5['dd']:.0%}; сделок {tk}, пропущено {sk}; {round(time.time() - t0)} с")
    rep["B"] = analyse("Б) 2020–2026: сборная (5-мин, правила бота) + v3", A5, LA5, B, EB)
    json.dump(rep, open(os.path.join(TR.MODELS, "exp35_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №35 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
