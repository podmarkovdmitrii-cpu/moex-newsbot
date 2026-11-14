# -*- coding: utf-8 -*-
"""Проверка №14: стратегия S1 на истории по 5-минутным свечам ALGOPACK (2020–2026) — точнее, чем по часовым.

Сигналы — те же, что в №12–13 (первый час 10…17, когда рост к индексу ≥ порога эшелона; ликвидные: lval ≥ 7, ≥ 10 млн ₽/день).
Дальше путь цены берётся из ALGOPACK tradestats (5-минутные OHLC, оборот) и obstats (спред лучших цен), по бумаге за день сигнала
и 5 следующих торговых дней (≈ 8 календарных дней). Что точнее, чем в №12–13:
  • порядок внутри часа: исполнение лимита и тейк — по 5-минутным барам; тейк засчитывается только в барах ПОСЛЕ исполнения;
  • издержки: комиссия 0,05% за сторону (тариф «Трейдер») + половина реального спреда на выходе по времени (рыночная заявка);
    вход и тейк — лимитные заявки, спред не платим;
  • строгий вариант: лимит исполнен, только если цена ушла НИЖЕ уровня (а не коснулась), тейк — если цена ушла ВЫШЕ;
  • ёмкость: оборот в баре исполнения — хватило бы на заявку 300 тыс. / 1 млн / 3 млн ₽.
Сравнение — та же стратегия по часовым свечам на тех же сигналах (как в №12).
Нужен секрет ALGOPACK_TOKEN и сертификат RU_CA (как в проверке №6). Кэш: EXP_A5_DIR (по файлу на сигнал).
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.parse

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import algopack as AP  # noqa: E402
import exp9 as E9  # noqa: E402
import exp12 as E12  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
COMM = float(os.environ.get("EXP_COMM", "0.0005"))          # комиссия за сторону
A5 = os.environ.get("EXP_A5_DIR") or os.path.join(TR.DATA, "a5")
BUDGET = float(os.environ.get("EXP_A5_BUDGET_MIN", "200")) * 60
FROM = "2020-01-01"


def fetch_range(ds, t, d0, d1, token):
    rows, cols, start = [], None, 0
    while True:
        q = urllib.parse.urlencode({"from": d0, "till": d1, "start": start})
        c, data = AP._table(AP._get(f"{AP.BASE}/{ds}/{t}.json?{q}", token))
        if not data:
            break
        cols = cols or c
        rows += data
        start += len(data)
        if len(data) % 100 or start > 20000:
            break
    return pd.DataFrame(rows, columns=cols) if cols else pd.DataFrame()


def load_paths(E, token):
    """По каждому сигналу: 5-минутные бары tradestats + спред obstats за день сигнала и 8 следующих календарных дней."""
    os.makedirs(A5, exist_ok=True)
    t_end = time.time() + BUDGET

    def one(i):
        r = E.iloc[i]
        d0 = r.d.date()
        key = f"{r.t}_{d0.isoformat()}"
        p = os.path.join(A5, key + ".json.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                return i, json.load(f)
        if time.time() > t_end:
            return i, None
        d1 = (d0 + dt.timedelta(days=9)).isoformat()
        try:
            tr = fetch_range("tradestats", r.t, d0.isoformat(), d1, token)
            ob = fetch_range("obstats", r.t, d0.isoformat(), d1, token)
        except Exception as e:  # noqa: BLE001
            log("ALGOPACK", key, str(e)[:120])
            return i, None
        if tr.empty:
            out = {"bars": []}
        else:
            tr["k"] = tr["tradedate"].astype(str) + " " + tr["tradetime"].astype(str).str[:5]
            sp = {}
            if not ob.empty and "spread_bbo" in ob.columns:
                ob["k"] = ob["tradedate"].astype(str) + " " + ob["tradetime"].astype(str).str[:5]
                sp = dict(zip(ob.k, pd.to_numeric(ob.spread_bbo, errors="coerce")))
            n = lambda c: pd.to_numeric(tr[c], errors="coerce").tolist() if c in tr.columns else [None] * len(tr)
            out = {"bars": [[k, o, h, l, c, v, sp.get(k)] for k, o, h, l, c, v in zip(tr.k, n("pr_open"), n("pr_high"), n("pr_low"), n("pr_close"), n("val"))]}
        with gzip.open(p, "wt") as f:
            json.dump(out, f, separators=(",", ":"))
        return i, out
    res = {}
    with cf.ThreadPoolExecutor(int(os.environ.get("EXP_A5_WORKERS", "8"))) as ex:
        for k, (i, out) in enumerate(ex.map(one, range(len(E)))):
            if out is not None:
                res[i] = out
            if k % 100 == 0:
                log(f"ALGOPACK 5 мин: {k}/{len(E)}, получено {len(res)}")
    return res


def simulate5(E, paths, strict=False, tp_k=0.25, pb=0.5, hold=5):
    """→ таблица сделок по 5-минутным барам (NaN — лимит не исполнен / нет данных)."""
    rows = []
    for i, r in E.iterrows():
        P = paths.get(i)
        if not P or not P["bars"]:
            rows.append(dict(i=i, status="нет данных"))
            continue
        B = [b for b in P["bars"] if b[2] is not None and b[3] is not None and b[4] is not None]
        d0 = r.d.strftime("%Y-%m-%d")
        t0 = f"{d0} {int(r.c) + 1:02d}:05"           # первый 5-минутный бар, целиком после закрытия часа сигнала
        days = sorted({b[0][:10] for b in B if b[0][:10] > d0 and dt.date.fromisoformat(b[0][:10]).weekday() < 5})
        if len(days) < hold:
            rows.append(dict(i=i, status="мало дней"))
            continue
        last_day = days[hold - 1]
        lim = r.p0 - pb * (r.p0 - r.prev)
        tp = lim * (1 + tp_k * abs(r.mv))
        fi = None
        for j, b in enumerate(B):
            if b[0] < t0 or b[0][:10] != d0:
                continue
            if (b[3] < lim) if strict else (b[3] <= lim):
                fi = j
                break
        if fi is None:
            rows.append(dict(i=i, status="не исполнен"))
            continue
        fill_val = B[fi][5] or 0
        ex, why = None, None
        for b in B[fi + 1:]:
            if b[0][:10] > last_day:
                break
            if (b[2] > tp) if strict else (b[2] >= tp):
                ex, why = tp, "тейк"
                break
        if ex is None:
            lastb = [b for b in B if b[0][:10] <= last_day][-1]
            spr = lastb[6]
            half = (spr / 1e4 / 2) if spr is not None and 0 <= spr < 2000 else 0.0015
            ex, why = lastb[4] * (1 - half), "срок"
        ret = ex / lim - 1 - 2 * COMM
        rows.append(dict(i=i, status="сделка", ret=ret, why=why, fill_val=fill_val))
    return pd.DataFrame(rows).set_index("i")


def summary(E, T, name):
    log(f"[{name}] статусы: " + ", ".join(f"{k} {v}" for k, v in T.status.value_counts().items()))
    m = T.status == "сделка"
    if "ret" not in T.columns or not m.any():
        log(f"[{name}] сделок нет")
        return {}
    x = T.ret[m]
    D = E.loc[x.index]
    daily = pd.Series(x.values, index=D.d.values).groupby(level=0).sum() * 0.1
    eq = daily.cumsum()
    yrs = max(1e-9, (E.d.max() - E.d.min()).days / 365.25)
    by = pd.Series(x.values, index=D.d.dt.year.values)
    out = dict(name=name, signals=int(len(T)), nodata=int((T.status != "сделка").sum() - (T.status == "не исполнен").sum()),
               fill=round(float(m.sum() / max(1, (T.status.isin(["сделка", "не исполнен"])).sum())), 3), n=int(len(x)),
               mean=round(float(x.mean()), 5), med=round(float(x.median()), 5), win=round(float((x > 0).mean()), 3),
               pf=round(float(x[x > 0].sum() / max(1e-9, -x[x < 0].sum())), 2), tp=round(float((T.why[m] == "тейк").mean()), 3),
               cap_year=round(float(daily.sum() / yrs), 4), dd=round(float((eq - eq.cummax()).min()), 4), worst=round(float(x.min()), 4),
               by_year={int(y): dict(n=int(len(g)), mean=round(float(g.mean()), 5), cap=round(float(g.sum() * 0.1), 4)) for y, g in by.groupby(level=0)})
    if "fill_val" in T.columns:
        fv = T.fill_val[m]
        out["capacity"] = {k: round(float((fv >= v).mean()), 3) for k, v in (("300 тыс", 3e5), ("1 млн", 1e6), ("3 млн", 3e6))}
    log(f"[{name}] сигналов {out['signals']} (без данных {out['nodata']}), исполнено {out['fill']:.0%} → сделок {out['n']}: "
        f"{out['mean']:+.2%}/сделку (медиана {out['med']:+.2%}), прибыльных {out['win']:.0%}, PF {out['pf']}, тейк {out['tp']:.0%}, "
        f"капитал {out['cap_year']:+.1%}/год, просадка {out['dd']:.0%}, худшая сделка {out['worst']:+.1%}")
    log(f"[{name}] по годам: " + "; ".join(f"{y}: {v['n']} сд, {v['mean']:+.2%}, кап {v['cap']:+.1%}" for y, v in out["by_year"].items()))
    if "capacity" in out:
        log(f"[{name}] оборот в баре исполнения хватил бы на заявку: " + ", ".join(f"{k} — {v:.0%}" for k, v in out["capacity"].items()))
    return out


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    token = (os.environ.get("ALGOPACK_TOKEN") or "").strip()
    if not token:
        log("нет ALGOPACK_TOKEN — проверка невозможна")
        return
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    E = E12.events(X, Hh, IXH, Dd)
    E = E[(E.d >= pd.Timestamp(FROM)) & (E.s > 0) & (E.lval >= 7)].reset_index(drop=True)
    log("сигналов S1 (рост, оборот ≥ 10 млн ₽/день) с 2020:", len(E), "| по годам", E.d.dt.year.value_counts().sort_index().to_dict())
    P = load_paths(E, token)
    log("5-минутные пути получены по", len(P), "сигналам из", len(E), round(time.time() - t0), "с")
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), n=int(len(E)), comm=COMM, runs=[])
    # та же стратегия по часовым свечам (как в №12) — для сравнения на тех же сигналах
    E12.COST_T = {1: 2 * COMM, 2: 2 * COMM + 0.001, 3: 2 * COMM + 0.002}
    Ph = E12.paths(E)
    rh = E12.simulate(E, Ph, "follow", "0.5", "0.25", None, 5)
    Th = pd.DataFrame({"status": np.where(np.isfinite(rh), "сделка", "не исполнен"), "ret": rh, "why": "?"}, index=E.index)
    out["runs"].append(summary(E, Th, "ЧАСОВЫЕ свечи (как в №12), издержки 0,1/0,2/0,3%"))
    for strict in (False, True):
        T = simulate5(E, P, strict=strict)
        out["runs"].append(summary(E, T, f"5 МИНУТ ALGOPACK{' строго (цена прошла уровень)' if strict else ''}, комиссия 0,05%×2 + полспреда на выходе"))
    for tp_k in (0.5,):
        T = simulate5(E, P, tp_k=tp_k)
        out["runs"].append(summary(E, T, f"5 МИНУТ, тейк {tp_k:.0%} хода"))
    for pb in (0.382,):
        T = simulate5(E, P, pb=pb)
        out["runs"].append(summary(E, T, f"5 МИНУТ, откат {pb:.0%}"))
    json.dump(out, open(os.path.join(TR.MODELS, "exp14_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №14 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
