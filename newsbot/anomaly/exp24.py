# -*- coding: utf-8 -*-
"""Проверка №24: вечерний сканер сделок — отбор бумаг, у которых сделка «вход по закрытию, стоп за уровнем, цель 5R, до 60 дней»
давала плюс на истории ВНЕ выборки. Ликвидные акции (медианный оборот ≥ 10 млн ₽), 2009–2026, лонг и шорт, каждый 2-й день.

Модель: градиентный бустинг предсказывает результат сделки в R (признаки — scan_core.FEATS, только данные до дня входа).
Обучение скользящее: модель года Y учится на годах < Y с разрывом 90 дней (окна сделок не перекрываются с годом проверки).
Отбор: оценка ≥ порога и не больше K лучших в день. Порог выбирается ТОЛЬКО по 2014–2019, проверка — 2020–2026 (и по годам).
Сравнение: все сделки, «по тренду» (как в №23), случайный отбор того же числа сделок.
Если отбор проходит проверку (2020–2026: ≥ +0,10R на сделку, ≥ 5 из 7 лет в плюсе, лучше «по тренду»), модель обучается на всей
истории и сохраняется в ANOM_MODELS/scan_model.pkl вместе с порогом и калибровкой (оценка → средний R, доля целей и стопов) —
её использует бот идей. Иначе файл не создаётся, и вечерний сканер в боте молчит.
"""
import datetime as dt
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
import exp23 as E23  # noqa: E402
import scan_core as SC  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
STEP = int(os.environ.get("EXP_STEP", "2"))
TEST0, SPLIT = 2014, 2020
K = int(os.environ.get("SCAN_K", "3"))


def load():
    tick = TR.universe()
    Dd = TR.load_series("d", tick + ["IMOEX"], "2012-01-01", index=("IMOEX",))
    IX = Dd.pop("IMOEX")
    Dd = TR.stitch_renamed(Dd)
    Dd, IX = TR.backfill(Dd, IX)
    ixd = CD.series(IX).dropna(subset=["c"])
    ixs = pd.Series(ixd.c.to_numpy(float), index=pd.to_datetime(ixd.d))
    D = {}
    for t, rows in Dd.items():
        df = CD.series(rows).dropna(subset=["o", "h", "l", "c"])
        df = df[(df.c > 0) & (df.h >= df.l)].reset_index(drop=True)
        if len(df) >= 400:
            D[t] = df
    return D, ixs


def build(D, ixs):
    from numpy.lib.stride_tricks import sliding_window_view
    X, meta, Rs, kinds = [], [], [], []
    H = SC.HOLD
    for t, df in D.items():
        B = SC.base_arrays(df, ixs)
        o, h, l, c = (df[k].to_numpy(float) for k in ("o", "h", "l", "c"))
        n = len(c)
        r1 = np.abs(np.diff(np.log(c)))
        bad = np.r_[False, r1 > 0.45] | (h / np.maximum(l, 1e-9) > 1.8)
        badc = np.cumsum(bad)
        pad = np.full(H, np.nan)
        win = [sliding_window_view(np.r_[x[1:], pad], H) for x in (o, h, l, c)]
        idx = []
        for i in range(260, n - 2, STEP):
            if not (B["A"][i] > 0) or not (B["val_med"][i] >= 1e7) or not np.isfinite(B["d200"][i]):
                continue
            if badc[min(n - 1, i + H)] - badc[i - 1] > 0:
                continue
            idx.append(i)
        if not idx:
            continue
        Wt = np.stack([np.stack([w[i] for w in win], 0) for i in idx]).astype(np.float32)
        for side in (1, -1):
            S = np.array([SC.level_stop(h, l, c, i, B["A"][i], side)[0] for i in idx])
            R, days = E23.sim(c[idx], side, Wt, S, [(SC.TARGET_R, 1.0)], H, 0)
            for j, i in enumerate(idx):
                X.append(SC.row(B, i, side, S[j]))
                meta.append((t, df.d.iloc[i], side, B["tr"][i] * side > 0 and B["sl"][i] * side > 0))
            Rs.append(R)
    X = np.asarray(X, dtype=np.float32)
    M = pd.DataFrame(meta, columns=["t", "d", "side", "trend"])
    R = np.concatenate(Rs)
    return X, M, R


def hgb():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.04, max_leaf_nodes=31, min_samples_leaf=200, l2_regularization=1.0,
                                         random_state=1)


def walk(X, M, R):
    yrs = M.d.dt.year.values
    p = np.full(len(R), np.nan)
    for Y in range(TEST0, int(yrs.max()) + 1):
        cut = pd.Timestamp(Y, 1, 1) - pd.Timedelta(days=90)
        tr = (M.d.values < cut.to_datetime64())
        te = yrs == Y
        if tr.sum() < 5000 or not te.any():
            continue
        y = np.clip(R[tr], np.quantile(R[tr], 0.01), np.quantile(R[tr], 0.99))
        p[te] = hgb().fit(X[tr], y).predict(X[te])
        log(f"  год {Y}: обучено на {int(tr.sum())}, проверка {int(te.sum())}")
    return p


def select(M, p, thr, k):
    """Маска отобранных: оценка ≥ thr и не больше k лучших в день."""
    ok = np.isfinite(p) & (p >= thr)
    df = pd.DataFrame({"d": M.d.values, "p": np.where(ok, p, -np.inf)})
    rank = df.groupby("d").p.rank(ascending=False, method="first").values
    return ok & (rank <= k)


def summ(R, mask, yrs, lo, hi):
    m = mask & (yrs >= lo) & (yrs <= hi)
    by = {int(y): float(R[m & (yrs == y)].mean()) for y in range(lo, hi + 1) if (m & (yrs == y)).any()}
    return dict(n=int(m.sum()), R=float(R[m].mean()) if m.any() else float("nan"), win=float((R[m] > 0).mean()) if m.any() else float("nan"),
                years=by, pos=sum(1 for v in by.values() if v > 0), per_year=float(m.sum() / max(1, len(by))))


RANK = {"ликвидность (оборот)": 13, "сила к индексу за 20 дн.": 17, "ход за 60 дн.": 5, "ход за 20 дн.": 4, "близость к 60-дн. экстремуму": 6}


def rules(X, M, R, p, yrs, trend, out):
    """№24б: правило «по тренду» (№23) — как выбрать из десятков сигналов дня не больше K: по одному признаку или оценке модели.
    Выбор способа — по 2014–2019, проверка — 2020–2026; контроль — случайный выбор K из трендовых."""
    log("===== №24б: ВЫБОР K ЛУЧШИХ ИЗ ТРЕНДОВЫХ СИГНАЛОВ ДНЯ (выбор по 2014–2019) =====")
    fin = np.isfinite(p)
    keys = dict(RANK)
    res = []
    for k in (3, 5):
        for nm in list(keys) + ["оценка модели", "случайно"]:
            if nm == "оценка модели":
                score = p
            elif nm == "случайно":
                score = np.random.RandomState(7).rand(len(p))
            else:
                score = X[:, keys[nm]].astype(float)
            sc = np.where(trend & fin & np.isfinite(score), score, np.nan)
            mk = select(M, sc, -np.inf, k)
            a, b = summ(R, mk, yrs, TEST0, SPLIT - 1), summ(R, mk, yrs, SPLIT, 2026)
            res.append((nm, k, a, b))
            log(f"  {nm}, ≤{k}/день: 2014–19 {a['R']:+.3f}R (в плюсе {a['pos']}/{len(a['years'])}) → 2020–26 {b['R']:+.3f}R "
                f"({b['per_year']:.0f}/год, прибыльных {b['win']:.0%}, в плюсе {b['pos']}/{len(b['years'])}; по годам "
                + ", ".join(f"{y}: {v:+.2f}" for y, v in b["years"].items()) + ")")
    cand = [r for r in res if r[0] != "случайно" and r[1] == 3]
    nm, k, a, b = max(cand, key=lambda r: r[2]["R"])
    rnd = next(r for r in res if r[0] == "случайно" and r[1] == 3)
    allt = summ(R, trend & fin, yrs, SPLIT, 2026)
    ok = a["R"] > 0.03 and a["pos"] >= 4 and b["R"] >= 0.10 and b["pos"] >= 5 and b["R"] >= rnd[3]["R"] - 0.02
    log("===== ИТОГ №24б =====")
    log(f"  выбран по 2014–2019: «{nm}», ≤{k}/день → 2014–19 {a['R']:+.3f}R; ПРОВЕРКА 2020–26 {b['R']:+.3f}R, лет в плюсе {b['pos']}/{len(b['years'])}; "
        f"случайные 3 из трендовых {rnd[3]['R']:+.3f}R, все трендовые {allt['R']:+.3f}R")
    log(f"  решение: {'ПОДКЛЮЧИТЬ правило' if ok else 'НЕ подключать'}")
    out["rules"] = dict(chosen=nm, k=k, early=a, late=b, random=rnd[3], deploy=bool(ok))
    if ok:
        sel = select(M, np.where(trend & fin, X[:, keys[nm]].astype(float) if nm in keys else p, np.nan), -np.inf, k)
        late = sel & (yrs >= SPLIT)
        rec = dict(type="rule", rank=nm, rank_idx=keys.get(nm), k=k, thr=-1e9, model=None, feats=SC.FEATS,
                   trained=dt.datetime.utcnow().isoformat(timespec="minutes"),
                   late=dict(R=b["R"], per_year=b["per_year"] * STEP, win=b["win"], pos=b["pos"], years=b["years"]),   # в проверке — каждый STEP-й день
                   calib=[dict(lo=-1e9, hi=1e9, n=int(late.sum()), R=float(R[late].mean()), p_tgt=float((R[late] >= SC.TARGET_R - 0.3).mean()),
                               p_stop=float((R[late] <= -0.95).mean()))])
        if nm == "оценка модели":
            yy = np.clip(R, np.quantile(R, 0.01), np.quantile(R, 0.99))
            rec["model"] = hgb().fit(X, yy)
        pickle.dump(rec, open(os.path.join(TR.MODELS, "scan_model.pkl"), "wb"))
        log("  правило сканера сохранено:", os.path.join(TR.MODELS, "scan_model.pkl"))


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    D, ixs = load()
    log(f"бумаг: {len(D)}; {round(time.time() - t0)} с")
    X, M, R = build(D, ixs)
    yrs = M.d.dt.year.values
    log(f"входов (бумага-день-сторона): {len(R)}, {M.t.nunique()} бумаг, {yrs.min()}–{yrs.max()}; средний R {R.mean():+.3f}; {round(time.time() - t0)} с")
    p = walk(X, M, R)
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), n=int(len(R)), grid=[])
    base_all = np.isfinite(p)
    trend = base_all & M.trend.values
    log("===== БАЗА (вне выборки, годы проверки) =====")
    for nm, mk in (("все сделки", base_all), ("по тренду (№23)", trend)):
        a, b = summ(R, mk, yrs, TEST0, SPLIT - 1), summ(R, mk, yrs, SPLIT, 2026)
        log(f"  {nm}: 2014–19 {a['R']:+.3f}R ({a['n']}) → 2020–26 {b['R']:+.3f}R ({b['n']}), лет в плюсе {b['pos']}/{len(b['years'])}")
        out[nm] = dict(early=a, late=b)
    log("===== ОТБОР МОДЕЛЬЮ: порог × не больше K в день (выбор только по 2014–2019) =====")
    best = None
    for thr in (-0.1, 0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4):
        for k in (1, 3, 5, 10):
            mk = select(M, p, thr, k)
            a, b = summ(R, mk, yrs, TEST0, SPLIT - 1), summ(R, mk, yrs, SPLIT, 2026)
            out["grid"].append(dict(thr=thr, k=k, early=a, late=b))
            log(f"  оценка ≥ {thr:+.2f}, ≤{k}/день: 2014–19 {a['R']:+.3f}R ({a['per_year']:.0f}/год, в плюсе {a['pos']}/{len(a['years'])}) → "
                f"2020–26 {b['R']:+.3f}R ({b['per_year']:.0f}/год, прибыльных {b['win']:.0%}, в плюсе {b['pos']}/{len(b['years'])})")
            if a["per_year"] >= 20 and a["pos"] >= 4 and (best is None or a["R"] > best[2]["R"]):
                best = (thr, k, a, b)
    # случайный отбор того же размера (контроль)
    if best:
        thr, k, a, b = best
        mk = select(M, p, thr, k)
        rs = np.random.RandomState(24)
        rnd = []
        for _ in range(200):
            pr = rs.rand(len(p))
            rnd.append(summ(R, select(M, np.where(np.isfinite(p), pr, np.nan), 1 - (mk.sum() / base_all.sum()), k), yrs, SPLIT, 2026)["R"])
        rnd = np.array(rnd)
        tl = summ(R, trend, yrs, SPLIT, 2026)
        ok = b["R"] >= 0.10 and b["pos"] >= 5 and b["R"] > tl["R"]
        log("===== ИТОГ ПРОВЕРКИ №24 =====")
        log(f"  выбран по 2014–2019: оценка ≥ {thr:+.2f}, не больше {k} в день → 2014–19 {a['R']:+.3f}R; ПРОВЕРКА 2020–26 {b['R']:+.3f}R на сделку, "
            f"{b['per_year']:.0f} сделок в год, прибыльных {b['win']:.0%}, лет в плюсе {b['pos']}/{len(b['years'])}; по годам: "
            + ", ".join(f"{y}: {v:+.2f}" for y, v in b["years"].items()))
        log(f"  случайный отбор того же размера: {np.mean(rnd):+.3f}R (95% — до {np.quantile(rnd, 0.95):+.3f}); «по тренду» {tl['R']:+.3f}R")
        log(f"  решение: {'ПОДКЛЮЧИТЬ — проверка пройдена' if ok else 'НЕ подключать — проверка не пройдена'}")
        out["chosen"] = dict(thr=thr, k=k, early=a, late=b, random_mean=float(np.mean(rnd)), random_q95=float(np.quantile(rnd, 0.95)), deploy=bool(ok))
        if ok:
            # калибровка по оценкам вне выборки: корзины оценки → средний R, доля целей и стопов
            mfin = np.isfinite(p)
            edges = np.unique(np.quantile(p[mfin], np.linspace(0, 1, 11)))
            cal = []
            for lo_, hi_ in zip(edges[:-1], edges[1:]):
                mm = mfin & (p >= lo_) & (p <= hi_)
                cal.append(dict(lo=float(lo_), hi=float(hi_), n=int(mm.sum()), R=float(R[mm].mean()),
                                p_tgt=float((R[mm] >= SC.TARGET_R - 0.3).mean()), p_stop=float((R[mm] <= -0.95).mean())))
            sel_late = mk & (yrs >= SPLIT)
            y = np.clip(R, np.quantile(R, 0.01), np.quantile(R, 0.99))
            model = hgb().fit(X, y)
            rec = dict(model=model, feats=SC.FEATS, thr=thr, k=k, calib=cal, trained=dt.datetime.utcnow().isoformat(timespec="minutes"),
                       late=dict(R=b["R"], per_year=b["per_year"], win=b["win"], pos=b["pos"], years=b["years"]),
                       sel_p_tgt=float((R[sel_late] >= SC.TARGET_R - 0.3).mean()), sel_p_stop=float((R[sel_late] <= -0.95).mean()))
            pickle.dump(rec, open(os.path.join(TR.MODELS, "scan_model.pkl"), "wb"))
            log("  модель сканера сохранена:", os.path.join(TR.MODELS, "scan_model.pkl"))
    if not best:
        log("===== ИТОГ ПРОВЕРКИ №24 =====")
        log("  ни один порог не дал на 2014–2019 ≥ 20 сделок в год и ≥ 4 лет в плюсе — НЕ подключать")
    if not os.path.exists(os.path.join(TR.MODELS, "scan_model.pkl")):
        rules(X, M, R, p, yrs, trend, out)
    json.dump(out, open(os.path.join(TR.MODELS, "exp24_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("время", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
