# -*- coding: utf-8 -*-
"""Проверка №11: точки входа и уровни для ценовых аномалий (часовые свечи 2020–2026, ~160 самых торгуемых акций).

Сигнал — первый час дня (10…17, по закрытию часа), когда бумага стала ценовой аномалией: |изменение к индексу от закрытия
вчера| ≥ порога (4/6/8% по эшелону или 3σ) — это момент, когда бот публикует аномалию. Направление — знак движения.
Все доходности — по направлению движения (рост → покупка, падение → продажа), минус издержки COST (по умолчанию 0,1% за сделку).

ВХОДЫ (сравнение с базой «сразу по сигналу»):
  E0 сразу по цене сигнала;  E_pb25/38/50 — лимит на откате на 25/38/50% хода от вчерашнего закрытия (в тот же день);
  E_open1 — открытие следующего дня;  E_brk — пробой экстремума дня на момент сигнала (продолжение).
  Выходы: закрытие дня (с вечерней сессией), закрытие D+1, закрытие D+3.
УРОВНИ (оцениваются на прошлых годах, проверяются на следующем — «вне выборки»):
  «дойдёт до» — медиана и 70-й процентиль максимального хода от цены сигнала (до закрытия дня и за 3 дня),
     по группам: эшелон × сила движения × час сигнала; вариант «модель» — градиентный бустинг с квантильной функцией потерь;
  «уровень продолжения» — экстремум дня на момент сигнала: вероятность пробоя и цель после пробоя (медиана расширения).
РЕЗУЛЬТАТИВНОСТЬ: стратегии «вход по сигналу, выход на уровне "дойдёт до" или на закрытии» и «вход на пробое, выход на цели
  или на закрытии» против «держать до закрытия»; средняя доходность на сигнал, доля прибыльных, по годам, 90% интервал
  разницы (бутстрэп по дням).
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
import core_daily as CD  # noqa: E402
import core_intra as CI  # noqa: E402
import exp9 as E9  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
COST = float(os.environ.get("EXP_COST", "0.001"))
FIRST_TEST = int(os.environ.get("EXP_FIRST_TEST", "2022"))
PB = (0.25, 0.382, 0.5)
MIN_TR = int(os.environ.get("EXP_MIN_TRAIN", "300"))


# ------------------------------------------------------------------ события
def events(X, Hh, IXH, Dd):
    ixC, _, _, _ = CI.pivot(IXH)
    ixf = ixC.ffill(axis=1)
    ixl = ixf.iloc[:, -1].shift(1)
    rows = []
    for t, a in Hh.items():
        dd = X[X.t == t].set_index("d")
        if len(dd) < 100 or not a or t not in Dd:
            continue
        C, V, H, L = CI.pivot(a)
        days = C.index.intersection(dd.index)
        if len(days) < 100:
            continue
        C, H, L = C.loc[days], H.loc[days], L.loc[days]
        Cf = C.ffill(axis=1)
        pc = Cf.iloc[:, -1].shift(1)
        O = CI.pivot([[r[0], r[1], r[1], r[1], r[1], r[5]] for r in a])[0].reindex(days)  # открытие часа
        tier, sd60 = dd.loc[days, "tier"].values, dd.loc[days, "sd60"].values
        thr, _ = CI.thresholds(tier, sd60)
        D = CD.series(Dd[t]).set_index("d")
        dix = {d: i for i, d in enumerate(D.index)}
        ixr_all = {c: (ixf[c].reindex(days) / ixl.reindex(days) - 1).fillna(0) for c in CI.CHK if c in ixf.columns}
        hrs = list(C.columns)
        done = np.zeros(len(days), bool)
        for c in CI.CHK:
            if c not in C.columns:
                continue
            cx = (Cf[c] / pc - 1) - ixr_all.get(c, 0)
            hit = (np.abs(cx.values) >= thr) & ~done & np.isfinite(cx.values) & np.isfinite(pc.values)
            done |= hit
            for i in np.where(hit)[0]:
                d = days[i]
                s = 1.0 if cx.values[i] > 0 else -1.0
                p0, prev = float(Cf[c].iloc[i]), float(pc.iloc[i])
                past = [h for h in hrs if h <= c]
                fut = [h for h in hrs if h > c]
                H0, L0 = float(H.loc[d, past].max()), float(L.loc[d, past].min())
                fh = H.loc[d, fut].dropna().values.astype(float)
                fl = L.loc[d, fut].dropna().values.astype(float)
                close = float(Cf.loc[d].dropna().iloc[-1])
                j = dix.get(d)
                if j is None or j + 3 >= len(D) or not p0:
                    continue
                n1, n3 = D.iloc[j + 1], D.iloc[j + 1: j + 4]
                rows.append(dict(t=t, d=d, c=c, s=s, p0=p0, prev=prev, H0=H0, L0=L0, close=close, tier=int(tier[i]), sd60=float(sd60[i]),
                                 acx=float(abs(cx.values[i]) / thr[i]), mv=float(p0 / prev - 1), fh=fh, fl=fl,
                                 o1=float(n1.o), c1=float(n1.c), c3=float(n3.c.iloc[-1]), h3=float(n3.h.max()), l3=float(n3.l.min()),
                                 nw=float(dd.loc[d, "nw_n"]) if "nw_n" in dd.columns else 0.0, lval=float(dd.loc[d, "lval"])))
    E = pd.DataFrame(rows)
    log("сигналов (первый час ценовой аномалии):", len(E), "| бумаг", E.t.nunique() if len(E) else 0, "| дней", E.d.nunique() if len(E) else 0)
    return E


def ret(s, e, x):
    return s * (x / e - 1) - COST


def mfe(r, entry):
    """Максимальный ход по направлению от цены входа: до закрытия дня и за 3 дня (дни D+1…D+3 по дневным свечам)."""
    s = r.s
    if s > 0:
        d = (max(r.fh.max() if len(r.fh) else entry, entry) / entry - 1)
        d3 = max(d, r.h3 / entry - 1)
        a = (min(r.fl.min() if len(r.fl) else entry, entry) / entry - 1)
    else:
        d = (1 - min(r.fl.min() if len(r.fl) else entry, entry) / entry)
        d3 = max(d, 1 - r.l3 / entry)
        a = -(max(r.fh.max() if len(r.fh) else entry, entry) / entry - 1)
    return d, d3, a


def add_paths(E):
    m = E.apply(lambda r: mfe(r, r.p0), axis=1, result_type="expand")
    E["mfe_d"], E["mfe_3"], E["mae_d"] = m[0], m[1], m[2]
    E["r0_close"] = ret(E.s, E.p0, E.close)
    E["r0_d1"] = ret(E.s, E.p0, E.c1)
    E["r0_d3"] = ret(E.s, E.p0, E.c3)
    E["r_open1_d1"] = ret(E.s, E.o1, E.c1)
    E["r_open1_d3"] = ret(E.s, E.o1, E.c3)
    for k in PB:
        lvl = E.p0 - E.s * k * (E.p0 - E.prev).abs()
        fill = [((r.fl <= lv).any() if r.s > 0 else (r.fh >= lv).any()) if len(r.fh) else False for r, lv in zip(E.itertuples(), lvl)]
        tag = f"pb{int(round(k * 100))}"
        E[f"{tag}_fill"] = fill
        E[f"r_{tag}_close"] = np.where(fill, ret(E.s, lvl, E.close), np.nan)
        E[f"r_{tag}_d1"] = np.where(fill, ret(E.s, lvl, E.c1), np.nan)
        E[f"r_{tag}_d3"] = np.where(fill, ret(E.s, lvl, E.c3), np.nan)
    B = np.where(E.s > 0, E.H0, E.L0)
    E["B"] = B
    brk = [((r.fh > b).any() if r.s > 0 else (r.fl < b).any()) if len(r.fh) else False for r, b in zip(E.itertuples(), B)]
    E["brk"] = brk
    ext = []
    for r, b, k in zip(E.itertuples(), B, brk):
        if not k:
            ext.append(np.nan)
        else:
            ext.append((r.fh.max() / b - 1) if r.s > 0 else (1 - r.fl.min() / b))
    E["ext"] = ext
    E["r_brk_close"] = np.where(brk, ret(E.s, B, E.close), np.nan)
    E["r_brk_d3"] = np.where(brk, ret(E.s, B, E.c3), np.nan)
    E["grp"] = (E.tier.astype(str) + "|" + pd.cut(E.acx, [0, 1.25, 1.6, 99], labels=["a", "b", "c"]).astype(str) + "|"
                + pd.cut(E.c, [0, 12, 15, 99], labels=["m", "d", "e"]).astype(str))
    return E


# ------------------------------------------------------------------ уровни вне выборки
def levels_oos(E):
    """По каждому году Y: квантили из лет < Y по группам (с откатом на общий квантиль при малой группе) → уровни для Y."""
    out = []
    for Y in sorted(E.d.dt.year.unique()):
        if Y < FIRST_TEST:
            continue
        tr, te = E[E.d.dt.year < Y], E[E.d.dt.year == Y].copy()
        if len(tr) < MIN_TR or not len(te):
            continue

        def q(col, p, sub=None):
            g = (sub if sub is not None else tr).groupby("grp")[col]
            cnt, qq = g.count(), g.quantile(p)
            glob = (sub if sub is not None else tr)[col].quantile(p)
            return te.grp.map(lambda k: qq[k] if k in qq and cnt[k] >= max(5, MIN_TR // 8) else glob).astype(float)
        te["q50_d"], te["q70_d"] = q("mfe_d", 0.5), q("mfe_d", 0.7)
        te["q50_3"], te["q70_3"] = q("mfe_3", 0.5), q("mfe_3", 0.7)
        te["mae50"] = q("mae_d", 0.3)  # «обычная просадка»: 30-й процентиль (70% сигналов просаживаются меньше)
        te["p_brk"] = te.grp.map(tr.groupby("grp").brk.mean()).fillna(tr.brk.mean())
        te["ext50"] = q("ext", 0.5, tr[tr.brk])
        te["ext70"] = q("ext", 0.7, tr[tr.brk])
        out.append(te)
    return pd.concat(out)  # индексы как в E — к ним привязаны уровни модели


def model_levels(E, feats=("acx", "tier", "sd60", "c", "mv", "nw", "lval"), alpha=0.5, col="mfe_d"):
    """Тот же уровень, но квантильный градиентный бустинг по признакам сигнала (обучение на прошлых годах)."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    out = []
    F = list(feats)
    E = E.assign(mv=E.mv.abs())
    for Y in sorted(E.d.dt.year.unique()):
        if Y < FIRST_TEST:
            continue
        tr, te = E[E.d.dt.year < Y], E[E.d.dt.year == Y]
        if len(tr) < MIN_TR or not len(te):
            continue
        m = HistGradientBoostingRegressor(loss="quantile", quantile=alpha, max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                          min_samples_leaf=40, random_state=0).fit(tr[F], tr[col])
        out.append(pd.Series(np.clip(m.predict(te[F]), 0.002, None), index=te.index))
    return pd.concat(out)


# ------------------------------------------------------------------ стратегии и сравнение
def tp_close(L, lvl_col, mfe_col="mfe_d", exit_col="close"):
    """Вход по сигналу, тейк на уровне, иначе выход на закрытии (дня или D+3)."""
    reached = L[mfe_col] >= L[lvl_col]
    ex = L[exit_col]
    r_ex = ret(L.s, L.p0, ex)
    return np.where(reached, L[lvl_col] - COST, r_ex), reached


def stats(x, name, days=None):
    x = pd.Series(x).dropna()
    if not len(x):
        return dict(name=name, n=0)
    r = dict(name=name, n=int(len(x)), mean=round(float(x.mean()), 5), med=round(float(x.median()), 5), win=round(float((x > 0).mean()), 3),
             pf=round(float(x[x > 0].sum() / max(1e-9, -x[x < 0].sum())), 2))
    log(f"  {name}: сделок {r['n']}, в среднем {r['mean']:+.2%}, медиана {r['med']:+.2%}, прибыльных {r['win']:.0%}, профит-фактор {r['pf']}")
    return r


def by_year(L, x, name):
    s = pd.Series(np.asarray(x, float), index=L.index)
    yr = s.groupby(L.d.dt.year).mean()
    log(f"  {name} по годам: " + ", ".join(f"{y}: {v:+.2%}" for y, v in yr.items()))
    return {int(k): round(float(v), 5) for k, v in yr.items()}


def diff_ci(L, a, b, name):
    """b − a на сигнал (неисполненные входы = 0), 90% интервал бутстрэпом по дням, лучше в скольких годах."""
    D = pd.DataFrame({"d": L.d.values, "a": np.nan_to_num(np.asarray(a, float)), "b": np.nan_to_num(np.asarray(b, float))})
    g = D.groupby("d")[["a", "b"]].sum()
    n = len(D)
    diff = (g.b - g.a).to_numpy()
    rs = np.random.RandomState(3)
    boot = [rs.choice(diff, len(diff)).sum() / n for _ in range(2000)]
    yr = D.assign(y=pd.to_datetime(D.d).dt.year).groupby("y").apply(lambda x: (x.b - x.a).mean(), include_groups=False)
    r = dict(name=name, delta=round(float(diff.sum() / n), 5), lo=round(float(np.percentile(boot, 5)), 5), hi=round(float(np.percentile(boot, 95)), 5),
             better_years=f"{int((yr > 0).sum())}/{len(yr)}")
    log(f"Δ {name}: {r['delta']:+.3%} на сигнал (90%: {r['lo']:+.3%}…{r['hi']:+.3%}), лучше в {r['better_years']} лет")
    return r


def main():
    os.makedirs(TR.MODELS, exist_ok=True)
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    E = add_paths(events(X, Hh, IXH, Dd))
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), cost=COST, n=int(len(E)), entries=[], levels={}, strat=[], deltas=[])
    path = os.path.join(TR.MODELS, "exp11_report.json")

    def save():
        json.dump(out, open(path, "w"), ensure_ascii=False, indent=1, default=str)
    T = E[E.d.dt.year >= FIRST_TEST]
    log(f"===== ВХОДЫ (годы {FIRST_TEST}+, сигналов {len(T)}, издержки {COST:.2%}) =====")
    log(f"доля сигналов-роста {float((T.s > 0).mean()):.0%}; медианный ход к сигналу {float(T.mv.abs().median()):.1%}")
    for ex in ("close", "d1", "d3"):
        out["entries"].append(stats(T[f"r0_{ex}"], f"E0 сразу по сигналу → выход {ex}"))
        by_year(T, T[f"r0_{ex}"], f"E0 → {ex}")
    for ex in ("d1", "d3"):
        out["entries"].append(stats(T[f"r_open1_{ex}"], f"открытие D+1 → {ex}"))
    for k in PB:
        tag = f"pb{int(round(k * 100))}"
        log(f"откат {tag}: исполнено {float(T[f'{tag}_fill'].mean()):.0%} сигналов")
        for ex in ("close", "d1", "d3"):
            out["entries"].append(stats(T[f"r_{tag}_{ex}"], f"откат {tag}% → {ex} (только исполненные)"))
        sub = T[T[f"{tag}_fill"]]
        stats(sub["r0_close"], f"   для сравнения: E0 на тех же сигналах → close")
        for ex in ("close", "d3"):
            out["deltas"].append(diff_ci(T, T[f"r0_{ex}"], T[f"r_{tag}_{ex}"], f"откат {tag}% против входа сразу, выход {ex} (неисполненные = 0)"))
    log(f"пробой экстремума дня на момент сигнала: случился в {float(T.brk.mean()):.0%} сигналов")
    out["entries"].append(stats(T.r_brk_close, "вход на пробое → close"))
    out["entries"].append(stats(T.r_brk_d3, "вход на пробое → d3"))
    save()

    log("===== УРОВНИ «вне выборки» =====")
    L = levels_oos(E)
    lv = {}
    for col, mf, nm in (("q50_d", "mfe_d", "медиана хода до закрытия"), ("q70_d", "mfe_d", "70-й процентиль до закрытия"),
                        ("q50_3", "mfe_3", "медиана хода за 3 дня"), ("q70_3", "mfe_3", "70-й процентиль за 3 дня")):
        reach = float((L[mf] >= L[col]).mean())
        lv[col] = dict(reach=round(reach, 3), level_med=round(float(L[col].median()), 4))
        log(f"уровень «{nm}»: в среднем {float(L[col].median()):.1%} от цены сигнала; достигнут в {reach:.0%} случаев (ожидалось "
            f"{'50' if '50' in col else '30'}%)")
    L["q50m"] = model_levels(E).reindex(L.index)
    reach_m = float((L.mfe_d >= L.q50m).mean())
    lv["q50_model"] = dict(reach=round(reach_m, 3), level_med=round(float(L.q50m.median()), 4))
    log(f"уровень «медиана до закрытия», модель: {float(L.q50m.median()):.1%}; достигнут в {reach_m:.0%}")
    dd_ = float((L.mae_d <= L.mae50).mean())
    log(f"«обычная просадка» после сигнала: медиана {float(L.mae50.median()):.1%}; глубже неё ушли {dd_:.0%} сигналов (ожидалось 30%)")
    pb = L.brk.mean()
    log(f"уровень продолжения (экстремум дня на сигнале): прогноз пробоя {float(L.p_brk.mean()):.0%}, факт {float(pb):.0%}")
    Lb = L[L.brk]
    reach_e = float((Lb.ext >= Lb.ext50).mean()) if len(Lb) else np.nan
    log(f"после пробоя цель (медиана расширения {float(Lb.ext50.median()):.1%} за уровнем) достигнута в {reach_e:.0%} (ожидалось 50%)")
    lv["brk"] = dict(p_pred=round(float(L.p_brk.mean()), 3), p_fact=round(float(pb), 3), ext50=round(float(Lb.ext50.median()), 4), reach=round(reach_e, 3))
    for g, x in L.groupby("tier"):
        log(f"  эшелон {g}: «дойдёт до» (медиана) {float(x.q50_d.median()):.1%}, достигнут {float((x.mfe_d >= x.q50_d).mean()):.0%}; "
            f"пробой {float(x.brk.mean()):.0%}; сигналов {len(x)}")
    out["levels"] = lv
    save()

    log("===== РЕЗУЛЬТАТИВНОСТЬ: выход на уровне против «держать» =====")
    base_c = ret(L.s, L.p0, L.close)
    base_3 = ret(L.s, L.p0, L.c3)
    out["strat"].append(stats(base_c, "держать до закрытия дня"))
    by_year(L, base_c, "держать до закрытия")
    out["strat"].append(stats(base_3, "держать до закрытия D+3"))
    for col, mf, exc, nm in (("q50_d", "mfe_d", "close", "тейк «медиана до закрытия», иначе закрытие"),
                             ("q70_d", "mfe_d", "close", "тейк «70-й процентиль», иначе закрытие"),
                             ("q50m", "mfe_d", "close", "тейк по модели (медиана), иначе закрытие"),
                             ("q50_3", "mfe_3", "c3", "тейк «медиана за 3 дня», иначе закрытие D+3")):
        r, _ = tp_close(L, col, mf, exc)
        out["strat"].append(stats(r, nm))
        by_year(L, r, nm)
        out["deltas"].append(diff_ci(L, base_c if exc == "close" else base_3, r, nm + " против «держать»"))
    # вход на пробое уровня продолжения с целью
    rb = np.where(L.brk, np.where(L.ext >= L.ext50, L.ext50 - COST, ret(L.s, L.B, L.close)), np.nan)
    out["strat"].append(stats(rb, "вход на пробое, тейк на цели, иначе закрытие (только пробои)"))
    out["strat"].append(stats(np.where(L.brk, ret(L.s, L.B, L.close), np.nan), "вход на пробое, держать до закрытия (только пробои)"))
    out["deltas"].append(diff_ci(L, base_c, rb, "вход на пробое с целью против «сразу и держать» (без пробоя = 0)"))
    # откат + тейк
    for k in PB:
        tag = f"pb{int(round(k * 100))}"
        lvl = L.p0 - L.s * k * (L.p0 - L.prev).abs()
        tp = L.p0 * (1 + L.s * L.q50_d)
        r = []
        for row, lv_, tp_ in zip(L.itertuples(), lvl, tp):
            fh, fl = row.fh, row.fl
            n = min(len(fh), len(fl))
            idx = next((i for i in range(n) if (fl[i] <= lv_ if row.s > 0 else fh[i] >= lv_)), None)
            if idx is None:
                r.append(np.nan)
                continue
            # тейк — только в часы после исполнения отката (в час исполнения порядок внутри часа неизвестен — не засчитываем)
            hit = any((fh[i] >= tp_ if row.s > 0 else fl[i] <= tp_) for i in range(idx + 1, n))
            r.append(row.s * (tp_ / lv_ - 1) - COST if hit else row.s * (row.close / lv_ - 1) - COST)
        r = np.array(r, float)
        out["strat"].append(stats(r, f"откат {tag}% + тейк на уровне сигнала «медиана», иначе закрытие (исполненные)"))
        out["deltas"].append(diff_ci(L, base_c, r, f"откат {tag}% + тейк против «сразу и держать»"))
    save()
    log("===== ИТОГ ПРОВЕРКИ №11 =====")
    for d in out["deltas"]:
        log(f"Δ {d['name']}: {d['delta']:+.3%} (90%: {d['lo']:+.3%}…{d['hi']:+.3%}), лучше в {d['better_years']} лет")


if __name__ == "__main__":
    main()
