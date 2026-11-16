# -*- coding: utf-8 -*-
"""Модель-фильтр аномалий для виртуального счёта (проверки №17–18): обучение на всей истории и сохранение в ANOM_MODELS.

  filter_R4.pkl — аномальное падение: покупка против движения, тейк +200% хода, до 20 торговых дней;
  filter_R2.pkl — аномальный рост: покупка сразу, держать 10 торговых дней.
В файле: модель (градиентный бустинг, предсказывает результат сделки), список признаков и порог — 70-й процентиль оценок
«вне выборки» за последние 3 года (каждый год — модель, обученная только на прошлых годах): сигнал с оценкой не ниже порога
попадает в верхние 30%, как в проверке №18. Запускается после еженедельного обучения модели аномалий и отдельно (anomaly-filter-fit.yml).
"""
import datetime as dt
import gzip
import json
import os
import pickle
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp9 as E9  # noqa: E402
import exp15 as E15  # noqa: E402
import exp17 as E17  # noqa: E402
import exp18 as E18  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
FEAT_INTRA = ["c", "acx", "gap_sh", "ixr_c", "nw_day", "wave", "cvol", "fp3", "fe", "p_m", "pdir"]


def hgb():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0, random_state=1)


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
    cache = {"cost": E15.cost_of(E.lval.values)}
    E, dfe = E17.intraday_feats(E, X, H2, IX2, Dd, ev)
    E = E17.flows_feats(E)
    E = E17.model_feats(E, X)
    feats = [f for f in FEAT_INTRA + dfe if f in E.columns and E[f].notna().mean() > 0.05]
    yr = E.d.dt.year.values
    Y1 = int(yr.max())
    meta = dict(trained=dt.datetime.utcnow().isoformat(timespec="minutes"), n=int(len(E)), feats=feats, ways={})
    for key, (nm, w) in zip(("R4", "R2"), E18.WAYS.items()):
        R = np.where(E.s.values == w["side"], E15.sim_a(E, P, *w["cfg"], cache), np.nan)
        ok = np.isfinite(R)
        oof = []
        for Y in range(Y1 - 2, Y1 + 1):  # оценки «вне выборки» за последние 3 года — для порога верхних 30%
            tr, te = ok & (yr < Y), (E.s.values == w["side"]) & (yr == Y)
            if tr.sum() < 300 or not te.any():
                continue
            clip = np.clip(R[tr], np.nanquantile(R[tr], 0.02), np.nanquantile(R[tr], 0.98))
            oof += list(hgb().fit(E.loc[tr, feats], clip).predict(E.loc[te, feats]))
        clip = np.clip(R[ok], np.nanquantile(R[ok], 0.02), np.nanquantile(R[ok], 0.98))
        m = hgb().fit(E.loc[ok, feats], clip)
        thr = float(np.quantile(oof, 0.7)) if oof else None
        rec = dict(model=m, feats=feats, thr=thr, side=w["side"], tp=w["tp"], hold=w["hold"], name=nm,
                   trained=meta["trained"], n_train=int(ok.sum()), oof_n=len(oof))
        pickle.dump(rec, open(os.path.join(TR.MODELS, f"filter_{key}.pkl"), "wb"))
        meta["ways"][key] = dict(name=nm, thr=thr, n_train=int(ok.sum()), oof_n=len(oof))
        log(f"фильтр {key} ({nm}): обучено на {int(ok.sum())} сделках, порог верхних 30% = {thr if thr is None else round(thr, 4)} (по {len(oof)} оценкам вне выборки)")
    json.dump(meta, open(os.path.join(TR.MODELS, "filter_meta.json"), "w"), ensure_ascii=False, indent=1)
    log("модель-фильтр сохранена:", TR.MODELS, round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
