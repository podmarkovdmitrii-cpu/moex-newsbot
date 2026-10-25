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
import regional as RG  # noqa: E402
import scenario as SC  # noqa: E402

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
            B = candles(path, 620 if kind == "d" else 6, 24 if kind == "d" else 60)
        except Exception as e:  # noqa: BLE001
            log("свечи", t, e)
            return t, []
        if kind == "d":
            return t, [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in B if b["t"] < today]
        return t, [[b["t"][:13], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in B if b["t"][:10] < today]
    D, Hh = {}, {}
    with cf.ThreadPoolExecutor(3) as ex:
        for t, rows in ex.map(lambda t: one(t, "d"), list(tickers) + ["IMOEX"]):
            if rows:
                D[t] = rows + [[today, np.nan, np.nan, np.nan, np.nan, np.nan]]
        for t, rows in ex.map(lambda t: one(t, "h"), list(tickers) + ["IMOEX"]):
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
    ev = [e for e in events(news_state) if e.get("tk") != "@REG"]
    RE = RG.company_events(RG.load_events(news_state))
    W = [CD.news_weight(e, impact) for e in ev]
    hd = CD.hourly_daily({t: v for t, v in Hh.items() if t != "IMOEX"})
    IXH = Hh.pop("IMOEX", None)
    X = CD.build({"sh": {t: v for t, v in D.items() if len(v) > 300}, "ix": {"IMOEX": IX}}, dv, ev, news_tickers=set(meta().get("covered", [])), weights=W, hourly=hd,
                 ix_hourly=IXH, sec_of=impact.get("secOf"), reg_events=RE)
    if X.empty:
        raise RuntimeError("нет свечей по бумагам (Мосбиржа недоступна?)")
    X = X[X.d == pd.Timestamp(today)].copy()
    # для сценариев сделки: бета к индексу и ATR (в долях цены) на утро дня
    bet, atrs = {}, {}
    for t in X["t"]:
        df = SC.series([r for r in D[t] if r[1] == r[1]])
        b = SC.beta_series(df, IX)
        bet[t] = float(b[-1]) if len(b) and b[-1] == b[-1] else np.nan
        h, l, c = df["h"].to_numpy(float), df["l"].to_numpy(float), df["c"].to_numpy(float)
        tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
        atrs[t] = float(np.mean(tr[-14:]) / c[-1]) if len(tr) >= 14 else np.nan
    X["beta"] = X["t"].map(bet)
    X["atr"] = X["t"].map(atrs)
    FD = meta()["feat_daily"]
    for c in FD:
        if c not in X.columns:
            X[c] = np.nan
    for name in ("y", "y_px", "y_up", "y_dn"):
        X["p_" + name] = cal("daily_" + name, model("daily_" + name).predict_proba(X[FD])[:, 1])
    if os.path.exists(os.path.join(MODELS, "daily_dir.pkl")):
        X["p_dir_up"] = model("daily_dir").predict_proba(X[FD])[:, 1]  # P(вверх | аномалия)
    return X.set_index("t")


def dir_acc(name):
    return (meta().get("dir_acc") or {}).get(name) or {}


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
    if os.path.exists(os.path.join(MODELS, "intra_dir.pkl")):
        F["p_dir_up"] = model("intra_dir").predict_proba(F[CI.FEAT])[:, 1]
    else:
        F["p_dir_up"] = np.where(F["cx"] > 0, 0.8, 0.2)
    return F.sort_values("p", ascending=False)


def scenario(feat, side, entry, stop, take=None, hz=10):
    """Сценарии сделки по признакам бумаги на утро (feat — словарь из прогноза дня).
    → вероятности: стоп раньше тейка, тейк раньше стопа, выход по времени; ожидаемый результат в R."""
    m = meta()
    if not os.path.exists(os.path.join(MODELS, "scenario.pkl")) or not m.get("scen_feat"):
        return None
    s = abs(entry - stop) / entry
    if not s:
        return None
    rr = abs(take - entry) / abs(entry - stop) if take else 2.0
    rr_m = min([1.0, 1.5, 2.0, 3.0], key=lambda v: abs(v - rr))
    hz_m = min([5, 10, 20], key=lambda v: abs(v - hz))
    row = {k: (feat.get(k) if feat.get(k) is not None else np.nan) for k in feat}
    atr = row.get("atr") or np.nan
    row.update(side=side, stop=s, stop_atr=s / atr if atr == atr and atr else np.nan, rr=rr_m, hz=hz_m)
    T = SC.add_signed(pd.DataFrame([row]))
    F = m["scen_feat"]
    for f in F:
        if f not in T.columns:
            T[f] = np.nan
    p = model("scenario").predict_proba(T[F].astype(float))[0]
    key = f"{rr_m}|{hz_m}|{pd.cut([row['stop_atr']], [0, 1, 1.5, 2, 3, 99]).astype(str)[0]}"
    b = m.get("scen_base", {}).get(key)
    if b:
        p = SC.BLEND * p + (1 - SC.BLEND) * np.array(b)
    tR = m.get("scen_time_R", {}).get(key, 0.0)
    return dict(p_stop=float(p[0]), p_take=float(p[1]), p_time=float(p[2]), exp_R=float(p[1] * rr_m - p[0] + p[2] * tR), rr=rr_m, hz=hz_m,
                stop_atr=row["stop_atr"], stop_pct=s)
