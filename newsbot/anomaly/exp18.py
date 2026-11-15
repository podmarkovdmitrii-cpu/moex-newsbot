# -*- coding: utf-8 -*-
"""Проверка №18: модель-фильтр из №17 на 5-минутных свечах ALGOPACK (2020–2026) для двух способов отработки аномалий.

  R4 — аномальное ПАДЕНИЕ ликвидной бумаги: покупка против движения сразу после часа сигнала, тейк +200% хода, без стопа,
       выход не позже закрытия 20-го торгового дня;
  R2 — аномальный РОСТ: покупка сразу после часа сигнала, без тейка и стопа, выход на закрытии 10-го торгового дня.
Модель-фильтр — как в №17: градиентный бустинг по всем признакам, известным в момент сигнала (≈100), учится на прошлых годах
(результаты сделок по часовым свечам), каждый год заново; берём 30% сигналов года с лучшей оценкой модели.
Что точнее, чем в №17: вход — по цене открытия первого 5-минутного бара ПОСЛЕ закрытия часа сигнала (а не по закрытию часа),
тейк — по 5-минутным максимумам после входа, выход по сроку — закрытие последнего 5-минутного бара дня. Бары вне дневного
диапазона ISS (0,7–1,3) — сбой данных, отбрасываются. Издержки те же, что в №15/№17 (0,1% / 0,2% по ликвидности).
Сравнение: все сигналы против отобранных моделью, по 5-минутным и по часовым свечам на одних и тех же сделках.
Нужны ALGOPACK_TOKEN и RU_CA; кэш EXP_A18_DIR (по файлу на бумагу-день).
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp9 as E9  # noqa: E402
import exp15 as E15  # noqa: E402
import exp17 as E17  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
A18 = os.environ.get("EXP_A18_DIR") or os.path.join(TR.DATA, "a18")
BUDGET = float(os.environ.get("EXP_A18_BUDGET_MIN", "200")) * 60
FROM5 = int(os.environ.get("EXP_FROM5", "2020"))
TOPQ = float(os.environ.get("EXP_TOPQ", "0.7"))
WAYS = {
    "R4 падение: покупка против, тейк 200% хода, до 20 дн.": dict(side=-1, cfg=("fade", "sig", ("m", 2.0), None, 20), tp=2.0, hold=20),
    "R2 рост: покупка сразу, держать 10 дн.": dict(side=1, cfg=("follow", "sig", None, None, 10), tp=None, hold=10),
}


# ------------------------------------------------------------------ 5-минутные бары
def load_bars(need, token):
    """need — множество (тикер, 'YYYY-MM-DD'); → {(t, d): [[ЧЧ:ММ, o, h, l, c], ...]}; кэш по файлу на бумагу-день."""
    os.makedirs(A18, exist_ok=True)
    have, miss = {}, []
    for t, d in sorted(need):
        p = os.path.join(A18, f"{t}_{d}.json.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                have[(t, d)] = json.load(f)
        else:
            miss.append((t, d))
    log(f"5-минутные бары: нужно {len(need)} бумаго-дней, в кэше {len(have)}, докачать {len(miss)}")
    if not token or not miss:
        return have
    runs = []
    for t, grp in itertools.groupby(miss, key=lambda x: x[0]):
        ds = [dt.date.fromisoformat(d) for _, d in grp]
        s = e = ds[0]
        for d in ds[1:]:
            if (d - e).days <= 4 and (d - s).days <= 30:
                e = d
            else:
                runs.append((t, s, e))
                s = e = d
        runs.append((t, s, e))
    t_end = time.time() + BUDGET
    import exp14 as E14

    def one(run):
        t, s, e = run
        if time.time() > t_end:
            return None
        try:
            df = E14.fetch_range("tradestats", t, s.isoformat(), e.isoformat(), token)
        except Exception as ex:  # noqa: BLE001
            log("ALGOPACK", t, s, str(ex)[:120])
            return None
        res = {}
        if not df.empty:
            n = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df.columns else pd.Series(np.nan, index=df.index)
            o, h, l, c = n("pr_open"), n("pr_high"), n("pr_low"), n("pr_close")
            for d, idx in df.groupby(df["tradedate"].astype(str)).groups.items():
                res[d] = [[str(df.tradetime[i])[:5], float(o[i]), float(h[i]), float(l[i]), float(c[i])] for i in idx
                          if all(x == x and x > 0 for x in (o[i], h[i], l[i], c[i]))]
        out = {}
        d = s
        while d <= e:
            k = d.isoformat()
            if (t, k) in need:
                bars = res.get(k, [])
                with gzip.open(os.path.join(A18, f"{t}_{k}.json.gz"), "wt") as f:
                    json.dump(bars, f, separators=(",", ":"))
                out[(t, k)] = bars
            d += dt.timedelta(days=1)
        return out
    with cf.ThreadPoolExecutor(int(os.environ.get("EXP_A18_WORKERS", "8"))) as ex:
        for k, out in enumerate(ex.map(one, runs)):
            if out:
                have.update(out)
            if k % 300 == 0:
                log(f"  ALGOPACK 5 мин: {k}/{len(runs)} запросов, бумаго-дней {len(have)}")
    log(f"5-минутные бары: есть {len(have)} из {len(need)} бумаго-дней")
    return have


def trading_days(Dd, t, d0, n):
    """Торговые дни бумаги после d0 по дневным свечам ISS: [(дата, low, high)] — n штук."""
    out = []
    for r in Dd.get(t, []):
        if r[0][:10] > d0 and dt.date.fromisoformat(r[0][:10]).weekday() < 5:
            out.append((r[0][:10], float(r[3]), float(r[2])))
            if len(out) >= n:
                break
    return out


def sim5(E, Dd, bars, way, cost):
    """Сделка по 5-минутным барам; NaN — нет данных (меньше дней с барами, чем нужно, или нет бара входа)."""
    res = np.full(len(E), np.nan)
    why = {"нет бара входа": 0, "нет 5-мин данных за срок": 0}
    for i, r in E.iterrows():
        d0 = r.d.strftime("%Y-%m-%d")
        days = trading_days(Dd, r.t, d0, way["hold"])
        if len(days) < way["hold"]:
            continue
        band = {dd: (lo, hi) for dd, lo, hi in days}
        D0 = None
        for x in Dd.get(r.t, []):
            if x[0][:10] == d0:
                D0 = (float(x[3]), float(x[2]))
                break
        if D0:
            band[d0] = D0
        seq = []
        miss = 0
        for dd in [d0] + [x[0] for x in days]:
            b = bars.get((r.t, dd))
            if not b:
                miss += dd != d0
                continue
            lo, hi = band.get(dd, (0, 1e18))
            seq += [(dd,) + tuple(x) for x in b if x[3] >= 0.7 * lo and x[2] <= 1.3 * hi]
        if miss > way["hold"] // 4:
            why["нет 5-мин данных за срок"] += 1
            continue
        t0 = f"{int(r.c) + 1:02d}:05"
        k0 = next((k for k, x in enumerate(seq) if x[0] == d0 and x[1] >= t0), None)
        if k0 is None:
            k0 = next((k for k, x in enumerate(seq) if x[0] > d0), None)  # сигнал в последний час — вход на открытии следующего дня
        if k0 is None:
            why["нет бара входа"] += 1
            continue
        e = seq[k0][2]
        tp = e * (1 + way["tp"] * abs(r.mv)) if way["tp"] else None
        last = days[-1][0]
        ex = None
        for x in seq[k0 + 1:]:
            if x[0] > last:
                break
            if tp is not None and x[3] >= tp:
                ex = tp
                break
        if ex is None:
            lb = [x for x in seq if x[0] <= last]
            ex = lb[-1][5]
        res[i] = ex / e - 1 - cost[i]
    return res, why


def stats(r):
    r = r[np.isfinite(r)]
    if not len(r):
        return dict(n=0)
    w, l_ = r[r > 0], r[r <= 0]
    return dict(n=int(len(r)), mean=float(r.mean()), med=float(np.median(r)), win=float((r > 0).mean()),
                pf=float(w.sum() / max(1e-9, -l_.sum())), worst=float(r.min()), best=float(r.max()))


def fmt(s):
    if not s.get("n"):
        return "нет сделок"
    return (f"{s['n']} сд, {s['mean']:+.2%}/сд (медиана {s['med']:+.2%}), прибыльных {s['win']:.0%}, PF {s['pf']:.2f}, "
            f"худшая {s['worst']:+.1%}, лучшая {s['best']:+.1%}")


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
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
    RH = {nm: np.where(E.s.values == w["side"], E15.sim_a(E, P, *w["cfg"], cache), np.nan) for nm, w in WAYS.items()}
    E, dfe = E17.intraday_feats(E, X, H2, IX2, Dd, ev)
    E = E17.flows_feats(E)
    E = E17.model_feats(E, X)
    feats = ["c", "acx", "gap_sh", "ixr_c", "nw_day", "wave", "cvol", "fp3", "fe", "p_m", "pdir"] + dfe
    feats = [f for f in feats if f in E.columns and E[f].notna().mean() > 0.05]
    yr = E.d.dt.year.values
    log(f"ликвидных сигналов {len(E)}, признаков {len(feats)}, {round(time.time() - t0)} с")
    from sklearn.ensemble import HistGradientBoostingRegressor
    PRED = {}
    for nm, w in WAYS.items():
        R = RH[nm]
        ok = np.isfinite(R)
        p = np.full(len(E), np.nan)
        for Y in range(FROM5, int(yr.max()) + 1):
            tr = ok & (yr < Y)
            te = (E.s.values == w["side"]) & (yr == Y)
            if tr.sum() < 300 or te.sum() < 10:
                continue
            clip = np.clip(R[tr], np.nanquantile(R[tr], 0.02), np.nanquantile(R[tr], 0.98))
            m = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0,
                                              random_state=1).fit(E.loc[tr, feats], clip)
            p[te] = m.predict(E.loc[te, feats])
        PRED[nm] = p
    # какие бумаго-дни нужны
    need = set()
    for nm, w in WAYS.items():
        m = (E.s.values == w["side"]) & (yr >= FROM5)
        for i in np.where(m)[0]:
            r = E.iloc[i]
            d0 = r.d.strftime("%Y-%m-%d")
            need.add((r.t, d0))
            for dd, _, _ in trading_days(Dd, r.t, d0, w["hold"]):
                need.add((r.t, dd))
    bars = load_bars(need, (os.environ.get("ALGOPACK_TOKEN") or "").strip())
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), ways={})
    for nm, w in WAYS.items():
        side = (E.s.values == w["side"]) & (yr >= FROM5)
        r5, why = sim5(E[side], Dd, bars, w, cost)
        R5 = np.full(len(E), np.nan)
        R5[np.where(side)[0]] = r5
        p = PRED[nm]
        top = np.zeros(len(E), bool)
        for Y in range(FROM5, int(yr.max()) + 1):
            my = side & (yr == Y) & np.isfinite(p)
            if my.sum():
                top[my] = p[my] >= np.quantile(p[my], TOPQ)
        has = side & np.isfinite(R5) & np.isfinite(p)
        log(f"===================== {nm} =====================")
        log(f"сигналов {FROM5}–2026: {int(side.sum())}; с 5-минутными данными и оценкой модели: {int(has.sum())}; без данных: {why}")
        res = {}
        for lab, mm in (("все сигналы", has), (f"отобраны моделью (верхние {1 - TOPQ:.0%})", has & top), ("остальные", has & ~top)):
            a5, ah = stats(R5[mm]), stats(RH[nm][mm])
            res[lab] = dict(m5=a5, h=ah)
            log(f"  {lab}: 5 мин — {fmt(a5)}")
            log(f"  {' ' * len(lab)}  часовые — {fmt(ah)}")
        log("  по годам (5 мин): " + "; ".join(
            f"{Y}: все {np.nanmean(R5[has & (yr == Y)]):+.1%} ({int((has & (yr == Y)).sum())}), модель {np.nanmean(R5[has & top & (yr == Y)]):+.1%} "
            f"({int((has & top & (yr == Y)).sum())})" for Y in range(FROM5, int(yr.max()) + 1) if (has & (yr == Y)).sum()))
        better = sum(1 for Y in range(FROM5, int(yr.max()) + 1)
                     if (has & top & (yr == Y)).sum() >= 3 and np.nanmean(R5[has & top & (yr == Y)]) > np.nanmean(R5[has & (yr == Y)]))
        nyr = sum(1 for Y in range(FROM5, int(yr.max()) + 1) if (has & top & (yr == Y)).sum() >= 3)
        log(f"  модель лучше всех сигналов в {better} из {nyr} лет (5 мин)")
        # концентрация: доля результата самых крупных дней
        mt = has & top
        if mt.sum():
            dd = pd.Series(R5[mt], index=E.d.values[mt]).groupby(level=0).sum().sort_values(ascending=False)
            tot = R5[mt].sum()
            log(f"  отобранные: разных дней {len(dd)}; 3 лучших дня дают {dd.iloc[:3].sum() / tot:.0%} суммарного результата "
                f"({', '.join(str(pd.Timestamp(x).date()) for x in dd.index[:3])}); без 2022 г.: "
                f"{np.nanmean(R5[mt & (yr != 2022)]):+.2%}/сд против {np.nanmean(R5[has & (yr != 2022)]):+.2%} у всех")
            worst = np.argsort(np.where(mt, R5, np.inf))[:5]
            log("  худшие отобранные: " + ", ".join(f"{E.t[i]} {E.d[i].date()} {R5[i]:+.1%}" for i in worst if np.isfinite(R5[i])))
        out["ways"][nm] = dict(res=res, better_years=better, years=nyr)
        json.dump(out, open(os.path.join(TR.MODELS, "exp18_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №18 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
