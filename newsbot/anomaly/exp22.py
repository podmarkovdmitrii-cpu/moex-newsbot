# -*- coding: utf-8 -*-
"""Проверка №22: всё, что давало преимущество, вместе — как портфель на 5-минутных свечах ALGOPACK (2020–2026).

Составляющие (каждая проверена раньше):
  S1  — аномальный рост, лимит на откате 50% хода; сейчас: тейк 25% хода, до 5 дн. (№12–15);
  S1+ — тот же вход, выход «тейк +15%, до 10 дн.» (№20);
  S1ф — S1+ только если вчера оборот ≥ 1,47 нормы и бумага не в сильном падении за 60 дней (xret60 ≥ −10%) (№17);
  F2  — аномальный рост, покупка сразу, держать 10 дн.; модель-фильтр берёт верхние 30% (№17–18);
  F4  — аномальное падение, покупка сразу, тейк 200% хода, до 20 дн.; модель-фильтр, верхние 30% (№17–18);
  Ш5  — шорт аномального падения на 5 дн. при спокойном рынке накануне и низкой волатильности бумаги (№17).
Модель-фильтр — как на виртуальном счёте: градиентный бустинг по ≈100 признакам, каждый год учится на прошлых годах
(результаты по часовым свечам с 2012 г.), отбирает верхние 30% оценок года. Вариант «+М» — с математическими признаками (№21).
Портфели: на каждую сделку 10% капитала, не больше 10 открытых позиций (лишние сигналы пропускаются), прибыль учитывается в день
выхода; издержки 0,1/0,2% по ликвидности. По годам: прирост капитала, сделок, средний результат, просадка.
"""
import datetime as dt
import gzip
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
import exp15 as E15  # noqa: E402
import exp17 as E17  # noqa: E402
import exp18 as E18  # noqa: E402
import exp20 as E20  # noqa: E402
import math_feats as MF  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
FROM5 = int(os.environ.get("EXP_FROM5", "2020"))
SIZE = float(os.environ.get("EXP_SIZE", "0.10"))
MAXPOS = int(os.environ.get("EXP_MAXPOS", "10"))


def hgb():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0, random_state=1)


def oof_scores(E, R, feats, side, yr):
    """Оценка модели-фильтра для каждого сигнала своей стороны: модель года Y учится на годах < Y. → (оценка, метка верхних 30%)."""
    ok = np.isfinite(R)
    p = np.full(len(E), np.nan)
    for Y in range(FROM5, int(yr.max()) + 1):
        tr = ok & (yr < Y)
        te = (E.s.values == side) & (yr == Y)
        if tr.sum() < 300 or te.sum() < 5:
            continue
        clip = np.clip(R[tr], np.nanquantile(R[tr], 0.02), np.nanquantile(R[tr], 0.98))
        p[te] = hgb().fit(E.loc[tr, feats], clip).predict(E.loc[te, feats])
    top = np.zeros(len(E), bool)
    for Y in range(FROM5, int(yr.max()) + 1):
        m = (yr == Y) & np.isfinite(p)
        if m.sum():
            top[m] = p[m] >= np.quantile(p[m], 0.7)
    return p, top


def trade5(r, Dd, bars, side_long, ent, tp_kind, tp_val, hold, cost):
    """Одна сделка по 5-минутным барам (функции №20). side_long: True — покупка, False — шорт. → (доходность, дата выхода) или None."""
    sq = E20.build_seq(r, Dd, bars, hold, ent == "lim")
    if sq is None:
        return None
    e, a, days = sq
    # даты баров: те же бары, что в build_seq, — восстанавливаем по дням срока (число баров каждого дня после входа)
    dates = []
    for dd in [r.d.strftime("%Y-%m-%d")] + [x[0] for x in days]:
        dates += [dd] * len(bars.get((r.t, dd)) or [])
    dates = dates[-len(a):] if len(dates) >= len(a) else [days[-1][0]] * len(a)
    o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    mv = abs(r.p0 / r.prev - 1)
    if side_long:
        tp = None if tp_kind is None else (e * (1 + tp_val) if tp_kind == "pct" else e * (1 + tp_val * mv))
        hit = np.nonzero(h >= tp)[0] if tp else []
        px = tp if len(hit) else c[-1]
        ret = px / e - 1 - cost
    else:
        ret = 1 - c[-1] / e - cost
        hit = []
    # дата выхода: день бара с тейком или последний день срока
    exit_day = dates[int(hit[0])] if len(hit) else days[-1][0]
    return ret, exit_day


def portfolio(trades, label):
    """trades — список (дата входа, дата выхода, доходность, тег). 10% на сделку, не больше MAXPOS открытых. → сводка по годам."""
    trades = sorted(trades, key=lambda x: (x[0], x[3]))
    open_, taken, skipped = [], [], 0
    for tr in trades:
        open_ = [x for x in open_ if x[1] >= tr[0]]
        if len(open_) >= MAXPOS:
            skipped += 1
            continue
        open_.append(tr)
        taken.append(tr)
    if not taken:
        return dict(name=label, n=0)
    df = pd.DataFrame(taken, columns=["d0", "d1", "ret", "tag"])
    df["pnl"] = SIZE * df.ret
    daily = df.groupby("d1").pnl.sum().sort_index()
    eq = (1 + daily).cumprod()
    dd = float((eq / eq.cummax() - 1).min())
    yrs = {}
    for y, g in df.groupby(pd.to_datetime(df.d1).dt.year):
        e_ = (1 + g.groupby("d1").pnl.sum().sort_index()).cumprod()
        yrs[int(y)] = dict(cap=float(e_.iloc[-1] - 1), n=int(len(g)), mean=float(g.ret.mean()),
                           dd=float((e_ / e_.cummax() - 1).min()))
    tot = float(eq.iloc[-1] - 1)
    ny = max(1, len(yrs))
    res = dict(name=label, n=int(len(df)), skipped=skipped, mean=float(df.ret.mean()), win=float((df.ret > 0).mean()), total=tot,
               cagr=float((1 + tot) ** (1 / ny) - 1), dd=dd, years=yrs, pos_years=sum(1 for v in yrs.values() if v["cap"] > 0),
               by_tag={k: dict(n=int(len(g)), mean=float(g.ret.mean())) for k, g in df.groupby("tag")})
    log(f"  {label}: сделок {res['n']} (пропущено из-за лимита позиций {skipped}), {res['mean']:+.2%}/сд, прибыльных {res['win']:.0%}; "
        f"капитал за 2020–2026 {tot:+.0%}, в среднем {res['cagr']:+.1%}/год, просадка {dd:.0%}, лет в плюсе {res['pos_years']} из {len(yrs)}")
    log("     по годам: " + "; ".join(f"{y}: {v['cap']:+.1%} ({v['n']} сд, просадка {v['dd']:.0%})" for y, v in yrs.items())
        + " | по составляющим: " + ", ".join(f"{k} {v['n']} сд {v['mean']:+.2%}" for k, v in res["by_tag"].items()))
    return res


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    Ho = {}
    hdir = os.environ.get("EXP_H_DIR")
    if hdir and os.path.isdir(hdir):
        for f in os.listdir(hdir):
            if f.endswith(".json.gz"):
                with gzip.open(os.path.join(hdir, f), "rt") as fh:
                    Ho[f[:-8]] = json.load(fh)
    IXo = Ho.pop("IMOEX", [])
    H2 = {}
    for t in set(Hh) | set(Ho):
        new = Hh.get(t, [])
        cut = new[0][0] if new else "9999"
        H2[t] = [r for r in Ho.get(t, []) if r[0] < cut] + new
    IX2 = [r for r in IXo if r[0] < (IXH[0][0] if IXH else "9999")] + IXH
    E = E15.events_a(X, H2, IX2, Dd)
    E = E[E.lval >= 7].reset_index(drop=True)
    P = E15.paths_a(E)
    cost = E15.cost_of(E.lval.values)
    cache = {"cost": cost}
    RH = {k: np.where(E.s.values == w["side"], E15.sim_a(E, P, *w["cfg"], cache), np.nan) for k, w in
          (("R4", E18.WAYS["R4 падение: покупка против, тейк 200% хода, до 20 дн."]), ("R2", E18.WAYS["R2 рост: покупка сразу, держать 10 дн."]))}
    E, dfe = E17.intraday_feats(E, X, H2, IX2, Dd, ev)
    E = E17.flows_feats(E)
    E = E17.model_feats(E, X)
    feats = [f for f in ["c", "acx", "gap_sh", "ixr_c", "nw_day", "wave", "cvol", "fp3", "fe", "p_m", "pdir"] + dfe if f in E.columns and E[f].notna().mean() > 0.05]
    M = MF.build(Dd, IX, CD.series)
    E = E.merge(M, on=["d", "t"], how="left")
    featsM = feats + [c for c in MF.COLS if c in E.columns]
    yr = E.d.dt.year.values
    sc = {}
    for k, side in (("R2", 1), ("R4", -1)):
        sc[k] = oof_scores(E, RH[k], feats, side, yr)
        sc[k + "M"] = oof_scores(E, RH[k], featsM, side, yr)
    log(f"сигналов {len(E)}, признаков фильтра {len(feats)} (+М {len(featsM) - len(feats)}); {round(time.time() - t0)} с")
    # 5-минутные бары (кэш №18/№20)
    m5 = yr >= FROM5
    need = set()
    for i in np.where(m5)[0]:
        r = E.iloc[i]
        d0 = r.d.strftime("%Y-%m-%d")
        need.add((r.t, d0))
        for dd, _, _ in E18.trading_days(Dd, r.t, d0, 20):
            need.add((r.t, dd))
    bars = E18.load_bars(need, (os.environ.get("ALGOPACK_TOKEN") or "").strip())
    # сделки по составляющим
    T = {k: [] for k in ("S1", "S1+", "S1ф", "F2все", "F2", "F2М", "F4все", "F4", "F4М", "Ш5")}
    for i in np.where(m5)[0]:
        r = E.iloc[i]
        d0 = r.d.strftime("%Y-%m-%d")
        up = r.s > 0
        if up:
            x = trade5(r, Dd, bars, True, "lim", "move", 0.25, 5, cost[i])
            if x:
                T["S1"].append((d0, x[1], x[0], "S1"))
            x = trade5(r, Dd, bars, True, "lim", "pct", 0.15, 10, cost[i])
            if x:
                T["S1+"].append((d0, x[1], x[0], "S1+"))
                if (r.get("vr1") or 0) >= 1.47 and (r.get("xret60") if r.get("xret60") == r.get("xret60") else 0) >= -0.102:
                    T["S1ф"].append((d0, x[1], x[0], "S1ф"))
            x = trade5(r, Dd, bars, True, "sig", None, None, 10, cost[i])
            if x:
                T["F2все"].append((d0, x[1], x[0], "F2"))
                if sc["R2"][1][i]:
                    T["F2"].append((d0, x[1], x[0], "F2"))
                if sc["R2M"][1][i]:
                    T["F2М"].append((d0, x[1], x[0], "F2"))
        else:
            x = trade5(r, Dd, bars, True, "sig", "move", 2.0, 20, cost[i])
            if x:
                T["F4все"].append((d0, x[1], x[0], "F4"))
                if sc["R4"][1][i]:
                    T["F4"].append((d0, x[1], x[0], "F4"))
                if sc["R4M"][1][i]:
                    T["F4М"].append((d0, x[1], x[0], "F4"))
            calm = (r.get("mkt_absx1") if r.get("mkt_absx1") == r.get("mkt_absx1") else 1) < 0.0214 and (r.get("sd5") if r.get("sd5") == r.get("sd5") else 1) < 0.0299
            if calm:
                x = trade5(r, Dd, bars, False, "sig", None, None, 5, cost[i])
                if x:
                    T["Ш5"].append((d0, x[1], x[0], "Ш5"))
    log("сделок по составляющим: " + ", ".join(f"{k} {len(v)}" for k, v in T.items()))
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), size=SIZE, maxpos=MAXPOS, parts={}, portfolios=[])
    log("===== СОСТАВЛЯЮЩИЕ ПО ОТДЕЛЬНОСТИ (10% на сделку, не больше 10 позиций) =====")
    for k, v in T.items():
        out["parts"][k] = portfolio(v, k)
    log("===== ПОРТФЕЛИ =====")
    combos = [("П0 сейчас: только S1", ["S1"]),
              ("П1 S1 с лучшим выходом (+15%, 10 дн.)", ["S1+"]),
              ("П2 S1 с выходом и фильтром по обороту", ["S1ф"]),
              ("П3 S1+ и F2, F4 с модель-фильтром", ["S1+", "F2", "F4"]),
              ("П4 то же, фильтр с математикой", ["S1+", "F2М", "F4М"]),
              ("П5 всё вместе: S1+, F2, F4, шорт Ш5", ["S1+", "F2", "F4", "Ш5"]),
              ("П6 без фильтров: S1+, все F2, все F4", ["S1+", "F2все", "F4все"])]
    for nm, keys in combos:
        tr = [x for k in keys for x in T[k]]
        out["portfolios"].append(portfolio(tr, nm))
        json.dump(out, open(os.path.join(TR.MODELS, "exp22_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №22 =====", round(time.time() - t0), "с")
    for p in out["portfolios"]:
        if p.get("n"):
            log(f"{p['name']}: {p['cagr']:+.1%}/год, просадка {p['dd']:.0%}, сделок {p['n']}, {p['mean']:+.2%}/сд, лет в плюсе {p['pos_years']}")
    json.dump(out, open(os.path.join(TR.MODELS, "exp22_report.json"), "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
