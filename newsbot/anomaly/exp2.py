# -*- coding: utf-8 -*-
"""Проверка №2: время публикации прогноза и способы обучения модели аномалий (workflow anomaly-experiment.yml, EXP=2).

Схема как у боевой модели: обучение на прошлых годах → проверка на следующем (год модель не видела). Метрики:
  AUC, точность в топ-5% прогнозов года и главное — «угадывание дня»: доля аномалий среди 5 и 10 бумаг с наибольшей
  вероятностью КАЖДЫЙ день (то, что видят читатели: топ прогноза на день).

Часть 1. Время публикации. Боевая модель учится на новостях до 09:55 МСК дня D, а прогноз публикуется утром в 06:00
(и вечером накануне в 19:00) — новости 06:00–09:55 (и вечерние/ночные для вечернего прогноза) модель в работе не видит.
  T0  учёба 09:55, проверка 09:55   — если публиковать прогноз в 09:50 (перед основной сессией);
  T1  учёба 09:55, проверка 06:00   — как сейчас утром (перекос «учёба ≠ работа»);
  T2  учёба 06:00, проверка 06:00   — утренний прогноз, обученный честно на том, что известно в 06:00;
  T3  учёба 09:55, проверка 19:00 D−1 — как сейчас вечером;
  T4  учёба 19:00, проверка 19:00   — вечерний прогноз, обученный на том, что известно в 19:00.
Часть 2. Способы обучения (на признаках T0): свежие годы весомее (полураспад 3 года); ранги признаков внутри дня;
ранжирование LightGBM LambdaRank по дням (прямо учит «топ дня»); бэггинг 5 моделей; активность розницы (число сделок
и средний размер сделки по ISS history) — и связка лучших.
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import sys
import time
import traceback

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import experiment as EX  # noqa: E402  (загрузка данных как в train.py, фиксированный seed)
import train as TR  # noqa: E402
import core_daily as CD  # noqa: E402

log = TR.log
DATA, MODELS = TR.DATA, TR.MODELS
T0 = time.time()
BUDGET = float(os.environ.get("EXP_BUDGET_MIN", "320")) * 60
YEARS_BACK = int(os.environ.get("EXP_YEARS", "6"))
SNEG = float(os.environ.get("EXP_SAMPLE_NEG", "0.3"))
# часть проверки (для параллельных задач workflow): a — шум и утро, b — вечер, c — свежие годы и ранги, d — розница,
# e — LambdaRank, f — бэггинг; all — всё подряд. База считается в каждой части одинаково (тот же seed) — дельты сравнимы.
PART = os.environ.get("EXP_PART", "all")


def on(p):
    return PART == "all" or PART == p
MSK = dt.timedelta(hours=3)
NEWS_COLS_N = ["n", "night", "rum", "off", "w", "wmax"] + ["n_" + g for g in list(CD.TYPES_GROUPS) + ["other"]]


def left():
    return BUDGET - (time.time() - T0)


# ------------------------------------------------------------------ новости с разным временем отсечки
def news_view(X, Dd, IX, ev, W, mode):
    """Копия новостных признаков X так, как их видно в момент публикации: mode = 'm06' (06:00 дня D) или 'e19' (19:00 D−1)."""
    keep_e, keep_w = [], []
    for e, w in zip(ev, W):
        when = dt.datetime.utcfromtimestamp(e["ts"]) + MSK
        hm = (when.hour, when.minute)
        if mode == "m06":
            ok = not ((6, 0) <= hm < (9, 55))           # утренние новости дня D ещё не вышли
        else:
            ok = when.weekday() < 5 and (9, 55) <= hm < (19, 0)  # для прогноза на D известно только до 19:00 D−1
        if ok:
            keep_e.append(e)
            keep_w.append(w)
    days_by = {t: CD.series(a)["d"].values for t, a in Dd.items()}
    days_by["IMOEX"] = CD.series(IX)["d"].values
    nd = CD.news_daily(keep_e, days_by, keep_w)
    rows = []
    for (tk, d), a in nd.items():
        rows.append(dict(t=tk, d=pd.Timestamp(d), **{"nw_" + k: a.get(k, 0.0) for k in NEWS_COLS_N}))
    N = pd.DataFrame(rows)
    M = N[N.t == "IMOEX"].rename(columns={"nw_n": "m_n", "nw_n_risk": "m_risk", "nw_n_macro": "m_macro"})[["d", "m_n", "m_risk", "m_macro"]]
    Y = X.copy()
    cols = ["nw_" + k for k in NEWS_COLS_N]
    Y = Y.drop(columns=[c for c in cols + ["m_n", "m_risk", "m_macro"] if c in Y.columns])
    Y = Y.merge(N[N.t != "IMOEX"][["t", "d"] + cols], on=["t", "d"], how="left").merge(M, on="d", how="left")
    for c in cols + ["m_n", "m_risk", "m_macro"]:
        Y[c] = Y[c].fillna(0.0)
    Y["nw_surprise"] = Y["nw_n"] / (Y["nw_prev60m"] + 0.05)
    Y = Y.sort_values(["t", "d"]).reset_index(drop=True)
    assert len(Y) == len(X)
    log("новости как в", mode, ": доля дней с новостями", round(float((Y.nw_n > 0).mean()), 4), "против", round(float((X.nw_n > 0).mean()), 4))
    return Y


# ------------------------------------------------------------------ активность розницы (ISS history)
def numtrades(tickers, start=os.environ.get("EXP_NT_FROM", "2008-01-01")):
    from common import iss, table
    os.makedirs(os.path.join(DATA, "nt"), exist_ok=True)

    def one(t):
        p = os.path.join(DATA, "nt", t + ".json.gz")
        old = []
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                old = json.load(f)
        frm = (dt.date.fromisoformat(old[-1][0]) - dt.timedelta(days=5)).isoformat() if old else start
        rows, st = [], 0
        try:
            while True:
                js = iss(f"history/engines/stock/markets/shares/boards/TQBR/securities/{t}.json", **{"from": frm, "start": st,
                         "history.columns": "TRADEDATE,NUMTRADES,VALUE"})
                part = table(js, "history")
                rows += [[r["TRADEDATE"], r.get("NUMTRADES") or 0, r.get("VALUE") or 0] for r in part]
                if len(part) < 100:
                    break
                st += len(part)
        except Exception as e:  # noqa: BLE001
            log("история сделок", t, e)
        if rows:
            old = [r for r in old if r[0] < rows[0][0]] + rows
            with gzip.open(p, "wt") as f:
                json.dump(old, f, separators=(",", ":"))
        return t, old
    out = {}
    with cf.ThreadPoolExecutor(6) as ex:
        for k, (t, rows) in enumerate(ex.map(one, tickers)):
            if rows:
                out[t] = rows
            if k % 50 == 0:
                log("число сделок", k, "/", len(tickers), round(time.time() - T0), "с")
    return out


def g_retail(X, NT):
    fr = []
    for t, rows in NT.items():
        df = pd.DataFrame(rows, columns=["d", "nt", "val"])
        df["d"] = pd.to_datetime(df["d"])
        df = df[(df.d.dt.weekday < 5) & (df.nt > 0)].sort_values("d")
        if len(df) < 80:
            continue
        ats = df.val / df.nt
        f = pd.DataFrame({"d": df.d, "t": t})
        lnt = np.log1p(df.nt)
        f["rt_nt1"] = (lnt - lnt.rolling(60, min_periods=20).mean()).shift(1).values             # всплеск числа сделок вчера
        f["rt_nt5"] = (lnt.rolling(5).mean() - lnt.rolling(60, min_periods=20).mean()).shift(1).values
        la = np.log(ats.replace(0, np.nan))
        f["rt_ats1"] = (la - la.rolling(60, min_periods=20).mean()).shift(1).values              # мельчание сделок (розница)
        f["rt_ats5"] = (la.rolling(5).mean() - la.rolling(60, min_periods=20).mean()).shift(1).values
        f["rt_ntsd"] = ((lnt - lnt.rolling(60, min_periods=20).mean()) / lnt.rolling(60, min_periods=20).std()).shift(1).values
        fr.append(f)
    R = pd.concat(fr, ignore_index=True)
    cols = ["rt_nt1", "rt_nt5", "rt_ats1", "rt_ats5", "rt_ntsd"]
    X.drop(columns=[c for c in cols if c in X.columns], inplace=True)
    Y = X.merge(R, on=["d", "t"], how="left")
    for c in cols:
        X[c] = Y[c].values
    return cols


def g_rank(X, FD):
    base = [c for c in ["vr1", "sd20", "sd60", "absx1", "an20", "an250", "lval", "vtrend", "nw_w", "nw_wmax", "nw_n", "ema20d", "dhi20",
                        "dlo20", "brk20", "sq", "h_hpeak", "h_ev_share", "since_an", "tier"] if c in FD]
    cols = []
    for c in base:
        X["rk_" + c] = X.groupby("d")[c].rank(pct=True)
        cols.append("rk_" + c)
    return cols


# ------------------------------------------------------------------ обучение и метрики
def day_topk(te, p, k):
    q = te[["d"]].assign(p=p, y=te["y"].values)
    q = q[q.groupby("d").p.transform("size") >= 50]
    q["r"] = q.groupby("d").p.rank(ascending=False, method="first")
    top = q[q.r <= k]
    return float(top.y.mean()) if len(top) else float("nan")


def fit_hgb(tr, feats, target, w, seed=0):
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=300, l2_regularization=1.0,
                                          random_state=seed).fit(tr[feats], tr[target], sample_weight=w)


def walk2(Xtr, Xte, feats, years, name, target="y", recency=None, model="hgb", base=None):
    if left() < 12 * 60:
        log("пропуск", name, "— нет времени")
        return None
    t1 = time.time()
    rep = []
    for Y in years:
        tr = Xtr[Xtr.d.dt.year < Y].dropna(subset=[target])
        te = Xte[Xte.d.dt.year == Y].dropna(subset=[target])
        if te[target].sum() < 20:
            continue
        tr = pd.concat([tr[tr[target] == 1], tr[tr[target] == 0].sample(frac=SNEG, random_state=1)])
        w = np.where(tr[target] == 1, 1.0, 1.0 / SNEG)
        if recency:
            w = w * 0.5 ** ((Y - 1 - tr.d.dt.year.values) / recency)
        if model == "hgb":
            p = fit_hgb(tr, feats, target, w).predict_proba(te[feats])[:, 1]
        elif model == "bag":
            p = np.zeros(len(te))
            nb = int(os.environ.get("EXP_BAG", "3"))
            for s in range(nb):
                sub = tr.sample(frac=0.8, random_state=10 + s)
                ws = np.where(sub[target] == 1, 1.0, 1.0 / SNEG) * (0.5 ** ((Y - 1 - sub.d.dt.year.values) / recency) if recency else 1.0)
                p += fit_hgb(sub, feats, target, ws, seed=s).predict_proba(te[feats])[:, 1] / nb
        elif model == "rank":
            import lightgbm as lgb
            trs = tr.sort_values("d")
            grp = trs.groupby("d").size().values
            rk = lgb.LGBMRanker(objective="lambdarank", n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=200,
                                reg_lambda=1.0, subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=0, verbose=-1,
                                lambdarank_truncation_level=20, label_gain=[0, 1])
            rk.fit(trs[feats], trs[target].astype(int), group=grp)
            p = rk.predict(te[feats])
            p = 1 / (1 + np.exp(-p))
        m = TR.metrics(te[target].values, p)
        m.update(day5=round(day_topk(te, p, 5), 4), day10=round(day_topk(te, p, 10), 4))
        rep.append(dict(year=Y, **m))
        log(name, Y, rep[-1])
    if not rep:
        return None
    r = pd.DataFrame(rep)
    res = dict(name=name, n_feat=len(feats), auc=round(float(r.auc.mean()), 4), top5=round(float(r.top5.mean()), 4),
               day5=round(float(r.day5.mean()), 4), day10=round(float(r.day10.mean()), 4), base=round(float(r.base.mean()), 4), years=rep)
    if base is not None:
        b = {x["year"]: x for x in base["years"]}
        dd = [x["day5"] - b[x["year"]]["day5"] for x in rep if x["year"] in b]
        res.update(d_day5=round(float(np.mean(dd)), 4),
                   d_day10=round(float(np.mean([x["day10"] - b[x["year"]]["day10"] for x in rep if x["year"] in b])), 4),
                   d_top5=round(float(np.mean([x["top5"] - b[x["year"]]["top5"] for x in rep if x["year"] in b])), 4),
                   d_auc=round(float(np.mean([x["auc"] - b[x["year"]]["auc"] for x in rep if x["year"] in b])), 4),
                   yrs_better=int(sum(1 for v in dd if v > 0)), yrs=len(dd))
    log(f"== {name}: AUC {res['auc']:.4f} топ-5% {res['top5']:.4f} топ-5 дня {res['day5']:.4f} топ-10 дня {res['day10']:.4f}"
        + (f" | Δтоп-5 дня {res['d_day5']:+.4f} Δтоп-10 дня {res['d_day10']:+.4f} ΔAUC {res['d_auc']:+.4f}, лучше в {res['yrs_better']}/{res['yrs']}" if base else "")
        + f" | {round(time.time() - t1)} с")
    return res


def main():
    os.makedirs(MODELS, exist_ok=True)
    X, Dd, IX, ev, impact = EX.load_all()
    W = [CD.news_weight(e, impact) for e in ev]
    FD = CD.feature_cols(X)
    yr = int(X.d.dt.year.max())
    years = [y for y in range(yr - YEARS_BACK + 1, yr + 1) if y > int(X.d.dt.year.min())]  # первый год истории — только обучение
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), rows=int(len(X)), years=years, runs=[])
    path = os.path.join(MODELS, f"exp2_report{'' if PART == 'all' else '_' + PART}.json")
    out["part"] = PART

    def add(r):
        if r:
            out["runs"].append(r)
            json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
        return r
    base = add(walk2(X, X, FD, years, "T0 база: учёба и проверка на новостях до 09:55"))
    # шум: та же база с другим seed модели — разница меньше этой не считается улучшением
    noise = None
    if on("a"):
        try:
            fit_hgb.__defaults__ = (1,)
            noise = add(walk2(X, X, FD, years, "шум: база с другим seed", base=base))
        except Exception as e:  # noqa: BLE001
            log("шум: ошибка", e)
        finally:
            fit_hgb.__defaults__ = (0,)
    out["noise_day5"] = abs(noise["d_day5"]) if noise else None
    # ---- часть 1: время публикации
    try:
        if on("a"):
            Xm = news_view(X, Dd, IX, ev, W, "m06")
            add(walk2(X, Xm, FD, years, "T1 как сейчас утром: учёба 09:55, прогноз в 06:00", base=base))
            add(walk2(Xm, Xm, FD, years, "T2 утро честно: учёба и прогноз на новостях до 06:00", base=base))
            del Xm
        if on("b"):
            Xe = news_view(X, Dd, IX, ev, W, "e19")
            add(walk2(X, Xe, FD, years, "T3 как сейчас вечером: учёба 09:55, прогноз в 19:00 накануне", base=base))
            add(walk2(Xe, Xe, FD, years, "T4 вечер честно: учёба и прогноз на новостях до 19:00 накануне", base=base))
            del Xe
    except Exception as e:  # noqa: BLE001
        log("время публикации: ошибка", e)
        traceback.print_exc()
    # ---- часть 2: способы обучения
    good = []
    nz = out.get("noise_day5") or 0.0

    def keep(r, tag):
        if r and r.get("d_day5", 0) > nz and r.get("yrs_better", 0) * 2 >= r.get("yrs", 1):
            good.append(tag)
    rc, nc = [], []
    if on("c"):
        keep(add(walk2(X, X, FD, years, "M1 свежие годы весомее (полураспад 3 года)", recency=3, base=base)), "recency")
        try:
            rc = g_rank(X, FD)
            keep(add(walk2(X, X, FD + rc, years, "M2 + ранги признаков внутри дня", base=base)), "rank_feats")
        except Exception as e:  # noqa: BLE001
            rc = []
            log("ранги: ошибка", e)
    try:
        if not on("d"):
            raise StopIteration
        NT = numtrades(sorted(X.t.unique()))
        nc = g_retail(X, NT)
        log("розница: заполнено", {c: round(float(X[c].notna().mean()), 3) for c in nc})
        keep(add(walk2(X, X, FD + nc, years, "M3 + активность розницы (число и размер сделок)", base=base)), "retail")
    except StopIteration:
        pass
    except Exception as e:  # noqa: BLE001
        nc = []
        log("розница: ошибка", e)
        traceback.print_exc()
    if on("e"):
        try:
            keep(add(walk2(X, X, FD, years, "M4 LightGBM LambdaRank по дням", model="rank", base=base)), "lambdarank")
        except Exception as e:  # noqa: BLE001
            log("lambdarank: ошибка", e)
            traceback.print_exc()
    if on("f"):
        keep(add(walk2(X, X, FD, years, f"M5 бэггинг {os.environ.get('EXP_BAG', '3')} моделей", model="bag", base=base)), "bag")
    if good and PART == "all":
        feats = FD + (rc if "rank_feats" in good else []) + (nc if "retail" in good else [])
        mdl = "rank" if "lambdarank" in good else ("bag" if "bag" in good else "hgb")
        add(walk2(X, X, feats, years, "M6 связка лучших: " + ", ".join(good), model=mdl, recency=3 if "recency" in good else None, base=base))
    out["good"] = good
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
    log("===== ИТОГ ПРОВЕРКИ №2 (годы", years[0], "–", years[-1], ") =====")
    for r in out["runs"]:
        log(f"{r['name']}: AUC {r['auc']:.4f}, топ-5% {r['top5']:.4f}, топ-5 дня {r['day5']:.4f}, топ-10 дня {r['day10']:.4f} (база аномалий {r['base']:.4f})"
            + (f"; Δтоп-5 дня {r['d_day5']:+.4f}, Δтоп-10 дня {r['d_day10']:+.4f}, ΔAUC {r['d_auc']:+.4f}, лучше в {r['yrs_better']}/{r['yrs']} лет" if "d_day5" in r else ""))
    log("шум (топ-5 дня):", out.get("noise_day5"), "| прошли отбор:", good or "ничего", "| время", round(time.time() - T0), "с")


if __name__ == "__main__":
    main()
