# -*- coding: utf-8 -*-
"""Проверка №5: углублённо — две области, давшие прирост в №2–4: способы обучения и потоки денег/внимание.

База — боевая модель (свежие годы весомее, полураспад 3 года; среднее 5 моделей; признаки доли денег уже в core_daily).
Годы 2009–2026, метрика «топ-5 дня» (доля аномалий среди 5 бумаг с наибольшей вероятностью каждый день).
  a — шум и настройки бустинга (скорость обучения, число листьев, размер листа, число деревьев);
  b — полураспад весов свежести: 2, 4, 6 лет;
  c — бэггинг: 10 и 20 моделей, случайные подмножества признаков (как в случайном лесе), без подвыборки строк;
  d — баланс классов: доля отрицательных примеров 0,5 и 1,0, полный вес редкого класса;
  e — внимание и деньги шире: доля бумаги в числе сделок рынка, размер сделки, доля денег за разные окна, ускорение доли;
  f — «лотерейность» и внимание по статьям (MAX, IVOL, 52-недельный максимум, затишье) — повтор упавшей части №4;
  g — отдельные модели для эшелонов ликвидности (1, 2, 3) против одной общей.
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp2 as E2  # noqa: E402
import exp3 as E3  # noqa: E402
import exp4 as E4  # noqa: E402
import experiment as EX  # noqa: E402
import core_daily as CD  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
MODELS = TR.MODELS
PART = os.environ.get("EXP_PART", "all")
YEARS_BACK = int(os.environ.get("EXP_YEARS", "18"))
HGB0 = dict(max_iter=200, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=300, l2_regularization=1.0)


def on(p):
    return PART == "all" or PART == p


def _fit(tr, feats, y, w, seed, hp, max_features=1.0):
    from sklearn.ensemble import HistGradientBoostingClassifier
    kw = dict(HGB0, **(hp or {}))
    if max_features < 1.0:
        kw["max_features"] = max_features
    return HistGradientBoostingClassifier(random_state=seed, **kw).fit(tr[feats], tr[y], sample_weight=w)


def walk(X, feats, years, name, half=3.0, nbag=5, hp=None, sneg=0.3, rowfrac=0.8, max_features=1.0, balanced=False,
         per_tier=False, seed0=0, base=None):
    t1 = time.time()
    rep = []
    for Y in years:
        tr = X[X.d.dt.year < Y].dropna(subset=["y"])
        te = X[X.d.dt.year == Y].dropna(subset=["y"])
        if te.y.sum() < 20:
            continue
        if sneg < 1:
            tr = pd.concat([tr[tr.y == 1], tr[tr.y == 0].sample(frac=sneg, random_state=1)])
        w = np.where(tr.y == 1, 1.0, 1.0 / sneg)
        if balanced:  # суммарный вес аномалий = суммарному весу обычных дней
            w = np.where(tr.y == 1, w[(tr.y == 0).values].sum() / max(1, int((tr.y == 1).sum())), w)
        if half:
            w = w * 0.5 ** ((Y - 1 - tr.d.dt.year.values) / half)

        def fit_pred(trs, ws, tes):
            p = np.zeros(len(tes))
            for s in range(nbag):
                idx = np.random.RandomState(10 + s).rand(len(trs)) < rowfrac if rowfrac < 1 else np.ones(len(trs), bool)
                m = _fit(trs[idx], feats, "y", ws[idx], seed0 + s, hp, max_features)
                p += m.predict_proba(tes[feats])[:, 1] / nbag
            return p
        if per_tier and "tier" in X.columns:
            p = np.zeros(len(te))
            for tv in (1, 2, 3):
                mtr, mte = (tr.tier.round() == tv).values, (te.tier.round() == tv).values
                if mte.sum() == 0:
                    continue
                if mtr.sum() < 5000 or tr.y[mtr].sum() < 200:
                    mtr = np.ones(len(tr), bool)
                p[mte] = fit_pred(tr[mtr], w[mtr], te[mte])
            rest = ~te.tier.round().isin([1, 2, 3]).values
            if rest.any():
                p[rest] = fit_pred(tr, w, te[rest])
        else:
            p = fit_pred(tr, w, te)
        m = TR.metrics(te.y.values, p)
        m.update(day5=round(E2.day_topk(te, p, 5), 4), day10=round(E2.day_topk(te, p, 10), 4))
        rep.append(dict(year=Y, **m))
    r = pd.DataFrame(rep)
    res = dict(name=name, auc=round(float(r.auc.mean()), 4), top5=round(float(r.top5.mean()), 4), day5=round(float(r.day5.mean()), 4),
               day10=round(float(r.day10.mean()), 4), years=rep)
    if base:
        b = {x["year"]: x for x in base["years"]}
        dd = [x["day5"] - b[x["year"]]["day5"] for x in rep if x["year"] in b]
        res.update(d_day5=round(float(np.mean(dd)), 4),
                   d_day10=round(float(np.mean([x["day10"] - b[x["year"]]["day10"] for x in rep if x["year"] in b])), 4),
                   d_auc=round(float(np.mean([x["auc"] - b[x["year"]]["auc"] for x in rep if x["year"] in b])), 4),
                   yrs_better=int(sum(1 for v in dd if v > 0)), yrs=len(dd))
    log(f"== {name}: AUC {res['auc']:.4f} топ-5 дня {res['day5']:.4f} топ-10 дня {res['day10']:.4f}"
        + (f" | Δтоп-5 дня {res['d_day5']:+.4f} Δтоп-10 дня {res['d_day10']:+.4f} ΔAUC {res['d_auc']:+.4f}, лучше в {res['yrs_better']}/{res['yrs']}" if base else "")
        + f" | {round(time.time() - t1)} с")
    return res


def g_attention(X, P, NT):
    """Внимание шире доли денег: доля бумаги в числе сделок рынка, средний размер сделки к рынку, доля денег за 3/10/60 дней."""
    cols = []
    if NT:
        fr = []
        for t, rows in NT.items():
            df = pd.DataFrame(rows, columns=["d", "nt", "val"])
            df["d"] = pd.to_datetime(df["d"])
            fr.append(df[df.d.dt.weekday < 5].assign(t=t))
        A = pd.concat(fr, ignore_index=True)
        N = A.pivot_table(index="d", columns="t", values="nt").sort_index()
        S = N.div(N.sum(axis=1), axis=0)
        z = ((S - S.rolling(20, min_periods=10).mean()) / S.rolling(20, min_periods=10).std()).shift(1)
        ch5 = np.log(S.rolling(5).mean() / S.rolling(60, min_periods=30).mean()).shift(1)
        V = A.pivot_table(index="d", columns="t", values="val").reindex(N.index)
        ats = (V / N.replace(0, np.nan))
        atsr = np.log(ats.div(ats.median(axis=1), axis=0))
        atsz = (atsr - atsr.rolling(60, min_periods=30).mean()).shift(1)
        F = pd.concat([M.replace([np.inf, -np.inf], np.nan).stack(future_stack=True).rename(k) for k, M in
                       (("at_nt_z", z), ("at_nt_ch5", ch5), ("at_ats_z", atsz))], axis=1)
        F.index.names = ["d", "t"]
        cols += E3._put(X, F.reset_index(), ["at_nt_z", "at_nt_ch5", "at_ats_z"])
    Vv = P.pivot_table(index="d", columns="t", values="v").sort_index()
    Sh = Vv.div(Vv.sum(axis=1), axis=0)
    feats = {"at_sh_z5": ((Sh - Sh.rolling(5, min_periods=3).mean()) / Sh.rolling(60, min_periods=30).std()).shift(1),
             "at_sh_z120": ((Sh - Sh.rolling(120, min_periods=60).mean()) / Sh.rolling(120, min_periods=60).std()).shift(1),
             "at_sh_acc": (np.log(Sh.rolling(3).mean() / Sh.rolling(10, min_periods=5).mean())
                           - np.log(Sh.rolling(10, min_periods=5).mean() / Sh.rolling(60, min_periods=30).mean())).shift(1)}
    F = pd.concat([M.replace([np.inf, -np.inf], np.nan).stack(future_stack=True).rename(k) for k, M in feats.items()], axis=1)
    F.index.names = ["d", "t"]
    cols += E3._put(X, F.reset_index(), list(feats))
    return cols


def main():
    os.makedirs(MODELS, exist_ok=True)
    X, Dd, IX, ev, impact = EX.load_all()
    FD = CD.feature_cols(X)
    log("признаков в базе:", len(FD), "доля денег в базе:", [c for c in FD if c.startswith("mf_")])
    yr = int(X.d.dt.year.max())
    years = [y for y in range(yr - YEARS_BACK + 1, yr + 1) if y > int(X.d.dt.year.min())]
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), part=PART, years=years, runs=[])
    path = os.path.join(MODELS, f"exp5_report_{PART}.json")

    def add(r):
        out["runs"].append(r)
        json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
        return r
    base = add(walk(X, FD, years, "база = боевая (свежие 3 г. + среднее 5 моделей + доля денег)"))
    if on("a"):
        add(walk(X, FD, years, "шум: боевая с другими seed", seed0=100, base=base))
        add(walk(X, FD, years, "бустинг медленнее и глубже: lr 0,03, 400 деревьев, 63 листа", hp=dict(learning_rate=0.03, max_iter=400, max_leaf_nodes=63), base=base))
        add(walk(X, FD, years, "бустинг мельче лист: min_samples_leaf 100", hp=dict(min_samples_leaf=100), base=base))
        add(walk(X, FD, years, "бустинг крупнее лист: min_samples_leaf 1000, l2 3", hp=dict(min_samples_leaf=1000, l2_regularization=3.0), base=base))
    if on("b"):
        for h in (2.0, 4.0, 6.0):
            add(walk(X, FD, years, f"полураспад свежести {h:g} года", half=h, base=base))
    if on("c"):
        add(walk(X, FD, years, "среднее 10 моделей", nbag=10, base=base))
        add(walk(X, FD, years, "среднее 5 моделей на 60% признаков (случайные подмножества)", max_features=0.6, base=base))
        add(walk(X, FD, years, "среднее 5 моделей без подвыборки строк (только разные seed)", rowfrac=1.0, base=base))
    if on("d"):
        add(walk(X, FD, years, "доля отрицательных примеров 0,5", sneg=0.5, base=base))
        add(walk(X, FD, years, "все отрицательные примеры (1,0)", sneg=1.0, base=base))
        add(walk(X, FD, years, "полный вес редкого класса (balanced)", balanced=True, base=base))
    if on("e"):
        P = EX.prices(Dd)
        try:
            NT = E2.numtrades(sorted(X.t.unique()))
        except Exception as e:  # noqa: BLE001
            log("число сделок: ошибка", e)
            NT = {}
        cols = g_attention(X, P, NT)
        log("внимание: заполнено", {c: round(float(X[c].notna().mean()), 3) for c in cols})
        add(walk(X, FD + cols, years, "+ внимание шире: доля в числе сделок рынка, размер сделки, доля денег за 3/10/60/120 дней", base=base))
        nt_cols = [c for c in cols if c.startswith("at_nt") or c.startswith("at_ats")]
        if nt_cols:
            add(walk(X, FD + nt_cols, years, "+ только доля в числе сделок и размер сделки", base=base))
    if on("f"):
        P = EX.prices(Dd)
        cols = E4.g_lottery(X, P, IX)
        add(walk(X, FD + cols, years, "+ лотерейность, IVOL, 52-нед. максимум, затишье, всплеск оборота (статьи)", base=base))
    if on("g"):
        add(walk(X, FD, years, "отдельные модели для эшелонов 1, 2, 3", per_tier=True, base=base))
    log("===== ИТОГ ПРОВЕРКИ №5, часть", PART, "(годы", years[0], "–", years[-1], ") =====")
    for r in out["runs"]:
        log(f"{r['name']}: AUC {r['auc']:.4f}, топ-5 дня {r['day5']:.4f}, топ-10 дня {r['day10']:.4f}"
            + (f"; Δтоп-5 дня {r['d_day5']:+.4f}, Δтоп-10 дня {r['d_day10']:+.4f}, ΔAUC {r['d_auc']:+.4f}, лучше в {r['yrs_better']}/{r['yrs']} лет" if "d_day5" in r else ""))


if __name__ == "__main__":
    main()
