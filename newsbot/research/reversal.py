# -*- coding: utf-8 -*-
"""Резкий рост → падение и резкое падение → рост: закономерности на истории 2008–2026 (с новостями), проверка стратегий и
связка с «выносом стопов» (research/shakeout.py). Итог — лучшая стратегия, прошедшая обучение на разных промежутках.

1) Закономерности (описательно, вся история): после резкого движения за W баров (|ΔC| ≥ thr·ATR, оборот ≥ vx×) — что дальше
   (1/3/5/10 баров, по направлению движения: «+» продолжение, «−» разворот). Разрезы: сила движения, были ли новости о компании,
   оборот, движение по тренду или против (цена относительно средней за 50 баров), рост или падение, дневные и часовые бары.
2) Стратегии (сделка: вход по открытию следующего бара, стоп, цель R·риск или выход по времени; издержки 0,3%):
   fade — против резкого движения; follow / follow_wide — по движению (стоп 1,5 или 3 ATR); confirm — после разворотного бара («рост, затем падение»);
   shake — резкое движение, затем вынос стопов против него (связка с прошлым исследованием); stop — сам вынос стопов (shakeout.py).
3) Обучение на разных промежутках (расширяющееся окно, скользящие 5 и 3 года) по каждому семейству и по всем вместе; лучшая
   стратегия = прошедшая порог вне выборки с наибольшей t. Её перепроверяет research/rev_verify.py (в т.ч. живым кодом channel/patterns.py).
Результат: research-stats/reversal.json (+ строки выбранных правил).
"""
import datetime as dt
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shakeout as SH  # noqa: E402
import study as S  # noqa: E402

OUT = S.OUT
COST = SH.COST
FAMS = ("fade", "follow", "follow_wide", "confirm", "shake")
GRID = {
    "d": dict(W=(1, 2, 3), thr=(2.0, 3.0, 4.5), vx=(1.0, 2.0), R=(1.5, 2.0, 3.0, 99.0), Hb=(3, 5, 10)),
    "h": dict(W=(1, 3, 6), thr=(2.0, 3.0, 4.5), vx=(1.0, 2.0), R=(1.5, 2.0, 3.0, 99.0), Hb=(4, 8, 24)),
}
LIQ = SH.LIQ


def log(*a):
    print(time.strftime("%H:%M:%S"), "[резкие движения]", *a, flush=True)


def move_arrays(B, W):
    """Движение за W баров до бара e в ATR(e), оборот окна к норме (медиана 20 баров до окна)."""
    C, V = B["C"], B["V"]
    mv = (C - pd.DataFrame(C).shift(W).values) / B["atr"]
    medW = pd.DataFrame(V).rolling(20, min_periods=15).median().shift(W).values
    vsum = pd.DataFrame(V).rolling(W, min_periods=W).sum().values
    with np.errstate(invalid="ignore", divide="ignore"):
        vr = vsum / (W * medW)
    lowW = pd.DataFrame(B["L"]).rolling(W, min_periods=W).min().values  # окно e−W+1…e
    highW = pd.DataFrame(B["H"]).rolling(W, min_periods=W).max().values
    return mv, vr, medW, lowW, highW


def shift1(a):
    return np.vstack([np.full((1, a.shape[1]), np.nan), a[:-1]])


def family_events(B, fam, W, thr, vx, liq):
    """Для семейства — маски (side=+1 рост, −1 падение) и стопы на баре сигнала j."""
    mv, vr, medW, lowW, highW = move_arrays(B, W)
    O, H, L, C = B["O"], B["H"], B["L"], B["C"]
    if fam in ("confirm", "shake"):  # движение заканчивается на j−1
        mv, vr, medW, lowW, highW = (shift1(x) for x in (mv, vr, medW, lowW, highW))
    with np.errstate(invalid="ignore"):
        big = (np.abs(mv) >= thr) & (vr >= vx) & (medW >= liq)
        d = np.sign(mv)
        out = {}
        if fam == "fade":
            for side in (1, -1):
                m = big & (d == -side)
                stop = np.where(side > 0, lowW * 0.999, highW * 1.001)
                out[side] = (m, stop)
        elif fam in ("follow", "follow_wide"):
            for side in (1, -1):
                m = big & (d == side)
                out[side] = (m, 1.5 if fam == "follow" else 3.0)  # стоп 1,5·ATR (или 3·ATR — «широкий») от входа
        elif fam == "confirm":
            dc = C - shift1(C)
            for side in (1, -1):
                m = big & (d == -side) & (dc * (-side) <= -0.5 * B["atr"])  # бар j против движения (для лонга: движение вниз, бар вверх)
                lw = np.fmin(lowW, L)
                hw = np.fmax(highW, H)
                stop = np.where(side > 0, lw * 0.999, hw * 1.001)
                out[side] = (m, stop)
        elif fam == "shake":
            rg = H - L
            for side in (1, -1):
                if side > 0:
                    m = big & (d == -1) & (rg > 0) & (L < lowW) & (C >= L + 0.6 * rg)
                    stop = L * 0.999
                else:
                    m = big & (d == 1) & (rg > 0) & (H > highW) & (C <= H - 0.6 * rg)
                    stop = H * 1.001
                out[side] = (m, stop)
    return out


def trades2(B, js, is_, side, stop_arr, R, Hb):
    """Как shakeout.trades, но стоп задан (матрица на баре сигнала) или 1,5·ATR от входа (stop_arr=None)."""
    O, H, L, C = B["O"], B["H"], B["L"], B["C"]
    n = len(O)
    idx = np.arange(len(js))
    ok = js + Hb < n
    js, is_, idx = js[ok], is_[ok], idx[ok]
    entry = O[js + 1, is_]
    stop = (entry - side * float(stop_arr) * B["atr"][js, is_]) if np.isscalar(stop_arr) else stop_arr[js, is_]
    risk = (entry - stop) * side
    with np.errstate(invalid="ignore", divide="ignore"):
        good = (entry > 0) & (risk > 0) & (risk / entry >= 0.003) & (risk / entry <= 0.15)
    js, is_, idx, entry, stop, risk = js[good], is_[good], idx[good], entry[good], stop[good], risk[good]
    tgt = entry + side * R * risk
    res = np.full(len(js), np.nan)
    hit = np.zeros(len(js), bool)
    open_ = np.ones(len(js), bool)
    last = entry.copy()
    for k in range(1, Hb + 1):
        o, hh, ll, cc = O[js + k, is_], H[js + k, is_], L[js + k, is_], C[js + k, is_]
        valid = open_ & (cc == cc)
        adverse = np.where(side > 0, ll <= stop, hh >= stop)
        fav = np.where(side > 0, hh >= tgt, ll <= tgt)
        px_stop = np.where(side > 0, np.minimum(o, stop), np.maximum(o, stop)) if k > 1 else stop
        st = valid & adverse
        res[st] = ((px_stop - entry) * side / entry)[st]
        open_ &= ~st
        tg = valid & ~adverse & fav
        res[tg] = (R * risk / entry)[tg]
        hit |= tg
        open_ &= ~tg
        last = np.where(valid, cc, last)
    res[open_] = ((last - entry) * side / entry)[open_]
    return idx, res - COST, hit, risk / entry


def variants(tf, P, news, day_idx, news_ok_from):
    B = SH.base(P)
    yrs = SH.years_of(P, tf)
    g = GRID[tf]
    V, raw = {}, {}
    for fam, W, thr, vx in itertools.product(FAMS, g["W"], g["thr"], g["vx"]):
        ev = family_events(B, fam, W, thr, vx, LIQ[tf])
        for side, (m, stop) in ev.items():
            js0, is0 = SH.dedup(m, gap=W + 2)
            if len(js0) < 20:
                continue
            nm = SH.news_mask(tf, P, news, js0, is0, day_idx)
            for R, Hb in itertools.product(g["R"], g["Hb"]):
                ix, r, hit, rk = trades2(B, js0, is0, side, stop, R, Hb)
                if not len(ix):
                    continue
                js, is_, nmk = js0[ix], is0[ix], nm[ix]
                y = yrs[js]
                known = js >= news_ok_from
                for nw in SH.NEWS:
                    sel = np.ones(len(js), bool) if nw == "any" else known & ((nmk == 0) if nw == "no" else (nmk == 1))
                    V[(("up" if side > 0 else "down"), fam, W, thr, vx, R, Hb, nw)] = (y[sel], r[sel], hit[sel], js[sel], is_[sel], rk[sel])
        log(f"{tf}: {fam} W={W} порог {thr}·ATR оборот {vx}× — событий рост {int(ev[1][0].sum())}, падение {int(ev[-1][0].sum())}")
    return V, B


# ------------------------------------------------------------------ закономерности
def patterns(tf, P, B, news, day_idx, news_ok_from):
    """После резкого движения (W, 3·ATR, оборот ≥ 1,5×): что дальше — по направлению движения, в % (без стопов)."""
    C = B["C"]
    n = len(C)
    yrs = SH.years_of(P, tf)
    ma50 = pd.DataFrame(C).rolling(50, min_periods=40).mean().values
    rows = []
    for W in GRID[tf]["W"]:
        mv, vr, medW, _, _ = move_arrays(B, W)
        with np.errstate(invalid="ignore"):
            m = (np.abs(mv) >= 2.0) & (vr >= 1.0) & (medW >= LIQ[tf])
        js, is_ = SH.dedup(m, gap=W + 2)
        js, is_ = js[js + 11 < n], is_[js + 11 < n]
        nm = SH.news_mask(tf, P, news, js, is_, day_idx)
        d = np.sign(mv[js, is_])
        for h in (1, 3, 5, 10):
            f = (C[js + h, is_] / C[js, is_] - 1) * d
            rows.append(pd.DataFrame(dict(W=W, h=h, f=f, d=d, mag=np.abs(mv[js, is_]), vr=vr[js, is_], news=np.where(js >= news_ok_from, nm, -1),
                                          trend=np.sign(C[js, is_] - ma50[js, is_]) * d, year=yrs[js])))
    D = pd.concat(rows, ignore_index=True).dropna(subset=["f"])
    out = {}

    def st(x):
        s = SH.tstat(x)
        return dict(n=s["n"], mean=s["mean"], cont=s["hit"], t=s["t"])
    for h in (1, 3, 5, 10):
        X = D[D.h == h]
        r = {"all": st(X.f), "рост": st(X[X.d > 0].f), "падение": st(X[X.d < 0].f),
             "2–3 ATR": st(X[(X.mag >= 2) & (X.mag < 3)].f), "3–4,5 ATR": st(X[(X.mag >= 3) & (X.mag < 4.5)].f), "4,5+ ATR": st(X[X.mag >= 4.5].f),
             "с новостями": st(X[X.news == 1].f), "без новостей": st(X[X.news == 0].f),
             "оборот < 2×": st(X[X.vr < 2].f), "оборот 2–4×": st(X[(X.vr >= 2) & (X.vr < 4)].f), "оборот 4×+": st(X[X.vr >= 4].f),
             "по тренду": st(X[X.trend > 0].f), "против тренда": st(X[X.trend < 0].f)}
        for W in GRID[tf]["W"]:
            r[f"окно {W}"] = st(X[X.W == W].f)
        out[f"f{h}"] = r
    log(f"=== {tf}: что после резкого движения (+ продолжение, − разворот), события {int((D.h == 1).sum())} ===")
    for k, v in out["f5"].items():
        log(f"   5 баров | {k:13}: {v['mean'] * 100:+.2f}% (продолжение в {v['cont']:.0%}, n={v['n']}, t={v['t']})")
    return out


# ------------------------------------------------------------------ лучшая стратегия
def choose(V, side, lo, hi, fams, n_min=40):
    best, bk = None, None
    for k, (y, r, *_) in V.items():
        if k[0] != side or k[1] not in fams:
            continue
        m = (y >= lo) & (y <= hi)
        if m.sum() < n_min:
            continue
        s = SH.tstat(r[m])
        if s["mean"] > 0 and s["t"] >= 2 and (best is None or s["t"] > best["t"]):
            best, bk = s, k
    return bk, best


def walk(tf, V, side, fams, first, last, first_test, label):
    res = {}
    for sch, w in SH.SCHEMES.items():
        folds, allr, allh = [], [], []
        for y in range(max(first_test, first + 2), last + 1):
            lo = first if w is None else max(first, y - w)
            if y - lo < 2:
                continue
            k, st = choose(V, side, lo, y - 1, fams)
            r = h = np.array([])
            if k:
                yy, rr, hh, *_ = V[k]
                r, h = rr[yy == y], hh[yy == y]
            folds.append(dict(year=y, pick=[x if isinstance(x, str) else float(x) for x in k] if k else None, test=SH.tstat(r) if len(r) else None))
            allr.append(r)
            allh.append(h)
        R_ = np.concatenate(allr) if allr else np.array([])
        H_ = np.concatenate(allh) if allh else np.array([])
        by = {f["year"]: f["test"]["mean"] for f in folds if f["test"] and f["test"]["n"] >= 3}
        pos = float(np.mean([v > 0 for v in by.values()])) if by else 0.0
        o = SH.tstat(R_)
        o.update(prob=round(float(H_.mean()), 3) if len(H_) else None, years_traded=len(by), pos_years=round(pos, 2), by_year=by)
        o["passes"] = bool(o["n"] >= SH.BAR["n"] and o["mean"] > 0 and o["t"] >= SH.BAR["t"] and pos >= SH.BAR["pos_years"] and len(by) >= SH.BAR["min_years"])
        res[sch] = dict(oos=o, folds=folds)
    best = max(res.items(), key=lambda z: z[1]["oos"]["t"])
    o = best[1]["oos"]
    log(f"{tf} {side} {label:9} лучшая схема {best[0]:9}: вне выборки {o['n']} сделок, {o['mean'] * 100:+.2f}%/сделку, t={o['t']}, "
        f"цель раньше стопа {(o['prob'] or 0) * 100:.0f}%, плюс в {o['pos_years']:.0%} из {o['years_traded']} лет → {'ПРОХОДИТ' if o['passes'] else 'нет'}")
    return res


def run_tf(tf, P, news, day_idx, news_ok_from, first_test, out, shake_board):
    yrs = SH.years_of(P, tf)
    first, last = int(yrs[0]), int(yrs[-1])
    V, B = variants(tf, P, news, day_idx, news_ok_from)
    out[tf] = {"patterns": patterns(tf, P, B, news, day_idx, news_ok_from), "board": {}}
    cols = list(P["c"].columns)
    for side in ("up", "down"):
        board = {}
        for fam in FAMS:
            res = walk(tf, V, side, (fam,), first, last, first_test, fam)
            b = max(res.items(), key=lambda z: z[1]["oos"]["t"])
            board[fam] = dict(scheme=b[0], oos=b[1]["oos"])
        if shake_board and tf in shake_board and side in shake_board[tf]:
            board["stop"] = shake_board[tf][side]
        res_all = walk(tf, V, side, FAMS, first, last, first_test, "все")
        ok = [(s, r) for s, r in res_all.items() if r["oos"]["passes"]]
        if ok:
            sch, r = max(ok, key=lambda z: z[1]["oos"]["t"])
            w = SH.SCHEMES[sch]
            lo = first if w is None else max(first, last + 1 - w)
            k, st = choose(V, side, lo, last, FAMS)
            if k:
                _, fam, W, thr, vx, R, Hb, nw = k
                dec = dict(on=True, scheme=sch, train=[lo, last], train_stat=st, oos=r["oos"], fam=fam, news=nw, prob=r["oos"]["prob"],
                           params=dict(W=int(W), thr=float(thr), vx=float(vx), R=float(R), Hb=int(Hb), liq=LIQ[tf]))
                y, rr, h, js, is_, rk = V[k]
                pd.DataFrame({"t": [cols[i] for i in is_], "j": js, "year": y, "r": rr, "hit": h.astype(int), "risk": rk}).to_csv(
                    os.path.join(OUT, f"rev_rows_{tf}_{side}.csv"), index=False)
            else:
                dec = dict(on=False, why="на последнем окне нет прибыльного варианта", oos=r["oos"])
        else:
            b = max(res_all.items(), key=lambda z: z[1]["oos"]["t"])
            dec = dict(on=False, scheme=b[0], oos=b[1]["oos"], why="ни одна стратегия не дала устойчивого результата вне выборки")
        out[tf]["board"][side] = board
        out[tf][side] = dict(decision=dec)
        log(f"ЛУЧШАЯ {tf} {side}: " + (f"{dec['fam']} {dec['params']}, новости={dec['news']}, схема {dec['scheme']}, цель раньше стопа {dec['prob']:.0%}"
                                       if dec["on"] else "нет — " + dec["why"]))


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    out = {"built": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"), "cost": COST, "grid": GRID, "bar": SH.BAR}
    shake_board = {}
    try:
        sh = json.load(open(os.path.join(OUT, "shakeout.json")))
        for tf in ("d", "h"):
            for side in ("up", "down"):
                b = ((sh.get(tf) or {}).get(side) or {}).get("schemes") or {}
                if b:
                    best = max(b.items(), key=lambda z: z[1]["oos"]["t"])
                    shake_board.setdefault(tf, {})[side] = dict(scheme=best[0], oos=best[1]["oos"])
    except Exception:  # noqa: BLE001
        pass
    P, _ = S.load_panel()
    days = P["c"].index
    news = S.load_news(days)
    nf = int(np.searchsorted(days.values, np.datetime64(S.NEWS_FROM)))
    run_tf("d", P, news, None, nf, 2012, out, shake_board)
    try:
        Ph = SH.hourly_panel()
    except Exception as e:  # noqa: BLE001
        log("часовые данные недоступны:", e)
        Ph = None
    if Ph is not None:
        dd = days.values.astype("datetime64[D]")
        day_idx = np.searchsorted(dd, Ph["ts"].values.astype("datetime64[D]"))
        nfh = int(np.searchsorted(Ph["ts"].values, np.datetime64(S.NEWS_FROM)))
        run_tf("h", Ph, news, day_idx, nfh, 2022, out, shake_board)
    json.dump(out, open(os.path.join(OUT, "reversal.json"), "w"), ensure_ascii=False, default=float)
    log("готово за", round((time.time() - t0) / 60, 1), "мин")


if __name__ == "__main__":
    main()
