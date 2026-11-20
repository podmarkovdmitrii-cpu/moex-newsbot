# -*- coding: utf-8 -*-
"""Проверка №26: фильтры к рабочим стратегиям S1, F2, F4 (5-минутные свечи ALGOPACK 2020–2026) и тест перестановкой.

Сделки — как в №22 (S1: лимит на откате 50% хода, тейк +25% хода, до 5 дн.; F2: покупка сразу, 10 дн., модель-фильтр «верхние 30%»;
F4: покупка сразу, тейк +200% хода, до 20 дн., модель-фильтр с математикой, «верхние 30%»).
1) Время дня: час сигнала (10, 11, 12–14, 15–18). 2) Курс доллара (USD000UTSTOM, ISS): изменение накануне и за 5 дней до дня сигнала
(терцили по 2020–2022). Фильтр выбирается ТОЛЬКО по 2020–2022 (исключается худшая группа, если она там в минусе и ≥ 15 сделок),
проверка — 2023–2026: исключённая группа и там в минусе (≥ 10 сделок), а средний результат после фильтра выше хотя бы на 0,2 п.п.
3) Тест перестановкой (день-кластерный): знаки результатов по дням меняются случайно 10 000 раз (нулевая гипотеза «средний = 0»);
плюс бутстрэп по дням (90% интервал) и t-статистика с учётом группировки сделок одного дня. Издержки в результатах уже учтены (0,1/0,2%).
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
import exp22 as E22  # noqa: E402
import math_feats as MF  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
SEL = 2023   # выбор фильтра — годы < SEL, проверка — ≥ SEL


def usd_series():
    from common import candles
    B = candles("engines/currency/markets/selt/boards/CETS/securities/USD000UTSTOM/candles.json", (dt.date.today() - dt.date(2019, 6, 1)).days, 24)
    s = pd.Series({pd.Timestamp(b["t"][:10]): float(b["c"]) for b in B if b.get("c")}).sort_index()
    return s


def build_trades():
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
    M = MF.build(Dd, IX, CD.series).drop_duplicates(["d", "t"])
    E = E.merge(M, on=["d", "t"], how="left")
    featsM = feats + [c for c in MF.COLS if c in E.columns]
    yr = E.d.dt.year.values
    top2 = E22.oof_scores(E, RH["R2"], feats, 1, yr)[1]
    top4 = E22.oof_scores(E, RH["R4"], featsM, -1, yr)[1]
    m5 = yr >= E22.FROM5
    need = set()
    for i in np.where(m5)[0]:
        r = E.iloc[i]
        d0 = r.d.strftime("%Y-%m-%d")
        need.add((r.t, d0))
        for dd, _, _ in E18.trading_days(Dd, r.t, d0, 20):
            need.add((r.t, dd))
    bars = E18.load_bars(need, (os.environ.get("ALGOPACK_TOKEN") or "").strip())
    rows = []
    for i in np.where(m5)[0]:
        r = E.iloc[i]
        d0 = r.d.strftime("%Y-%m-%d")
        hour = int(r.get("c")) if r.get("c") == r.get("c") else None
        if r.s > 0:
            x = E22.trade5(r, Dd, bars, True, "lim", "move", 0.25, 5, cost[i])
            if x:
                rows.append(("S1", r.t, r.d, hour, x[0]))
            if top2[i]:
                x = E22.trade5(r, Dd, bars, True, "sig", None, None, 10, cost[i])
                if x:
                    rows.append(("F2", r.t, r.d, hour, x[0]))
        elif top4[i]:
            x = E22.trade5(r, Dd, bars, True, "sig", "move", 2.0, 20, cost[i])
            if x:
                rows.append(("F4", r.t, r.d, hour, x[0]))
    return pd.DataFrame(rows, columns=["strat", "t", "d", "hour", "ret"])


def perm_test(g, n=10000, seed=26):
    """День-кластерный тест: суммы результатов по дням, случайная смена знака дня. → (средний, p, t, 90% бутстрэп)."""
    day = g.groupby("d").ret.agg(["sum", "count"])
    s, c = day["sum"].values, day["count"].values
    obs = s.sum() / c.sum()
    rs = np.random.RandomState(seed)
    signs = rs.choice([-1.0, 1.0], size=(n, len(s)))
    null = (signs * s).sum(1) / c.sum()
    p = float((null >= obs).mean())
    m = s / c
    t = float(np.mean(m) / (np.std(m, ddof=1) / np.sqrt(len(m)))) if len(m) > 2 else float("nan")
    bs = [s[ix].sum() / c[ix].sum() for ix in (rs.randint(0, len(s), len(s)) for _ in range(3000))]
    return float(obs), p, t, (float(np.quantile(bs, 0.05)), float(np.quantile(bs, 0.95))), len(s)


def filt(T, col, labels, name, out):
    log(f"===== ФИЛЬТР: {name} (выбор по 2020–{SEL - 1}, проверка {SEL}–2026) =====")
    res = {}
    for st in ("S1", "F2", "F4"):
        g = T[(T.strat == st) & T[col].notna()]
        early, late = g[g.d.dt.year < SEL], g[g.d.dt.year >= SEL]
        line = []
        stats = {}
        for lb in labels:
            e, l_ = early[early[col] == lb].ret, late[late[col] == lb].ret
            stats[lb] = (e.mean() if len(e) else np.nan, len(e), l_.mean() if len(l_) else np.nan, len(l_))
            line.append(f"{lb}: {stats[lb][0]:+.2%} ({stats[lb][1]}) → {stats[lb][2]:+.2%} ({stats[lb][3]})")
        log(f"  {st}: " + "; ".join(line))
        cand = [lb for lb in labels if stats[lb][1] >= 15 and stats[lb][0] < 0]
        if not cand:
            log(f"    {st}: на 2020–{SEL - 1} нет группы в минусе с ≥ 15 сделками — фильтр не нужен")
            res[st] = dict(excl=None)
            continue
        worst = min(cand, key=lambda lb: stats[lb][0])
        keep_l = late[late[col] != worst].ret
        bad_l = late[late[col] == worst].ret
        ok = len(bad_l) >= 10 and bad_l.mean() < 0 and keep_l.mean() - late.ret.mean() >= 0.002
        log(f"    {st}: исключить «{worst}» → {SEL}–2026: без фильтра {late.ret.mean():+.2%} ({len(late)}), с фильтром {keep_l.mean():+.2%} "
            f"({len(keep_l)}), исключённые {bad_l.mean() if len(bad_l) else float('nan'):+.2%} ({len(bad_l)}) — {'ПОДТВЕРЖДЁН' if ok else 'не подтверждён'}")
        res[st] = dict(excl=worst, late_all=float(late.ret.mean()), late_keep=float(keep_l.mean()), late_bad=float(bad_l.mean()) if len(bad_l) else None,
                       ok=bool(ok), stats={k: [float(x) if x == x else None for x in v] for k, v in stats.items()})
    out[name] = res


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    T = build_trades()
    log(f"сделок: " + ", ".join(f"{k} {v}" for k, v in T.strat.value_counts().items()) + f"; {round(time.time() - t0)} с")
    out = {}
    # 1) время дня
    T["hb"] = T.hour.map(lambda h: None if h is None or h != h else ("10 ч" if h <= 10 else "11 ч" if h == 11 else "12–14 ч" if h <= 14 else "15–18 ч"))
    filt(T, "hb", ["10 ч", "11 ч", "12–14 ч", "15–18 ч"], "время сигнала", out)
    # 2) курс доллара
    try:
        u = usd_series()
        lu = np.log(u)
        ch1 = lu.diff().shift(1)              # изменение накануне (известно утром дня сигнала)
        ch5 = (lu - lu.shift(5)).shift(1)     # за 5 дней до дня сигнала
        T["u1"] = T.d.map(lambda d: ch1.asof(d) if d >= ch1.index[0] else np.nan)
        T["u5"] = T.d.map(lambda d: ch5.asof(d) if d >= ch5.index[0] else np.nan)
        for col, nm in (("u1", "доллар накануне"), ("u5", "доллар за 5 дней")):
            base = T[T.d.dt.year < SEL][col].dropna()
            q1, q2 = np.quantile(base, [1 / 3, 2 / 3])
            T[col + "g"] = T[col].map(lambda v: None if v != v else ("рубль крепнет" if v <= q1 else "рубль слабеет" if v >= q2 else "спокойно"))
            filt(T, col + "g", ["рубль крепнет", "спокойно", "рубль слабеет"], nm, out)
    except Exception as e:  # noqa: BLE001
        log("курс доллара не загружен:", e)
    # 3) перестановка
    log("===== ТЕСТ ПЕРЕСТАНОВКОЙ (знаки по дням, 10 000 раз) И БУТСТРЭП ПО ДНЯМ =====")
    out["perm"] = {}
    for st in ("S1", "F2", "F4"):
        for nm, g in (("2020–2026", T[T.strat == st]), (f"{SEL}–2026", T[(T.strat == st) & (T.d.dt.year >= SEL)])):
            if len(g) < 10:
                continue
            m, p, tt, ci, nd = perm_test(g)
            yrs = g.groupby(g.d.dt.year).ret.mean()
            log(f"  {st} {nm}: {len(g)} сделок в {nd} днях, средний {m:+.2%}, p = {p:.4f}, t = {tt:.2f}, 90% интервал {ci[0]:+.2%}…{ci[1]:+.2%}; "
                f"лет в плюсе {int((yrs > 0).sum())}/{len(yrs)}")
            out["perm"][f"{st} {nm}"] = dict(n=len(g), days=nd, mean=m, p=p, t=tt, ci=ci, pos=int((yrs > 0).sum()), yrs=len(yrs))
    json.dump(out, open(os.path.join(TR.MODELS, "exp26_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №26 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
