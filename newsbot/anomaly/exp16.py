# -*- coding: utf-8 -*-
"""Проверка №16: подтверждение и отмена идеи аномалии внутри дня. Сколько ждать аномалию и при каких условиях.

Идея пользователя: если бумага из утреннего прогноза уже идёт в предсказанную сторону, деньги (покупки/продажи) и оборот это
подтверждают, но резкого движения ещё нет — сообщить «признаки подтверждаются, аномалия может случиться позже»; если бумага
уходит в обратную сторону — «идея отменяется». Здесь проверяем на истории, работает ли это и на какой срок.

Кандидаты: каждый день 10 бумаг с наибольшим утренним прогнозом аномалии (прогноз «вне выборки»: модель каждого года учится
только на прошлых годах, как боевая), только с уверенным направлением (P(вверх | аномалия) ≥ 60% или ≤ 40%) и с часовыми
свечами. Моменты проверки — 11:00, 13:00, 15:00 МСК; берутся только бумаги, ещё не ставшие аномалией к этому часу.
Признаки в момент проверки (знак s — предсказанное направление):
  ход    a   = s × (изменение к индексу от вчерашнего закрытия) / порог аномалии эшелона;
  деньги fl  = s × (покупки − продажи) / (покупки + продажи) с начала дня — 5-минутные tradestats ALGOPACK (val_b/val_s);
         flh = то же за последний час;  fp = s × то же за 3 предыдущих дня (дневные данные ALGOPACK, кэш проверки №6);
  оборот v   = оборот с начала дня к обычному на этот час (последние 60 дней).
Исходы: ценовая аномалия в предсказанную сторону позже в тот же день (D0), к D+1, D+2, D+5, D+10; в обратную сторону; любая.
Срок ожидания: в какой день случается первая аномалия в предсказанную сторону и во сколько раз чаще, чем у всех кандидатов.
Правила «подтверждение» и «отмена» выбираются по 2021–2023 гг., цифры итога — на 2024–2026 гг., которых выбор не видел.
Нужны ALGOPACK_TOKEN и RU_CA (5-минутные данные по кандидатам, кэш EXP_A16_DIR). Это исследование, а не сигнал на сделку.
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
import algopack as AP  # noqa: E402
import core_daily as CD  # noqa: E402
import core_intra as CI  # noqa: E402
import exp9 as E9  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
A16 = os.environ.get("EXP_A16_DIR") or os.path.join(TR.DATA, "a16")
BUDGET = float(os.environ.get("EXP_A16_BUDGET_MIN", "150")) * 60
TOPN = int(os.environ.get("EXP_TOPN", "10"))
FIRST = int(os.environ.get("EXP_FIRST_TEST", "2021"))
SPLIT = int(os.environ.get("EXP_SPLIT", "2024"))           # выбор правил — годы до SPLIT, итог — SPLIT и позже
CHECKS = (11, 13, 15)
K = 10                                                    # горизонт ожидания, торговых дней после дня сигнала
MIN_N = int(os.environ.get("EXP_MIN_N", "150"))


# ------------------------------------------------------------------ прогноз вне выборки
def dir_oof(X, FD, years):
    D = X[(X.y == 1) & X.x.notna()]
    out = []
    for Y in years:
        tr, te = D[D.d.dt.year < Y], X[X.d.dt.year == Y]
        if len(tr) < 300 or te.empty:
            continue
        clf = TR.hgb_dir().fit(tr[FD], (tr.x > 0).astype(int))
        out.append(te[["d", "t"]].assign(p_up=clf.predict_proba(te[FD])[:, 1]))
        log("  направление, год", Y, "готов")
    return pd.concat(out, ignore_index=True)


def future(X):
    """По каждой бумаге и дню: ценовая аномалия вверх/вниз и любая в дни D+0…D+K (следующие строки бумаги = торговые дни)."""
    X = X.sort_values(["t", "d"]).reset_index(drop=True)
    g = X.groupby("t")
    F = {}
    for k in range(K + 1):
        F[f"u{k}"] = g.y_up.shift(-k).values
        F[f"n{k}"] = g.y_dn.shift(-k).values
        F[f"a{k}"] = g.y.shift(-k).values
    return pd.concat([X[["d", "t"]], pd.DataFrame(F)], axis=1)


# ------------------------------------------------------------------ 5-минутные потоки ALGOPACK по кандидатам
def load_flows(C, token):
    """{(t, 'YYYY-MM-DD'): [[время бара, покупки ₽, продажи ₽], ...]} для пар бумага-день из C; кэш по файлу на бумагу-день."""
    os.makedirs(A16, exist_ok=True)
    need = sorted({(t, d.date().isoformat()) for t, d in zip(C.t, C.d)})
    have = {}
    miss = []
    for t, d in need:
        p = os.path.join(A16, f"{t}_{d}.json.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                have[(t, d)] = json.load(f)
        else:
            miss.append((t, d))
    log(f"5-минутные потоки: нужно {len(need)} бумаго-дней, в кэше {len(have)}, докачать {len(miss)}")
    if not token:
        log("нет ALGOPACK_TOKEN — потоки внутри дня не считаем")
        return have
    # подряд идущие дни одной бумаги — одним запросом (кандидаты часто повторяются несколько дней)
    runs = []
    for t, grp in itertools.groupby(sorted(miss), key=lambda x: x[0]):
        ds = [dt.date.fromisoformat(d) for _, d in grp]
        s = e = ds[0]
        for d in ds[1:]:
            if (d - e).days <= 4 and (d - s).days <= 14:
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
            return run, None
        try:
            df = E14.fetch_range("tradestats", t, s.isoformat(), e.isoformat(), token)
        except Exception as ex:  # noqa: BLE001
            log("ALGOPACK", t, s, str(ex)[:120])
            return run, None
        res = {}
        if not df.empty:
            df["vb"] = pd.to_numeric(df.get("val_b"), errors="coerce")
            df["vs"] = pd.to_numeric(df.get("val_s"), errors="coerce")
            for d, g in df.groupby(df["tradedate"].astype(str)):
                res[d] = [[str(a)[:5], float(b or 0), float(c or 0)] for a, b, c in zip(g.tradetime, g.vb.fillna(0), g.vs.fillna(0))
                          if str(a)[:5] <= "16:05"]
        out = {}
        d = s
        while d <= e:
            if d.weekday() < 5:
                k = d.isoformat()
                bars = res.get(k, [])
                with gzip.open(os.path.join(A16, f"{t}_{k}.json.gz"), "wt") as f:
                    json.dump(bars, f, separators=(",", ":"))
                out[(t, k)] = bars
            d += dt.timedelta(days=1)
        return run, out
    done = 0
    with cf.ThreadPoolExecutor(int(os.environ.get("EXP_A16_WORKERS", "8"))) as ex:
        for k, (run, out) in enumerate(ex.map(one, runs)):
            if out:
                have.update(out)
                done += 1
            if k % 200 == 0:
                log(f"  ALGOPACK 5 мин: {k}/{len(runs)} запросов, бумаго-дней с данными {len(have)}")
    log(f"5-минутные потоки: готово {len(have)} из {len(need)} бумаго-дней (запросов успешно {done} из {len(runs)})")
    return have


def flow_feats(bars, c):
    """Доля покупок минус продаж с начала дня до (c+1):00 и за последний час. Время бара в tradestats — его конец."""
    if not bars:
        return np.nan, np.nan
    end, beg = f"{c + 1:02d}:00", f"{c:02d}:00"
    vb = sum(b[1] for b in bars if b[0] <= end)
    vs = sum(b[2] for b in bars if b[0] <= end)
    hb = sum(b[1] for b in bars if beg < b[0] <= end)
    hs = sum(b[2] for b in bars if beg < b[0] <= end)
    f = (vb - vs) / (vb + vs) if vb + vs > 0 else np.nan
    h = (hb - hs) / (hb + hs) if hb + hs > 0 else np.nan
    return f, h


def prior_flows():
    """Дисбаланс покупок/продаж за 3 предыдущих торговых дня по дневным данным ALGOPACK (кэш проверки №6)."""
    try:
        A = AP.load()
    except Exception as e:  # noqa: BLE001
        log("дневные ALGOPACK:", e)
        return pd.DataFrame(columns=["d", "t", "fp_raw"])
    if A.empty or "val_b" not in A.columns:
        log("дневных данных ALGOPACK в кэше нет — признак «деньги за 3 дня» не считаем")
        return pd.DataFrame(columns=["d", "t", "fp_raw"])
    A = A[["t", "d", "val_b", "val_s"]].dropna().sort_values(["t", "d"])
    g = A.groupby("t")
    b3 = g.val_b.transform(lambda s: s.shift(1).rolling(3, min_periods=2).sum())
    s3 = g.val_s.transform(lambda s: s.shift(1).rolling(3, min_periods=2).sum())
    A["fp_raw"] = (b3 - s3) / (b3 + s3)
    log("дневные ALGOPACK:", len(A), "строк,", A.t.nunique(), "бумаг,", str(A.d.min().date()), "–", str(A.d.max().date()))
    return A[["d", "t", "fp_raw"]]


# ------------------------------------------------------------------ метрики
def wilson(p, n, z=1.96):
    if n == 0:
        return 0.0, 1.0
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def outcome(S):
    """Для каждой строки: первый день (0…K) ценовой аномалии в предсказанную сторону / в обратную / любой; NaN — не было."""
    up = S.s.values > 0
    U = np.vstack([S[f"u{k}"].values for k in range(K + 1)]).T
    N = np.vstack([S[f"n{k}"].values for k in range(K + 1)]).T
    A = np.vstack([S[f"a{k}"].values for k in range(K + 1)]).T
    Dm = np.where(up[:, None], U, N)
    Om = np.where(up[:, None], N, U)

    def first(M):
        M = np.nan_to_num(M) > 0
        return np.where(M.any(axis=1), M.argmax(axis=1), np.nan)
    S = S.copy()
    S["kd"], S["ko"], S["ka"] = first(Dm), first(Om), first(A)
    return S


def stats(S, m=None):
    S = S if m is None else S[m]
    n = len(S)
    if n == 0:
        return dict(n=0)
    kd = S.kd.values
    r = dict(n=n)
    for k in (0, 1, 2, 5, 10):
        r[f"d{k}"] = float(np.mean(kd <= k))
    r["o2"] = float(np.mean(S.ko.values <= 2))
    r["a0"] = float(np.mean(S.ka.values <= 0))
    w = kd[np.isfinite(kd)]
    r["wait_mean"] = float(w.mean()) if len(w) else None
    r["wait_med"] = float(np.median(w)) if len(w) else None
    r["lo2"], r["hi2"] = wilson(r["d2"], n)
    return r


def fmt(r, base=None):
    if not r.get("n"):
        return "нет случаев"
    lift = f" (×{r['d2'] / base['d2']:.1f} к базе)" if base and base.get("d2") else ""
    return (f"n={r['n']}: в свою сторону сегодня {r['d0']:.1%}, к D+1 {r['d1']:.1%}, к D+2 {r['d2']:.1%}{lift}, к D+5 {r['d5']:.1%}, "
            f"к D+10 {r['d10']:.1%}; в обратную к D+2 {r['o2']:.1%}; любая сегодня {r['a0']:.1%}; "
            f"ждать (если случилась за 10 дн.): в среднем {r['wait_mean'] or 0:.1f} дн., медиана {r['wait_med'] or 0:.0f}")


def hazard(S, m):
    """Вероятность первой аномалии в свою сторону именно в день k, если раньше её не было: правило против всех кандидатов."""
    out = []
    for k in range(K + 1):
        a = S[m]
        b = S
        ra = ((a.kd == k).sum()) / max(1, ((a.kd >= k) | a.kd.isna()).sum())
        rb = ((b.kd == k).sum()) / max(1, ((b.kd >= k) | b.kd.isna()).sum())
        out.append((k, ra, rb))
    return out


def rule_mask(S, r):
    m = np.ones(len(S), bool)
    for col, op, v in r:
        if v is None:
            continue
        x = S[col].values
        m &= (x >= v) if op == ">=" else (x <= v)
    return m


def rule_name(r):
    nm = {"a": "ход", "fl": "деньги за день", "flh": "деньги за час", "v": "оборот к обычному", "fp": "деньги за 3 дня"}
    parts = []
    for col, op, v in r:
        if v is None:
            continue
        if col == "a":
            parts.append(f"ход в свою сторону ≥ {v:.0%} порога" if op == ">=" else f"ход против ≥ {-v:.0%} порога")
        elif col == "v":
            parts.append(f"оборот ≥ ×{v:g} к обычному")
        else:
            parts.append(f"{nm[col]} {'≥' if op == '>=' else '≤'} {v:+.0%}")
    return "; ".join(parts) or "все кандидаты"


def search(S, tr, grid, better):
    base = stats(S[tr])
    best = None
    rows = []
    for r in grid:
        m = rule_mask(S, r) & tr
        st = stats(S, m)
        if st["n"] < MIN_N:
            continue
        key = st["lo2"] if better == "hi" else -st["hi2"]
        rows.append((key, r, st))
        if best is None or key > best[0]:
            best = (key, r, st)
    rows.sort(key=lambda x: -x[0])
    return base, best, rows[:8]


def table(S, col, bins, label, scale=1.0):
    log(f"--- {label} (все годы {FIRST}–, все моменты) ---")
    b = stats(S)
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (S[col].values >= lo) & (S[col].values < hi)
        if m.sum() < 30:
            continue
        r = stats(S, m)
        log(f"  {lo * scale:+g}…{hi * scale:+g}: n={r['n']}, своя сторона сегодня {r['d0']:.1%}, к D+2 {r['d2']:.1%} (×{r['d2'] / b['d2']:.2f}), "
            f"к D+5 {r['d5']:.1%}; обратная к D+2 {r['o2']:.1%}")


# ------------------------------------------------------------------ main
def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    FD = CD.feature_cols(X)
    yr = int(X.d.dt.year.max())
    years = list(range(FIRST, yr + 1))
    log("утренний прогноз вне выборки…")
    Pm = E9.oof(X, FD, list(range(2020, yr + 1)), nbag=int(os.environ.get("EXP_NBAG", "3")))
    Pd = dir_oof(X, FD, list(range(2020, yr + 1)))
    Fu = future(X)
    P = Pm[["d", "t", "p"]].merge(Pd, on=["d", "t"], how="left")
    hourly = set(Hh)
    P = P[P.t.isin(hourly) & (P.d.dt.year >= FIRST)]
    C = P.sort_values("p", ascending=False).groupby("d").head(TOPN)
    log(f"кандидатов (топ-{TOPN} в день, с часовыми свечами): {len(C)}; уверенное направление: {(np.abs(C.p_up - 0.5) >= 0.1).mean():.0%}")
    # состояние внутри дня (как в обучении внутридневной модели) — только бумаги, ещё не ставшие аномалией к часу
    pre = Pm[["d", "t", "p"]].rename(columns={"p": "p_pre"})
    tick = sorted(set(C.t))
    XI, _ = CI.build_train({t: Hh[t] for t in tick}, IXH, X[X.t.isin(tick)], ev, pre)
    XI = XI[XI.c.isin(CHECKS)][["d", "t", "c", "cx", "cvol_typ", "tier", "sd60"]]
    S = C.merge(XI, on=["d", "t"], how="inner")
    tot = len(C) * len(CHECKS)
    log(f"строк «кандидат × момент», ещё не ставших аномалией: {len(S)} из {tot} ({1 - len(S) / tot:.0%} уже случились или нет часовых)")
    S = S.merge(Fu, on=["d", "t"], how="left")
    S["s"] = np.where(S.p_up >= 0.5, 1.0, -1.0)
    conf = np.abs(S.p_up - 0.5) >= 0.1
    log(f"уверенное направление: {conf.mean():.0%} строк; остальные — «направление неясно», статус по ходу не ставим")
    S = S[conf].reset_index(drop=True)
    thr, _ = CI.thresholds(S.tier.values, S.sd60.values)
    S["a"] = S.s * S.cx / thr
    S["v"] = S.cvol_typ
    # деньги
    flows = load_flows(S[["t", "d"]].drop_duplicates(), (os.environ.get("ALGOPACK_TOKEN") or "").strip())
    ff = [flow_feats(flows.get((t, d.date().isoformat())), int(c)) for t, d, c in zip(S.t, S.d, S.c)]
    S["fl"] = S.s * np.array([f for f, _ in ff], float)
    S["flh"] = S.s * np.array([h for _, h in ff], float)
    FP = prior_flows()
    S = S.merge(FP, on=["d", "t"], how="left")
    S["fp"] = S.s * S.fp_raw
    S = outcome(S)
    log(f"заполнено: деньги за день {S.fl.notna().mean():.0%}, за 3 дня {S.fp.notna().mean():.0%}, оборот к обычному {S.v.notna().mean():.0%}")
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), n=int(len(S)), split=SPLIT)

    # 1) сколько ждать: все кандидаты
    log("===== СКОЛЬКО ЖДАТЬ: все кандидаты (ещё не аномалия к часу проверки) =====")
    for c in CHECKS:
        log(f"  {c}:00 — " + fmt(stats(S, S.c.values == c)))
    b_all = stats(S)
    log("  все моменты — " + fmt(b_all))
    out["base"] = b_all

    # 2) разрезы по одному признаку
    table(S, "a", [-1, -0.5, -0.3, -0.2, -0.1, 0.1, 0.2, 0.3, 0.5, 1.01], "ход к порогу аномалии в предсказанную сторону, доля порога")
    table(S, "fl", [-1, -0.2, -0.1, -0.05, 0.05, 0.1, 0.2, 1.01], "деньги с начала дня в предсказанную сторону (покупки−продажи)/(сумма)")
    table(S, "flh", [-1, -0.2, -0.1, 0.1, 0.2, 1.01], "деньги за последний час в предсказанную сторону")
    table(S, "fp", [-1, -0.1, -0.05, 0.05, 0.1, 1.01], "деньги за 3 предыдущих дня в предсказанную сторону")
    table(S, "v", [0, 0.7, 1.0, 1.3, 2.0, 3.0, 100], "оборот с начала дня к обычному на этот час")

    # 3) поиск правил на ранних годах, итог на поздних
    tr = (S.d.dt.year < SPLIT).values
    te = ~tr
    G_conf = [[("a", ">=", a), ("fl", ">=", f), ("flh", ">=", h), ("v", ">=", v), ("fp", ">=", p)]
              for a in (0.1, 0.2, 0.3, 0.5) for f in (None, 0.0, 0.05, 0.1, 0.2) for h in (None, 0.0, 0.1)
              for v in (None, 1.0, 1.3, 2.0) for p in (None, 0.0, 0.05)]
    G_canc = [[("a", "<=", a), ("fl", "<=", f), ("flh", "<=", h), ("fp", "<=", p)]
              for a in (-0.1, -0.2, -0.3, -0.5) for f in (None, 0.0, -0.05, -0.1, -0.2) for h in (None, 0.0, -0.1)
              for p in (None, 0.0, -0.05)]
    for label, grid, better in (("ПОДТВЕРЖДЕНИЕ", G_conf, "hi"), ("ОТМЕНА", G_canc, "lo")):
        base, best, top = search(S, tr, grid, better)
        log(f"===== {label}: выбор правила по {FIRST}–{SPLIT - 1} (база {fmt(base)}) =====")
        for key, r, st in top:
            log(f"  {rule_name(r)} → " + fmt(st, base))
        if not best:
            log("  ни одно правило не набрало достаточно случаев")
            continue
        r = best[1]
        bt = stats(S, te)
        st = stats(S, rule_mask(S, r) & te)
        log(f"===== {label} — ИТОГ без подгонки на {SPLIT}–{yr}: «{rule_name(r)}» =====")
        log("  правило: " + fmt(st, bt))
        log("  все кандидаты: " + fmt(bt))
        for c in CHECKS:
            mc = te & (S.c.values == c)
            log(f"  {c}:00 — правило: " + fmt(stats(S, rule_mask(S, r) & mc), stats(S, mc)))
        hz = hazard(S[te], rule_mask(S, r)[te])
        log("  шанс первой аномалии в свою сторону в день k (если раньше не было), правило / все: "
            + "; ".join(f"D+{k}: {a:.1%}/{b:.1%}" for k, a, b in hz))
        out[label] = dict(rule=rule_name(r), test=st, base_test=bt, hazard=hz, by_year={})
        for y in range(SPLIT, yr + 1):
            my = (S.d.dt.year.values == y)
            sy = stats(S, rule_mask(S, r) & my)
            if sy.get("n"):
                out[label]["by_year"][y] = sy
                log(f"  {y}: правило n={sy['n']}, к D+2 {sy['d2']:.1%}; все {stats(S, my)['d2']:.1%}")
        json.dump(out, open(os.path.join(TR.MODELS, "exp16_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    json.dump(out, open(os.path.join(TR.MODELS, "exp16_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №16 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
