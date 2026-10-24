# -*- coding: utf-8 -*-
"""Обучение моделей аномалий (запуск: GitHub Actions, workflow anomaly-model.yml, раз в неделю).

1) Данные: дневные свечи всех акций TQBR с 2012 г. и часовые с 2020 г. (Мосбиржа ISS, кэш с докачкой), индекс IMOEX,
   история новостей (news_events.json.gz — 129 тыс. событий 2016–2026 + журнал бота news_log.jsonl), дивиденды divhist.json.
2) Модель «до открытия»: вероятность аномалии дня по каждой бумаге (любая / ценовая / вверх / вниз).
3) Модель «внутри дня»: вероятность, что бумага, ещё не ставшая аномалией к часу c, станет ею до конца дня.
4) Честная проверка: обучение на прошлых годах → прогноз следующего года; калибровка вероятностей по этим прогнозам.
Результат: models/*.pkl + models/meta.json; отчёт — в журнал запуска.
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import core_intra as CI  # noqa: E402

DATA = os.environ.get("ANOM_DATA") or os.path.join(HERE, "data")
MODELS = os.environ.get("ANOM_MODELS") or os.path.join(HERE, "models")
LOCAL = "--local" in sys.argv


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ данные
def universe():
    from common import iss, table
    js = iss("engines/stock/markets/shares/boards/TQBR/securities.json", **{"iss.only": "securities", "securities.columns": "SECID,INSTRID"})
    return [r["SECID"] for r in table(js, "securities") if (r.get("INSTRID") or "EQIN") == "EQIN"]


def fetch(kind, t, since, index=False):
    from common import candles
    path = f"engines/stock/markets/{'index' if index else 'shares'}/{'' if index else 'boards/TQBR/'}securities/{t}/candles.json"
    days = (dt.date.today() - dt.date.fromisoformat(since)).days + 1
    B = candles(path, days, 24 if kind == "d" else 60)
    if kind == "d":
        return [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in B]
    return [[b["t"][:13], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in B]


def load_series(kind, tickers, start, index=()):
    os.makedirs(os.path.join(DATA, kind), exist_ok=True)

    def one(t):
        p = os.path.join(DATA, kind, t + ".json.gz")
        old = []
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                old = json.load(f)
        since = start
        if old:
            since = (dt.date.fromisoformat(old[-1][0][:10]) - dt.timedelta(days=5)).isoformat()
        try:
            new = fetch(kind, t, since, index=t in index)
        except Exception as e:  # noqa: BLE001
            log("нет данных", kind, t, e)
            new = []
        if new:
            cut = new[0][0]
            old = [r for r in old if r[0] < cut] + new
            with gzip.open(p, "wt") as f:
                json.dump(old, f, separators=(",", ":"))
        return t, old
    out = {}
    with cf.ThreadPoolExecutor(4) as ex:
        for k, (t, rows) in enumerate(ex.map(one, tickers)):
            if rows:
                out[t] = rows
            if k % 50 == 0:
                log(kind, k, "/", len(tickers))
    return out


def events_all():
    ev = []
    with gzip.open(os.path.join(HERE, "news_events.json.gz"), "rt") as f:
        for tk, ts, ty, s, rum, off in json.load(f):
            ev.append(dict(tk=tk, ts=ts, ty=ty, s=s, rum=rum, off=off))
    last = max(e["ts"] for e in ev)
    lp = os.environ.get("NEWS_LOG") or os.path.join(os.environ.get("NEWS_STATE", ""), "news_log.jsonl")
    if os.path.exists(lp):
        for line in open(lp, encoding="utf-8"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e["ts"] > last and e["tk"] != "MOEX":
                ev.append(e)
    ev.sort(key=lambda e: e["ts"])
    return ev


# ------------------------------------------------------------------ модели
def hgb(n=200):
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=n, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=300, l2_regularization=1.0)


def metrics(y, p):
    from sklearn.metrics import roc_auc_score, average_precision_score
    k = max(1, len(p) // 20)
    top = np.argsort(-p)[:k]
    return dict(auc=round(float(roc_auc_score(y, p)), 3), ap=round(float(average_precision_score(y, p)), 3), base=round(float(y.mean()), 4), top5=round(float(y[top].mean()), 3))


def calib(p, y):
    """Таблица калибровки по прогнозам «вне выборки»: прогноз → фактическая частота (монотонно)."""
    q = np.unique(np.quantile(p, np.linspace(0, 1, 41)))
    idx = np.clip(np.searchsorted(q, p, side="right") - 1, 0, len(q) - 2)
    xs, ys = [], []
    for i in range(len(q) - 1):
        m = idx == i
        if m.sum() >= 50:
            xs.append(float(p[m].mean())); ys.append(float(y[m].mean()))
    ys = list(np.maximum.accumulate(ys))
    return [xs, ys]


def over_budget(t0):
    return time.time() - t0 > float(os.environ.get("ANOM_BUDGET_MIN", "200")) * 60


def walk(X, feats, target, years, sample_neg=1.0, weight_col=None):
    preds = []
    rep = []
    for Y in years:
        tr = X[(X.d.dt.year < Y)].dropna(subset=[target])
        te = X[(X.d.dt.year == Y)].dropna(subset=[target])
        if te[target].sum() < 20:
            continue
        if sample_neg < 1:
            tr = pd.concat([tr[tr[target] == 1], tr[tr[target] == 0].sample(frac=sample_neg, random_state=1)])
        w = np.where(tr[target] == 1, 1.0, 1.0 / sample_neg)
        clf = hgb().fit(tr[feats], tr[target], sample_weight=w)
        p = clf.predict_proba(te[feats])[:, 1]
        rep.append(dict(year=Y, **metrics(te[target].values, p)))
        preds.append(te[["d", "t"]].assign(p=p, y=te[target].values))
        log(target, Y, rep[-1])
    return pd.concat(preds, ignore_index=True), rep


def hgb_dir():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=100, l2_regularization=1.0)


def direction(D, feats, years, name, meta):
    """Направление аномалии (вверх/вниз к индексу) при условии, что она случится: проверка по годам + финальная модель."""
    from sklearn.metrics import roc_auc_score
    D = D[(D.y == 1) & D.x.notna()].copy()
    D["up"] = (D.x > 0).astype(int)
    rep = []
    for Y in years:
        tr, te = D[D.d.dt.year < Y], D[D.d.dt.year == Y]
        if len(te) < 50 or len(tr) < 300:
            continue
        p = hgb_dir().fit(tr[feats], tr.up).predict_proba(te[feats])[:, 1]
        y = te.up.values
        m = np.abs(p - 0.5) >= 0.15
        rep.append(dict(year=Y, auc=round(float(roc_auc_score(y, p)), 3), acc=round(float(((p > 0.5) == y).mean()), 3), base_up=round(float(y.mean()), 3),
                        acc_conf=round(float(((p[m] > 0.5) == y[m]).mean()), 3) if m.sum() else None, n=int(len(y))))
        log(name, rep[-1])
    meta["report"][name] = rep
    clf = hgb_dir().fit(D[feats], D.up)
    pickle.dump(clf, open(os.path.join(MODELS, name + ".pkl"), "wb"))
    if rep:
        r = pd.DataFrame(rep)
        meta.setdefault("dir_acc", {})[name] = dict(acc=float(r.acc.mean()), acc_conf=float(r.acc_conf.dropna().mean()) if r.acc_conf.notna().any() else None,
                                                     base_up=float(r.base_up.mean()))
    json.dump(meta, open(os.path.join(MODELS, "meta.json"), "w"), ensure_ascii=False)


def fit_final(X, feats, target, sample_neg=1.0):
    tr = X.dropna(subset=[target])
    if sample_neg < 1:
        tr = pd.concat([tr[tr[target] == 1], tr[tr[target] == 0].sample(frac=sample_neg, random_state=1)])
    w = np.where(tr[target] == 1, 1.0, 1.0 / sample_neg)
    return hgb().fit(tr[feats], tr[target], sample_weight=w)


def main():
    t0 = time.time()
    os.makedirs(MODELS, exist_ok=True)
    divh = json.load(open(os.path.join(HERE, "..", "divhist.json"), encoding="utf-8"))
    impact = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8"))
    if LOCAL:
        H0 = json.load(open("/home/claude/research/data/hist_nodiv.json"))
        Dd = H0["sh"]; IX = H0["ix"]["IMOEX"]
        HH0 = json.load(open("/home/claude/news/hourly.json"))
        Hh = HH0["sh"]; IXH = HH0["ix"]["IMOEX"]
    else:
        tick = universe()
        log("акций TQBR:", len(tick))
        Dd = load_series("d", tick + ["IMOEX"], "2012-01-01", index=("IMOEX",))
        IX = Dd.pop("IMOEX")
        # часовые — для 220 самых торгуемых за последние 60 дней
        turn = {t: np.mean([r[5] for r in v[-60:]]) for t, v in Dd.items() if len(v) > 60}
        top = sorted(turn, key=lambda t: -turn[t])[: int(os.environ.get("ANOM_HOURLY_N", "160"))]
        Hh = load_series("h", top + ["IMOEX"], "2020-01-01", index=("IMOEX",))
        IXH = Hh.pop("IMOEX")
    log("дневных рядов", len(Dd), "часовых", len(Hh), round(time.time() - t0), "с")
    ev = events_all()
    W = [CD.news_weight(e, impact) for e in ev]
    covered = sorted({e["tk"] for e in ev})
    hd = CD.hourly_daily(Hh)
    X = CD.build({"sh": Dd, "ix": {"IMOEX": IX}}, divh, ev, news_tickers=set(covered), weights=W, hourly=hd, ix_hourly=IXH, sec_of=impact.get("secOf"))
    X = X[X.d >= "2013-01-01"].reset_index(drop=True)
    FD = CD.feature_cols(X)
    log("признаки дня:", X.shape, "база аномалий", round(X.y.mean(), 4), round(time.time() - t0), "с")
    meta = dict(trained=dt.datetime.utcnow().isoformat(timespec="minutes"), feat_daily=FD, feat_intra=CI.FEAT, covered=covered, report={}, calib={})
    yr = X.d.dt.year.max()
    # ---------- до открытия
    pre_oof = None
    for target in ("y", "y_px", "y_up", "y_dn"):
        years = list(range(yr - 5, yr + 1)) if target == "y" else list(range(yr - 2, yr + 1))
        if over_budget(t0):
            years = []
        P, rep = walk(X, FD, target, years, sample_neg=0.5) if years else (None, [])
        meta["report"]["daily_" + target] = rep
        # калибровка «до открытия» не нужна: на проверке по годам сырые вероятности точнее (logloss 0,1151 против 0,1158)
        clf = fit_final(X, FD, target, sample_neg=0.5)
        if target == "y":
            # прогноз «до открытия» для внутридневной модели: вне выборки там, где он есть, иначе финальной моделью
            pre_all = X[["d", "t"]].assign(p_pre=clf.predict_proba(X[FD])[:, 1])
            if P is not None:
                oof = P[["d", "t", "p"]].rename(columns={"p": "p_oof"})
                pre_all = pre_all.merge(oof, on=["d", "t"], how="left")
                pre_all["p_pre"] = pre_all["p_oof"].fillna(pre_all["p_pre"])
                pre_all = pre_all.drop(columns="p_oof")
            pre_oof = pre_all
        pickle.dump(clf, open(os.path.join(MODELS, f"daily_{target}.pkl"), "wb"))
        json.dump(meta, open(os.path.join(MODELS, "meta.json"), "w"), ensure_ascii=False)
        log("готово: daily", target, round(time.time() - t0), "с")
    direction(X, FD, list(range(yr - 5, yr + 1)), "daily_dir", meta)
    # ---------- внутри дня
    XI, typ = CI.build_train(Hh, IXH, X, ev, pre_oof)
    XI = XI[XI.d.dt.year >= 2017].reset_index(drop=True)
    meta["typ"] = typ
    log("внутридневных строк:", XI.shape, "база", round(XI.y.mean(), 4), round(time.time() - t0), "с")
    for target in ("y", "y_px"):
        yrs = [] if over_budget(t0) else list(range(yr - 2, yr + 1))
        P, rep = walk(XI, CI.FEAT, target, yrs, sample_neg=0.2) if yrs else (None, [])
        meta["report"]["intra_" + target] = rep
        if P is not None:
            meta["calib"]["intra_" + target] = calib(P.p.values, P.y.values)
        clf = fit_final(XI, CI.FEAT, target, sample_neg=0.2)
        pickle.dump(clf, open(os.path.join(MODELS, f"intra_{target}.pkl"), "wb"))
        json.dump(meta, open(os.path.join(MODELS, "meta.json"), "w"), ensure_ascii=False)
        log("готово: intra", target, round(time.time() - t0), "с")
    direction(XI.merge(X[["d", "t", "x"]], on=["d", "t"], how="left"), CI.FEAT, list(range(yr - 3, yr + 1)), "intra_dir", meta)
    json.dump(meta, open(os.path.join(MODELS, "meta.json"), "w"), ensure_ascii=False)
    # ---------- отчёт
    log("===== ИТОГ (проверка на будущих годах, модель не видела эти данные) =====")
    for k, rep in meta["report"].items():
        if not rep:
            continue
        r = pd.DataFrame(rep)
        if k.endswith("_dir"):
            log(f"{k}: направление угадано в {r.acc.mean():.1%} случаев (всегда «вверх» — {r.base_up.mean():.1%}), при уверенном прогнозе — {r.acc_conf.dropna().mean():.1%}")
            continue
        log(f"{k}: AUC {r.auc.mean():.3f}, точность в топ-5% {r.top5.mean():.3f} при базе {r.base.mean():.4f} (в {r.top5.mean() / max(r.base.mean(), 1e-9):.1f} раза выше)")
    log("время", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
