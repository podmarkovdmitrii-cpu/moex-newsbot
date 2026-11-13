# -*- coding: utf-8 -*-
"""Проверка №9: точность прогноза аномалий в зависимости от момента публикации (годы 2021–2026, модель их не видела).

  ВЕЧЕР  — прогноз на день D, опубликованный вечером D−1: свеча дня D−1 целиком, новости до 19:00 D−1
           (в истории свеча полная — это соответствует ночному пересчёту после вечерней сессии; пост в 19:25 видит день без
           вечерней сессии, поэтому на деле он чуть хуже этой оценки);
  УТРО   — как сейчас в боте: всё вечера плюс ночные и утренние новости до 09:55 дня D;
  ДЕНЬ   — прогноз в середине дня D (в 11, 13 и 15 часов МСК): утренний прогноз + как бумага уже расторговалась (изменение
           к индексу, оборот к обычному на этот час, диапазон, оборот последнего часа) + новости дня до этого часа.
           Считается только по бумагам, которые к этому часу ещё НЕ стали аномалией: «станет ли до закрытия».
Одни и те же бумаги (с часовыми свечами, ~160 самых торгуемых) и одни и те же дни. Модель — боевая (свежие годы весомее,
среднее 5 моделей). Метрики: доля аномалий среди 5 и 10 бумаг с наибольшей вероятностью каждый день, доля дней, когда из
топ-5 угадана хотя бы одна, AUC, частота аномалий в среднем (база) и во сколько раз топ-5 точнее базы.
Плюс фактические итоги бота (anom_track.json: утренний топ-10 и ранние предупреждения), если файл есть.
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp2 as E2  # noqa: E402
import exp5 as E5  # noqa: E402
import core_daily as CD  # noqa: E402
import core_intra as CI  # noqa: E402
import regional as RG  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
MODELS = TR.MODELS
FIRST_TEST = int(os.environ.get("EXP_FIRST_TEST", "2021"))


def load():
    t0 = time.time()
    divh = json.load(open(os.path.join(HERE, "..", "divhist.json"), encoding="utf-8"))
    impact = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8"))
    tick = TR.universe()
    Dd = TR.load_series("d", tick + ["IMOEX"], "2012-01-01", index=("IMOEX",))
    IX = Dd.pop("IMOEX")
    turn = {t: np.mean([r[5] for r in v[-60:]]) for t, v in Dd.items() if len(v) > 60}
    top = sorted(turn, key=lambda t: -turn[t])[: int(os.environ.get("ANOM_HOURLY_N", "160"))]
    Hh = TR.load_series("h", top + ["IMOEX"], "2020-01-01", index=("IMOEX",))
    IXH = Hh.pop("IMOEX")
    Dd = TR.stitch_renamed(Dd)
    Dd, IX = TR.backfill(Dd, IX)
    first_day = {t: dt.date.fromisoformat(v[0][0][:10]) for t, v in Dd.items() if v}
    ev, _ = TR.events_all(first_day)
    W = [CD.news_weight(e, impact) for e in ev]
    hd = CD.hourly_daily(Hh)
    RE = RG.company_events(RG.load_events(os.environ.get("NEWS_STATE")))
    X = CD.build({"sh": Dd, "ix": {"IMOEX": IX}}, divh, ev, news_tickers=set(e["tk"] for e in ev), weights=W, hourly=hd, ix_hourly=IXH,
                 sec_of=impact.get("secOf"), reg_events=RE)
    X = X[X.d >= TR.FROM].sort_values(["t", "d"]).reset_index(drop=True)
    log("данные:", X.shape, "часовых рядов", len(Hh), round(time.time() - t0), "с")
    return X, Dd, IX, Hh, IXH, ev, W


def oof(X, feats, years, half=3.0, nbag=5, sneg=0.3, target="y"):
    """Прогнозы «вне выборки» по годам — как боевое обучение (свежие годы весомее, среднее nbag моделей)."""
    out = []
    for Y in years:
        tr = X[X.d.dt.year < Y].dropna(subset=[target])
        te = X[X.d.dt.year == Y].dropna(subset=[target])
        if te[target].sum() < 20 or len(tr) < 1000:
            continue
        if sneg < 1:
            tr = pd.concat([tr[tr[target] == 1], tr[tr[target] == 0].sample(frac=sneg, random_state=1)])
        w = np.where(tr[target] == 1, 1.0, 1.0 / sneg)
        if half:
            w = w * 0.5 ** ((Y - 1 - tr.d.dt.year.values) / half)
        p = np.zeros(len(te))
        for s in range(nbag):
            idx = np.random.RandomState(10 + s).rand(len(tr)) < 0.8
            p += E5._fit(tr[idx], feats, target, w[idx], s, None).predict_proba(te[feats])[:, 1] / nbag
        out.append(te[["d", "t"]].assign(p=p, y=te[target].values))
        log("  год", Y, "готов")
    return pd.concat(out, ignore_index=True)


def score(P, name):
    """Метрики по дням: топ-k, хотя бы одна в топ-5, AUC по годам, калибровка."""
    from sklearn.metrics import roc_auc_score
    P = P.dropna(subset=["p", "y"])
    days = [g for _, g in P.groupby("d") if len(g) >= 20]
    res = dict(name=name, days=len(days), rows=int(len(P)), base=round(float(P.y.mean()), 4))
    for k in (1, 3, 5, 10):
        res[f"top{k}"] = round(float(np.mean([g.nlargest(k, "p").y.mean() for g in days])), 4)
    res["any5"] = round(float(np.mean([g.nlargest(5, "p").y.max() for g in days])), 4)
    res["hits5_per_day"] = round(float(np.mean([g.nlargest(5, "p").y.sum() for g in days])), 3)
    res["real_per_day"] = round(float(np.mean([g.y.sum() for g in days])), 2)
    res["lift5"] = round(res["top5"] / res["base"], 1) if res["base"] else None
    res["auc"] = round(float(np.mean([roc_auc_score(g.y, g.p) for _, g in P.groupby(P.d.dt.year) if g.y.nunique() > 1])), 4)
    yrs = {}
    for yv, g in P.groupby(P.d.dt.year):
        dd = [x for _, x in g.groupby("d") if len(x) >= 20]
        if dd:
            yrs[int(yv)] = round(float(np.mean([x.nlargest(5, "p").y.mean() for x in dd])), 4)
    res["top5_by_year"] = yrs
    cal = []
    for lo, hi in ((0.0, 0.05), (0.05, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 1.01)):
        m = (P.p >= lo) & (P.p < hi)
        if m.sum() >= 30:
            cal.append(dict(bin=f"{lo:.0%}–{min(hi, 1):.0%}", n=int(m.sum()), p=round(float(P.p[m].mean()), 3), fact=round(float(P.y[m].mean()), 3)))
    res["calib"] = cal
    log(f"[{name}] дней {res['days']}, база {res['base']:.2%}: топ-1 {res['top1']:.1%}, топ-3 {res['top3']:.1%}, топ-5 {res['top5']:.1%}, "
        f"топ-10 {res['top10']:.1%}; хотя бы одна в топ-5 — {res['any5']:.0%} дней ({res['hits5_per_day']} из 5 в среднем); AUC {res['auc']:.3f}")
    log(f"[{name}] топ-5 по годам: " + ", ".join(f"{y}: {v:.1%}" for y, v in yrs.items()))
    log(f"[{name}] калибровка: " + "; ".join(f"{c['bin']}: обещано {c['p']:.0%}, сбылось {c['fact']:.0%} (n={c['n']})" for c in cal))
    return res


def live_track():
    for base in (os.environ.get("CHANNEL_STATE_DIR"), os.path.join(os.environ.get("NEWS_STATE") or "", "channel"),
                 os.environ.get("CHANNEL_STATE")):
        p = os.path.join(base or "", "anom_track.json")
        if base and os.path.exists(p):
            D = json.load(open(p, encoding="utf-8")).get("days") or []
            if not D:
                continue
            n = len(D)
            top = sum(len(d.get("top") or []) for d in D)
            hit = sum(len(d.get("hit") or []) for d in D)
            wn = sum(len(d.get("warn") or []) for d in D)
            wh = sum(len(d.get("whit") or []) for d in D)
            dn = sum(d.get("dir_n") or 0 for d in D)
            do = sum(d.get("dir_ok") or 0 for d in D)
            r = dict(days=n, first=D[0]["date"], last=D[-1]["date"], top10_hit=round(hit / top, 3) if top else None,
                     any_hit_days=round(sum(1 for d in D if d.get("hit")) / n, 3), warn=wn, warn_hit=round(wh / wn, 3) if wn else None,
                     dir_n=dn, dir_ok=round(do / dn, 3) if dn else None, real_per_day=round(sum(d.get("n_real") or 0 for d in D) / n, 1))
            log("ФАКТ бота:", r)
            return r
    log("ФАКТ бота: файла anom_track.json нет")
    return None


def main():
    os.makedirs(MODELS, exist_ok=True)
    X, Dd, IX, Hh, IXH, ev, W = load()
    FD = CD.feature_cols(X)
    yr = int(X.d.dt.year.max())
    test = list(range(FIRST_TEST, yr + 1))
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), test_years=test, runs=[])
    path = os.path.join(MODELS, "exp9_report.json")

    def add(r):
        out["runs"].append(r)
        json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
        return r
    hourly = set(Hh)
    log("ВЕЧЕР: новости до 19:00 накануне")
    Xe = E2.news_view(X, Dd, IX, ev, W, "e19")
    Pe = oof(Xe, FD, test)
    log("УТРО: как сейчас (новости до 09:55)")
    Pm = oof(X, FD, list(range(2020, yr + 1)))           # 2020 — для обучения дневной модели внутри дня
    for P, nm in ((Pe, "ВЕЧЕР накануне"), (Pm[Pm.d.dt.year >= FIRST_TEST], "УТРО до открытия")):
        add(score(P, nm + ", все бумаги"))
        add(score(P[P.t.isin(hourly)], nm + ", бумаги с часовыми свечами"))
    # внутри дня: утренний прогноз «вне выборки» как признак p_pre
    pre = Pm[["d", "t", "p"]].rename(columns={"p": "p_pre"})
    XI, _ = CI.build_train(Hh, IXH, X, ev, pre)
    XI = XI[XI.d.dt.year >= 2020].reset_index(drop=True)
    log("внутридневных строк:", XI.shape, "база", round(float(XI.y.mean()), 4))
    for c in (11, 13, 15):
        Xc = XI[XI.c == c].reset_index(drop=True)
        Pc = oof(Xc, CI.FEAT, test, half=0, nbag=3, sneg=0.3)
        add(score(Pc, f"ДЕНЬ {c}:00, бумаги, ещё не ставшие аномалией"))
        # для сравнения: утренний прогноз на тех же оставшихся бумагах (без знания о торгах дня)
        Pm2 = Xc[Xc.d.dt.year >= FIRST_TEST][["d", "t", "y", "p_pre"]].rename(columns={"p_pre": "p"})
        add(score(Pm2, f"утренний прогноз на тех же бумагах в {c}:00 (без торгов дня)"))
        # доля дневных аномалий, которые к этому часу уже случились (их прогноз «в середине дня» уже не ловит)
        tot = X[(X.d.dt.year >= FIRST_TEST) & X.t.isin(hourly)].y.sum()
        left = Xc[Xc.d.dt.year >= FIRST_TEST].y.sum()
        out.setdefault("already", {})[c] = round(1 - float(left) / float(tot), 3) if tot else None
        log(f"к {c}:00 уже случилось {out['already'][c]:.0%} аномалий дня")
    out["live"] = live_track()
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
    log("===== ИТОГ ПРОВЕРКИ №9 (годы", test[0], "–", test[-1], ") =====")
    for r in out["runs"]:
        log(f"{r['name']}: топ-1 {r['top1']:.1%} | топ-3 {r['top3']:.1%} | топ-5 {r['top5']:.1%} (×{r['lift5']} к базе {r['base']:.2%}) | "
            f"топ-10 {r['top10']:.1%} | ≥1 в топ-5 {r['any5']:.0%} дней | угадано {r['hits5_per_day']} из 5 при {r['real_per_day']} аномалиях в день | AUC {r['auc']:.3f}")
    log("уже случилось к часу:", out.get("already"), "| факт бота:", out.get("live"))


if __name__ == "__main__":
    main()
