# -*- coding: utf-8 -*-
"""Проверка №19: инструменты технического анализа Investing.com — помогают ли они нашему прогнозу аномалий и их отработке.

Что берём (newsbot/anomaly/ta_investing.py): 12 индикаторов (RSI, STOCH, STOCHRSI, MACD, ADX, Williams %R, CCI, ATR, Highs/Lows,
Ultimate Oscillator, ROC, Bull/Bear Power), 12 скользящих средних (5–200, простые и экспоненциальные), сводку «активно продавать …
активно покупать», перекупленность/перепроданность, точки разворота (классические, Фибоначчи, Камарилья), свечные модели;
на дневных барах, на часовых (в момент сигнала) и недельную сводку. Фундаментальные инструменты InvestingPro (справедливая цена,
финансовое здоровье, ProPicks, консенсус аналитиков) на истории проверить нельзя — точечных архивов этих оценок нет.

A. Прогноз аномалий (дневная модель): боевые признаки против «боевые + Investing» и против «боевые + столько же случайных
   признаков» (контроль: сколько даёт простое добавление признаков). Годы 2021–2026, каждый год модель учится на прошлых.
   Метрики: доля аномалий в топ-5 дня, AUC. И модель направления (вверх/вниз при аномалии): точность.
B. Отработка аномалий (как в №17): S1, рост 10 дн., шорт роста до закрытия, выкуп падения 20 дн., шорт падения 5 дн.
   — средний результат по метке сводки Investing (дневной и часовой, в сторону сигнала), модель-фильтр с признаками Investing
   и без них (верхние 30% по годам 2016–2026).
C. Сама сводка Investing как сигнал: все ликвидные бумаго-дни 2014–2026 — отклонение от индекса за следующие 1 и 5 дней по метке.
"""
import datetime as dt
import gzip
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import core_intra as CI  # noqa: E402
import exp9 as E9  # noqa: E402
import exp15 as E15  # noqa: E402
import exp16 as E16  # noqa: E402
import exp17 as E17  # noqa: E402
import ta_investing as TA  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
FIRST = int(os.environ.get("EXP_FIRST_TEST", "2021"))
LABEL = {-2: "активно продавать", -1: "продавать", 0: "нейтрально", 1: "покупать", 2: "активно покупать"}
KEEP = ["rsi", "stoch", "stochrsi", "macd", "macd_h", "adx", "di", "willr", "cci", "atr", "hl", "uo", "roc", "bbp", "ob", "os",
        "ind_score", "ma_score", "score", "label", "sma50", "sma200", "ema20", "ema100", "pv_p", "pv_r1", "pv_s1", "pv_r2", "pv_s2",
        "pv_cr3", "pv_cs3", "cdl", "cdl_doji", "cdl_engulf", "cdl_hammer", "cdl_star", "cdl_three"]


def daily_ta(Dd):
    """Дневные индикаторы по всем бумагам (на закрытии дня) и недельная сводка (по закрытым неделям)."""
    out = []
    for t, a in Dd.items():
        df = CD.series(a)
        if len(df) < 60:
            continue
        F = TA.indicators(df)[KEEP]
        F.columns = ["ta_" + k for k in F.columns]
        F["d"], F["t"] = df["d"].values, t
        w = df.set_index("d").resample("W-FRI").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
        if len(w) > 30:
            Wk = TA.indicators(w.reset_index(drop=True))[["score", "label", "rsi"]]
            Wk.index = w.index
            wk = Wk.reindex(df["d"], method="ffill").shift(5)   # только закрытые недели: значение пятницы — со следующей недели
            F["taw_score"], F["taw_label"], F["taw_rsi"] = wk["score"].values, wk["label"].values, wk["rsi"].values
        out.append(F)
    A = pd.concat(out, ignore_index=True)
    log("дневной теханализ:", A.shape)
    return A


def shifted(A):
    """Для прогноза на день D — значения на закрытии D−1."""
    A = A.sort_values(["t", "d"]).copy()
    cols = [c for c in A.columns if c.startswith(("ta_", "taw_"))]
    A[cols] = A.groupby("t")[cols].shift(1)
    return A


def hourly_ta(E, H2):
    """Часовые индикаторы на закрытии часа сигнала (d, c)."""
    rows = {}
    for t in sorted(set(E.t)):
        a = H2.get(t)
        if not a:
            continue
        df = pd.DataFrame([r[:5] for r in a], columns=["k", "o", "h", "l", "c"])
        F = TA.indicators(df)[["rsi", "stoch", "macd", "adx", "di", "willr", "cci", "uo", "roc", "bbp", "ob", "os", "ind_score", "ma_score",
                                "score", "label", "pv_p", "pv_r1", "pv_s1", "cdl"]]
        F["key"] = df["k"].values
        rows[t] = F.set_index("key")
    vals = []
    for t, d, c in zip(E.t, E.d, E.c):
        F = rows.get(t)
        k = f"{d.strftime('%Y-%m-%d')} {int(c):02d}"
        vals.append(F.loc[k].to_dict() if F is not None and k in F.index else {})
    H = pd.DataFrame(vals, index=E.index).add_prefix("tah_")
    return H


def score_compare(X, FD, extra, label):
    P = E9.oof(X, FD + extra, list(range(FIRST, int(X.d.dt.year.max()) + 1)), nbag=int(os.environ.get("EXP_NBAG", "3")))
    return E9.score(P, label)


def dir_acc(X, feats, label):
    P = E16.dir_oof(X, feats, list(range(FIRST, int(X.d.dt.year.max()) + 1)))
    M = X[["d", "t", "x", "y"]].merge(P, on=["d", "t"])
    M = M[(M.y == 1) & M.x.notna()]
    acc = float(((M.p_up > 0.5) == (M.x > 0)).mean())
    conf = (M.p_up - 0.5).abs() >= 0.15
    accc = float(((M.p_up[conf] > 0.5) == (M.x[conf] > 0)).mean()) if conf.any() else float("nan")
    log(f"[{label}] направление аномалии: точность {acc:.1%} (n={len(M)}), при уверенном прогнозе {accc:.1%} (n={int(conf.sum())})")
    return dict(acc=acc, acc_conf=accc, n=int(len(M)))


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"))
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    FD = CD.feature_cols(X)
    A = daily_ta(Dd)
    As = shifted(A)
    X = X.merge(As, on=["d", "t"], how="left")
    TAF = [c for c in As.columns if c.startswith(("ta_", "taw_"))]
    log(f"признаков Investing: {len(TAF)}; заполнено в среднем {X[TAF].notna().mean().mean():.0%}")

    # ---------- C. сводка Investing как самостоятельный сигнал
    log("===== C. СВОДКА INVESTING КАК СИГНАЛ (ликвидные бумаги, оборот ≥ 10 млн ₽; отклонение от индекса) =====")
    L = X[(X.lval >= 7) & (X.d.dt.year >= 2014)].sort_values(["t", "d"]).copy()
    g = L.groupby("t")["x"]
    L["f1"] = g.shift(-1)
    L["f5"] = sum(g.shift(-k) for k in range(1, 6))
    C = {}
    for lab in (-2, -1, 0, 1, 2):
        m = L.ta_label == lab
        a, b = L.f1[m].mean(), L.f5[m].mean()
        C[lab] = dict(n=int(m.sum()), f1=float(a), f5=float(b))
        log(f"  {LABEL[lab]}: {int(m.sum())} бумаго-дней; завтра {a:+.3%} к индексу, за 5 дней {b:+.3%}")
    for lab in (-2, 2):
        m = L.taw_label == lab
        log(f"  неделя «{LABEL[lab]}»: {int(m.sum())}; за 5 дней {L.f5[m].mean():+.3%}")
    for y in sorted(set(L.d.dt.year)):
        m = L.d.dt.year == y
        a, b = L.f5[m & (L.ta_label == 2)].mean(), L.f5[m & (L.ta_label == -2)].mean()
        log(f"  {y}: «активно покупать» за 5 дней {a:+.2%}, «активно продавать» {b:+.2%}, разница {a - b:+.2%}")
    out["C"] = C
    json.dump(out, open(os.path.join(TR.MODELS, "exp19_report.json"), "w"), ensure_ascii=False, indent=1, default=str)

    # ---------- A. прогноз аномалий
    log("===== A. ПРОГНОЗ АНОМАЛИЙ: боевые признаки / + Investing / + столько же случайных =====")
    rs = np.random.RandomState(11)
    NOISE = [f"шум{k}" for k in range(len(TAF))]
    for k in NOISE:
        X[k] = rs.normal(size=len(X))
    out["A"] = {}
    for nm, extra in ((("боевые", []), ("боевые + Investing", TAF), ("боевые + случайные", NOISE)) if not os.environ.get("EXP_SKIP_A") else ()):
        out["A"][nm] = score_compare(X, FD, extra, nm)
        out["A"][nm]["dir"] = dir_acc(X, FD + extra, nm)
        json.dump(out, open(os.path.join(TR.MODELS, "exp19_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    X = X.drop(columns=NOISE)
    b = out["A"].get("боевые")
    for nm in (("боевые + Investing", "боевые + случайные") if b else ()):
        r = out["A"][nm]
        log(f"  {nm}: топ-5 {r['top5']:.2%} против {b['top5']:.2%} ({r['top5'] - b['top5']:+.2%}), AUC {r['auc']:.4f} против {b['auc']:.4f}; "
            f"по годам топ-5: " + ", ".join(f"{y}: {v - b['top5_by_year'].get(y, 0):+.1%}" for y, v in r["top5_by_year"].items())
            + f"; направление {r['dir']['acc']:.1%} против {b['dir']['acc']:.1%}")

    # ---------- B. отработка аномалий
    log("===== B. ОТРАБОТКА АНОМАЛИЙ: сводка Investing в момент сигнала и модель-фильтр с ней и без =====")
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
    X0 = X.drop(columns=TAF)
    E = E15.events_a(X0, H2, IX2, Dd)
    E = E[E.lval >= 7].reset_index(drop=True)
    P = E15.paths_a(E)
    cache = {"cost": E15.cost_of(E.lval.values)}
    RR = {}
    for nm, (side, cfg) in E17.STRATS.items():
        r = E15.sim_a(E, P, *cfg, cache)
        RR[nm] = np.where((E.s.values > 0) if side == "up" else (E.s.values < 0), r, np.nan)
    E, dfe = E17.intraday_feats(E, X0, H2, IX2, Dd, ev)
    E = E17.flows_feats(E)
    E = E17.model_feats(E, X0)
    base = [f for f in ["c", "acx", "gap_sh", "ixr_c", "nw_day", "wave", "cvol", "fp3", "fe", "p_m", "pdir"] + dfe if f in E.columns and E[f].notna().mean() > 0.05]
    # Investing в момент сигнала: дневные (на вчерашнем закрытии), часовые (на закрытии часа сигнала) — в сторону сигнала
    Ed = E[["d", "t"]].merge(As, on=["d", "t"], how="left")
    Dl = TA.oriented(Ed[[c for c in TAF if c.startswith("ta_")]].rename(columns=lambda c: c[3:]), E.s.values).add_prefix("ta_")
    Wl = Ed[["taw_score", "taw_label", "taw_rsi"]].copy()
    Wl["taw_score"] *= E.s.values
    Wl["taw_label"] *= E.s.values
    Wl["taw_rsi"] = (Wl["taw_rsi"] - 50) * E.s.values
    Hl = hourly_ta(E, H2)
    Hl = TA.oriented(Hl.rename(columns=lambda c: c[4:]), E.s.values).add_prefix("tah_")
    E = pd.concat([E.reset_index(drop=True), Dl.reset_index(drop=True), Wl.reset_index(drop=True), Hl.reset_index(drop=True)], axis=1)
    inv = [c for c in E.columns if c.startswith(("ta_", "taw_", "tah_")) and E[c].notna().mean() > 0.05]
    log(f"сигналов {len(E)}; признаков: боевых {len(base)}, Investing {len(inv)}; часовые есть у {E['tah_score'].notna().mean():.0%}")
    yr = E.d.dt.year.values
    out["B"] = {}
    for nm, R in RR.items():
        log(f"--- {nm} ---")
        ok = np.isfinite(R)
        tr, te = ok & (yr < 2021), ok & (yr >= 2021)
        res = dict(labels={})
        for col, name in (("ta_label", "дневная сводка"), ("tah_label", "часовая сводка"), ("taw_label", "недельная сводка")):
            parts = []
            for lab in (-2, -1, 0, 1, 2):
                m = (E[col].values == lab)
                a_, b_ = R[m & tr], R[m & te]
                if len(b_) >= 15:
                    parts.append(f"{LABEL[lab]}: до 2021 {np.nanmean(a_) if len(a_) else np.nan:+.2%} ({len(a_)}) / с 2021 {np.nanmean(b_):+.2%} ({len(b_)})")
                    res["labels"].setdefault(col, {})[lab] = dict(tr=float(np.nanmean(a_)) if len(a_) else None, te=float(np.nanmean(b_)), n_te=len(b_))
            log(f"  {name} в сторону сигнала — " + "; ".join(parts))
        res["base"] = E17.model_filter(E, R, base, nm + " | боевые признаки")
        res["inv"] = E17.model_filter(E, R, base + inv, nm + " | боевые + Investing")
        rs2 = np.random.RandomState(5)
        nz = [f"шумB{k}" for k in range(len(inv))]
        for k in nz:
            E[k] = rs2.normal(size=len(E))
        res["noise"] = E17.model_filter(E, R, base + nz, nm + " | боевые + столько же случайных")
        E = E.drop(columns=nz)
        out["B"][nm] = res
        json.dump(out, open(os.path.join(TR.MODELS, "exp19_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №19 =====")
    b = out["A"].get("боевые")
    for nm in (("боевые + Investing", "боевые + случайные") if b else ()):
        r = out["A"][nm]
        log(f"A {nm}: топ-5 {r['top5']:+.2%} ({r['top5'] - b['top5']:+.2%} к боевой), AUC {r['auc'] - b['auc']:+.4f}, направление {r['dir']['acc'] - b['dir']['acc']:+.1%}")
    for nm, res in out["B"].items():
        if res["base"] and res["inv"]:
            yb = {o["y"]: o["top30"]["mean"] for o in res["base"]["years"]}
            yi = {o["y"]: o["top30"]["mean"] for o in res["inv"]["years"]}
            better = sum(1 for y in yi if y in yb and yi[y] > yb[y])
            yn = {o["y"]: o["top30"]["mean"] for o in (res.get("noise") or {}).get("years", [])}
            bn = sum(1 for y in yn if y in yb and yn[y] > yb[y])
            log(f"B {nm}: модель-фильтр верхние 30% — без Investing {res['base']['top30']['mean']:+.2%}, с Investing {res['inv']['top30']['mean']:+.2%} "
                f"(лучше в {better} из {len(yi)} лет), со случайными {(res.get('noise') or {}).get('top30', {}).get('mean', float('nan')):+.2%} "
                f"(лучше в {bn} из {len(yn)} лет)")
    log("время", round(time.time() - t0), "с")
    json.dump(out, open(os.path.join(TR.MODELS, "exp19_report.json"), "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
