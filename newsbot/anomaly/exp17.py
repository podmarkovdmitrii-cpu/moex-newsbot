# -*- coding: utf-8 -*-
"""Проверка №17: с чем совместить аномалии, чтобы результат был лучше. Перебор всех признаков, пар условий и модели-фильтра.

Сигналы — внутридневные аномалии цены (как в №12–15: первый час 10…17, ход к индексу ≥ порога эшелона), 2012–2026, ликвидные
(оборот ≥ 10 млн ₽/день). Базовые способы отработки (издержки по ликвидности как в №15):
  R1 S1: рост, лимит на откате 50% хода, тейк +25% хода, без стопа, до 5 дн. (лучший устойчивый в №15);
  R2 рост, вход сразу, без тейка и стопа, держать 10 дн. (продолжение движения);
  R3 рост, против движения (шорт), сразу, тейк 50% хода, до закрытия дня (откат внутри дня);
  R4 падение, покупка против движения, сразу, тейк 200% хода, без стопа, до 20 дн. (выкуп падения, лучший в №15);
  R5 падение, по движению (шорт), сразу, без тейка, 5 дн.
С чем совмещаем (всё известно в момент сигнала):
  • все признаки дневной модели аномалий (≈110: тренд, 20/250-дн. максимумы и минимумы, волатильность, обороты, серии, аномалии
    в прошлом, новости до открытия и их «неожиданность», рынок и сектор накануне, дивиденды, день недели, месяц …);
  • внутри дня: час сигнала, сила хода к порогу, гэп на открытии (доля хода, сделанная гэпом), индекс к часу сигнала, новости
    по бумаге в этот день до сигнала, «волна» — сколько бумаг того же сектора уже дали сигнал в ту же сторону, оборот к медиане;
  • деньги ALGOPACK (с 2020 г.): перевес покупок за 3 предыдущих дня, перевес покупок в первый час (если сигнал позже 11:00);
  • утренний прогноз модели аномалий (вне выборки) и её направление (с 2020 г.).
Идеи из литературы и форумов, которые здесь проверяются: «премия за высокий объём» (Gervais–Kaniel–Mingelgrin 2001),
движения с новостями продолжаются, без новостей — откатываются (Chan 2003), рост у 52-недельного максимума продолжается
(George–Hwang 2004), фильтр по тренду рынка, «кульминация» объёма после долгого движения, дельта (перевес покупок).
Как считаем: разбивка каждого признака на квинтили по 2012–2020 гг. и та же разбивка на 2021–2026 гг.; правила из 1–2 условий
выбираются по 2012–2020, итог — на 2021–2026; модель-фильтр (градиентный бустинг) — по годам, каждый год учится на прошлых.
"""
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
import algopack as AP  # noqa: E402
import core_daily as CD  # noqa: E402
import core_intra as CI  # noqa: E402
import exp9 as E9  # noqa: E402
import exp15 as E15  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
SPLIT = int(os.environ.get("EXP_SPLIT", "2021"))
MIN_TR = int(os.environ.get("EXP_MIN_TR", "80"))
STRATS = {
    "R1 S1: рост, откат 50%, тейк 25% хода, до 5 дн.": ("up", ("follow", 0.5, ("m", 0.25), None, 5)),
    "R2 рост, сразу, держать 10 дн.": ("up", ("follow", "sig", None, None, 10)),
    "R3 рост, шорт против движения до закрытия, тейк 50% хода": ("up", ("fade", "sig", ("m", 0.5), None, 0)),
    "R4 падение, покупка против, тейк 200% хода, до 20 дн.": ("dn", ("fade", "sig", ("m", 2.0), None, 20)),
    "R5 падение, шорт по движению, 5 дн.": ("dn", ("follow", "sig", None, None, 5)),
}
NICE = {"c": "час сигнала", "acx": "сила хода к порогу", "gap_sh": "доля хода гэпом на открытии", "ixr_c": "индекс к часу сигнала (в сторону сигнала)",
        "nw_day": "новости по бумаге в день сигнала до него", "wave": "волна: сигналов сектора в ту же сторону раньше", "cvol": "оборот к медиане к часу сигнала",
        "fp3": "перевес покупок за 3 дня (в сторону сигнала)", "fe": "перевес покупок в первый час (в сторону сигнала)", "p_m": "утренний прогноз аномалии",
        "pdir": "утренний прогноз направления (в сторону сигнала)", "dhi250": "до 52-нед. максимума", "dlo250": "от 52-нед. минимума", "dhi20": "до 20-дн. максимума",
        "ema20d": "цена к EMA20", "ema50d": "цена к EMA50", "nw_n": "новости до открытия", "nw_surprise": "неожиданность новостей", "an20": "аномалий за 20 дн.",
        "since_an": "дней с прошлой аномалии", "vr1": "оборот вчера к норме", "sd20": "волатильность 20 дн.", "streak": "серия дней", "to_ex": "дней до отсечки",
        "dow": "день недели", "month": "месяц", "lval": "оборот (log10)"}


def nice(f):
    return NICE.get(f, f)


# ------------------------------------------------------------------ признаки в момент сигнала
def intraday_feats(E, X, H2, IX2, Dd, ev):
    E = E.copy()
    s = E.s.values
    # индекс к часу сигнала
    ixC, _, _, _ = CI.pivot(IX2)
    ixf = ixC.ffill(axis=1)
    ixl = ixf.iloc[:, -1].shift(1)
    ixr = []
    for d, c in zip(E.d, E.c):
        try:
            ixr.append(float(ixf.loc[d, c] / ixl.loc[d] - 1))
        except KeyError:
            ixr.append(np.nan)
    E["ixr_c"] = s * np.array(ixr)
    # гэп: доля хода, сделанная открытием
    gap = []
    for t, d, prev, p0 in zip(E.t, E.d, E.prev, E.p0):
        o = np.nan
        for r in Dd.get(t, []):
            if r[0][:10] == d.strftime("%Y-%m-%d"):
                o = r[1]
                break
        gap.append((o / prev - 1) / (p0 / prev - 1) if o == o and p0 != prev else np.nan)
    E["gap_sh"] = np.clip(gap, -2, 3)
    # оборот к медиане к часу сигнала
    cv = []
    piv = {}
    for t, d, c, lv in zip(E.t, E.d, E.c, E.lval):
        if t not in piv:
            _, V, _, _ = CI.pivot(H2[t])
            piv[t] = V.cumsum(axis=1)
        V = piv[t]
        try:
            cv.append(float(V.loc[d, c]) / 10 ** lv)
        except KeyError:
            cv.append(np.nan)
    E["cvol"] = cv
    # новости по бумаге в день сигнала до конца часа сигнала
    nh = CI.news_hours(ev)
    E["nw_day"] = [sum(1 for h in nh.get((t, d), []) if h < c + 1) for t, d, c in zip(E.t, E.d, E.c)]
    # волна по сектору: сигналы той же стороны в том же секторе в тот же день раньше или в тот же час
    sec = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8"))["secOf"]
    E["sec"] = [sec.get(t, "?") for t in E.t]
    w = np.zeros(len(E))
    for _, g in E.groupby(["d", "sec"]):
        if g.sec.iloc[0] == "?":
            continue
        for i in g.index:
            w[i] = ((g.s == g.s[i]) & (g.c <= g.c[i]) & (g.index != i)).sum()
    E["wave"] = w
    # дневные признаки модели (известны до открытия)
    FD = CD.feature_cols(X)
    keep = [f for f in FD if f not in ("tier", "sd60", "lval")]
    E = E.merge(X[["d", "t"] + keep], on=["d", "t"], how="left", suffixes=("", "_x"))
    return E, keep


def flows_feats(E):
    try:
        A = AP.load()
    except Exception as e:  # noqa: BLE001
        log("дневные ALGOPACK:", e)
        A = pd.DataFrame()
    if A.empty or "val_b" not in A.columns:
        E["fp3"], E["fe"] = np.nan, np.nan
        return E
    A = A[["t", "d", "val_b", "val_s", "eb", "es"]].sort_values(["t", "d"])
    g = A.groupby("t")
    b3 = g.val_b.transform(lambda x: x.shift(1).rolling(3, min_periods=2).sum())
    s3 = g.val_s.transform(lambda x: x.shift(1).rolling(3, min_periods=2).sum())
    A["fp3_raw"] = (b3 - s3) / (b3 + s3)
    A["fe_raw"] = (A.eb - A.es) / (A.eb + A.es)
    E = E.merge(A[["t", "d", "fp3_raw", "fe_raw"]], on=["t", "d"], how="left")
    E["fp3"] = E.s * E.fp3_raw
    E["fe"] = np.where(E.c >= 11, E.s * E.fe_raw, np.nan)
    log(f"ALGOPACK: перевес за 3 дня есть у {E.fp3.notna().mean():.0%} сигналов, первый час — у {E.fe.notna().mean():.0%}")
    return E


def model_feats(E, X):
    FD = CD.feature_cols(X)
    yr = int(X.d.dt.year.max())
    try:
        P = E9.oof(X, FD, list(range(2020, yr + 1)), nbag=int(os.environ.get("EXP_NBAG", "3")))
        import exp16 as E16
        Pd = E16.dir_oof(X, FD, list(range(2020, yr + 1)))
        P = P[["d", "t", "p"]].merge(Pd, on=["d", "t"], how="left").rename(columns={"p": "p_m"})
        E = E.merge(P, on=["d", "t"], how="left")
        E["pdir"] = E.s * (E.p_up - 0.5)
    except Exception as e:  # noqa: BLE001
        log("утренний прогноз вне выборки не посчитан:", e)
        E["p_m"], E["pdir"] = np.nan, np.nan
    return E


# ------------------------------------------------------------------ анализ
def st(r):
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return dict(n=0, mean=np.nan, win=np.nan)
    return dict(n=int(len(r)), mean=float(r.mean()), win=float((r > 0).mean()))


def quint(E, R, feats, tr, te, label):
    """Квинтили каждого признака по обучающим годам; средний результат по корзинам на обучающих и проверочных годах."""
    rows = []
    for f in feats:
        x = E[f].values.astype(float)
        ok = np.isfinite(R) & np.isfinite(x)
        if (ok & tr).sum() < 5 * MIN_TR // 2 or (ok & te).sum() < 100:
            continue
        q = np.unique(np.nanquantile(x[ok & tr], [0.2, 0.4, 0.6, 0.8]))
        if len(q) < 2:
            continue
        b = np.digitize(x, q)
        mtr = [np.nanmean(R[ok & tr & (b == k)]) if (ok & tr & (b == k)).sum() >= 20 else np.nan for k in range(len(q) + 1)]
        mte = [np.nanmean(R[ok & te & (b == k)]) if (ok & te & (b == k)).sum() >= 20 else np.nan for k in range(len(q) + 1)]
        ntr = [int((ok & te & (b == k)).sum()) for k in range(len(q) + 1)]
        if np.isnan(mte[0]) or np.isnan(mte[-1]) or np.isnan(mtr[0]) or np.isnan(mtr[-1]):
            continue
        sp_tr, sp_te = mtr[-1] - mtr[0], mte[-1] - mte[0]
        rows.append(dict(f=f, q=q, mtr=mtr, mte=mte, n_te=ntr, sp_tr=sp_tr, sp_te=sp_te, agree=np.sign(sp_tr) == np.sign(sp_te)))
    rows.sort(key=lambda r: -(abs(r["sp_te"]) if r["agree"] else 0) - 0.5 * min(abs(r["sp_tr"]), abs(r["sp_te"])) * r["agree"])
    log(f"--- {label}: признаки, у которых разница между верхним и нижним квинтилем одного знака в {E15.WF_FROM if False else '2012–' + str(SPLIT - 1)} и {SPLIT}–2026 ---")
    for r in [x for x in rows if x["agree"]][:14]:
        log(f"  {nice(r['f'])} [{r['f']}] границы {', '.join(f'{v:.3g}' for v in r['q'])}: до {SPLIT} "
            + " ".join(f"{m:+.2%}" for m in r["mtr"]) + f" | с {SPLIT} " + " ".join(f"{m:+.2%}" for m in r["mte"])
            + f" (n {'/'.join(map(str, r['n_te']))})")
    nag = sum(1 for x in rows if x["agree"])
    log(f"  совпадает знак у {nag} из {len(rows)} признаков (случайно ожидалось бы ~{len(rows) // 2})")
    return rows


def rules(E, R, feats, tr, te, label, qrows):
    """Правила из 1 и 2 условий (порог — граница квинтиля по обучающим годам), выбор по 2012–(SPLIT−1), итог — на SPLIT–2026."""
    top = [r["f"] for r in qrows if r["agree"]][:16] or [r["f"] for r in qrows][:16]
    conds = []
    for r in qrows:
        if r["f"] not in top:
            continue
        x = E[r["f"]].values.astype(float)
        for v in r["q"]:
            conds.append((f"{nice(r['f'])} ≥ {v:.3g}", x >= v))
            conds.append((f"{nice(r['f'])} < {v:.3g}", x < v))
    ok = np.isfinite(R)
    base_tr, base_te = st(R[ok & tr]), st(R[ok & te])
    cand = []
    for k in (1, 2):
        for cs in itertools.combinations(conds, k):
            if k == 2 and cs[0][0].split(" ")[0] == cs[1][0].split(" ")[0]:
                continue
            m = np.logical_and.reduce([c[1] for c in cs]) & ok
            n = int((m & tr).sum())
            if n < MIN_TR:
                continue
            r_ = R[m & tr]
            sd = r_.std() or 1e-9
            cand.append((r_.mean() / sd * np.sqrt(n), " И ".join(c[0] for c in cs), m))
    cand.sort(key=lambda x: -x[0])
    log(f"--- {label}: правила, выбранные по 2012–{SPLIT - 1} (база там {base_tr['mean']:+.2%}/сд, n={base_tr['n']}), "
        f"итог на {SPLIT}–2026 (база {base_te['mean']:+.2%}/сд, n={base_te['n']}, прибыльных {base_te['win']:.0%}) ---")
    out = []
    seen = set()
    for sc, nm, m in cand:
        if nm in seen:
            continue
        seen.add(nm)
        a, b = st(R[m & tr]), st(R[m & te])
        out.append(dict(rule=nm, train=a, test=b))
        log(f"  {nm}: до {SPLIT} {a['mean']:+.2%}/сд n={a['n']} → с {SPLIT} {b['mean']:+.2%}/сд n={b['n']}, прибыльных {b['win']:.0%}" if b["n"] else f"  {nm}: нет сделок с {SPLIT}")
        if len(out) >= 8:
            break
    yrs = E.d.dt.year.values
    masks = {}
    for _sc, nm_, m_ in cand:
        masks.setdefault(nm_, m_)
    for o in out[:3]:
        nm = o["rule"]
        mt = masks[nm] & te
        days = E.d.values[mt]
        if not len(days):
            continue
        u, cnt = np.unique(days, return_counts=True)
        o["days"], o["top_day_share"] = int(len(u)), float(cnt.max() / cnt.sum())
        by = "; ".join(f"{y}: {np.nanmean(R[mt & (yrs == y)]):+.1%} ({int((mt & (yrs == y)).sum())})" for y in sorted(set(yrs[mt])))
        log(f"  · «{nm}» по годам: {by}; разных дней {len(u)}, самый крупный день — {cnt.max() / cnt.sum():.0%} сделок "
            f"({pd.Timestamp(u[cnt.argmax()]).date()})")
    if out:
        good = [o for o in out if o["test"]["n"] >= 30]
        better = sum(1 for o in good if o["test"]["mean"] > base_te["mean"])
        log(f"  из {len(good)} лучших правил (≥30 сделок с {SPLIT}) лучше базы на проверке: {better}")
    return dict(base_train=base_tr, base_test=base_te, top=out)


def model_filter(E, R, feats, label):
    """Модель-фильтр: каждый год учится на прошлых (с 2012), предсказывает результат сделки; берём верхние 30% и 50% предсказаний."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    yr = E.d.dt.year.values
    ok = np.isfinite(R)
    res = {}
    out = []
    for Y in range(2016, int(yr.max()) + 1):
        tr, te = ok & (yr < Y), ok & (yr == Y)
        if tr.sum() < 300 or te.sum() < 20:
            continue
        clip = np.clip(R[tr], np.nanquantile(R[tr], 0.02), np.nanquantile(R[tr], 0.98))
        m = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0,
                                          random_state=1).fit(E.loc[tr, feats], clip)
        p = m.predict(E.loc[te, feats])
        r = R[te]
        q70, q50 = np.quantile(p, 0.7), np.quantile(p, 0.5)
        out.append(dict(y=Y, all=st(r), top30=st(r[p >= q70]), top50=st(r[p >= q50]), bot30=st(r[p <= np.quantile(p, 0.3)])))
    if not out:
        return res
    log(f"--- {label}: модель-фильтр по всем признакам (каждый год учится на прошлых) ---")
    agg = {k: [] for k in ("all", "top30", "top50", "bot30")}
    for o in out:
        log(f"  {o['y']}: все {o['all']['mean']:+.2%} (n={o['all']['n']}) | верхние 30% {o['top30']['mean']:+.2%} | верхние 50% {o['top50']['mean']:+.2%} | нижние 30% {o['bot30']['mean']:+.2%}")
        for k in agg:
            agg[k] += [(o[k]["mean"], o[k]["n"])]
    for k, v in agg.items():
        n = sum(x[1] for x in v)
        res[k] = dict(mean=sum(x[0] * x[1] for x in v if x[1]) / max(1, n), n=n)
    lift = sum(1 for o in out if o["top30"]["mean"] > o["all"]["mean"])
    log(f"  ИТОГ 2016–2026: все {res['all']['mean']:+.2%} (n={res['all']['n']}) | верхние 30% {res['top30']['mean']:+.2%} (n={res['top30']['n']}) | "
        f"верхние 50% {res['top50']['mean']:+.2%} | нижние 30% {res['bot30']['mean']:+.2%}; верхние 30% лучше всех в {lift} из {len(out)} лет")
    res["years"] = out
    res["lift_years"] = lift
    return res


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
    E = E[E.lval >= 7].reset_index(drop=True)          # только ликвидные: ≥ 10 млн ₽ в день
    log("ликвидных сигналов:", len(E), "| по годам", E.d.dt.year.value_counts().sort_index().to_dict())
    P = E15.paths_a(E)
    cache = {"cost": E15.cost_of(E.lval.values)}
    RR = {}
    for nm, (side, cfg) in STRATS.items():
        r = E15.sim_a(E, P, *cfg, cache)
        m = (E.s.values > 0) if side == "up" else (E.s.values < 0)
        RR[nm] = np.where(m, r, np.nan)
    E, dfe = intraday_feats(E, X, H2, IX2, Dd, ev)
    E = flows_feats(E)
    E = model_feats(E, X)
    feats = ["c", "acx", "gap_sh", "ixr_c", "nw_day", "wave", "cvol", "fp3", "fe", "p_m", "pdir"] + dfe
    rs = np.random.RandomState(7)
    for k in range(5):  # контроль: случайные «признаки» — сколько из них случайно покажут устойчивую разницу
        E[f"шум{k + 1}"] = rs.normal(size=len(E))
    feats = [f for f in feats if f in E.columns and E[f].notna().mean() > 0.05] + [f"шум{k + 1}" for k in range(5)]
    log(f"признаков: {len(feats)}; {round(time.time() - t0)} с")
    global SPLIT
    split0 = SPLIT
    yr = E.d.dt.year.values
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), split=SPLIT, n=int(len(E)), strategies={})
    for nm, R in RR.items():
        SPLIT = int(os.environ.get("EXP_SPLIT_R1", "2023")) if nm.startswith("R1") else split0  # у S1 до 2021 г. мало сделок
        tr, te = yr < SPLIT, yr >= SPLIT
        log(f"===================== {nm} =====================")
        b = st(R[np.isfinite(R)])
        log(f"всего сделок {b['n']}, {b['mean']:+.2%}/сд, прибыльных {b['win']:.0%}; до {SPLIT}: {st(R[tr])['mean']:+.2%}, с {SPLIT}: {st(R[te])['mean']:+.2%}")
        q = quint(E, R, feats, tr, te, nm)
        ru = rules(E, R, feats, tr, te, nm, q)
        mf = model_filter(E, R, feats, nm)
        out["strategies"][nm] = dict(split=SPLIT, quint=[dict(f=x["f"], mtr=x["mtr"], mte=x["mte"], agree=bool(x["agree"])) for x in q[:30]], rules=ru, model=mf)
        json.dump(out, open(os.path.join(TR.MODELS, "exp17_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    # сводка
    log("===================== ИТОГ ПРОВЕРКИ №17 =====================")
    for nm, o in out["strategies"].items():
        bt = o["rules"]["base_test"]
        best = (o["rules"]["top"] or [{}])[0]
        mf = o["model"]
        log(f"{nm}: база с {o['split']} {bt['mean']:+.2%} (n={bt['n']}); лучшее правило по прошлым годам «{best.get('rule', '—')}» → "
            + (f"{best['test']['mean']:+.2%} (n={best['test']['n']})" if best.get("test", {}).get("n") else "—")
            + (f"; модель-фильтр, верхние 30%: {mf['top30']['mean']:+.2%} против {mf['all']['mean']:+.2%} (лучше в {mf['lift_years']} из {len(mf['years'])} лет)" if mf else ""))
    log("время", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
