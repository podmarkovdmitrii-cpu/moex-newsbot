# -*- coding: utf-8 -*-
"""Проверка №10: помогают ли торги выходного дня (сессии Мосбиржи по субботам и воскресеньям, с марта 2025) прогнозу
аномалий в понедельник — и насколько прогноз, сделанный в воскресенье в 19:00, точнее пятничного.

Только понедельники после выходных с торгами. Варианты (модель — как боевая, обучение «вне выборки» по годам):
  ПЯТНИЦА   — прогноз в пятницу в 19:00: свеча пятницы, новости до 19:00 пятницы;
  ВОСКР     — воскресенье в 19:00: то же + новости пятничного вечера и выходных;
  ВОСКР+В   — ВОСКР + признаки торгов выходных в модели (ход к закрытию пятницы, размах, оборот к будням); у будних дней — пусто;
  ВОСКР+В2  — ВОСКР, затем поправка второй ступенью (логистическая регрессия на понедельниках: вероятность модели + признаки
              выходных), обучение только на прошлых выходных (по кварталам);
  УТРО      — как сейчас в понедельник утром (новости до 09:55), для сравнения.
Плюс связь: частота аномалий понедельника и «факт к прогнозу модели» по квинтилям хода и оборота выходных.
Шум: тот же вариант с другим seed. Внедрять — только если прирост больше шума и держится в большинстве кварталов.
"""
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp2 as E2  # noqa: E402
import exp5 as E5  # noqa: E402
import exp9 as E9  # noqa: E402
import core_daily as CD  # noqa: E402
import train as TR  # noqa: E402
import live as LV  # noqa: E402

log = TR.log
MSK = dt.timedelta(hours=3)
WSTART = os.environ.get("EXP_WEEKEND_FROM", "2025-03-01")
WCOLS = ["w_ret", "w_abs", "w_max", "w_range", "w_vr", "w_days"]


def news_view_fn(X, Dd, IX, ev, W, ok_fn, name):
    """Как exp2.news_view, но с произвольным правилом «новость уже известна»."""
    keep_e, keep_w = [], []
    for e, w in zip(ev, W):
        if ok_fn(dt.datetime.utcfromtimestamp(e["ts"]) + MSK):
            keep_e.append(e)
            keep_w.append(w)
    days_by = {t: CD.series(a)["d"].values for t, a in Dd.items()}
    days_by["IMOEX"] = CD.series(IX)["d"].values
    nd = CD.news_daily(keep_e, days_by, keep_w)
    rows = [dict(t=tk, d=pd.Timestamp(d), **{"nw_" + k: a.get(k, 0.0) for k in E2.NEWS_COLS_N}) for (tk, d), a in nd.items()]
    N = pd.DataFrame(rows)
    M = N[N.t == "IMOEX"].rename(columns={"nw_n": "m_n", "nw_n_risk": "m_risk", "nw_n_macro": "m_macro"})[["d", "m_n", "m_risk", "m_macro"]]
    cols = ["nw_" + k for k in E2.NEWS_COLS_N]
    Y = X.drop(columns=[c for c in cols + ["m_n", "m_risk", "m_macro"] if c in X.columns])
    Y = Y.merge(N[N.t != "IMOEX"][["t", "d"] + cols], on=["t", "d"], how="left").merge(M, on="d", how="left")
    for c in cols + ["m_n", "m_risk", "m_macro"]:
        Y[c] = Y[c].fillna(0.0)
    Y["nw_surprise"] = Y["nw_n"] / (Y["nw_prev60m"] + 0.05)
    Y = Y.sort_values(["t", "d"]).reset_index(drop=True)
    assert len(Y) == len(X)
    log("новости как в", name, ": доля дней с новостями", round(float((Y.nw_n > 0).mean()), 4))
    return Y


def ok_sun19(when):
    hm = (when.hour, when.minute)
    wd = when.weekday()
    if wd < 5 and (9, 55) <= hm < (19, 0):
        return True                      # как вечером в будни
    return (wd == 4 and hm >= (19, 0)) or wd == 5 or (wd == 6 and hm < (19, 0))  # вечер пятницы и выходные до 19:00 воскресенья


def weekend_table(Dd):
    """(t, понедельник) → признаки торгов выходных перед ним."""
    rows = []
    n_we = 0
    for t, R in Dd.items():
        R = [r for r in R if r[4] == r[4] and r[4]]
        days = [dt.date.fromisoformat(r[0][:10]) for r in R]
        n_we += sum(1 for d in days if d.weekday() >= 5)
        mons = sorted({d for d in days if d.weekday() == 0 and d.isoformat() >= WSTART})
        for m in mons:
            f = LV.weekend_feats([r for r in R if r[0][:10] <= m.isoformat()], m.isoformat())
            if not f or (m - dt.timedelta(days=3)) not in days:   # пятница перед понедельником — торговый день (без праздников)
                continue
            rows.append(dict(t=t, d=pd.Timestamp(m), w_ret=f["w_ret"], w_abs=abs(f["w_ret"]), w_max=f["w_max"], w_range=abs(f["w_max"]),
                             w_vr=f["w_vr"], w_days=f["w_days"]))
    log("баров выходного дня в кэше:", n_we, "| понедельников с торгами выходных:", len({r["d"] for r in rows}), "| строк:", len(rows))
    return pd.DataFrame(rows)


def score_mon(P, name, by="q"):
    r = E9.score(P, name)
    q = {}
    for k, g in P.groupby(P.d.dt.to_period("Q")):
        dd = [x for _, x in g.groupby("d") if len(x) >= 20]
        if dd:
            q[str(k)] = round(float(np.mean([x.nlargest(5, "p").y.mean() for x in dd])), 4)
    r["top5_by_q"] = q
    log(f"[{name}] топ-5 по кварталам: " + ", ".join(f"{k}: {v:.1%}" for k, v in q.items()))
    return r


def delta(A, B, name):
    """Разница топ-5 по дням B − A на одних и тех же понедельниках + 90% интервал (бутстрэп по дням) и доля кварталов, где лучше."""
    M = A.merge(B, on=["d", "t", "y"], suffixes=("_a", "_b"))
    dd = []
    for d, g in M.groupby("d"):
        if len(g) >= 20:
            dd.append((d, g.nlargest(5, "p_a").y.mean(), g.nlargest(5, "p_b").y.mean()))
    if not dd:
        return {}
    D = pd.DataFrame(dd, columns=["d", "a", "b"])
    diff = (D.b - D.a).to_numpy()
    rs = np.random.RandomState(7)
    boot = [rs.choice(diff, len(diff)).mean() for _ in range(2000)]
    qd = D.assign(q=D.d.dt.to_period("Q")).groupby("q").apply(lambda g: (g.b - g.a).mean(), include_groups=False)
    out = dict(name=name, days=len(D), delta=round(float(diff.mean()), 4), lo=round(float(np.percentile(boot, 5)), 4),
               hi=round(float(np.percentile(boot, 95)), 4), better_q=f"{int((qd > 0).sum())}/{len(qd)}")
    log(f"Δ {name}: топ-5 {out['delta']:+.4f} (90%: {out['lo']:+.4f}…{out['hi']:+.4f}), лучше в {out['better_q']} кварталов, понедельников {out['days']}")
    return out


def stack(P, WK, seed=0):
    """Вторая ступень по понедельникам: логистическая регрессия [logit p, признаки выходных], обучение на прошлых кварталах."""
    from sklearn.linear_model import LogisticRegression
    M = P.merge(WK, on=["t", "d"], how="inner").dropna(subset=["w_ret"])
    M["lp"] = np.log(np.clip(M.p, 1e-4, 1 - 1e-4) / (1 - np.clip(M.p, 1e-4, 1 - 1e-4)))
    M["w_vr"] = np.log1p(M.w_vr.fillna(0).clip(0, 20))
    M["q"] = M.d.dt.to_period("Q")
    feats = ["lp", "w_abs", "w_range", "w_vr", "w_days"]
    out = []
    qs = sorted(M.q.unique())
    for i, q in enumerate(qs):
        tr = M[M.q < q]
        te = M[M.q == q]
        if tr.y.sum() < 40 or i < 2:
            continue
        mu, sd = tr[feats].mean(), tr[feats].std().replace(0, 1)
        clf = LogisticRegression(C=0.5, max_iter=500, random_state=seed).fit((tr[feats] - mu) / sd, tr.y)
        out.append(te[["d", "t", "y"]].assign(p=clf.predict_proba((te[feats] - mu) / sd)[:, 1]))
        log("  вторая ступень, квартал", q, "| коэф.:", dict(zip(feats, np.round(clf.coef_[0], 3))))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["d", "t", "y", "p"])


def links(P, WK):
    """Частота аномалий понедельника и факт/прогноз по квинтилям признаков выходных."""
    M = P.merge(WK, on=["t", "d"], how="inner")
    res = {}
    for c in ("w_abs", "w_range", "w_vr"):
        g = M.dropna(subset=[c])
        if len(g) < 500:
            continue
        g = g.assign(qq=pd.qcut(g[c].rank(method="first"), 5, labels=False))
        tab = g.groupby("qq").agg(lo=(c, "min"), hi=(c, "max"), n=("y", "size"), rate=("y", "mean"), pred=("p", "mean"))
        tab["fact_to_pred"] = tab.rate / tab.pred
        res[c] = tab.round(4).reset_index().to_dict("records")
        log(f"[связь {c}] " + " | ".join(f"Q{int(r['qq']) + 1} ({r['lo']:.3g}…{r['hi']:.3g}): аномалий {r['rate']:.2%}, модель {r['pred']:.2%}, факт/прогноз ×{r['fact_to_pred']:.2f}"
                                       for r in res[c]))
    return res


def main():
    os.makedirs(TR.MODELS, exist_ok=True)
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    FD = CD.feature_cols(X)
    WK = weekend_table(Dd)
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), weekend_from=WSTART, runs=[], deltas=[])
    path = os.path.join(TR.MODELS, "exp10_report.json")

    def save():
        json.dump(out, open(path, "w"), ensure_ascii=False, indent=1, default=str)
    if WK.empty:
        log("торгов выходного дня в данных нет — проверять нечего")
        save()
        return
    yr = int(X.d.dt.year.max())
    years = list(range(int(WSTART[:4]), yr + 1))
    mon = set(zip(WK.t, WK.d))

    def only_mon(P):
        return P[[(t, d) in mon for t, d in zip(P.t, P.d)]].reset_index(drop=True)
    log("ПЯТНИЦА 19:00")
    Pf = only_mon(E9.oof(E2.news_view(X, Dd, IX, ev, W, "e19"), FD, years))
    log("ВОСКРЕСЕНЬЕ 19:00")
    Xs = news_view_fn(X, Dd, IX, ev, W, ok_sun19, "s19")
    Ps = only_mon(E9.oof(Xs, FD, years))
    log("ВОСКРЕСЕНЬЕ 19:00, другой seed (шум)")
    import exp5
    old_fit = exp5._fit

    def fit_seed(tr, feats, target, w, s, *a):
        return old_fit(tr, feats, target, w, s + 100, *a)
    exp5._fit = fit_seed
    Ps2 = only_mon(E9.oof(Xs, FD, years))
    exp5._fit = old_fit
    log("ВОСКРЕСЕНЬЕ 19:00 + торги выходных в модели")
    Xw = Xs.merge(WK, on=["t", "d"], how="left")
    Pw = only_mon(E9.oof(Xw, FD + WCOLS, years))
    log("УТРО понедельника (как сейчас)")
    Pm = only_mon(E9.oof(X, FD, years))
    Pst = stack(Ps, WK)
    for P, nm in ((Pf, "ПЯТНИЦА 19:00"), (Ps, "ВОСКРЕСЕНЬЕ 19:00"), (Ps2, "ВОСКРЕСЕНЬЕ 19:00, другой seed"), (Pw, "ВОСКРЕСЕНЬЕ 19:00 + выходные в модели"),
                  (Pm, "УТРО понедельника"), (Pst, "ВОСКРЕСЕНЬЕ 19:00 + поправка по выходным (2-я ступень)")):
        if len(P):
            out["runs"].append(score_mon(P, nm))
            save()
    for A, B, nm in ((Pf, Ps, "воскресенье против пятницы"), (Ps, Ps2, "шум: другой seed"), (Ps, Pw, "выходные в модели против воскресенья"),
                     (Ps, Pst, "поправка по выходным против воскресенья"), (Ps, Pm, "утро понедельника против воскресенья")):
        if len(A) and len(B):
            out["deltas"].append(delta(A, B, nm))
    out["links"] = links(Ps, WK)
    save()
    log("===== ИТОГ ПРОВЕРКИ №10 (понедельники после выходных с торгами, с", WSTART, ") =====")
    for r in out["runs"]:
        log(f"{r['name']}: дней {r['days']}, база {r['base']:.2%} | топ-1 {r['top1']:.1%} | топ-5 {r['top5']:.1%} | топ-10 {r['top10']:.1%} | "
            f"≥1 в топ-5 {r['any5']:.0%} | угадано {r['hits5_per_day']} из 5 | AUC {r['auc']:.3f}")
    for d in out["deltas"]:
        if d:
            log(f"Δ {d['name']}: {d['delta']:+.4f} (90%: {d['lo']:+.4f}…{d['hi']:+.4f}), лучше в {d['better_q']} кварталов")


if __name__ == "__main__":
    main()
