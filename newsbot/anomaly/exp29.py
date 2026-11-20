# -*- coding: utf-8 -*-
"""Проверка №29: сборная модель (S1 + F2 + F4) честно «вперёд» — правила, фильтры и пороги выбираются только по прошлому.

Что было не так в №22–28: правила сделок S1 (откат 50%, тейк 25% хода, 5 дн.), F2 (сразу, 10 дн.), F4 (выкуп падения, тейк 200% хода,
20 дн.) и решение «F2/F4 только с модель-фильтром, F4 — с математикой» выбирались по истории, в которую входили 2020–2026 (№12, №15, №17, №22).
Модель-фильтр училась на прошлых годах, но порог «верхние 30%» брался по оценкам того же года, и не было разрыва перед годом проверки.

Здесь для каждого года проверки Y = 2020…2026 всё решается только по данным до 1 января Y (часовые свечи 2012…Y−1):
  • три слота (по устройству, без подглядывания в результаты): РЛ — покупка роста лимитом на откате 50% хода; РС — покупка роста сразу;
    ПС — покупка падения сразу (шорт не берём — бот только покупает);
  • в каждом слоте перебор тейка (нет / 25 / 50 / 100 / 200% хода) и срока (1 / 5 / 10 / 20 дн.) — выбирается вариант с лучшим средним
    по годам 2012…Y−1 (каждый год с равным весом); если лучший ≤ 0 — слот в году Y выключен;
  • модель-фильтр (HGB): модель года Y учится на сигналах, закрытых до 1 декабря Y−1 (разрыв 30 дн.); нужен ли фильтр и какой набор
    признаков (базовый / + математика) — по результатам «вне выборки» за Y−4…Y−1; порог «верхние 30%» — 70-й процентиль оценок
    за Y−3…Y−1 (как в боте), а не по году проверки;
  • сделки года Y исполняются по 5-минутным свечам ALGOPACK (как в №22–28), издержки 0,1/0,2%.
Портфель: 10% капитала на сделку, не больше 10 позиций (и для сравнения — без лимита, 5% на сделку), реинвест.
Дополнительно — «правила бота, фильтр строго»: старые S1/F2/F4 (правила в выборке), но порог и разрыв фильтра — честные.
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp22 as E22  # noqa: E402
import exp15 as E15  # noqa: E402
import exp28 as E28  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
Y0, Y1 = 2020, 2026
TPS = [None, ("m", 0.25), ("m", 0.5), ("m", 1.0), ("m", 2.0)]
HOLDS = [1, 5, 10, 20]
SLOTS = {"РЛ": ("follow", 0.5, 1), "РС": ("follow", "sig", 1), "ПС": ("fade", "sig", -1)}   # сторона сима, вход, знак события


def load_all():
    """Те же данные, что в №26 (события, признаки, 5-мин бары), но с результатами сетки правил на часовых свечах."""
    import gzip
    import core_daily as CD
    import exp9 as E9
    import exp17 as E17
    import exp18 as E18
    import math_feats as MF
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
    R = {}
    for sl, (side, ent, sg) in SLOTS.items():
        for tp in TPS:
            for hold in HOLDS:
                r = E15.sim_a(E, P, side, ent, tp, None, hold, cache)
                if r is not None:
                    R[(sl, tp, hold)] = np.where(E.s.values == sg, r, np.nan)
    E, dfe = E17.intraday_feats(E, X, H2, IX2, Dd, ev)
    E = E17.flows_feats(E)
    E = E17.model_feats(E, X)
    feats = [f for f in ["c", "acx", "gap_sh", "ixr_c", "nw_day", "wave", "cvol", "fp3", "fe", "p_m", "pdir"] + dfe if f in E.columns and E[f].notna().mean() > 0.05]
    M = MF.build(Dd, IX, CD.series).drop_duplicates(["d", "t"])
    E = E.merge(M, on=["d", "t"], how="left")
    featsM = feats + [c for c in MF.COLS if c in E.columns]
    yr = E.d.dt.year.values
    need = set()
    for i in np.where(yr >= Y0)[0]:
        r = E.iloc[i]
        d0 = r.d.strftime("%Y-%m-%d")
        need.add((r.t, d0))
        for dd, _, _ in E18.trading_days(Dd, r.t, d0, 20):
            need.add((r.t, dd))
    bars = E18.load_bars(need, (os.environ.get("ALGOPACK_TOKEN") or "").strip())
    return E, R, {"base": feats, "math": featsM}, Dd, bars, cost


def yearly_score(r, yr, Y):
    """Среднее по годам 2012…Y−1 (каждый год с равным весом, годы с ≥ 10 сделками)."""
    ms = []
    for y in range(2012, Y):
        x = r[(yr == y) & np.isfinite(r)]
        if len(x) >= 10:
            ms.append(x.mean())
    return (float(np.mean(ms)), len(ms)) if ms else (np.nan, 0)


class Filt:
    def __init__(self, E, feats):
        self.E, self.feats, self.cache = E, feats, {}

    def scores(self, key, r, sg, y):
        """Оценки событий года y моделью, обученной на событиях до 1 декабря y−1 (разрыв 30 дн.)."""
        k = (key, y)
        if k in self.cache:
            return self.cache[k]
        E = self.E
        cut = pd.Timestamp(f"{y}-01-01") - pd.Timedelta(days=30)
        tr = np.isfinite(r) & (E.d.values < cut.to_datetime64())
        te = (E.s.values == sg) & (E.d.dt.year.values == y)
        out = np.full(len(E), np.nan)
        if tr.sum() >= 300 and te.sum() >= 1:
            f = self.feats[key[1]]
            clip = np.clip(r[tr], np.nanquantile(r[tr], 0.02), np.nanquantile(r[tr], 0.98))
            out[te] = E22.hgb().fit(E.loc[tr, f], clip).predict(E.loc[te, f])
        self.cache[k] = out
        return out


def choose(E, R, F, yr, sl, Y):
    """Выбор правила, фильтра и набора признаков для слота sl на год Y — только по данным до Y."""
    sg = SLOTS[sl][2]
    best = None
    for (s_, tp, hold), r in R.items():
        if s_ != sl:
            continue
        sc, ny = yearly_score(r, yr, Y)
        if ny >= 4 and (best is None or sc > best[0]):
            best = (sc, tp, hold)
    if best is None or not best[0] > 0:
        return dict(on=False, why=f"лучший вариант по прошлым годам ≤ 0 ({best[0]:+.2%})" if best else "мало истории")
    sc, tp, hold = best
    r = R[(sl, tp, hold)]
    # фильтр: оценки «вне выборки» за Y−4…Y−1 по каждому набору признаков
    opt = {"all": None}
    wins = {}
    allm = []
    for y in range(Y - 4, Y):
        x = r[(yr == y) & np.isfinite(r)]
        allm.append(x.mean() if len(x) else np.nan)
    opt["all"] = float(np.nanmean(allm))
    for fs in ("base", "math"):
        ps = {y: F.scores((sl, fs, tp, hold), r, sg, y) for y in range(Y - 4, Y)}
        pool = np.concatenate([p[np.isfinite(p)] for p in ps.values()]) if ps else np.array([])
        if len(pool) < 30:
            continue
        thr = np.quantile(pool, 0.7)
        tm = []
        for y, p in ps.items():
            m = np.isfinite(p) & (p >= thr) & np.isfinite(r)
            tm.append(r[m].mean() if m.sum() else np.nan)
        opt[fs] = float(np.nanmean(tm))
        wins[fs] = int(sum(1 for t_, a_ in zip(tm, allm) if t_ == t_ and a_ == a_ and t_ > a_))
    # фильтр берём, только если он лучше «всех сигналов» хотя бы на 0,2 п.п. и не хуже в 3 из 4 лет (иначе выбор между 3 вариантами — шум)
    good = [k for k in ("base", "math") if k in opt and opt[k] == opt[k] and opt[k] - opt["all"] >= 0.002 and wins.get(k, 0) >= 3]
    pick = max(good, key=lambda k: opt[k]) if good else "all"
    if not opt[pick] > 0:
        return dict(on=False, why=f"правило {tp}/{hold} дн.: и с фильтром, и без за 4 года ≤ 0", tp=tp, hold=hold)
    res = dict(on=True, tp=tp, hold=hold, score=sc, filt=pick, opt=opt)
    if pick != "all":
        p_hist = np.concatenate([F.scores((sl, pick, tp, hold), r, sg, y) for y in range(Y - 3, Y)])
        p_hist = p_hist[np.isfinite(p_hist)]
        res["thr"] = float(np.quantile(p_hist, 0.7))
        res["p"] = F.scores((sl, pick, tp, hold), r, sg, Y)
    return res


def tpname(tp):
    return "без тейка" if tp is None else f"тейк {int(tp[1] * 100)}% хода"


def execute(E, yr, sl, ch, Y, Dd, bars, cost, tag):
    rows = []
    side, ent, sg = SLOTS[sl]
    tpk, tpv = (None, None) if ch["tp"] is None else ("move", ch["tp"][1])
    ent5 = "lim" if ent == 0.5 else "sig"
    for i in np.where((yr == Y) & (E.s.values == sg))[0]:
        if ch["filt"] != "all":
            p = ch["p"][i]
            if not (p == p and p >= ch["thr"]):
                continue
        r = E.iloc[i]
        x = E22.trade5(r, Dd, bars, True, ent5, tpk, tpv, ch["hold"], cost[i])
        if x:
            rows.append((tag, r.t, r.d, x[0], pd.Timestamp(x[1])))
    return rows


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    E, R, FE, Dd, bars, cost = load_all()
    yr = E.d.dt.year.values
    log(f"событий {len(E)} (лонг-сигналов роста {int((E.s > 0).sum())}, падения {int((E.s < 0).sum())}), 2012–{yr.max()}; {round(time.time() - t0)} с")
    F = Filt(E, FE)
    rows, plan = [], {}
    log("===== ВЫБОР ПРАВИЛ ПО ГОДАМ (только по данным до 1 января года) =====")
    for Y in range(Y0, Y1 + 1):
        for sl in SLOTS:
            ch = choose(E, R, F, yr, sl, Y)
            if ch["on"]:
                o = ch["opt"]
                fl = {"all": "все сигналы", "base": "фильтр (базовые признаки)", "math": "фильтр (+математика)"}[ch["filt"]]
                log(f"  {Y} {sl}: {tpname(ch['tp'])}, {ch['hold']} дн. (прошлые годы {ch['score']:+.2%}); {fl} — за 4 года: все {o['all']:+.2%}"
                    + "".join(f", {k} {v:+.2%}" for k, v in o.items() if k != "all"))
                rows += execute(E, yr, sl, ch, Y, Dd, bars, cost, sl)
            else:
                log(f"  {Y} {sl}: выключен — {ch['why']}")
            plan[f"{Y} {sl}"] = {k: (v if not isinstance(v, np.ndarray) else None) for k, v in ch.items() if k != "p"}
    T = pd.DataFrame(rows, columns=["strat", "t", "d", "ret", "exit"])
    # правила бота, фильтр строго
    log("===== ПРАВИЛА БОТА (S1/F2/F4), ФИЛЬТР СТРОГО (порог по прошлым 3 годам, разрыв 30 дн.) =====")
    bot = {"S1": ("РЛ", ("m", 0.25), 5, "all"), "F2": ("РС", None, 10, "base"), "F4": ("ПС", ("m", 2.0), 20, "math")}
    rows2 = []
    for Y in range(Y0, Y1 + 1):
        for nm, (sl, tp, hold, fs) in bot.items():
            ch = dict(tp=tp, hold=hold, filt=fs)
            if fs != "all":
                r = R[(sl, tp, hold)]
                sg = SLOTS[sl][2]
                ph = np.concatenate([F.scores((sl, fs, tp, hold), r, sg, y) for y in range(Y - 3, Y)])
                ch["thr"] = float(np.quantile(ph[np.isfinite(ph)], 0.7))
                ch["p"] = F.scores((sl, fs, tp, hold), r, sg, Y)
            rows2 += execute(E, yr, sl, ch, Y, Dd, bars, cost, nm)
    T2 = pd.DataFrame(rows2, columns=["strat", "t", "d", "ret", "exit"])
    out = {"plan": plan}
    for label, TT, keysets in (("ЧЕСТНО ВПЕРЁД", T, (("РЛ (как S1)", ["РЛ"]), ("РС (как F2)", ["РС"]), ("ПС (как F4)", ["ПС"]), ("Портфель", ["РЛ", "РС", "ПС"]))),
                               ("ПРАВИЛА БОТА, ФИЛЬТР СТРОГО", T2, (("S1", ["S1"]), ("F2", ["F2"]), ("F4", ["F4"]), ("Портфель", ["S1", "F2", "F4"])))):
        log(f"===== {label}: 10% на сделку, ≤ 10 позиций; и без лимита по 5% =====")
        E28.W0 = {k: 0.10 for k in ("РЛ", "РС", "ПС", "S1", "F2", "F4")}
        out[label] = {}
        for nm, keys in keysets:
            g = TT[TT.strat.isin(keys)]
            if len(g) < 3:
                log(f"  {nm}: сделок {len(g)} — мало")
                continue
            for vn, kw in (("лимит 10", dict(maxpos=10)), ("без лимита, 5%", dict(maxpos=None, mode="lev", rate=0.20, scale=0.5))):
                rr = E28.run(TT, keys, **kw)
                rets = g.ret.values
                rr.update(mean=float(rets.mean()), win=float((rets > 0).mean()), trades=len(g))
                out[label][f"{nm} / {vn}"] = rr
                log(f"  {nm} / {vn}: сделок {rr['n']} из {len(g)} (пропущено {rr['skipped']}), средняя {rets.mean():+.2%}, прибыльных {(rets > 0).mean():.0%}; "
                    f"капитал ×{rr['final']:.2f}, {rr['cagr']:+.1%} в год, просадка {rr['dd']:.0%}, макс. загрузка {rr['maxload']:.0%}")
                log("     по годам: " + ", ".join(f"{y}: {v:+.1%}" for y, v in rr["years"].items()))
                log(f"КРИВАЯ {label} / {nm} / {vn} | " + " ".join(f"{k}={v}" for k, v in rr["monthly"].items()))
    json.dump(out, open(os.path.join(TR.MODELS, "exp29_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №29 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
