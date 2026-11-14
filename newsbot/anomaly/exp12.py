# -*- coding: utf-8 -*-
"""Проверка №12: какая стратегия на сигналах ценовых аномалий даёт наибольшую прибыль (часовые свечи 2020–2026, ~160 акций).

Сигнал — как в №11: первый час (10…17), когда |ход к индексу от вчерашнего закрытия| ≥ порога эшелона.
Перебор (≈5 тыс. вариантов):
  сторона:  ПО движению (рост → покупка) или ПРОТИВ (рост → продажа);
  вход:     сразу; лимит на «лучшей» цене 25/38/50% хода (по движению — откат, против — продолжение) в тот же день; открытие D+1;
  тейк:     нет; 25% или 50% хода; (по движению) «дойдёт до» — медиана / 70-й процентиль вне выборки из №11;
  стоп:     нет; 25% или 50% хода; (по движению) «обычная просадка» вне выборки;
  выход:    закрытие дня / D+1 / D+3 / D+5;
  фильтры:  эшелон (все / 1–2 / 3) × направление аномалии (все / рост / падение).
Порядок внутри бара неизвестен: если в одном баре и тейк, и стоп — считаем стоп; в баре исполнения лимита тейк/стоп не проверяем.
Издержки за сделку (вход+выход, спред и комиссия): эшелон 1 — 0,1%, 2 — 0,2%, 3 — 0,3% (EXP_COST1/2/3).
Выбор — только на 2022–2024 (обучение), проверка — на 2025–2026 (их выбор не видел). Капитал: до 10 сделок в день по 10% капитала.
Отдельно — только покупки (без шортов: в Т-Инвестициях шорт доступен не по всем бумагам).
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
import core_daily as CD  # noqa: E402
import core_intra as CI  # noqa: E402
import exp9 as E9  # noqa: E402
import exp11 as E11  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
COST_T = {1: float(os.environ.get("EXP_COST1", "0.001")), 2: float(os.environ.get("EXP_COST2", "0.002")), 3: float(os.environ.get("EXP_COST3", "0.003"))}
TRAIN = (2022, 2024)
TEST = (2025, 2026)
NDAYS = 5
MIN_N = int(os.environ.get("EXP_MIN_N", "30"))
MIN_BIG = int(os.environ.get("EXP_MIN_BIG", "150"))
KH = 14  # часов после сигнала в тот же день (до конца вечерней сессии)


def events(X, Hh, IXH, Dd):
    """Сигналы + путь цены: часы того же дня после сигнала и 5 следующих торговых дней (дневные бары)."""
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
                j = dix.get(d)
                if j is None or j + NDAYS >= len(D):
                    continue
                p0, prev = float(Cf[c].iloc[i]), float(pc.iloc[i])
                if not p0 or not prev:
                    continue
                past = [h for h in hrs if h <= c]
                fut = [h for h in hrs if h > c]
                fr = pd.DataFrame({"h": H.loc[d, fut].values, "l": L.loc[d, fut].values, "c": C.loc[d, fut].values}).dropna()
                nx = D.iloc[j + 1: j + 1 + NDAYS]
                rows.append(dict(t=t, d=d, c=c, s=1.0 if cx.values[i] > 0 else -1.0, p0=p0, prev=prev,
                                 H0=float(H.loc[d, past].max()), L0=float(L.loc[d, past].min()), close=float(Cf.loc[d].dropna().iloc[-1]),
                                 tier=int(tier[i]), sd60=float(sd60[i]), acx=float(abs(cx.values[i]) / thr[i]), mv=float(p0 / prev - 1),
                                 fh=fr.h.values.astype(float), fl=fr.l.values.astype(float), fc=fr.c.values.astype(float),
                                 no=nx.o.values.astype(float), nh=nx.h.values.astype(float), nl=nx.l.values.astype(float), nc=nx.c.values.astype(float),
                                 o1=float(nx.o.iloc[0]), c1=float(nx.c.iloc[0]), c3=float(nx.c.iloc[2]), h3=float(nx.h.iloc[:3].max()),
                                 l3=float(nx.l.iloc[:3].min()), nw=float(dd.loc[d, "nw_n"]) if "nw_n" in dd.columns else 0.0,
                                 lval=float(dd.loc[d, "lval"])))
    E = pd.DataFrame(rows)
    log("сигналов:", len(E), "| бумаг", E.t.nunique(), "| дней", E.d.nunique())
    return E


def paths(E):
    """Матрицы пути: бары 0..KH-1 — часы того же дня, KH..KH+NDAYS-1 — дни D+1…D+5; day[j] — номер дня бара."""
    n, K = len(E), KH + NDAYS
    Hm, Lm, Cm = np.full((n, K), np.nan), np.full((n, K), np.nan), np.full((n, K), np.nan)
    for i, r in enumerate(E.itertuples()):
        k = min(len(r.fh), KH)
        Hm[i, :k], Lm[i, :k], Cm[i, :k] = r.fh[:k], r.fl[:k], r.fc[:k]
        Hm[i, KH:], Lm[i, KH:], Cm[i, KH:] = r.nh, r.nl, r.nc
    day = np.r_[np.zeros(KH, int), np.arange(1, NDAYS + 1)]
    return Hm, Lm, Cm, day


def first_true(M):
    """Индекс первого True по строке, иначе большое число."""
    any_ = M.any(axis=1)
    return np.where(any_, M.argmax(axis=1), 10 ** 6)


def simulate(E, P, side, entry, tp, sl, exit_day):
    """Вектором по всем сигналам → доходность сделки (NaN — вход не исполнен)."""
    Hm, Lm, Cm, day = P
    n, K = Hm.shape
    s = E.s.values * (1 if side == "follow" else -1)        # знак позиции
    m = np.abs(E.mv.values)
    p0 = E.p0.values
    # вход
    if entry == "sig":
        e, j0 = p0.copy(), np.zeros(n, int)
    elif entry == "open1":
        e, j0 = E.o1.values.copy(), np.full(n, KH)
    else:
        k = float(entry)
        e = p0 - s * k * np.abs(p0 - E.prev.values)        # по движению — откат, против — продолжение хода
        same = day == 0
        hitL = (Lm <= e[:, None]) & same[None, :]
        hitH = (Hm >= e[:, None]) & same[None, :]
        jf = np.where(s > 0, first_true(hitL), first_true(hitH))
        ok = jf < K
        j0 = np.where(ok, jf + 1, 10 ** 6)                  # тейк/стоп — со следующего бара
        e = np.where(ok, e, np.nan)
    # уровни
    tpl = np.full(n, np.nan)
    if tp in ("0.25", "0.5"):
        tpl = e * (1 + s * float(tp) * m)
    elif tp in ("q50", "q70"):
        tpl = e * (1 + s * E[f"{tp}_d"].values)
    sll = np.full(n, np.nan)
    if sl in ("0.25", "0.5"):
        sll = e * (1 - s * float(sl) * m)
    elif sl == "mae":
        sll = e * (1 + s * E["mae50"].values)               # mae50 отрицательный
    # последний бар выхода по времени
    last = np.where(day <= exit_day)[0].max()
    idx = np.arange(K)[None, :]
    act = (idx >= j0[:, None]) & (idx <= last)
    tp_hit = np.where(s[:, None] > 0, Hm >= tpl[:, None], Lm <= tpl[:, None]) & act & np.isfinite(tpl)[:, None]
    sl_hit = np.where(s[:, None] > 0, Lm <= sll[:, None], Hm >= sll[:, None]) & act & np.isfinite(sll)[:, None]
    jt, js = first_true(tp_hit), first_true(sl_hit)
    # цена выхода по времени: закрытие последнего бара с данными до last
    Cl = Cm[:, : last + 1]
    lastc = pd.DataFrame(Cl).ffill(axis=1).iloc[:, -1].values
    if exit_day == 0:
        lastc = E.close.values                                # закрытие дня — с вечерней сессией
    if entry == "open1" and exit_day == 0:
        return np.full(n, np.nan)
    xp = np.where(js <= jt, np.where(js < 10 ** 6, sll, lastc), tpl)  # стоп раньше или в том же баре — стоп
    xp = np.where((jt < 10 ** 6) & (jt < js), tpl, xp)
    cost = E.tier.map(COST_T).values
    r = s * (xp / e - 1) - cost
    return np.where(np.isfinite(e), r, np.nan)


def grid():
    G = []
    for side in ("follow", "fade"):
        for entry in ("sig", "0.25", "0.382", "0.5", "open1"):
            tps = (None, "0.25", "0.5", "q50", "q70") if side == "follow" else (None, "0.25", "0.5")
            sls = (None, "0.25", "0.5", "mae") if side == "follow" else (None, "0.25", "0.5")
            for tp in tps:
                for sl in sls:
                    for ex in (0, 1, 3, 5):
                        if entry == "open1" and ex == 0:
                            continue
                        G.append((side, entry, tp, sl, ex))
    return G


def name(g):
    side, entry, tp, sl, ex = g
    en = {"sig": "сразу", "open1": "открытие D+1"}[entry] if entry in ("sig", "open1") else f"лимит {float(entry):.0%} хода"
    tpn = {None: "без тейка", "0.25": "тейк 25% хода", "0.5": "тейк 50% хода", "q50": "тейк «дойдёт до» (медиана)", "q70": "тейк «дойдёт до» (70%)"}[tp]
    sln = {None: "без стопа", "0.25": "стоп 25% хода", "0.5": "стоп 50% хода", "mae": "стоп «обычная просадка»"}[sl]
    exn = {0: "закрытие дня", 1: "закрытие D+1", 3: "закрытие D+3", 5: "закрытие D+5"}[ex]
    return f"{'ПО движению' if side == 'follow' else 'ПРОТИВ движения'}; вход {en}; {tpn}; {sln}; выход {exn}"


def metrics(E, r, mask):
    x = r[mask]
    f = np.isfinite(x)
    if f.sum() < MIN_N:
        return None
    xt = x[f]
    D = pd.DataFrame({"d": E.d.values[mask][f], "c": E.c.values[mask][f], "r": xt}).sort_values(["d", "c"])
    D = D.groupby("d").head(10)                                # не больше 10 сделок в день по 10% капитала
    daily = D.groupby("d").r.sum() * 0.1
    eq = daily.cumsum()
    dd = float((eq - eq.cummax()).min())
    yrs = max(1e-9, (E.d[mask].max() - E.d[mask].min()).days / 365.25)
    return dict(n=int(f.sum()), fill=round(float(f.mean()), 3), mean=round(float(xt.mean()), 5), win=round(float((xt > 0).mean()), 3),
                pf=round(float(xt[xt > 0].sum() / max(1e-9, -xt[xt < 0].sum())), 2), per_sig=round(float(np.nan_to_num(x).mean()), 5),
                year=round(float(daily.sum() / yrs), 4), dd=round(dd, 4),
                by_year={int(y): round(float(v), 4) for y, v in daily.groupby(pd.to_datetime(daily.index).year).sum().items()})


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    E = events(X, Hh, IXH, Dd)
    E = E11.add_paths(E)
    L = E11.levels_oos(E)                                       # уровни вне выборки (с 2022 г.)
    E = L.copy()
    E["mae50"] = E["mae50"].clip(upper=-0.003)
    P = paths(E)
    yr = E.d.dt.year.values
    tr_m = (yr >= TRAIN[0]) & (yr <= TRAIN[1])
    te_m = (yr >= TEST[0]) & (yr <= TEST[1])
    log("сигналов: обучение", int(tr_m.sum()), "| проверка", int(te_m.sum()), "| издержки", COST_T)
    filters = {"все": np.ones(len(E), bool), "эшелон 1–2": E.tier.values <= 2, "эшелон 3": E.tier.values == 3}
    dirs = {"все": np.ones(len(E), bool), "рост": E.s.values > 0, "падение": E.s.values < 0}
    res = []
    for g in grid():
        r = simulate(E, P, *g)
        for fn, fm in filters.items():
            for dn, dm in dirs.items():
                m = fm & dm
                a, b = metrics(E, r, m & tr_m), metrics(E, r, m & te_m)
                if not a or not b:
                    continue
                long_only = (g[0] == "follow" and dn == "рост") or (g[0] == "fade" and dn == "падение")
                res.append(dict(g=g, name=name(g), filt=fn, dir=dn, long=long_only, tr=a, te=b))
    log("вариантов посчитано:", len(res), round(time.time() - t0), "с")
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), cost=COST_T, train=TRAIN, test=TEST, n=len(res))

    def show(R, title, k=12):
        log(f"===== {title} =====")
        rows = []
        for x in R[:k]:
            a, b = x["tr"], x["te"]
            log(f"[{x['filt']}, аномалии: {x['dir']}{', только покупки' if x['long'] else ''}] {x['name']}\n"
                f"     обучение 22–24: сделок {a['n']}, {a['mean']:+.2%}/сделку, прибыльных {a['win']:.0%}, PF {a['pf']}, капитал {a['year']:+.0%}/год, просадка {a['dd']:.0%}\n"
                f"     ПРОВЕРКА 25–26: сделок {b['n']}, {b['mean']:+.2%}/сделку, прибыльных {b['win']:.0%}, PF {b['pf']}, капитал {b['year']:+.0%}/год, просадка {b['dd']:.0%}; по годам {b['by_year']}")
            rows.append(dict(name=x["name"], filt=x["filt"], dir=x["dir"], long=x["long"], train=a, test=b))
        return rows
    big = [x for x in res if x["tr"]["n"] >= MIN_BIG]
    # выбор только по обучению: средняя доходность на сделку × устойчивость (каждый год обучения в плюсе)
    for x in big:
        by = x["tr"]["by_year"]
        x["tr_ok"] = all(v > 0 for v in by.values()) and len(by) >= TRAIN[1] - TRAIN[0] + 1
    pick = sorted([x for x in big if x["tr_ok"]], key=lambda x: -x["tr"]["year"])
    out["top_train"] = show(pick, "ЛУЧШИЕ НА ОБУЧЕНИИ (каждый год 2022–2024 в плюсе, ≥150 сделок) → как они прошли проверку 2025–2026")
    out["top_train_long"] = show([x for x in pick if x["long"]], "ТО ЖЕ, ТОЛЬКО ПОКУПКИ")
    tr_y = np.array([x["tr"]["year"] for x in big])
    te_y = np.array([x["te"]["year"] for x in big])
    rc = float(pd.Series(tr_y).corr(pd.Series(te_y), method="spearman")) if len(big) > 10 else None
    log(f"связь «обучение → проверка» по всем вариантам (ранговая корреляция): {rc:.2f}" if rc is not None else "")
    out["rank_corr"] = rc
    k50 = pick[:50]
    if k50:
        pos = np.mean([x["te"]["year"] > 0 for x in k50])
        log(f"из 50 лучших на обучении в плюсе на проверке: {pos:.0%}; медиана капитала на проверке {np.median([x['te']['year'] for x in k50]):+.0%}/год")
        out["top50_test_pos"] = round(float(pos), 3)
    base = [x for x in res if x["g"] == ("follow", "sig", None, None, 0) and x["filt"] == "все" and x["dir"] == "все"]
    if base:
        out["base"] = show(base, "БАЗА: по движению, сразу, держать до закрытия дня", 1)
    # для справки: лучшие «задним числом» на проверке (так выбирать нельзя — это подгонка)
    hind = sorted(big, key=lambda x: -x["te"]["year"])
    out["hindsight"] = show(hind, "СПРАВКА: лучшие на 2025–2026 задним числом (подгонка, не для выбора)", 5)
    json.dump(out, open(os.path.join(TR.MODELS, "exp12_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №12 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
