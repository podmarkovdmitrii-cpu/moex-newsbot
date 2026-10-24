# -*- coding: utf-8 -*-
"""Прогноз аномалий в работе бота: до открытия (на день) и внутри дня (ранние признаки).
Модели обучает train.py (workflow anomaly-model.yml); здесь — только загрузка и расчёт."""
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
import core_daily as CD  # noqa: E402
import core_intra as CI  # noqa: E402

MODELS = os.environ.get("ANOM_MODELS") or os.path.join(HERE, "models")
_M = {}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[аномалии-прогноз]", *a, flush=True)


def ready():
    return os.path.exists(os.path.join(MODELS, "meta.json")) and os.path.exists(os.path.join(MODELS, "daily_y.pkl"))


def model(name):
    if name not in _M:
        _M[name] = pickle.load(open(os.path.join(MODELS, name + ".pkl"), "rb"))
    return _M[name]


def meta():
    if "meta" not in _M:
        _M["meta"] = json.load(open(os.path.join(MODELS, "meta.json"), encoding="utf-8"))
    return _M["meta"]


def cal(name, p):
    c = meta().get("calib", {}).get(name)
    if not c or len(c[0]) < 3:
        return p
    return np.interp(p, c[0], c[1])


def events(news_state):
    ev = []
    with gzip.open(os.path.join(HERE, "news_events.json.gz"), "rt") as f:
        for tk, ts, ty, s, rum, off in json.load(f):
            ev.append(dict(tk=tk, ts=ts, ty=ty, s=s, rum=rum, off=off))
    last = max(e["ts"] for e in ev)
    lp = os.path.join(news_state or "", "news_log.jsonl")
    if os.path.exists(lp):
        for line in open(lp, encoding="utf-8"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("ts", 0) > last and e.get("tk") != "MOEX":
                ev.append(e)
    ev.sort(key=lambda e: e["ts"])
    return ev


def daily_probs(tickers, today, divh, upcoming, news_state, impact):
    """Вероятности аномалий на день today по списку бумаг. Возвращает (DataFrame с прогнозами, признаки дня по бумагам)."""
    from common import candles

    def one(t, kind):
        idx = t == "IMOEX"
        path = f"engines/stock/markets/{'index' if idx else 'shares'}/{'' if idx else 'boards/TQBR/'}securities/{t}/candles.json"
        try:
            B = candles(path, 420 if kind == "d" else 5, 24 if kind == "d" else 60)
        except Exception as e:  # noqa: BLE001
            log("свечи", t, e)
            return t, []
        if kind == "d":
            return t, [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in B if b["t"] < today]
        return t, [[b["t"][:13], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in B if b["t"][:10] < today]
    D, Hh = {}, {}
    with cf.ThreadPoolExecutor(4) as ex:
        for t, rows in ex.map(lambda t: one(t, "d"), list(tickers) + ["IMOEX"]):
            if rows:
                D[t] = rows + [[today, np.nan, np.nan, np.nan, np.nan, np.nan]]
        for t, rows in ex.map(lambda t: one(t, "h"), list(tickers)):
            if rows:
                Hh[t] = rows
    IX = D.pop("IMOEX", None)
    if not IX:
        raise RuntimeError("нет свечей индекса")
    dv = {t: list(v) for t, v in divh.items()}
    for r in upcoming or []:
        lb = r.get("last_buy")
        if lb:
            ex = dt.date.fromisoformat(lb) + dt.timedelta(days=1)
            while ex.weekday() >= 5:
                ex += dt.timedelta(days=1)
            dv.setdefault(r["t"], []).append([ex.isoformat(), r.get("amount", 0)])
    ev = events(news_state)
    W = [CD.news_weight(e, impact) for e in ev]
    hd = CD.hourly_daily(Hh)
    X = CD.build({"sh": {t: v for t, v in D.items() if len(v) > 300}, "ix": {"IMOEX": IX}}, dv, ev, news_tickers=set(meta().get("covered", [])), weights=W, hourly=hd)
    X = X[X.d == pd.Timestamp(today)].copy()
    FD = meta()["feat_daily"]
    for c in FD:
        if c not in X.columns:
            X[c] = np.nan
    for name in ("y", "y_px", "y_up", "y_dn"):
        X["p_" + name] = cal("daily_" + name, model("daily_" + name).predict_proba(X[FD])[:, 1])
    return X.set_index("t")


def reasons(r):
    out = []
    if r.get("vr1", 0) and r["vr1"] >= 2.5:
        out.append(f"вчера оборот ×{r['vr1']:.1f} к обычному")
    if r.get("nw_n", 0) >= 1:
        out.append(f"новости до открытия: {int(r['nw_n'])}" + (" (весомые)" if r.get("nw_wmax", 0) >= 0.005 else ""))
    if r.get("an20", 0) >= 2:
        out.append(f"{int(r['an20'])} аномалии за 20 дней")
    if r.get("absx1", 0) and r["absx1"] >= 0.04:
        out.append(f"вчера сильное движение к индексу ({r['absx1'] * 100:.0f}%)")
    if r.get("h_ev_ret") is not None and abs(r.get("h_ev_ret") or 0) >= 0.015:
        out.append(f"вечерняя сессия {r['h_ev_ret'] * 100:+.1f}%")
    if r.get("brk20", 0):
        out.append("пробой 20-дневного " + ("максимума" if r["brk20"] > 0 else "минимума"))
    if r.get("to_ex", 60) <= 5:
        out.append(f"отсечка через {int(r['to_ex'])} торг. дн.")
    if r.get("sq", 1) and r.get("sq", 1) < 0.5:
        out.append("затишье перед движением (волатильность сжата)")
    if r.get("tier") == 3:
        out.append("3-й эшелон")
    return out


def intraday(md, ix_chg, hour, today_df, prev_val, news_hours):
    """Вероятность, что бумага станет аномалией до конца дня (бумаги, уже ставшие аномалией, пропускаются)."""
    p_pre = today_df["p_y"].to_dict() if "p_y" in today_df.columns else {}
    F = CI.build_live(md, ix_chg, hour, today_df, p_pre, meta().get("typ", {}), prev_val, news_hours)
    if F.empty:
        return F
    F["p"] = cal("intra_y", model("intra_y").predict_proba(F[CI.FEAT])[:, 1])
    F["p_px"] = cal("intra_y_px", model("intra_y_px").predict_proba(F[CI.FEAT])[:, 1])
    return F.sort_values("p", ascending=False)
