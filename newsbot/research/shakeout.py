# -*- coding: utf-8 -*-
"""«Вынос стопов» перед сильным движением в обратную сторону — исследование на истории 2008–2026 с новостями.

Что ищем (две стороны):
  • вынос лонгистов → рост: бумага проколола минимум последних N баров (стопы покупателей сработали), но к закрытию бара
    вернулась вверх (закрытие в верхней части диапазона бара, диапазон бара ≥ k·ATR, оборот выше обычного);
  • вынос шортистов → падение: зеркально — прокол максимума N баров и закрытие в нижней части диапазона.
Сделка по идее: вход по открытию следующего бара, стоп — за экстремумом выноса, цель — R·риск, выход по времени через H баров.
Результат сделки — в % (после издержек 0,3%), и попала ли цель раньше стопа («вероятность исполнения»).
Если стоп и цель в одном баре — считаем, что сработал стоп (осторожно).

Таймфреймы: дневные бары (с 2008 г., все акции TQBR) и часовые (с 2020 г., самые ликвидные бумаги — кэш моделей аномалий).
Новости: архив Интерфакса 2008–2018 + журнал бота с 2018 г. (study.load_news). Вариант правила: любые / без новостей / с новостями.
Обучение на разных промежутках (как walkforward.py): расширяющееся окно, скользящие 5 и 3 года, проверка на следующем годе;
параметры подбираются только на прошлом. Живое правило — только если схема прошла порог вне выборки; дальше его перепроверяет
агент-проверяющий (shake_verify.py). Результат: research-stats/shakeout.json (+ строки выбранных правил для проверки).
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
import study as S  # noqa: E402

OUT = S.OUT
COST = float(os.environ.get("WF_COST", 0.003))
BAR = dict(n=30, t=2.0, pos_years=0.6, min_years=3)
SCHEMES = {"expanding": None, "roll5": 5, "roll3": 3}

GRID = {
    "d": dict(N=(5, 20, 60), pen=(0.0, 0.01), pos=(0.5, 0.7), rng=(1.0, 1.5), vx=(1.0, 1.5, 2.5), R=(1.5, 2.0, 3.0), Hb=(5, 10)),
    "h": dict(N=(8, 40), pen=(0.0, 0.005), pos=(0.5, 0.7), rng=(1.5, 2.5), vx=(1.5, 3.0), R=(1.5, 2.0, 3.0), Hb=(8, 24)),
}
NEWS = ("any", "no", "with")
LIQ = {"d": 30e6, "h": 30e6 / 10}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[вынос стопов]", *a, flush=True)


def tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[x == x]
    if len(x) < 2:
        return dict(n=int(len(x)), mean=float(x.mean()) if len(x) else 0.0, hit=0.0, t=0.0)
    sd = x.std(ddof=1)
    return dict(n=int(len(x)), mean=round(float(x.mean()), 5), hit=round(float((x > 0).mean()), 3), t=round(float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else 0.0, 2))


# ------------------------------------------------------------------ данные
def hourly_panel(tickers=None):
    import train as T
    d = os.path.join(T.DATA, "h")
    have = [f[:-8] for f in os.listdir(d) if f.endswith(".json.gz")] if os.path.isdir(d) else []
    tick = [t for t in (tickers or have) if t != "IMOEX"]
    if not tick:
        return None
    H = T.load_series("h", tick, "2020-01-01")
    frames = {}
    for t, rows in H.items():
        if len(rows) < 500:
            continue
        df = pd.DataFrame([r[:6] for r in rows], columns=["t", "o", "h", "l", "c", "v"]).drop_duplicates("t").set_index("t").sort_index()
        frames[t] = df
    if not frames:
        return None
    idx = sorted(set().union(*[set(f.index) for f in frames.values()]))
    P = {k: pd.DataFrame({t: f[k] for t, f in frames.items()}).reindex(idx) for k in ("o", "h", "l", "c", "v")}
    P["ts"] = pd.to_datetime(pd.Index(idx).str[:13] + ":00")
    log("часовые бары:", P["c"].shape[1], "бумаг,", len(idx), "баров", idx[0], "—", idx[-1])
    return P


# ------------------------------------------------------------------ события и сделки
def base(P):
    O, Hh, L, C, V = (P[k].values.astype(float) for k in ("o", "h", "l", "c", "v"))
    pc = np.vstack([np.full((1, C.shape[1]), np.nan), C[:-1]])
    tr = np.nanmax(np.stack([Hh - L, np.abs(Hh - pc), np.abs(L - pc)]), axis=0)
    atr = pd.DataFrame(tr).rolling(14, min_periods=10).mean().shift(1).values  # ATR до бара — без заглядывания
    med = pd.DataFrame(V).rolling(20, min_periods=15).median().shift(1).values
    return dict(O=O, H=Hh, L=L, C=C, V=V, atr=atr, med=med)


def detect(B, N, pen, pos, rng, vx, liq):
    """Маски выноса: up — вынос лонгистов (сигнал на рост), dn — вынос шортистов (сигнал на падение)."""
    O, Hh, L, C, V = B["O"], B["H"], B["L"], B["C"], B["V"]
    lowN = pd.DataFrame(L).rolling(N, min_periods=N).min().shift(1).values
    highN = pd.DataFrame(Hh).rolling(N, min_periods=N).max().shift(1).values
    rg = Hh - L
    with np.errstate(invalid="ignore", divide="ignore"):
        common = (rg >= rng * B["atr"]) & (V >= vx * B["med"]) & (B["med"] >= liq) & (rg > 0)
        up = common & (L < lowN * (1 - pen)) & (C >= L + pos * rg) & (C > lowN)
        dn = common & (Hh > highN * (1 + pen)) & (C <= Hh - pos * rg) & (C < highN)
    return up, dn


def dedup(mask, gap=3):
    """Одно событие на бумагу в пределах gap баров (повторные проколы подряд — то же движение)."""
    js, is_ = np.nonzero(mask)
    order = np.lexsort((js, is_))
    js, is_ = js[order], is_[order]
    keep = np.ones(len(js), bool)
    last_i, last_j = -1, -10 ** 9
    for k in range(len(js)):
        if is_[k] == last_i and js[k] - last_j < gap:
            keep[k] = False
        else:
            last_i, last_j = is_[k], js[k]
    return js[keep], is_[keep]


def trades(B, js, is_, side, R, Hb):
    """Сделка по каждому событию: % результата после издержек и попадание цели раньше стопа."""
    O, Hh, L, C = B["O"], B["H"], B["L"], B["C"]
    n = len(O)
    idx = np.arange(len(js))
    ok = js + Hb < n
    js, is_, idx = js[ok], is_[ok], idx[ok]
    entry = O[js + 1, is_]
    stop = np.where(side > 0, L[js, is_] * 0.999, Hh[js, is_] * 1.001)
    risk = (entry - stop) * side
    good = (entry > 0) & (risk > 0) & (risk / entry >= 0.003) & (risk / entry <= 0.15)
    js, is_, idx, entry, stop, risk = js[good], is_[good], idx[good], entry[good], stop[good], risk[good]
    tgt = entry + side * R * risk
    res = np.full(len(js), np.nan)
    hit = np.zeros(len(js), bool)
    open_ = np.ones(len(js), bool)
    last = entry.copy()
    for k in range(1, Hb + 1):
        o, hh, ll, cc = O[js + k, is_], Hh[js + k, is_], L[js + k, is_], C[js + k, is_]
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
    rem = open_
    res[rem] = ((last - entry) * side / entry)[rem]
    return idx, res - COST, hit, risk / entry


# ------------------------------------------------------------------ обучение на разных промежутках
def years_of(P, tf):
    return (P["c"].index.year.values if tf == "d" else P["ts"].year.values)


def news_mask(tf, P, news, js, is_, day_idx=None):
    """Есть ли новость о компании в день события или накануне. −1 — новостей за тот период нет в архиве."""
    cols = list(P["c"].columns)
    out = np.zeros(len(js), int)
    for k, (j, i) in enumerate(zip(js, is_)):
        d = j if tf == "d" else day_idx[j]
        nd = news.get(cols[i], ())
        out[k] = 1 if (d in nd or d - 1 in nd) else 0
    return out


def variants(tf, P, news, day_idx, news_ok_from):
    B = base(P)
    yrs = years_of(P, tf)
    g = GRID[tf]
    V = {}
    for N, pen, pos, rng, vx in itertools.product(g["N"], g["pen"], g["pos"], g["rng"], g["vx"]):
        up, dn = detect(B, N, pen, pos, rng, vx, LIQ[tf])
        for side, m in ((1, up), (-1, dn)):
            js0, is0 = dedup(m)
            if len(js0) < 20:
                continue
            nm = news_mask(tf, P, news, js0, is0, day_idx)
            for R, Hb in itertools.product(g["R"], g["Hb"]):
                ix, r, hit, rk = trades(B, js0, is0, side, R, Hb)
                if not len(ix):
                    continue
                js, is_, nmk = js0[ix], is0[ix], nm[ix]
                y = yrs[js]
                known = js >= news_ok_from
                for nw in NEWS:
                    sel = np.ones(len(js), bool) if nw == "any" else known & ((nmk == 0) if nw == "no" else (nmk == 1))
                    V[("up" if side > 0 else "down", N, pen, pos, rng, vx, R, Hb, nw)] = (y[sel], r[sel], hit[sel], js[sel], is_[sel], rk[sel])
        log(f"{tf}: N={N} прокол {pen:.1%} закрытие {pos} диапазон {rng}·ATR оборот {vx}× — событий рост {int(up.sum())}, падение {int(dn.sum())}")
    return V


def choose(V, side, lo, hi, n_min=40):
    best, bk = None, None
    for k, (y, r, *_) in V.items():
        if k[0] != side:
            continue
        m = (y >= lo) & (y <= hi)
        if m.sum() < n_min:
            continue
        s = tstat(r[m])
        if s["mean"] > 0 and s["t"] >= 2 and (best is None or s["t"] > best["t"]):
            best, bk = s, k
    return bk, best


def walk(tf, V, side, first, last, first_test):
    res = {}
    for sch, w in SCHEMES.items():
        folds, allr, allh = [], [], []
        for y in range(max(first_test, first + 2), last + 1):
            lo = first if w is None else max(first, y - w)
            if y - lo < 2:
                continue
            k, st = choose(V, side, lo, y - 1)
            r = h = np.array([])
            if k:
                yy, rr, hh, *_ = V[k]
                r, h = rr[yy == y], hh[yy == y]
            folds.append(dict(year=y, train=[lo, y - 1], pick=[x if isinstance(x, str) else float(x) for x in k] if k else None, train_stat=st,
                              test=tstat(r) if len(r) else None, hit=round(float(h.mean()), 3) if len(h) else None))
            allr.append(r)
            allh.append(h)
        R_ = np.concatenate(allr) if allr else np.array([])
        H_ = np.concatenate(allh) if allh else np.array([])
        by = {f["year"]: f["test"]["mean"] for f in folds if f["test"] and f["test"]["n"] >= 3}
        pos = float(np.mean([v > 0 for v in by.values()])) if by else 0.0
        o = tstat(R_)
        o.update(prob=round(float(H_.mean()), 3) if len(H_) else None, years_traded=len(by), pos_years=round(pos, 2), by_year=by)
        o["passes"] = bool(o["n"] >= BAR["n"] and o["mean"] > 0 and o["t"] >= BAR["t"] and pos >= BAR["pos_years"] and len(by) >= BAR["min_years"])
        res[sch] = dict(oos=o, folds=folds)
        log(f"{tf} {side} [{sch}]: вне выборки {o['n']} сделок, среднее {o['mean'] * 100:+.2f}% после издержек, t={o['t']}, "
            f"цель раньше стопа {(o['prob'] or 0) * 100:.0f}%, плюс в {pos:.0%} из {len(by)} лет → {'ПРОХОДИТ' if o['passes'] else 'не проходит'}")
    return res


def decide(tf, V, side, res, first, last):
    ok = [(s, r) for s, r in res.items() if r["oos"]["passes"]]
    if not ok:
        best = max(res.items(), key=lambda z: z[1]["oos"]["t"])
        return dict(on=False, scheme=best[0], oos=best[1]["oos"], why="ни одна схема обучения не дала устойчивого результата вне выборки")
    sch, r = max(ok, key=lambda z: z[1]["oos"]["t"])
    w = SCHEMES[sch]
    lo = first if w is None else max(first, last + 1 - w)
    k, st = choose(V, side, lo, last)
    if not k:
        return dict(on=False, scheme=sch, oos=r["oos"], why="на последнем окне нет прибыльного варианта")
    _, N, pen, pos, rng, vx, R, Hb, nw = k
    return dict(on=True, scheme=sch, rawkey=k, train=[lo, last], train_stat=st, oos=r["oos"], news=nw,
                params=dict(N=int(N), pen=float(pen), pos=float(pos), rng=float(rng), vx=float(vx), R=float(R), Hb=int(Hb), liq=LIQ[tf]),
                prob=r["oos"]["prob"], key=[x if isinstance(x, str) else float(x) for x in k])


def run_tf(tf, P, news, day_idx, news_ok_from, first_test, out):
    yrs = years_of(P, tf)
    first, last = int(yrs[0]), int(yrs[-1])
    V = variants(tf, P, news, day_idx, news_ok_from)
    out[tf] = {"range": [str(P["c"].index[0]) if tf == "h" else str(P["c"].index[0].date()), str(P["c"].index[-1]) if tf == "h" else str(P["c"].index[-1].date())],
               "tickers": int(P["c"].shape[1])}
    cols = list(P["c"].columns)
    for side in ("up", "down"):
        res = walk(tf, V, side, first, last, first_test)
        dec = decide(tf, V, side, res, first, last)
        out[tf][side] = dict(decision=dec, schemes={k: dict(oos=v["oos"], folds=v["folds"]) for k, v in res.items()})
        if dec["on"]:
            y, r, h, js, is_, rk = V[dec.pop("rawkey")]
            when = P["c"].index[js]
            pd.DataFrame({"t": [cols[i] for i in is_], "j": js, "when": [str(w) for w in when], "year": y, "r": r, "hit": h.astype(int), "risk": rk}).to_csv(
                os.path.join(OUT, f"shake_rows_{tf}_{side}.csv"), index=False)
        log(f"ИТОГ {tf} {side}: " + (f"правило {dec['params']}, новости={dec['news']}, вероятность цели {dec['prob']:.0%}" if dec["on"] else "сигналов нет — " + dec["why"]))


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    out = {"built": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"), "cost": COST, "bar": BAR,
           "schemes": {k: (v or "все годы") for k, v in SCHEMES.items()}, "grid": GRID}
    P, _ = S.load_panel()
    days = P["c"].index
    news = S.load_news(days)
    nf = int(np.searchsorted(days.values, np.datetime64(S.NEWS_FROM)))
    out["news_from"] = S.NEWS_FROM
    run_tf("d", P, news, None, nf, 2012, out)
    try:
        Ph = hourly_panel()
    except Exception as e:  # noqa: BLE001
        log("часовые данные недоступны:", e)
        Ph = None
    if Ph is not None:
        dd = days.values.astype("datetime64[D]")
        day_idx = np.searchsorted(dd, Ph["ts"].values.astype("datetime64[D]"))
        nfh = int(np.searchsorted(Ph["ts"].values, np.datetime64(S.NEWS_FROM)))
        run_tf("h", Ph, news, day_idx, nfh, 2022, out)
    json.dump(out, open(os.path.join(OUT, "shakeout.json"), "w"), ensure_ascii=False, default=float)
    log("готово за", round((time.time() - t0) / 60, 1), "мин")


if __name__ == "__main__":
    main()
