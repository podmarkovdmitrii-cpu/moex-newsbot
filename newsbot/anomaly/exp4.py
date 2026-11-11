# -*- coding: utf-8 -*-
"""Проверка №4: потоки денег (доля бумаги в обороте рынка, направление денег) и идеи из статей, форумов и видео
о торговых роботах — для модели аномалий (workflow anomaly-experiment.yml, exp=4).

Схема как в №2–3: обучение на прошлых годах → проверка на следующем (2009–2026), метрика «топ-5 дня». База — боевая
настройка без бэггинга (свежие годы, полураспад 3 года). Все признаки — по данным до D−1 включительно.

  F1 доля денег: какую долю всего оборота акций Мосбиржи забрала бумага вчера (в %), её отклонение от 20 дней (z),
     рост доли за 1 и 5 дней, ранг роста среди бумаг, приток доли в сектор («куда переливаются деньги»);
  F2 направление денег по свечам (оценка притока/оттока без ленты сделок): Chaikin Money Flow 20, Money Flow Index 14,
     положение закрытия в дневном диапазоне (IBS), «знаковый» оборот (оборот × знак свечи) за 1 и 5 дней к обороту 20 дней;
  F3 статьи о «лотерейных» бумагах и внимании: MAX/MIN дневной доходности за 21 день (Bali и др.), идиосинкратическая
     волатильность к IMOEX за 60 дней, расстояние до максимума/минимума 250 дней (George–Hwang), гэп открытия вчера,
     «затишье перед бурей» (оборот 5 дней к 60 при сжатом диапазоне, Akbas 2016), всплеск оборота (Gervais и др.).
Часть e — без обучения: как частота аномалий зависит от доли денег и направления денег (квинтили, все годы).
Части: a — база, шум, F1; b — F2; c — F3; d — всё вместе и с бэггингом против боевой; e — таблицы зависимости.
"""
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp2 as E2  # noqa: E402
import exp3 as E3  # noqa: E402
import experiment as EX  # noqa: E402
import core_daily as CD  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
MODELS = TR.MODELS
PART = os.environ.get("EXP_PART", "all")
YEARS_BACK = int(os.environ.get("EXP_YEARS", "18"))
REC = 3


def on(p):
    return PART == "all" or PART == p


# ------------------------------------------------------------------ F1: доля бумаги в деньгах рынка
def g_share(X, P, sec_of):
    V = P.pivot_table(index="d", columns="t", values="v").sort_index()
    tot = V.sum(axis=1)
    S = V.div(tot, axis=0) * 100                       # % всего оборота акций за день
    m20, s20 = S.rolling(20, min_periods=10).mean(), S.rolling(20, min_periods=10).std()
    feats = {
        "mf_share": S.shift(1),
        "mf_share_z": ((S - m20) / s20).shift(1),
        "mf_share_ch1": (np.log(S / S.shift(1))).shift(1),
        "mf_share_ch5": (np.log(S.rolling(5).mean() / S.rolling(60, min_periods=30).mean())).shift(1),
    }
    feats["mf_share_rk"] = feats["mf_share_ch1"].rank(axis=1, pct=True)
    sec = pd.Series({t: (sec_of or {}).get(t, "other") for t in V.columns})
    SS = V.T.groupby(sec).sum().T.div(tot, axis=0)
    secz = ((SS - SS.rolling(20, min_periods=10).mean()) / SS.rolling(20, min_periods=10).std()).shift(1)
    feats["mf_sec_z"] = pd.DataFrame({t: secz[sec[t]] for t in V.columns}, index=V.index)
    rows = []
    for k, M in feats.items():
        rows.append(M.stack(future_stack=True).rename(k))
    F = pd.concat(rows, axis=1).reset_index().rename(columns={"level_0": "d", "level_1": "t"})
    F.columns = ["d", "t"] + list(feats)
    return E3._put(X, F, list(feats))


# ------------------------------------------------------------------ F2: направление денег по свечам
def g_direction(X, P):
    fr = []
    for t, df in P.groupby("t"):
        df = df.sort_values("d")
        h, l, c, o, v = df.h, df.l, df.c, df.o, df.v
        rng = (h - l).replace(0, np.nan)
        clv = ((c - l) - (h - c)) / rng
        f = pd.DataFrame({"t": t, "d": df.d.values})
        f["mf_cmf20"] = ((clv * v).rolling(20, min_periods=10).sum() / v.rolling(20, min_periods=10).sum()).shift(1).values
        tp = (h + l + c) / 3
        up = (tp > tp.shift(1)).astype(float) * tp * v
        dn = (tp < tp.shift(1)).astype(float) * tp * v
        mr = up.rolling(14).sum() / dn.rolling(14).sum().replace(0, np.nan)
        f["mf_mfi14"] = (100 - 100 / (1 + mr)).shift(1).values
        f["mf_ibs"] = ((c - l) / rng).shift(1).values
        sv = np.sign(c - o) * v
        v20 = v.rolling(20, min_periods=10).mean()
        f["mf_net1"] = (sv / v20).shift(1).values
        f["mf_net5"] = (sv.rolling(5).sum() / (5 * v20)).shift(1).values
        fr.append(f)
    return E3._put(X, pd.concat(fr, ignore_index=True), ["mf_cmf20", "mf_mfi14", "mf_ibs", "mf_net1", "mf_net5"])


# ------------------------------------------------------------------ F3: «лотерейность», внимание, затишье
def g_lottery(X, P, IX):
    ix = CD.series(IX)[["d", "c"]].rename(columns={"c": "ixc"})
    ix["ixr"] = ix.ixc.pct_change()
    fr = []
    for t, df in P.groupby("t"):
        df = df.sort_values("d").merge(ix[["d", "ixr"]], on="d", how="left")
        r = df.c.pct_change()
        f = pd.DataFrame({"t": t, "d": df.d.values})
        f["lt_max21"] = r.rolling(21, min_periods=15).max().shift(1).values
        f["lt_min21"] = r.rolling(21, min_periods=15).min().shift(1).values
        cov = r.rolling(60, min_periods=40).cov(df.ixr)
        beta = cov / df.ixr.rolling(60, min_periods=40).var()
        resid = r - beta * df.ixr
        f["lt_ivol60"] = resid.rolling(60, min_periods=40).std().shift(1).values
        f["lt_hi250"] = (df.c / df.h.rolling(250, min_periods=120).max()).shift(1).values
        f["lt_lo250"] = (df.c / df.l.rolling(250, min_periods=120).min()).shift(1).values
        f["lt_gap1"] = (df.o / df.c.shift(1) - 1).shift(1).values
        rng = (df.h - df.l) / df.c
        f["lt_calm"] = ((df.v.rolling(5).mean() / df.v.rolling(60, min_periods=30).mean())
                        * (rng.rolling(5).mean() / rng.rolling(60, min_periods=30).mean())).shift(1).values
        vz = np.log(df.v.replace(0, np.nan))
        f["lt_vshock"] = ((vz - vz.rolling(50, min_periods=25).mean()) / vz.rolling(50, min_periods=25).std()).shift(1).values
        fr.append(f)
    cols = ["lt_max21", "lt_min21", "lt_ivol60", "lt_hi250", "lt_lo250", "lt_gap1", "lt_calm", "lt_vshock"]
    return E3._put(X, pd.concat(fr, ignore_index=True), cols)


# ------------------------------------------------------------------ e: как деньги связаны с аномалиями
def lift_tables(X, cols):
    out = {}
    base = float(X.y.mean())
    for c in cols:
        q = X[[c, "y", "y_up", "y_dn"]].dropna()
        if len(q) < 1000:
            continue
        q["b"] = pd.qcut(q[c].rank(method="first"), 5, labels=False)
        g = q.groupby("b").agg(lo=(c, "min"), hi=(c, "max"), n=("y", "size"), y=("y", "mean"), up=("y_up", "mean"), dn=("y_dn", "mean"))
        rows = [dict(q=int(i) + 1, lo=round(float(r.lo), 4), hi=round(float(r.hi), 4), n=int(r.n), p=round(float(r.y), 4),
                     lift=round(float(r.y) / base, 2), up=round(float(r.up), 4), dn=round(float(r.dn), 4)) for i, r in g.iterrows()]
        out[c] = rows
        log(f"[{c}] " + " | ".join(f"Q{x['q']} ({x['lo']}…{x['hi']}): аномалий {x['p']:.2%} (×{x['lift']}), рост {x['up']:.2%}, падение {x['dn']:.2%}" for x in rows))
    return out


def main():
    os.makedirs(MODELS, exist_ok=True)
    X, Dd, IX, ev, impact = EX.load_all()
    P = EX.prices(Dd)
    FD = CD.feature_cols(X)
    yr = int(X.d.dt.year.max())
    years = [y for y in range(yr - YEARS_BACK + 1, yr + 1) if y > int(X.d.dt.year.min())]
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), part=PART, years=years, runs=[], groups={})
    path = os.path.join(MODELS, f"exp4_report_{PART}.json")

    def add(r):
        if r:
            out["runs"].append(r)
        json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
        return r
    groups = {}
    for key, name, fn in (("F1", "F1 доля бумаги в деньгах рынка", lambda: g_share(X, P, impact.get("secOf"))),
                          ("F2", "F2 направление денег по свечам", lambda: g_direction(X, P)),
                          ("F3", "F3 лотерейность, внимание, затишье (статьи)", lambda: g_lottery(X, P, IX))):
        need = {"F1": "a", "F2": "b", "F3": "c"}[key]
        if on(need) or PART in ("d", "e"):
            groups[name] = fn()
            out["groups"][name] = {c: round(float(X[c].notna().mean()), 3) for c in groups[name]}
            log("группа", name, out["groups"][name])
    if PART == "e":
        out["lift"] = lift_tables(X, [c for v in groups.values() for c in v])
        add(None)
        return
    base = add(E2.walk2(X, X, FD, years, "база: свежие годы весомее (боевая без бэггинга)", recency=REC))
    if on("a"):
        try:
            E2.fit_hgb.__defaults__ = (1,)
            add(E2.walk2(X, X, FD, years, "шум: база с другим seed", recency=REC, base=base))
        finally:
            E2.fit_hgb.__defaults__ = (0,)
    for name, cols in groups.items():
        part = {"F1": "a", "F2": "b", "F3": "c"}[name[:2]]
        if on(part) and PART != "d":
            add(E2.walk2(X, X, FD + cols, years, "база + " + name, recency=REC, base=base))
    if PART == "d":
        allc = [c for v in groups.values() for c in v]
        add(E2.walk2(X, X, FD + allc, years, "база + все денежные и статейные признаки", recency=REC, base=base))
        os.environ["EXP_BAG"] = "5"
        add(E2.walk2(X, X, FD + allc, years, "все признаки + бэггинг 5", model="bag", recency=REC, base=base))
        add(E2.walk2(X, X, FD, years, "боевая: свежие годы + бэггинг 5", model="bag", recency=REC, base=base))
        f1 = groups.get("F1 доля бумаги в деньгах рынка", [])
        add(E2.walk2(X, X, FD + f1, years, "доля денег + бэггинг 5", model="bag", recency=REC, base=base))
    log("===== ИТОГ ПРОВЕРКИ №4, часть", PART, "(годы", years[0], "–", years[-1], ") =====")
    for r in out["runs"]:
        log(f"{r['name']}: AUC {r['auc']:.4f}, топ-5% {r['top5']:.4f}, топ-5 дня {r['day5']:.4f}, топ-10 дня {r['day10']:.4f}"
            + (f"; Δтоп-5 дня {r['d_day5']:+.4f}, Δтоп-10 дня {r['d_day10']:+.4f}, ΔAUC {r['d_auc']:+.4f}, лучше в {r['yrs_better']}/{r['yrs']} лет" if "d_day5" in r else ""))


if __name__ == "__main__":
    main()
