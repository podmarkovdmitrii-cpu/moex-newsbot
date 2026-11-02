# -*- coding: utf-8 -*-
"""Обучение на разных промежутках и проверка на следующих (walk-forward) для «психологии толпы» и «смежных бумаг», 2008–2026.

Зачем: одно разбиение «2018–2022 → 2023–2026» может случайно оказаться удачным или неудачным. Здесь правило каждый раз
подбирается ТОЛЬКО на прошлом и проверяется на следующем годе, который при подборе не виден:
  • схемы обучения: расширяющееся окно (все годы с 2008 до года проверки), скользящие окна 5 и 3 года;
  • годы проверки: 2012…текущий (по одному году вперёд);
  • на обучении перебираются параметры (окно, порог движения, оборот, горизонт, продолжение/откат, учёт новостей;
    для смежных — порог лидера, оборот, отставание, горизонт, охват: тот же сектор / пары секторов / все бумаги);
  • выбирается вариант с лучшей t-статистикой чистого результата (минус издержки 0,3% на сделку); если ни один вариант
    на обучении не прибылен устойчиво — в этом году сигналов нет.
Итог по схеме — только результаты лет проверки (вне выборки). Лучшая схема, прошедшая порог (не меньше 30 сделок, средний
чистый результат > 0, t ≥ 2, плюс в ≥ 60% лет), даёт живое правило: тот же подбор на последнем окне. Если ни одна схема порог
не прошла — живой бот сигнала не даёт (лучший вариант — «не давать»).
Результат: research-stats/wf.json и wf_spill_rows.csv.gz (строки выбранного правила для проверяющего агента).
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
import study as S  # noqa: E402

OUT = S.OUT
COST = float(os.environ.get("WF_COST", 0.003))
FIRST_TEST = int(os.environ.get("WF_FIRST_TEST", 2012))
SCHEMES = {"expanding": None, "roll5": 5, "roll3": 3}
BAR = dict(n=30, t=2.0, pos_years=0.6, min_years=4)


def log(*a):
    print(time.strftime("%H:%M:%S"), "[обучение/проверка]", *a, flush=True)


def tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[x == x]
    if len(x) < 2:
        return dict(n=int(len(x)), mean=float(x.mean()) if len(x) else 0.0, hit=float((x > 0).mean()) if len(x) else 0.0, t=0.0)
    sd = x.std(ddof=1)
    return dict(n=int(len(x)), mean=round(float(x.mean()), 5), hit=round(float((x > 0).mean()), 3), t=round(float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else 0.0, 2))


def train_years(scheme, y, first):
    w = SCHEMES[scheme]
    lo = first if w is None else max(first, y - w)
    return lo, y - 1


# ------------------------------------------------------------------ варианты: подготовка
CROWD_GRID = dict(win=(2, 3, 5), thr=(0.05, 0.07, 0.10), vx=(1.5, 2.0, 3.0))
CROWD_H = (3, 5, 10)


def crowd_variants(P, news, divs):
    """Все варианты «толпы»: ключ → (годы событий, результат по направлению движения: + продолжение, − откат)."""
    V = {}
    for win in CROWD_GRID["win"]:
        for thr in CROWD_GRID["thr"]:
            for vx in CROWD_GRID["vx"]:
                prm = dict(win=win, thr=thr, vx=vx, liq=30e6, gap=5)
                ev = S.forward(P, S.crowd_events(P, news, divs, prm), hs=CROWD_H)
                if ev.empty:
                    continue
                yr = ev.d.dt.year.values
                for side, sname in ((1, "up"), (-1, "down")):
                    for nw in ("no", "any"):
                        m = (ev.dir.values == side) & ((ev.news.values == 0) if nw == "no" else True)
                        for h in CROWD_H:
                            r = ev[f"f{h}"].values[m] * side
                            ok = r == r
                            for mode, sg in (("continue", 1), ("reverse", -1)):
                                V[(sname, win, thr, vx, nw, h, mode)] = (yr[m][ok], r[ok] * sg - COST)
                log(f"толпа: окно {win}, порог {thr:.0%}, оборот {vx}× — событий {len(ev)}")
    return V


SPILL_GRID = dict(thr=(0.03, 0.04, 0.05, 0.07), vx=(1.5, 2.5, 4.0), lag=(0.0, 0.3, 0.5))
SPILL_H = (1, 3, 5)


def spill_base(P):
    """Все пары «лидер дня → отставшая бумага» при самых мягких порогах; дальше варианты — фильтры по этим строкам."""
    C, V, ix = P["c"], P["v"], P["ix"]
    so = S.sec_of()
    ex1 = C.pct_change(fill_method=None).sub(ix.pct_change(fill_method=None), axis=0)
    vxm = V / V.rolling(20, min_periods=15).median().shift(1)
    liq = V.rolling(20, min_periods=15).median().shift(1)
    cols = [t for t in C.columns if t in so]
    days = C.index
    Cv, ixv = C[cols].values, ix.values
    E, X, L = ex1[cols].values, vxm[cols].values, liq[cols].values
    n, hmax = len(days), max(SPILL_H)
    sec = np.array([so[t] for t in cols])
    tmin, vmin, lmax = min(SPILL_GRID["thr"]), min(SPILL_GRID["vx"]), max(SPILL_GRID["lag"])
    lead_rows, parts = [], []
    ar = np.arange(len(cols))
    for j in range(25, n - hmax):
        e, x, lq = E[j], X[j], L[j]
        lead = np.where((np.abs(e) >= tmin) & (x >= vmin) & (lq >= 50e6))[0]
        for i in lead:
            lead_rows.append((j, i, abs(e[i]), x[i]))
        if len(lead) > 40:
            continue
        base = Cv[j]
        fwd = [((Cv[j + h] / base - 1) - (ixv[j + h] / ixv[j] - 1)) for h in SPILL_H]
        for i in lead:
            d = 1.0 if e[i] > 0 else -1.0
            lagr = e * d / abs(e[i])
            k = np.where((lq >= 30e6) & (lagr < lmax) & (ar != i) & (base == base) & (base > 0))[0]
            if not len(k):
                continue
            blk = np.empty((len(k), 7 + len(SPILL_H)))
            blk[:, 0], blk[:, 1], blk[:, 2], blk[:, 3], blk[:, 4], blk[:, 5], blk[:, 6] = j, i, k, d, abs(e[i]), x[i], lagr[k]
            for q, f in enumerate(fwd):
                blk[:, 7 + q] = f[k] * d
            parts.append(blk)
    rows = np.vstack(parts) if parts else np.empty((0, 7 + len(SPILL_H)))
    R = pd.DataFrame(rows, columns=["j", "lead", "peer", "dir", "e", "x", "lag"] + [f"f{h}" for h in SPILL_H])
    for c in ("j", "lead", "peer"):
        R[c] = R[c].astype(np.int32)
    R["s1"], R["s2"] = sec[R.lead.values], sec[R.peer.values]
    R["year"] = days.year.values[R.j.values]
    L_ = pd.DataFrame(lead_rows, columns=["j", "lead", "e", "x"])
    log("смежные: строк лидер→бумага", len(R), "лидеров-дней", len(L_), "бумаг с сектором", len(cols))
    return R, L_, cols, days


def spill_units(R, L_, thr, vx, lag, h, d, scope):
    """Единица сделки — «событие лидера» (день, лидер) × (сектор бумаг). Возвращает таблицу: год, s1, s2, результат (минус издержки)."""
    lc = L_[(L_.e >= thr) & (L_.x >= vx)].groupby("j").size()
    busy = set(lc[lc > 6].index)  # 7+ лидеров в день — движение рынка, а не цепочка
    m = (R.dir.values == d) & (R.e.values >= thr) & (R.x.values >= vx) & (R.lag.values < lag if lag > 0 else R.lag.values <= 0) & ~R.j.isin(busy).values
    if scope == "same":
        m &= R.s1.values == R.s2.values
    g = R[m]
    f = g[f"f{h}"]
    g = g.assign(f=f.values)[f.values == f.values]
    if g.empty:
        return pd.DataFrame(columns=["year", "s1", "s2", "r", "j", "lead", "pk"])
    if scope == "all":
        u = g.groupby(["j", "lead"]).agg(year=("year", "first"), s1=("s1", "first"), r=("f", "mean")).reset_index()
        u["s2"] = "*"
    else:
        u = g.groupby(["j", "lead", "s2"]).agg(year=("year", "first"), s1=("s1", "first"), r=("f", "mean")).reset_index()
    u["r"] = u["r"] - COST
    u["pk"] = u["s1"].astype(str) + ">" + u["s2"].astype(str)
    return u


def spill_variants(R, L_):
    V = {}
    for d, dn in ((1.0, "up"), (-1.0, "down")):
        for thr in SPILL_GRID["thr"]:
            for vx in SPILL_GRID["vx"]:
                for lag in SPILL_GRID["lag"]:
                    for h in SPILL_H:
                        for scope in ("same", "pairs", "all"):
                            V[(dn, thr, vx, lag, h, scope)] = spill_units(R, L_, thr, vx, lag, h, d, scope)
        log("смежные: варианты для", "роста" if d > 0 else "падения", "готовы")
    return V


def pick_pairs(u, n_min=15):
    """Пары секторов, где на обучении отставшие устойчиво догоняли лидера."""
    if u.empty:
        return set()
    g = u.groupby("pk").r.agg(["count", "mean", "std"])
    g = g[(g["count"] >= n_min) & (g["mean"] > 0)]
    g = g[g["mean"] / (g["std"] / np.sqrt(g["count"])) >= 2]
    return set(g.index)


# ------------------------------------------------------------------ подбор и проверка
def choose(cands, lo, hi, n_min=30):
    """cands: ключ → (годы, результат). Лучший вариант на годах [lo, hi] по t чистого результата; None — нет прибыльного."""
    best, bk = None, None
    for k, (yr, r) in cands.items():
        m = (yr >= lo) & (yr <= hi)
        if m.sum() < n_min:
            continue
        s = tstat(r[m])
        if s["mean"] > 0 and s["t"] >= 2 and (best is None or s["t"] > best["t"]):
            best, bk = s, k
    return bk, best


def choose_spill(V, dn, lo, hi, n_min=30):
    best, bk, bpairs = None, None, None
    for k, u in V.items():
        if k[0] != dn or u.empty:
            continue
        tr = u[(u.year >= lo) & (u.year <= hi)]
        pairs = None
        if k[5] == "pairs":
            pairs = pick_pairs(tr)
            if not pairs:
                continue
            tr = tr[tr.pk.isin(pairs)]
        if len(tr) < n_min:
            continue
        s = tstat(tr.r.values)
        if s["mean"] > 0 and s["t"] >= 2 and (best is None or s["t"] > best["t"]):
            best, bk, bpairs = s, k, pairs
    return bk, best, bpairs


def apply_spill(V, k, pairs, y):
    u = V[k]
    u = u[u.year == y]
    if pairs is not None:
        u = u[u.pk.isin(pairs)]
    return u.r.values


def summarize_oos(folds):
    allr = np.concatenate([f["r"] for f in folds if len(f["r"])]) if any(len(f["r"]) for f in folds) else np.array([])
    by_year = {f["year"]: tstat(f["r"]) for f in folds if len(f["r"])}
    yrs = [v["mean"] for v in by_year.values() if v["n"] >= 3]
    pos = float(np.mean([m > 0 for m in yrs])) if yrs else 0.0
    s = tstat(allr)
    s.update(years_traded=len(yrs), pos_years=round(pos, 2), by_year={int(k): v for k, v in by_year.items()})
    s["passes"] = bool(s["n"] >= BAR["n"] and s["mean"] > 0 and s["t"] >= BAR["t"] and pos >= BAR["pos_years"] and len(yrs) >= BAR["min_years"])
    return s


def walk(kind, first, last, choose_fn, apply_fn):
    res = {}
    for sch in SCHEMES:
        folds = []
        for y in range(max(FIRST_TEST, first + 2), last + 1):
            lo, hi = train_years(sch, y, first)
            if hi - lo + 1 < 2:
                continue
            k, st, extra = choose_fn(lo, hi)
            r = apply_fn(k, extra, y) if k else np.array([])
            folds.append(dict(year=y, train=[lo, hi], pick=[x if isinstance(x, str) else float(x) for x in k] if k else None, train_stat=st, r=r))
        oos = summarize_oos(folds)
        res[sch] = dict(oos=oos, folds=[dict(year=f["year"], train=f["train"], pick=f["pick"], train_stat=f["train_stat"], test=tstat(f["r"]) if len(f["r"]) else None) for f in folds])
        log(f"{kind} [{sch}]: вне выборки {oos['n']} сделок, среднее {oos['mean'] * 100:+.2f}% после издержек, t={oos['t']}, "
            f"плюс в {oos['pos_years']:.0%} из {oos['years_traded']} лет → {'ПРОХОДИТ' if oos['passes'] else 'не проходит'}")
    return res


def decide(res, kind, last, choose_fn, first):
    """Лучшая схема среди прошедших порог → живое правило (подбор на последнем окне этой схемы, включая текущий год)."""
    ok = [(s, r) for s, r in res.items() if r["oos"]["passes"]]
    if not ok:
        best = max(res.items(), key=lambda z: z[1]["oos"]["t"])
        return dict(on=False, scheme=best[0], oos=best[1]["oos"], why="ни одна схема обучения не дала устойчивого результата вне выборки")
    sch, r = max(ok, key=lambda z: z[1]["oos"]["t"])
    lo, _ = train_years(sch, last + 1, first)
    k, st, extra = choose_fn(lo, last)
    if not k:
        return dict(on=False, scheme=sch, oos=r["oos"], why="на последнем окне нет прибыльного варианта")
    return dict(on=True, scheme=sch, key=[x if isinstance(x, str) else float(x) for x in k], train=[lo, last], train_stat=st, oos=r["oos"], extra=extra)


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    P, _ = S.load_panel()
    days = P["c"].index
    news = S.load_news(days)
    divs = S.ex_div(days)
    first, last = int(days[0].year), int(days[-1].year)
    log("история", days[0].date(), "—", days[-1].date(), "| бумаг", P["c"].shape[1], "| издержки на сделку", f"{COST:.1%}")
    out = {"built": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"), "range": [str(days[0].date()), str(days[-1].date())],
           "cost": COST, "schemes": {k: (v or "все годы") for k, v in SCHEMES.items()}, "bar": BAR, "first_test": FIRST_TEST}

    # ---- толпа
    CV = crowd_variants(P, news, divs)
    out["crowd"] = {}
    for side in ("up", "down"):
        cand = {k: v for k, v in CV.items() if k[0] == side}

        def ch(lo, hi, cand=cand):
            k, st = choose(cand, lo, hi)
            return k, st, None

        def ap(k, extra, y, cand=cand):
            yr, r = cand[k]
            return r[yr == y]
        res = walk(f"толпа {side}", first, last, ch, ap)
        dec = decide(res, f"толпа {side}", last, ch, first)
        if dec["on"]:
            _, win, thr, vx, nw, h, mode = dec["key"]
            win, h = int(win), int(h)
            dec.update(params=dict(win=int(win), thr=float(thr), vx=float(vx), liq=30e6, gap=5), news=nw, h=int(h), mode=mode)
        dec.pop("extra", None)
        out["crowd"][side] = dict(decision=dec, schemes=res)
        log(f"ТОЛПА {side}: {'правило ' + dec['mode'] + ', ' + str(dec['params']) + ', горизонт ' + str(dec['h']) + ' дн., новости: ' + dec['news'] if dec['on'] else 'сигналов нет — ' + dec['why']}")

    # ---- смежные бумаги
    R, L_, cols, sdays = spill_base(P)
    SV = spill_variants(R, L_)
    out["spill"] = {}
    for dn, d in (("up", 1.0), ("down", -1.0)):
        def ch(lo, hi, dn=dn):
            return choose_spill(SV, dn, lo, hi)

        def ap(k, pairs, y):
            return apply_spill(SV, k, pairs, y)
        res = walk(f"смежные {dn}", first, last, ch, ap)
        dec = decide(res, f"смежные {dn}", last, ch, first)
        if dec["on"]:
            _, thr, vx, lag, h, scope = dec["key"]
            h = int(h)
            pairs = dec.pop("extra")
            dec.update(params=dict(thr=float(thr), vx=float(vx), lag=float(lag), liq=50e6), h=int(h), scope=scope,
                       pairs=sorted([p.split(">") for p in pairs]) if pairs else None)
            # строки выбранного правила — для независимой проверки (перестановочный тест по соседям)
            lc = L_[(L_.e >= thr) & (L_.x >= vx)].groupby("j").size()
            m = (R.dir.values == d) & (R.e.values >= thr) & (R.x.values >= vx) & ((R.lag.values < lag) if lag > 0 else (R.lag.values <= 0)) & ~R.j.isin(set(lc[lc > 6].index)).values
            g = R[m][["j", "lead", "peer", "s1", "s2", "year", f"f{int(h)}"]].rename(columns={f"f{int(h)}": "f"})
            g["d"] = sdays[g.j.values].strftime("%Y-%m-%d")
            g["lead"] = [cols[i] for i in g.lead]
            g["peer"] = [cols[i] for i in g.peer]
            g.to_csv(os.path.join(OUT, f"wf_spill_rows_{dn}.csv.gz"), index=False, compression="gzip")
            # самые отзывчивые бумаги-соседи на всей истории выбранного правила
            sel = g if scope == "all" else g[g.s1 == g.s2] if scope == "same" else g[(g.s1 + ">" + g.s2).isin(set(">".join(p) for p in dec["pairs"]))]
            dec["top_peers"] = sel.groupby(["s1", "peer"]).f.agg(["count", "mean"]).query("count >= 10").sort_values("mean", ascending=False).groupby(level=0).head(5).round(4).reset_index().values.tolist()
        else:
            dec.pop("extra", None)
        out["spill"][dn] = dict(decision=dec, schemes=res)
        log(f"СМЕЖНЫЕ {dn}: {'давать сигналы — ' + dec['scope'] + ', ' + str(dec['params']) + ', горизонт ' + str(dec['h']) + ' дн.' if dec['on'] else 'НЕ давать сигналы — ' + dec['why']}")
    json.dump(out, open(os.path.join(OUT, "wf.json"), "w"), ensure_ascii=False, default=float)
    log("готово за", round((time.time() - t0) / 60, 1), "мин")


if __name__ == "__main__":
    main()
