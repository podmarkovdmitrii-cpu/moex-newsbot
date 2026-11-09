# -*- coding: utf-8 -*-
"""Проверка идей по улучшению модели аномалий на реальных данных с 2008 г. (workflow anomaly-experiment.yml).

Та же схема, что у боевой модели: признаки строятся как в train.py, обучение на прошлых годах → проверка на следующем
(годы, которых модель не видела). Каждая идея — отдельная группа признаков: «база» против «база + группа», затем всё вместе.

Группы (всё — только то, что известно утром дня D до открытия):
  E1 затухание     — вчерашняя аномалия, где закрылся вчерашний день (у максимума/отката), была ли новая новость, z вчерашнего хода;
  E2 память реакции — как эта бумага раньше реагировала на новости того же типа (средний |ход| и частота аномалий, только прошлое);
  E3 календарь     — годовщины прошлых СД по дивидендам и отчётности (по новостям прошлых лет: ±дней до «ожидаемого» события);
  E4 группа        — ход сектора вчера без самой бумаги и «догоняющий» разрыв, спред обычка/преф (z к 60 дням);
  E5 режим рынка   — RVI (уровень, изменение, z), курс доллара (CETS), дни заседаний ЦБ, экспирации фьючерсов;
  E6 срочный рынок — открытый интерес фьючерса на акцию (изменение за 1/5 дней, z к 60), базис к акции и его сдвиг, оборот фьючерса к акции;
  C  связки        — «затишье перед событием», «тихий набор», «затухание без новостей», «день шока».
Внутридневные идеи (поток сделок, Пульс, pump-каналы, ALGOPACK) здесь не проверяются: у них нет истории с 2008 г.

Отчёт: журнал запуска + models/exp_report.json. Метрики: AUC, точность в топ-5% прогноза (её видят читатели),
ложные тревоги на следующий день после аномалии, доля «тихих» ценовых скачков (без роста оборота накануне) в топ-5%.
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
import train as TR  # noqa: E402
import core_daily as CD  # noqa: E402
import regional as RG  # noqa: E402

DATA, MODELS = TR.DATA, TR.MODELS
log = TR.log
T0 = time.time()
BUDGET = float(os.environ.get("EXP_BUDGET_MIN", "300")) * 60
YEARS_BACK = int(os.environ.get("EXP_YEARS", "10"))
SNEG = float(os.environ.get("EXP_SAMPLE_NEG", "0.3"))

PAIRS = [("SBER", "SBERP"), ("TATN", "TATNP"), ("SNGS", "SNGSP"), ("RTKM", "RTKMP"), ("BANE", "BANEP"), ("MTLR", "MTLRP"),
         ("KZOS", "KZOSP"), ("NKNC", "NKNCP"), ("LSNG", "LSNGP"), ("MGTS", "MGTSP"), ("KRKN", "KRKNP"), ("TRNF", "TRNFP")]
# код базового актива фьючерса → тикер акции (когда не совпадает)
ASSET2TK = {"SBRF": "SBER", "SBPR": "SBERP", "GAZR": "GAZP", "NOTK": "NVTK", "SNGR": "SNGS", "SNGP": "SNGSP", "MTSI": "MTSS",
            "TRNF": "TRNFP", "TATP": "TATNP", "RTKM": "RTKM", "YNDF": "YDEX", "YDEX": "YDEX", "TCSI": "T", "FIVE": "X5", "VKCO": "VKCO",
            "HHRU": "HEAD", "POLY": "POLY", "ISKJ": "ABIO", "SPBE": "SPBE", "FESH": "FESH", "MVID": "MVID", "SMLT": "SMLT",
            "SGZH": "SGZH", "BELU": "BELU", "FLOT": "FLOT", "ASTR": "ASTR", "POSI": "POSI", "WUSH": "WUSH", "SOFL": "SOFL"}


SEED = [0]


def _hgb(n=200):
    """Как в train.py, но с фиксированным seed: иначе внутренняя валидация (early stopping) случайна и разница между
    вариантами тонет в шуме. Шум отдельно измеряется повтором базы с другим seed."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=n, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=300, l2_regularization=1.0,
                                          random_state=SEED[0])


TR.hgb = _hgb


def left():
    return BUDGET - (time.time() - T0)


# ------------------------------------------------------------------ загрузка как в train.py
def load_all():
    divh = json.load(open(os.path.join(HERE, "..", "divhist.json"), encoding="utf-8"))
    impact = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8"))
    tick = TR.universe()
    Dd = TR.load_series("d", tick + ["IMOEX"], "2012-01-01", index=("IMOEX",))
    IX = Dd.pop("IMOEX")
    turn = {t: np.mean([r[5] for r in v[-60:]]) for t, v in Dd.items() if len(v) > 60}
    top = sorted(turn, key=lambda t: -turn[t])[: int(os.environ.get("ANOM_HOURLY_N", "160"))]
    Hh = TR.load_series("h", top + ["IMOEX"], "2020-01-01", index=("IMOEX",))
    IXH = Hh.pop("IMOEX")
    Dd = TR.stitch_renamed(Dd)
    Dd, IX = TR.backfill(Dd, IX)
    first_day = {t: dt.date.fromisoformat(v[0][0][:10]) for t, v in Dd.items() if v}
    ev, _ = TR.events_all(first_day)
    W = [CD.news_weight(e, impact) for e in ev]
    hd = CD.hourly_daily(Hh)
    RE = RG.company_events(RG.load_events(os.environ.get("NEWS_STATE")))
    X = CD.build({"sh": Dd, "ix": {"IMOEX": IX}}, divh, ev, news_tickers=set(e["tk"] for e in ev), weights=W, hourly=hd, ix_hourly=IXH,
                 sec_of=impact.get("secOf"), reg_events=RE)
    X = X[X.d >= TR.FROM].sort_values(["t", "d"]).reset_index(drop=True)
    log("признаки базы:", X.shape, "база аномалий", round(X.y.mean(), 4), round(time.time() - T0), "с")
    return X, Dd, IX, ev, impact


def prices(Dd):
    fr = []
    for t, a in Dd.items():
        df = CD.series(a)
        if len(df) >= 300:
            fr.append(df[["d", "o", "h", "l", "c", "v"]].assign(t=t))
    return pd.concat(fr, ignore_index=True)


def asof_prev(X, s, name):
    """Значение ряда s (index = дата) на последнюю дату СТРОГО раньше дня D."""
    s = s.dropna().sort_index()
    src = pd.DataFrame({"_d": pd.to_datetime(s.index).values.astype("datetime64[ns]"), name: s.values})
    days = pd.DataFrame({"d": np.sort(X.d.unique()).astype("datetime64[ns]")})
    m = pd.merge_asof(days, src, left_on="d", right_on="_d", allow_exact_matches=False, direction="backward")
    return X.d.astype("datetime64[ns]").map(dict(zip(m.d, m[name])))


# ------------------------------------------------------------------ группы признаков
def e1_fade(X, P):
    g = X.groupby("t")
    p = P.set_index(["t", "d"])
    cp = ((p.c - p.l) / (p.h - p.l).replace(0, np.nan)).rename("cp")
    X["_cp"] = X.set_index(["t", "d"]).index.map(cp).values
    X["e_cp1"] = g["_cp"].shift(1)
    X["e_y1"] = g["y"].shift(1).fillna(0)
    X["e_y2"] = g["y"].shift(2).fillna(0)
    X["e_x1z"] = g["x"].shift(1) / X["sd60"]
    X["e_y1_nonews"] = X["e_y1"] * (X["nw_n"] == 0)
    X["e_fade"] = X["e_y1"] * (1 - X["e_cp1"].fillna(0.5)) * (X["nw_n"] == 0)
    X.drop(columns="_cp", inplace=True)
    return ["e_cp1", "e_y1", "e_y2", "e_x1z", "e_y1_nonews", "e_fade"]


def e2_memory(X):
    groups = [g for g in list(CD.TYPES_GROUPS) + ["other"] if "nw_n_" + g in X.columns]
    ax = X["x"].abs()
    yy = X["y"]
    out, mem_cols, memy_cols = [], [], []
    for gname in groups:
        m = (X["nw_n_" + gname] > 0) & ax.notna()
        s = ax.where(m, 0.0)
        sy = yy.where(m, 0.0).fillna(0)
        c = m.astype(float)
        cs = s.groupby(X.t).cumsum() - s
        csy = sy.groupby(X.t).cumsum() - sy
        cn = c.groupby(X.t).cumsum() - c
        X[f"_mem_{gname}"] = np.where(cn >= 3, cs / cn.replace(0, np.nan), np.nan) / X["sd60"]
        X[f"_memy_{gname}"] = np.where(cn >= 3, csy / cn.replace(0, np.nan), np.nan)
        mem_cols.append(f"_mem_{gname}")
        memy_cols.append(f"_memy_{gname}")
    pres = np.stack([(X["nw_n_" + gname] > 0).values for gname in groups], axis=1)
    M = X[mem_cols].values
    MY = X[memy_cols].values
    X["e_mem_today"] = np.nanmax(np.where(pres, M, np.nan), axis=1) if len(groups) else np.nan
    X["e_memy_today"] = np.nanmax(np.where(pres, MY, np.nan), axis=1) if len(groups) else np.nan
    # общая «чувствительность к новостям»: частота аномалий в дни с новостями / в дни без
    anyn = (X["nw_n"] > 0)
    for nm, mask in (("news", anyn), ("quiet", ~anyn)):
        c = (mask & yy.notna()).astype(float)
        s = yy.where(mask, 0).fillna(0)
        X[f"_r_{nm}"] = (s.groupby(X.t).cumsum() - s) / ((c.groupby(X.t).cumsum() - c) + 5)
    X["e_news_sens"] = X["_r_news"] / (X["_r_quiet"] + 0.005)
    X["e_memy_div"] = X.get("_memy_div")
    X["e_memy_results"] = X.get("_memy_results")
    X.drop(columns=[c for c in X.columns if c.startswith("_mem") or c.startswith("_r_")], inplace=True)
    return ["e_mem_today", "e_memy_today", "e_news_sens", "e_memy_div", "e_memy_results"]


def e3_calendar(X, ev):
    kinds = {"div": {"div_rec", "div_cut"}, "res": {"results_up", "results_down", "guidance_up", "guidance_down"}}
    ev_days = {k: {} for k in kinds}
    for e in ev:
        for k, tys in kinds.items():
            if e["ty"] in tys and e["tk"] != "IMOEX":
                d = (dt.datetime.utcfromtimestamp(e["ts"]) + dt.timedelta(hours=3)).date().toordinal()
                ev_days[k].setdefault(e["tk"], set()).add(d)
    cols = []
    dord = X.d.map(lambda d: d.toordinal()).values
    for k in kinds:
        dist = np.full(len(X), np.nan)
        for t, idx in X.groupby("t").indices.items():
            evs = np.array(sorted(ev_days[k].get(t, ())), dtype=float)
            if not len(evs):
                continue
            D = dord[idx].astype(float)[:, None]
            age = D - evs[None, :]
            ok = (age >= 200) & (age <= 1200)           # только события прошлых лет: годовщина — «ожидаемая» дата
            ph = np.abs(((age + 182.6) % 365.25) - 182.6)  # расстояние до ближайшей годовщины, дни
            ph = np.where(ok, ph, np.nan)
            with np.errstate(all="ignore"):
                dist[idx] = np.nanmin(ph, axis=1)
        X[f"e_cal_{k}"] = np.clip(dist, 0, 60)
        X[f"e_cal_{k}_near"] = (X[f"e_cal_{k}"] <= 7).astype(float)
        cols += [f"e_cal_{k}", f"e_cal_{k}_near"]
    return cols


def e4_group(X, P, sec_of):
    g = X.groupby("t")
    X["_x1"] = g["x"].shift(1)
    X["_sec"] = X["t"].map(sec_of or {}).fillna("other")
    gs = X.groupby(["d", "_sec"])["_x1"]
    s, n = gs.transform("sum"), gs.transform("count")
    X["e_secx1"] = (s - X["_x1"].fillna(0)) / (n - X["_x1"].notna()).clip(lower=1)
    X.loc[X["_sec"] == "other", "e_secx1"] = np.nan
    X["e_catch"] = X["e_secx1"] - X["_x1"]
    X["_ax1"] = X["_x1"].abs()
    ga = X.groupby(["d", "_sec"])["_ax1"]
    X["e_secabs1"] = (ga.transform("sum") - X["_ax1"].fillna(0)) / (ga.transform("count") - X["_ax1"].notna()).clip(lower=1)
    X.loc[X["_sec"] == "other", "e_secabs1"] = np.nan
    # спред обычка/преф
    c = P.pivot_table(index="d", columns="t", values="c")
    X["e_pair_z"] = np.nan
    X["e_pair_chg1"] = np.nan
    for a, b in PAIRS:
        if a not in c or b not in c:
            continue
        lr = np.log(c[a] / c[b])
        z = ((lr - lr.rolling(60, min_periods=30).mean()) / lr.rolling(60, min_periods=30).std())
        ch = lr.diff()
        zs, chs = z.shift(1), ch.shift(1)   # к утру дня D известно закрытие D−1
        for tk, sg in ((a, 1), (b, -1)):
            m = X.t == tk
            X.loc[m, "e_pair_z"] = X.loc[m, "d"].map(zs).values * sg
            X.loc[m, "e_pair_chg1"] = X.loc[m, "d"].map(chs).values * sg
    X.drop(columns=["_x1", "_sec", "_ax1"], inplace=True)
    return ["e_secx1", "e_catch", "e_secabs1", "e_pair_z", "e_pair_chg1"]


def daily_series(path, start="2008-01-01"):
    from common import candles
    days = (dt.date.today() - dt.date.fromisoformat(start)).days + 1
    B = candles(path, days, 24)
    return pd.Series({pd.Timestamp(b["t"][:10]): b["c"] for b in B}).sort_index()


def e5_regime(X):
    cols = []
    try:
        rvi = daily_series("engines/stock/markets/index/securities/RVI/candles.json", "2013-01-01")
        log("RVI: дней", len(rvi), "с", rvi.index.min().date() if len(rvi) else None)
        X["e_rvi"] = asof_prev(X, rvi, "v").values
        X["e_rvi_chg1"] = asof_prev(X, rvi.pct_change(), "v").values
        X["e_rvi_z"] = asof_prev(X, (rvi - rvi.rolling(60).mean()) / rvi.rolling(60).std(), "v").values
        cols += ["e_rvi", "e_rvi_chg1", "e_rvi_z"]
    except Exception as e:  # noqa: BLE001
        log("RVI не загружен:", e)
    try:
        usd = daily_series("engines/currency/markets/selt/boards/CETS/securities/USD000UTSTOM/candles.json", "2008-01-01")
        log("USD/RUB: дней", len(usd), "с", usd.index.min().date() if len(usd) else None)
        r = usd.pct_change()
        X["e_usd_r1"] = asof_prev(X, r, "v").values
        X["e_usd_abs5"] = asof_prev(X, r.abs().rolling(5).mean(), "v").values
        cols += ["e_usd_r1", "e_usd_abs5"]
    except Exception as e:  # noqa: BLE001
        log("USD/RUB не загружен:", e)
    cbr = set(pd.to_datetime(json.load(open(os.path.join(HERE, "..", "cbr_dates.json")))))
    days = np.sort(X.d.unique())
    cb = np.array([pd.Timestamp(d) in cbr for d in days])
    nxt = np.full(len(days), 99.0)
    j = 99.0
    for i in range(len(days) - 1, -1, -1):  # торговых дней до ближайшего заседания (расписание ЦБ публикуется заранее)
        j = 0 if cb[i] else j + 1
        nxt[i] = min(j, 99)
    X["e_cbr_day"] = X.d.map(dict(zip(days, cb.astype(float)))).values
    X["e_to_cbr"] = X.d.map(dict(zip(days, nxt))).values

    def third_friday(y, m):
        d = dt.date(y, m, 15)
        return d + dt.timedelta(days=(4 - d.weekday()) % 7)
    exp = sorted(third_friday(y, m) for y in range(2008, 2028) for m in (3, 6, 9, 12))
    eo = np.array([d.toordinal() for d in exp])
    do = X.d.map(lambda d: d.toordinal()).values
    k = np.searchsorted(eo, do)
    X["e_to_exp"] = np.clip(eo[np.clip(k, 0, len(eo) - 1)] - do, 0, 60)
    cols += ["e_cbr_day", "e_to_cbr", "e_to_exp"]
    return cols


# ------------------------------------------------------------------ срочный рынок: история по дням (кэш)
def fut_history(days):
    from common import iss, table
    p = os.path.join(DATA, "fut_oi.json.gz")
    cache = {}
    if os.path.exists(p):
        with gzip.open(p, "rt") as f:
            cache = json.load(f)
    need = [d for d in days if d not in cache or d >= (dt.date.today() - dt.timedelta(days=7)).isoformat()]
    log("срочный рынок: дней в кэше", len(cache), "докачать", len(need))

    def one(d):
        rows, start = [], 0
        for _ in range(30):
            js = iss("history/engines/futures/markets/forts/securities.json", date=d, start=start,
                     **{"history.columns": "SECID,OPENPOSITION,VALUE,SETTLEPRICE"})
            part = table(js, "history")
            rows += [[r["SECID"], r.get("OPENPOSITION") or 0, r.get("VALUE") or 0, r.get("SETTLEPRICE")] for r in part]
            if len(part) < 100:
                break
            start += len(part)
        return d, rows
    t1 = time.time()
    with cf.ThreadPoolExecutor(6) as ex:
        futs = [ex.submit(one, d) for d in need]
        for k, fu in enumerate(cf.as_completed(futs)):
            try:
                d, rows = fu.result()
                cache[d] = rows
            except Exception as e:  # noqa: BLE001
                log("срочный рынок: ошибка", e)
            if k % 250 == 0:
                log("срочный рынок:", k, "/", len(need), round(time.time() - t1), "с")
                with gzip.open(p, "wt") as f:
                    json.dump(cache, f, separators=(",", ":"))
            if left() < 150 * 60:   # оставить время на обучение
                log("срочный рынок: время на исходе — остановка докачки")
                break
    with gzip.open(p, "wt") as f:
        json.dump(cache, f, separators=(",", ":"))
    return cache


def fut_prefix_map(stocks):
    from common import iss, table
    js = iss("engines/futures/markets/forts/securities.json", **{"iss.only": "securities", "securities.columns": "SECID,ASSETCODE"})
    mp = {}
    for r in table(js, "securities"):
        a, s = r.get("ASSETCODE") or "", r["SECID"]
        tk = ASSET2TK.get(a, a)
        if tk in stocks and len(s) >= 4:
            mp[s[:-2]] = tk
    return mp


def e6_futures(X, P):
    days = sorted(d.strftime("%Y-%m-%d") for d in X.d.unique() if d >= pd.Timestamp("2012-01-01"))
    cache = fut_history(days)
    mp = fut_prefix_map(set(X.t.unique()))
    log("фьючерсы на акции: префиксов", len(mp), sorted(set(mp.values()))[:60])
    rows = []
    for d, rs in cache.items():
        agg = {}
        for sid, oi, val, st in rs:
            tk = mp.get(sid[:-2])
            if not tk:
                continue
            a = agg.setdefault(tk, [0.0, 0.0, None, -1.0, None])
            a[0] += oi or 0
            a[1] += val or 0
            if (oi or 0) > a[3] and st:
                a[2], a[3], a[4] = st, oi or 0, sid
        for tk, (oi, val, st, _, sid) in agg.items():
            rows.append((pd.Timestamp(d), tk, oi, val, st, sid))
    if not rows:
        return []
    F = pd.DataFrame(rows, columns=["d", "t", "oi", "fval", "settle", "front"]).sort_values(["t", "d"])
    F = F.merge(P[["t", "d", "c", "v"]], on=["t", "d"], how="left")
    ratio = F.settle / F.c
    lot = 10 ** np.round(np.log10(ratio.where(ratio > 0)))
    F["basis"] = ratio / lot - 1
    g = F.groupby("t")
    F["oi_l1"] = np.log(F.oi.replace(0, np.nan)).groupby(F.t).diff()
    F["oi_l5"] = np.log(F.oi.replace(0, np.nan)).groupby(F.t).diff(5)
    F["oi_z"] = (F.oi - g.oi.transform(lambda s: s.rolling(60, min_periods=20).mean())) / g.oi.transform(lambda s: s.rolling(60, min_periods=20).std())
    roll = F.front != g.front.shift(1)
    F["basis_chg"] = g.basis.diff().where(~roll)
    F["basis_z"] = (F.basis - g.basis.transform(lambda s: s.rolling(60, min_periods=20).mean())) / g.basis.transform(lambda s: s.rolling(60, min_periods=20).std())
    F["fv_ratio"] = F.fval / F.v.replace(0, np.nan)
    cols = ["oi_l1", "oi_l5", "oi_z", "basis", "basis_chg", "basis_z", "fv_ratio"]
    # данные дня D−1 → признаки дня D: сдвиг на следующий торговый день бумаги
    out = []
    for t, idx in X.groupby("t").indices.items():
        f = F[F.t == t].set_index("d")[cols]
        if f.empty:
            continue
        dd = X.d.values[idx]
        prev = pd.Series(dd).shift(1)
        out.append(pd.DataFrame({"i": idx, **{"e_f_" + c: prev.map(f[c]).values for c in cols}}))
    if not out:
        return []
    O = pd.concat(out).set_index("i")
    for c in cols:
        X["e_f_" + c] = O["e_f_" + c].reindex(X.index).values
    X["e_f_has"] = X["e_f_oi_l1"].notna().astype(float)
    log("фьючерсы: строк с признаками", int(X["e_f_has"].sum()))
    return ["e_f_" + c for c in cols] + ["e_f_has"]


def combos(X):
    cols = []
    if "e_cal_div" in X:
        near = ((X.get("e_cal_div", 99) <= 7) | (X.get("e_cal_res", 99) <= 7)).astype(float)
        X["c_quiet_event"] = near / X["sq"].clip(lower=0.2)
        cols.append("c_quiet_event")
    if "e_f_oi_z" in X:
        X["c_accum"] = X["e_f_oi_z"] / X["sq"].clip(lower=0.2)
        cols.append("c_accum")
    if "e_fade" in X:
        X["c_fade_quiet"] = X["e_y1_nonews"] * (X["vr1"] < 2)
        cols.append("c_fade_quiet")
    if "e_rvi_chg1" in X:
        X["c_shock"] = X["e_rvi_chg1"] * X["sd60"]
        cols.append("c_shock")
    return cols


# ------------------------------------------------------------------ проверка
def special(P, X):
    """Целевые метрики: ложные тревоги после вчерашней аномалии и «тихие» ценовые скачки."""
    Q = P.merge(X[["d", "t", "since_an", "vr1", "y_px"]], on=["d", "t"], how="left")
    Q["yr"] = Q.d.dt.year
    Q["thr"] = Q.groupby("yr").p.transform(lambda s: s.quantile(0.95))
    Q["top"] = Q.p >= Q.thr
    a = Q[(Q.since_an == 1) & Q.top]
    silent = Q[(Q.y_px == 1) & (Q.vr1 < 1.5)]
    return dict(prec_after_an=round(float(a.y.mean()), 3) if len(a) else None, n_after_an=int(len(a)),
                silent_recall=round(float(silent.top.mean()), 3) if len(silent) else None, n_silent=int(len(silent)))


def run(X, feats, name, years, target="y", base=None):
    if left() < 15 * 60:
        log("пропуск", name, "— нет времени")
        return None
    t1 = time.time()
    P, rep = TR.walk(X, feats, target, years, sample_neg=SNEG)
    r = pd.DataFrame(rep)
    res = dict(name=name, target=target, n_feat=len(feats), auc=round(float(r.auc.mean()), 4), top5=round(float(r.top5.mean()), 4),
               ap=round(float(r.ap.mean()), 4), base=round(float(r.base.mean()), 4), years=rep, **special(P, X))
    if base is not None:
        b = {x["year"]: x for x in base["years"]}
        d5 = [x["top5"] - b[x["year"]]["top5"] for x in rep if x["year"] in b]
        da = [x["auc"] - b[x["year"]]["auc"] for x in rep if x["year"] in b]
        res.update(d_top5=round(float(np.mean(d5)), 4), d_auc=round(float(np.mean(da)), 4),
                   yrs_better=int(sum(1 for v in d5 if v > 0)), yrs=len(d5))
    log(f"== {name} [{target}]: AUC {res['auc']:.4f} топ-5% {res['top5']:.4f}" + (f" | Δтоп-5% {res['d_top5']:+.4f} ΔAUC {res['d_auc']:+.4f}, лучше в {res['yrs_better']}/{res['yrs']} лет" if base else "")
        + f" | после аномалии {res['prec_after_an']} | тихие скачки {res['silent_recall']} | {round(time.time() - t1)} с")
    return res


def direction_check(X, feats, years, name):
    from sklearn.metrics import roc_auc_score
    D = X[(X.y == 1) & X.x.notna()].copy()
    D["up"] = (D.x > 0).astype(int)
    acc, aucs = [], []
    for Y in years:
        tr, te = D[D.d.dt.year < Y], D[D.d.dt.year == Y]
        if len(te) < 50 or len(tr) < 300:
            continue
        p = TR.hgb_dir().fit(tr[feats], tr.up).predict_proba(te[feats])[:, 1]
        acc.append(float(((p > 0.5) == te.up.values).mean()))
        aucs.append(float(roc_auc_score(te.up.values, p)))
    res = dict(name=name, target="dir", acc=round(float(np.mean(acc)), 4) if acc else None, auc=round(float(np.mean(aucs)), 4) if aucs else None)
    log(f"== направление {name}: точность {res['acc']} AUC {res['auc']}")
    return res


def main():
    os.makedirs(MODELS, exist_ok=True)
    X, Dd, IX, ev, impact = load_all()
    P = prices(Dd)
    FD = CD.feature_cols(X)
    groups = {}
    for key, fn in (("E1 затухание", lambda: e1_fade(X, P)), ("E2 память реакции", lambda: e2_memory(X)),
                    ("E3 календарь", lambda: e3_calendar(X, ev)), ("E4 группа и пары", lambda: e4_group(X, P, impact.get("secOf"))),
                    ("E5 режим рынка", lambda: e5_regime(X)), ("E6 срочный рынок", lambda: e6_futures(X, P))):
        try:
            cols = fn()
            groups[key] = [c for c in cols if c in X.columns]
            nn = {c: round(float(X[c].notna().mean()), 3) for c in groups[key]}
            log("группа", key, "признаков", len(groups[key]), "заполнено", nn, round(time.time() - T0), "с")
        except Exception as e:  # noqa: BLE001
            log("группа", key, "ОШИБКА", e)
            traceback.print_exc()
    try:
        groups["C связки"] = combos(X)
    except Exception as e:  # noqa: BLE001
        log("связки: ошибка", e)
    yr = int(X.d.dt.year.max())
    years = list(range(yr - YEARS_BACK + 1, yr + 1))
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), rows=int(len(X)), years=years, groups={k: v for k, v in groups.items()}, runs=[])

    def save():
        json.dump(out, open(os.path.join(MODELS, "exp_report.json"), "w"), ensure_ascii=False, indent=1)
    base = run(X, FD, "база", years)
    out["runs"].append(base)
    SEED[0] = 1
    noise = run(X, FD, "база (повтор с другим seed — уровень шума)", years, base=base)
    SEED[0] = 0
    if noise:
        out["runs"].append(noise)
        out["noise_top5"] = abs(noise["d_top5"])
    save()
    for key, cols in groups.items():
        if not cols or key.startswith("C"):
            continue
        r = run(X, FD + cols, "база + " + key, years, base=base)
        if r:
            out["runs"].append(r)
            save()
    allc = [c for k, v in groups.items() for c in v]
    r_all = run(X, FD + allc, "база + все идеи и связки", years, base=base)
    if r_all:
        out["runs"].append(r_all)
    # лучшие группы (прирост топ-5% и лучше хотя бы в половине лет) — вместе
    nz = out.get("noise_top5", 0)
    good = [r["name"].replace("база + ", "") for r in out["runs"] if r and r["name"].startswith("база + E") and r.get("d_top5", 0) > nz
            and r.get("yrs_better", 0) * 2 >= r.get("yrs", 1)]
    if good:
        gc = [c for k in good for c in groups.get(k, [])] + groups.get("C связки", [])
        r_g = run(X, FD + gc, "база + лучшие: " + ", ".join(good), years, base=base)
        if r_g:
            out["runs"].append(r_g)
    save()
    # ценовые аномалии и направление — база против всего
    yrs3 = years[-5:]
    bpx = run(X, FD, "база", yrs3, target="y_px")
    if bpx:
        out["runs"].append(bpx)
        a = run(X, FD + allc, "база + все идеи и связки", yrs3, target="y_px", base=bpx)
        if a:
            out["runs"].append(a)
    save()
    if left() > 10 * 60:
        out["dir"] = [direction_check(X, FD, years, "база"), direction_check(X, FD + allc, years, "база + все")]
    save()
    log("===== ИТОГ ПРОВЕРКИ ИДЕЙ (годы", years[0], "–", years[-1], ", модель не видела проверочный год) =====")
    for r in out["runs"]:
        if not r:
            continue
        log(f"{r['name']} [{r['target']}]: AUC {r['auc']:.4f}, топ-5% {r['top5']:.4f} (база {r['base']:.4f})"
            + (f", Δтоп-5% {r['d_top5']:+.4f}, ΔAUC {r['d_auc']:+.4f}, лучше в {r['yrs_better']}/{r['yrs']} лет" if "d_top5" in r else "")
            + f"; точность после аномалии {r['prec_after_an']}, тихие скачки в топ-5% {r['silent_recall']}")
    for r in out.get("dir", []):
        log(f"направление {r['name']}: точность {r['acc']}, AUC {r['auc']}")
    log("время", round(time.time() - T0), "с")


if __name__ == "__main__":
    main()
