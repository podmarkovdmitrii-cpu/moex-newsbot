# -*- coding: utf-8 -*-
"""Проверка №3: идеи из других областей для модели аномалий (workflow anomaly-experiment.yml, exp=3).

Схема как в проверке №2 (exp2.py): обучение на прошлых годах → проверка на следующем, годы 2009–2026, метрика «топ-5
дня». База — боевая настройка без бэггинга (свежие годы весомее, полураспад 3 года), к ней добавляется группа признаков.
Все признаки — только то, что известно утром дня D (данные по D−1 включительно).

  H  сейсмология (модель Хоукса/ETAS «афтершоков»): затухающая сумма прошлых аномалий бумаги (τ 2, 5, 20, 60 дней),
     рынка и сектора без самой бумаги — аномалии порождают аномалии;
  P  эпидемиология (заражение по сети): 5 самых коррелированных с бумагой акций за 120 дней — доля «заражённых»
     (аномалия вчера), их средний и максимальный |ход| вчера;
  W  экология и климат (ранние признаки «переломных точек»): рост автокорреляции и дисперсии, асимметрия и эксцесс,
     перестановочная энтропия доходностей за 20 дней;
  U  астрономия и поиск выбросов: насколько вчерашнее сочетание оборота, хода и размаха необычно для самой бумаги
     (z-оценки к её 120 дням, их максимум и длина вектора);
  T  кредитный скоринг и спорт (кодирование объекта, рейтинг Эло): накопленная частота аномалий бумаги, по дню недели,
     затухающий «рейтинг аномальности» (полураспад 250 дней);
  G  календарь и маркетинг: длина паузы до дня D и после него (выходные, праздники) — новости копятся;
  E  прогноз погоды (ансамбль разных моделей): бустинг + случайный лес, среднее рангов.
Части: a — база, шум, H; b — P; c — W; d — U и G; e — T; f — E; g — все группы вместе.
"""
import datetime as dt
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
import exp2 as E2  # noqa: E402  (walk2, метрики «топ дня», загрузка данных)
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


def _put(X, F, cols):
    Y = X[["t", "d"]].merge(F[["t", "d"] + cols], on=["t", "d"], how="left")
    for c in cols:
        X[c] = Y[c].values
    return cols


def _decay(y, tau):
    """s_t = a·(s_{t−1} + y_{t−1}): затухающая сумма прошлых событий (только прошлое)."""
    from scipy.signal import lfilter
    a = np.exp(-1.0 / tau)
    return lfilter([0, a], [1, -a], np.nan_to_num(y))


# ------------------------------------------------------------------ H: Хоукс
def g_hawkes(X, sec_of):
    X.sort_values(["t", "d"], inplace=True)
    X.reset_index(drop=True, inplace=True)
    cols = []
    yv = X["y"].fillna(0).values
    out = {f"hk_self{tau}": np.zeros(len(X)) for tau in (2, 5, 20, 60)}
    for t, idx in X.groupby("t").indices.items():
        for tau in (2, 5, 20, 60):
            out[f"hk_self{tau}"][idx] = _decay(yv[idx], tau)
    for k, v in out.items():
        X[k] = v
        cols.append(k)
    # рынок и сектор: число аномалий за день без самой бумаги, затухание по торговым дням
    days = np.sort(X.d.unique())
    cnt = X.groupby("d")["y"].sum().reindex(days).fillna(0).values
    n = X.groupby("d")["y"].count().reindex(days).fillna(1).values
    for tau in (1, 5):
        m = pd.Series(_decay(cnt / np.maximum(n, 1), tau), index=days)
        X[f"hk_mkt{tau}"] = X.d.map(m).values
        cols.append(f"hk_mkt{tau}")
    sec = X.t.map(lambda t: (sec_of or {}).get(t, "other"))
    G = X.assign(sec=sec).groupby(["sec", "d"])["y"].agg(["sum", "count"]).reset_index()
    parts = []
    for s_, g in G.groupby("sec"):
        g = g.set_index("d").reindex(days).fillna(0)
        for tau in (2, 10):
            g[f"sd{tau}"] = _decay(g["sum"].values, tau)
            g[f"sn{tau}"] = _decay(g["count"].values, tau)
        parts.append(g.reset_index().rename(columns={"index": "d"}).assign(sec=s_))
    S = pd.concat(parts, ignore_index=True)
    Z = X[["d"]].assign(sec=sec.values).merge(S, on=["sec", "d"], how="left")
    for tau in (2, 10):
        # своя затухающая сумма в масштабе того же τ — вычитаем, чтобы сектор был «без самой бумаги»
        selfd = np.zeros(len(X))
        for t, idx in X.groupby("t").indices.items():
            selfd[idx] = _decay(yv[idx], tau)
        X[f"hk_sec{tau}"] = (Z[f"sd{tau}"].values - selfd) / np.maximum(Z[f"sn{tau}"].values, 1)
        cols.append(f"hk_sec{tau}")
    return cols


# ------------------------------------------------------------------ P: заражение по сети корреляций
def g_peers(X, P):
    R = P.pivot_table(index="d", columns="t", values="c").sort_index().pct_change()
    Y = X.pivot_table(index="d", columns="t", values="y").reindex(R.index)
    AX = X.assign(ax=X.x.abs()).pivot_table(index="d", columns="t", values="ax").reindex(R.index)
    Ys, AXs = Y.shift(1), AX.shift(1)
    tick = list(R.columns)
    days = R.index
    fr, fa, fm = (np.full((len(days), len(tick)), np.nan) for _ in range(3))
    Yv, Av = Ys[tick].values, AXs[tick].values
    for b in range(120, len(days), 21):
        C = R.iloc[b - 120:b].corr(min_periods=60).to_numpy(copy=True)
        np.fill_diagonal(C, -np.inf)
        C = np.nan_to_num(C, nan=-np.inf)
        top = np.argsort(-C, axis=1)[:, :5]
        ok = np.take_along_axis(C, top, axis=1) > 0.1
        sl = slice(b, min(b + 21, len(days)))
        yy = Yv[sl][:, top]       # дни × бумаги × 5
        aa = Av[sl][:, top]
        m = np.where(ok[None, :, :], 1.0, np.nan)
        with np.errstate(all="ignore"):
            fr[sl] = np.nanmean(yy * m, axis=2)
            fa[sl] = np.nanmean(aa * m, axis=2)
            fm[sl] = np.nanmax(aa * m, axis=2)
    F = pd.DataFrame({"d": np.repeat(days.values, len(tick)), "t": np.tile(tick, len(days)),
                      "pe_inf": fr.ravel(), "pe_ax": fa.ravel(), "pe_axmax": fm.ravel()})
    return _put(X, F, ["pe_inf", "pe_ax", "pe_axmax"])


# ------------------------------------------------------------------ W: ранние признаки перелома
def _perm_entropy(r, w=20):
    a, b, c = r.shift(2), r.shift(1), r
    code = ((a < b).astype(int) * 1 + (b < c).astype(int) * 2 + (a < c).astype(int) * 4)
    oh = pd.get_dummies(code.where(r.notna() & a.notna())).astype(float)
    cnt = oh.rolling(w, min_periods=w // 2).sum()
    p = cnt.div(cnt.sum(axis=1), axis=0)
    with np.errstate(all="ignore"):
        return -(p * np.log(p.where(p > 0))).sum(axis=1, min_count=1) / np.log(6)


def g_warning(X, P):
    fr = []
    for t, df in P.groupby("t"):
        df = df.sort_values("d")
        r = df.c.pct_change()
        f = pd.DataFrame({"t": t, "d": df.d.values})
        ac20 = r.rolling(20, min_periods=15).corr(r.shift(1))
        ac60 = r.rolling(60, min_periods=40).corr(r.shift(1))
        v10, v60 = r.rolling(10).var(), r.rolling(60, min_periods=40).var()
        f["ew_ac20"] = ac20.shift(1).values
        f["ew_dac"] = (ac20 - ac60).shift(1).values
        f["ew_vr"] = (v10 / v60).shift(1).values
        f["ew_skew"] = r.rolling(20, min_periods=15).skew().shift(1).values
        f["ew_kurt"] = r.rolling(20, min_periods=15).kurt().shift(1).values
        f["ew_pent"] = _perm_entropy(r).shift(1).values
        fr.append(f)
    return _put(X, pd.concat(fr, ignore_index=True), ["ew_ac20", "ew_dac", "ew_vr", "ew_skew", "ew_kurt", "ew_pent"])


# ------------------------------------------------------------------ U: необычность состояния
def g_unusual(X, P):
    fr = []
    for t, df in P.groupby("t"):
        df = df.sort_values("d")
        r = df.c.pct_change()
        a = np.log((df.v.replace(0, np.nan)) / df.v.rolling(20, min_periods=10).median())
        b = r.abs() / r.rolling(60, min_periods=30).std()
        c = ((df.h - df.l) / df.c) / ((df.h - df.l) / df.c).rolling(60, min_periods=30).mean()
        zs = []
        for s in (a, b, c):
            zs.append((s - s.rolling(120, min_periods=60).mean()) / s.rolling(120, min_periods=60).std())
        Z = pd.concat(zs, axis=1)
        f = pd.DataFrame({"t": t, "d": df.d.values})
        f["un_max"] = Z.abs().max(axis=1, skipna=True).shift(1).values
        f["un_norm"] = np.sqrt((Z ** 2).sum(axis=1, min_count=2)).shift(1).values
        f["un_norm5"] = np.sqrt((Z ** 2).sum(axis=1, min_count=2)).rolling(5).max().shift(1).values
        fr.append(f)
    return _put(X, pd.concat(fr, ignore_index=True), ["un_max", "un_norm", "un_norm5"])


# ------------------------------------------------------------------ G: паузы в торгах
def g_gaps(X):
    days = pd.Series(np.sort(X.d.unique()))
    prev = (days - days.shift(1)).dt.days
    nxt = (days.shift(-1) - days).dt.days
    m1, m2 = dict(zip(days, prev)), dict(zip(days, nxt))
    X["gp_prev"] = X.d.map(m1).values
    X["gp_next"] = X.d.map(m2).values
    X["gp_long"] = ((X["gp_prev"] >= 4) | (X["gp_next"] >= 4)).astype(float)
    return ["gp_prev", "gp_next", "gp_long"]


# ------------------------------------------------------------------ T: кодирование бумаги и «рейтинг»
def g_target(X):
    X.sort_values(["t", "d"], inplace=True)
    X.reset_index(drop=True, inplace=True)
    y = X["y"]
    g = X.groupby("t")["y"]
    cs = g.cumsum() - y.fillna(0)
    cn = g.cumcount()
    prior = 0.03
    X["te_all"] = (cs + 50 * prior) / (cn + 50)
    dow = X.d.dt.weekday
    g2 = X.assign(dw=dow).groupby(["t", "dw"])["y"]
    cs2 = g2.cumsum() - y.fillna(0)
    cn2 = g2.cumcount()
    X["te_dow"] = (cs2 + 20 * X["te_all"]) / (cn2 + 20)
    el = np.zeros(len(X))
    yv = y.fillna(0).values
    for t, idx in X.groupby("t").indices.items():
        el[idx] = _decay(yv[idx], 250 / np.log(2))
    X["te_elo"] = el / (250 / np.log(2))
    return ["te_all", "te_dow", "te_elo"]


def main():
    os.makedirs(MODELS, exist_ok=True)
    X, Dd, IX, ev, impact = EX.load_all()
    P = EX.prices(Dd)
    FD = CD.feature_cols(X)
    yr = int(X.d.dt.year.max())
    years = [y for y in range(yr - YEARS_BACK + 1, yr + 1) if y > int(X.d.dt.year.min())]
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), part=PART, years=years, runs=[], groups={})
    path = os.path.join(MODELS, f"exp3_report_{PART}.json")

    def add(r):
        if r:
            out["runs"].append(r)
            json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
        return r
    base = add(E2.walk2(X, X, FD, years, "база: свежие годы весомее (как в боевой модели, без бэггинга)", recency=REC))
    if on("a"):
        try:
            E2.fit_hgb.__defaults__ = (1,)
            add(E2.walk2(X, X, FD, years, "шум: база с другим seed", recency=REC, base=base))
        finally:
            E2.fit_hgb.__defaults__ = (0,)
    groups = {}
    builders = [("a", "H сейсмология: аномалии порождают аномалии (Хоукс)", lambda: g_hawkes(X, impact.get("secOf"))),
                ("b", "P эпидемиология: заражение от коррелированных акций", lambda: g_peers(X, P)),
                ("c", "W климат: ранние признаки перелома", lambda: g_warning(X, P)),
                ("d", "U астрономия: необычность состояния бумаги", lambda: g_unusual(X, P)),
                ("d", "G календарь: паузы в торгах", lambda: g_gaps(X)),
                ("e", "T скоринг: частота и рейтинг аномальности бумаги", lambda: g_target(X))]
    for part, name, fn in builders:
        if not (on(part) or PART == "g"):
            continue
        try:
            t1 = time.time()
            cols = fn()
            groups[name] = cols
            out["groups"][name] = {c: round(float(X[c].notna().mean()), 3) for c in cols}
            log("группа", name, out["groups"][name], round(time.time() - t1), "с")
        except Exception as e:  # noqa: BLE001
            log("группа", name, "ОШИБКА", e)
            traceback.print_exc()
    for part, name, _ in builders:
        if on(part) and PART != "g" and name in groups:
            add(E2.walk2(X, X, FD + groups[name], years, "база + " + name, recency=REC, base=base))
    if on("f"):
        add(E2.walk2(X, X, FD, years, "E прогноз погоды: ансамбль бустинга и случайного леса", model="ens", recency=REC, base=base))
    if PART == "g" and groups:
        allc = [c for v in groups.values() for c in v]
        add(E2.walk2(X, X, FD + allc, years, "база + все группы из других областей", recency=REC, base=base))
        os.environ["EXP_BAG"] = "5"
        add(E2.walk2(X, X, FD + allc, years, "все группы + бэггинг 5 (как боевая)", model="bag", recency=REC, base=base))
        add(E2.walk2(X, X, FD, years, "боевая модель: свежие годы + бэггинг 5", model="bag", recency=REC, base=base))
    log("===== ИТОГ ПРОВЕРКИ №3, часть", PART, "(годы", years[0], "–", years[-1], ") =====")
    for r in out["runs"]:
        log(f"{r['name']}: AUC {r['auc']:.4f}, топ-5% {r['top5']:.4f}, топ-5 дня {r['day5']:.4f}, топ-10 дня {r['day10']:.4f}"
            + (f"; Δтоп-5 дня {r['d_day5']:+.4f}, Δтоп-10 дня {r['d_day10']:+.4f}, ΔAUC {r['d_auc']:+.4f}, лучше в {r['yrs_better']}/{r['yrs']} лет" if "d_day5" in r else ""))


if __name__ == "__main__":
    main()
