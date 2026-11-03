# -*- coding: utf-8 -*-
"""Агент-проверяющий для «выноса стопов» (research/shakeout.py): живому боту — только правила, которые он подтвердил.

Для каждого правила, выбранного обучением на разных промежутках (дневные и часовые бары, рост и падение):
  1. Свой пересчёт (дневные бары): события и сделки заново — простыми циклами по каждой бумаге, без кода shakeout.py;
     число сделок и средний результат на окне обучения должны совпасть.
  2. Без заглядывания в будущее: событие бара j находится по данным, обрезанным на баре j (ATR, оборот, экстремумы — до j).
  3. Перестановочный тест: те же бумаги, та же сторона и тот же размер риска, но случайные бары входа — насколько результат
     правила лучше случайного входа (p-value).
  4. Стабильность: результат после издержек положителен не меньше чем в 60% лет.
Итог пишется в research-stats/shake_verify.json и добавляется в stats.json (ключ shakeout) — его читает живой агент.
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.environ.get("RESEARCH_DIR") or os.path.join(HERE, "out")
COST = float(os.environ.get("WF_COST", 0.003))
RNG = np.random.default_rng(20261103)


def log(*a):
    print(time.strftime("%H:%M:%S"), "[проверка выносов]", *a, flush=True)


def recount_daily(P, prm, side, news, nw, news_from_j):
    """Независимый пересчёт: цикл по бумагам и барам."""
    out = []
    N, pen, pos, rng, vx, R, Hb, liq = (prm[k] for k in ("N", "pen", "pos", "rng", "vx", "R", "Hb", "liq"))
    cols = list(P["c"].columns)
    for i, t in enumerate(cols):
        o, h, l, c, v = (P[k][t].values.astype(float) for k in ("o", "h", "l", "c", "v"))
        n = len(c)
        tr = np.full(n, np.nan)
        for j in range(1, n):
            if c[j - 1] == c[j - 1] and h[j] == h[j]:
                tr[j] = max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1]))
        last = -99
        nd = news.get(t, set())
        for j in range(max(N, 25), n - Hb - 1):
            if not (c[j] == c[j]) or j - last < 3:
                continue
            a = tr[j - 14:j]
            a = a[a == a]
            vv = v[j - 20:j]
            vv = vv[vv == vv]
            if len(a) < 10 or len(vv) < 15:
                continue
            atr, med = a.mean(), np.median(vv)
            rg = h[j] - l[j]
            if not (rg > 0 and rg >= rng * atr and v[j] >= vx * med and med >= liq):
                continue
            prev = l[j - N:j] if side > 0 else h[j - N:j]
            if np.isnan(prev).any():
                continue
            if side > 0:
                ext = prev.min()
                ok = l[j] < ext * (1 - pen) and c[j] >= l[j] + pos * rg and c[j] > ext
            else:
                ext = prev.max()
                ok = h[j] > ext * (1 + pen) and c[j] <= h[j] - pos * rg and c[j] < ext
            if not ok:
                continue
            last = j
            if nw != "any":
                if j < news_from_j:
                    continue
                has = (j in nd) or (j - 1 in nd)
                if (nw == "no") == has:
                    continue
            entry = o[j + 1]
            stop = l[j] * 0.999 if side > 0 else h[j] * 1.001
            risk = (entry - stop) * side
            if not (entry > 0 and risk > 0 and 0.003 <= risk / entry <= 0.15):
                continue
            tgt = entry + side * R * risk
            res, lastc = None, entry
            for k in range(1, Hb + 1):
                if not (c[j + k] == c[j + k]):
                    continue
                adverse = l[j + k] <= stop if side > 0 else h[j + k] >= stop
                fav = h[j + k] >= tgt if side > 0 else l[j + k] <= tgt
                if adverse:
                    px = stop if k == 1 else (min(o[j + k], stop) if side > 0 else max(o[j + k], stop))
                    res = (px - entry) * side / entry
                    break
                if fav:
                    res = R * risk / entry
                    break
                lastc = c[j + k]
            if res is None:
                res = (lastc - entry) * side / entry
            out.append((t, j, P["c"].index[j].year, res - COST))
    return pd.DataFrame(out, columns=["t", "j", "year", "r"])


def lookahead(P, rows, prm, side, k=100):
    """Событие на баре j должно находиться и по данным, обрезанным на j."""
    if rows.empty:
        return 1.0
    smp = rows.sample(min(k, len(rows)), random_state=1)
    ok = 0
    for _, r in smp.iterrows():
        j, t, N = int(r.j), r.t, prm["N"]
        h, l, c = (P[x][t].values[:j + 1] for x in ("h", "l", "c"))
        rg = h[j] - l[j]
        if side > 0:
            ok += int(l[j] < np.nanmin(l[j - N:j]) * (1 - prm["pen"]) and c[j] >= l[j] + prm["pos"] * rg)
        else:
            ok += int(h[j] > np.nanmax(h[j - N:j]) * (1 + prm["pen"]) and c[j] <= h[j] - prm["pos"] * rg)
    return ok / len(smp)


def permutation(P, rows, prm, side, n_perm=200):
    """Случайные бары входа у тех же бумаг, та же сторона и тот же риск (в %), те же R и горизонт."""
    O, Hh, L, C = (P[x] for x in ("o", "h", "l", "c"))
    col = {t: i for i, t in enumerate(C.columns)}
    O, Hh, L, C = O.values, Hh.values, L.values, C.values
    n, R, Hb = len(C), prm["R"], prm["Hb"]
    tick = np.array([col[t] for t in rows.t])
    rk = rows.risk.values
    real = float(rows.r.mean())
    null = []
    for _ in range(n_perm):
        js = RNG.integers(30, n - Hb - 2, size=len(tick))
        entry = O[js + 1, tick]
        stop = entry * (1 - side * rk)
        tgt = entry + side * R * entry * rk
        res = np.full(len(js), np.nan)
        open_ = np.ones(len(js), bool)
        last = entry.copy()
        for k in range(1, Hb + 1):
            hh, ll, cc = Hh[js + k, tick], L[js + k, tick], C[js + k, tick]
            v = open_ & (cc == cc)
            adv = np.where(side > 0, ll <= stop, hh >= stop)
            fav = np.where(side > 0, hh >= tgt, ll <= tgt)
            st = v & adv
            res[st] = -rk[st]
            open_ &= ~st
            tg = v & ~adv & fav
            res[tg] = R * rk[tg]
            open_ &= ~tg
            last = np.where(v, cc, last)
        res[open_] = ((last - entry) * side / entry)[open_]
        res = res - COST
        null.append(np.nanmean(res))
    null = np.array(null)
    return real, float((null >= real).mean())


def main():
    import study as S
    try:
        sh = json.load(open(os.path.join(OUT, "shakeout.json")))
    except Exception:  # noqa: BLE001
        log("shakeout.json нет — проверять нечего")
        return
    P, _ = S.load_panel()
    news = S.load_news(P["c"].index)
    nfj = int(np.searchsorted(P["c"].index.values, np.datetime64(S.NEWS_FROM)))
    Ph = None
    res = {}
    for tf in ("d", "h"):
        if tf not in sh:
            continue
        res[tf] = {}
        for side_name, side in (("up", 1), ("down", -1)):
            dec = (sh[tf].get(side_name) or {}).get("decision") or {}
            if not dec.get("on"):
                res[tf][side_name] = dict(on=False, verified=False, reasons=[dec.get("why") or "правило не выбрано"], oos=dec.get("oos"))
                log(f"{tf} {side_name}: сигналов нет — {dec.get('why')}")
                continue
            prm = dec["params"]
            p = os.path.join(OUT, f"shake_rows_{tf}_{side_name}.csv")
            rows = pd.read_csv(p)
            if tf == "h":
                if Ph is None:
                    import shakeout as SH
                    Ph = SH.hourly_panel()
                PP = Ph
            else:
                PP = P
            reasons = []
            lo = dec["train"][0]
            mine = None
            if tf == "d":
                rc = recount_daily(P, prm, side, news, dec["news"], nfj)
                a, b = rc[rc.year >= lo], rows[rows.year >= lo]
                mine = dict(n=int(len(a)), mean=round(float(a.r.mean()), 5) if len(a) else None)
                theirs = dict(n=int(len(b)), mean=round(float(b.r.mean()), 5) if len(b) else None)
                if not len(a) or abs(len(a) - len(b)) > max(5, 0.1 * len(b)) or abs((mine["mean"] or 0) - (theirs["mean"] or 0)) > max(0.002, 0.3 * abs(theirs["mean"] or 0)):
                    reasons.append(f"пересчёт не совпал: {mine} против {theirs}")
            la = lookahead(PP, rows, prm, side)
            if la < 0.97:
                reasons.append(f"событие находится без будущих данных лишь в {la:.0%} случаев")
            real, pv = permutation(PP, rows, prm, side)
            if pv > 0.05:
                reasons.append(f"не лучше случайного входа с тем же риском (p={pv:.2f})")
            by = rows.groupby("year").r.agg(["mean", "size"])
            by = by[by["size"] >= 5]
            frac = float((by["mean"] > 0).mean()) if len(by) else 0.0
            if frac < 0.6:
                reasons.append(f"результат положителен лишь в {frac:.0%} лет")
            res[tf][side_name] = dict(on=True, verified=not reasons, reasons=reasons, params=prm, news=dec["news"], prob=dec.get("prob"), oos=dec.get("oos"),
                                      perm_p=round(pv, 3), years_pos=round(frac, 2), lookahead_ok=round(la, 3), recount=mine,
                                      hist=dict(n=int(len(rows)), mean=round(float(rows.r.mean()), 5), prob=round(float(rows.hit.mean()), 3)))
            log(f"{tf} {side_name}: {prm}, новости={dec['news']} → {'ПОДТВЕРЖДЕНО' if not reasons else 'не подтверждено: ' + '; '.join(reasons)}")
    json.dump(res, open(os.path.join(OUT, "shake_verify.json"), "w"), ensure_ascii=False, default=float)
    sp = os.path.join(OUT, "stats.json")
    try:
        st = json.load(open(sp))
    except Exception:  # noqa: BLE001
        st = {}
    st["shakeout"] = res
    json.dump(st, open(sp, "w"), ensure_ascii=False, default=float)
    n_ok = sum(1 for tf in res for s in res[tf].values() if s.get("verified"))
    log("ИТОГ: подтверждённых правил выноса стопов", n_ok, "— живой агент даёт идеи только по ним")


if __name__ == "__main__":
    main()
