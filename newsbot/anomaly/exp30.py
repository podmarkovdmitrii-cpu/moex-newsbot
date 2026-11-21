# -*- coding: utf-8 -*-
"""Проверка №30: стопы, тейки, правила риска и размер позиций для сборной стратегии (S1 + F2 + F4), 5-минутные свечи 2020–2026.

Сигналы — как у бота (№29, «правила бота, фильтр строго»): S1 — все сигналы роста (лимит на откате 50% хода); F2 — рост, покупка сразу,
фильтр «верхние 30%» (базовые признаки); F4 — падение, покупка сразу, фильтр с математикой. Порог фильтра — по 3 прошлым годам.

1) Выход: стоп × тейк × срок по 5-мин барам. Стоп: нет / 1,5 / 2 / 2,5 / 3 ATR / «за уровнем» (уровень разворотов в 1,2–3,2 ATR
   от входа + 0,3 ATR, иначе 2,5 ATR) / катастрофический −15% и −20%. Тейк: как сейчас (S1 +25% хода, F4 +200% хода, F2 нет) / нет /
   2R / 3R / 4R / 5R. Срок: свой (5 / 10 / 20 дн.) или 20 дн. Гэп через стоп — по открытию бара; стоп и тейк в одном баре — стоп;
   издержки как в №22 + 0,05% на стопе. ATR — дневной (14) на закрытии дня перед сигналом.
   Выбор лучшего по 2020–2022, проверка 2023–2026.
2) Правила из журнала пользователя как фильтры входа: стоп < 1,5 ATR и цель < 1,5R — запрещены (это часть сетки); против тренда
   (цена и наклон SMA50); рынок против (индекс к часу сигнала ≤ −1%); догоняние (ход за 5 дней до сигнала > 2 ATR); вход дальше
   1 ATR от рынка (лимит S1).
3) Портфель с переоценкой открытых позиций по дневным закрытиям (просадка честнее, чем по закрытым сделкам):
   размер — фиксированная доля капитала (2,5…15%) или по риску (0,5 / 1 / 1,5% капитала на стоп); потолок загрузки 100 / 150 / 200% /
   без потолка (не влезает — позиция урезается до свободного места, меньше трети — пропуск); поведенческие правила: после убытка за 48 ч —
   половинный размер, после 2 убытков подряд — пауза 1 день, дневной убыток закрытых ≥ 2% капитала — до конца дня новых сделок нет.
Не является индивидуальной инвестиционной рекомендацией.
"""
import bisect
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp18 as E18  # noqa: E402
import exp29 as E29  # noqa: E402
import scan_core as SC  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
SEL = 2023
OWN = {"S1": 5, "F2": 10, "F4": 20}
STOPS = ["нет", "1,5 ATR", "2 ATR", "2,5 ATR", "3 ATR", "за уровнем", "−15%", "−20%"]
TAKES = ["как сейчас", "нет", "2R", "3R", "4R", "5R"]


def daily_arrays(Dd):
    out = {}
    for t, v in Dd.items():
        if len(v) < 60:
            continue
        d = [r[0][:10] for r in v]
        o, h, l, c = (np.array([float(r[k]) for r in v]) for k in (1, 2, 3, 4))
        A = SC.atr(h, l, c)
        m50 = pd.Series(c).rolling(50).mean().values
        sl = np.r_[np.full(10, np.nan), m50[10:] - m50[:-10]]
        out[t] = dict(d=d, o=o, h=h, l=l, c=c, A=A, m50=m50, sl=sl)
    return out


def seq_full(r, Dd, bars, hold, lim):
    """Как exp20.build_seq, но с датами баров и сроком hold. → (вход, массив o/h/l/c, даты баров, дни срока) или None."""
    d0 = r.d.strftime("%Y-%m-%d")
    days = E18.trading_days(Dd, r.t, d0, hold)
    if len(days) < hold:
        return None
    band = {dd: (lo, hi) for dd, lo, hi in days}
    for x in Dd.get(r.t, []):
        if x[0][:10] == d0:
            band[d0] = (float(x[3]), float(x[2]))
            break
    seq, miss = [], 0
    for dd in [d0] + [x[0] for x in days]:
        b = bars.get((r.t, dd))
        if not b:
            miss += dd != d0
            continue
        lo, hi = band.get(dd, (0, 1e18))
        seq += [(dd, x[0], x[1], x[2], x[3], x[4]) for x in b if x[3] >= 0.7 * lo and x[2] <= 1.3 * hi]
    if miss > hold // 4 or not seq:
        return None
    t0 = f"{int(r.c) + 1:02d}:05"
    if lim:
        e = r.p0 - 0.5 * (r.p0 - r.prev)
        k0 = next((k for k, x in enumerate(seq) if x[0] == d0 and x[1] >= t0 and x[4] <= e), None)
        if k0 is None:
            return None
    else:
        k0 = next((k for k, x in enumerate(seq) if x[0] == d0 and x[1] >= t0), None)
        if k0 is None:
            k0 = next((k for k, x in enumerate(seq) if x[0] > d0), None)
        if k0 is None:
            return None
        e = seq[k0][2]
    S = [x for x in seq[k0 + 1:] if x[0] <= days[-1][0]]
    if not S:
        return None
    return e, np.array([[x[2], x[3], x[4], x[5]] for x in S], float), [x[0] for x in S], [x[0] for x in days], seq[k0][0]


def sim(e, a, dts, last_day, S, T, cost):
    """Лонг: стоп S (цена или None), тейк T (цена или None), выход по закрытию last_day. → (доходность, дата выхода, по стопу?)."""
    n = bisect.bisect_right(dts, last_day)
    if n == 0:
        return None
    o, h, l, c = a[:n, 0], a[:n, 1], a[:n, 2], a[:n, 3]
    js = np.nonzero(l <= S)[0] if S else []
    jt = np.nonzero(h >= T)[0] if T else []
    j_s = js[0] if len(js) else 10 ** 9
    j_t = jt[0] if len(jt) else 10 ** 9
    if j_s <= j_t and j_s < 10 ** 9:
        px = min(o[j_s], S)
        return px / e - 1 - cost - 0.0005, dts[j_s], True
    if j_t < 10 ** 9:
        return T / e - 1 - cost, dts[j_t], False
    return c[-1] / e - 1 - cost, dts[n - 1], False


def build(E, R, F, Dd, bars, cost):
    yr = E.d.dt.year.values
    DA = daily_arrays(Dd)
    bot = {"S1": ("РЛ", ("m", 0.25), "all"), "F2": ("РС", None, "base"), "F4": ("ПС", ("m", 2.0), "math")}
    rows = []
    for Y in range(2020, 2027):
        for nm, (sl, tp, fs) in bot.items():
            hold = OWN[nm]
            sg = E29.SLOTS[sl][2]
            p = thr = None
            if fs != "all":
                r_ = R[(sl, tp, hold)]
                ph = np.concatenate([F.scores((sl, fs, tp, hold), r_, sg, y) for y in range(Y - 3, Y)])
                thr = float(np.quantile(ph[np.isfinite(ph)], 0.7))
                p = F.scores((sl, fs, tp, hold), r_, sg, Y)
            for i in np.where((yr == Y) & (E.s.values == sg))[0]:
                if p is not None and not (p[i] == p[i] and p[i] >= thr):
                    continue
                r = E.iloc[i]
                q = seq_full(r, Dd, bars, 20, nm == "S1")
                if q is None:
                    continue
                e, a, dts, days, d_in = q
                da = DA.get(r.t)
                if da is None:
                    continue
                d0 = r.d.strftime("%Y-%m-%d")
                k = bisect.bisect_left(da["d"], d0) - 1      # последний день до сигнала
                if k < 60 or not da["A"][k] > 0:
                    continue
                A = float(da["A"][k])
                lv = SC.strong_levels(da["h"], da["l"], k, A)
                dist = e - lv
                ok = (dist >= 1.2 * A) & (dist <= 3.2 * A)
                lvl = float(e - (dist[ok].min() + 0.3 * A)) if ok.any() else e - 2.5 * A
                c_ = da["c"]
                mv = abs(r.p0 / r.prev - 1)
                rows.append(dict(strat=nm, t=r.t, d=r.d, e=e, a=a, dts=dts, own_last=days[OWN[nm] - 1], last20=days[-1], d_in=d_in, A=A, lvl=lvl,
                                 tp_now=(e * (1 + tp[1] * mv) if tp else None), cost=float(cost[i]),
                                 trend=bool(c_[k] > da["m50"][k] and da["sl"][k] > 0),
                                 mkt=float((r.get("ixr_c") if r.get("ixr_c") == r.get("ixr_c") else 0) * (1 if sg > 0 else -1)),
                                 chase=float((c_[k] - c_[k - 5]) / A), far=float((r.p0 - e) / A)))
    return rows


def stop_px(x, s):
    e, A = x["e"], x["A"]
    return {"нет": None, "1,5 ATR": e - 1.5 * A, "2 ATR": e - 2 * A, "2,5 ATR": e - 2.5 * A, "3 ATR": e - 3 * A, "за уровнем": x["lvl"],
            "−15%": e * 0.85, "−20%": e * 0.80}[s]


def take_px(x, t, S):
    if t == "как сейчас":
        return x["tp_now"]
    if t == "нет":
        return None
    if S is None:
        return "skip"
    k = float(t[0])
    return x["e"] + k * (x["e"] - S)


def variants(rows):
    """Все комбинации выхода → {(стратегия, стоп, тейк, срок): [(доходность, R, дата выхода, по стопу) на каждую сделку]}."""
    out = {}
    for s in STOPS:
        for t in TAKES:
            for hold in ("свой", "20 дн."):
                res = []
                skip = False
                for x in rows:
                    S = stop_px(x, s)
                    T = take_px(x, t, S)
                    if T == "skip":
                        skip = True
                        break
                    last = x["own_last"] if hold == "свой" else x["last20"]
                    z = sim(x["e"], x["a"], x["dts"], last, S, T, x["cost"])
                    if z is None:
                        res.append(None)
                        continue
                    risk = (x["e"] - S) / x["e"] if S else None
                    res.append((z[0], z[0] / risk if risk else None, z[1], z[2], risk))
                if not skip:
                    out[(s, t, hold)] = res
    return out


def stats(vals, rows, strat, mask=None):
    m = [(v, x) for v, x in zip(vals, rows) if v is not None and x["strat"] == strat and (mask is None or mask(x))]
    out = {}
    for nm, f in (("all", lambda x: True), ("sel", lambda x: x["d"].year < SEL), ("test", lambda x: x["d"].year >= SEL)):
        r = np.array([v[0] for v, x in m if f(x)])
        out[nm] = (float(r.mean()) if len(r) else np.nan, len(r), float((r > 0).mean()) if len(r) else np.nan)
    rr = [v[1] for v, x in m if v[1] is not None]
    out["R"] = float(np.mean(rr)) if rr else None
    out["stop_share"] = float(np.mean([v[3] for v, x in m])) if m else np.nan
    return out


def closes(Dd):
    return {t: {r[0][:10]: float(r[4]) for r in v} for t, v in Dd.items()}


def portfolio(rows, V, pick, CL, days_all, mode="fix", w=0.10, rk=0.01, cap=None, behave=False):
    """pick: {стратегия: (стоп, тейк, срок)}; переоценка по дневным закрытиям. → метрики."""
    trades = []
    for strat, key in pick.items():
        for v, x in zip(V[key], rows):
            if v is None or x["strat"] != strat:
                continue
            trades.append(dict(strat=strat, t=x["t"], d_in=x["d_in"], d_out=v[2], ret=v[0], risk=v[4], e=x["e"]))
    trades.sort(key=lambda z: (z["d_in"], z["t"]))
    by_day = {}
    for z in trades:
        by_day.setdefault(z["d_in"], []).append(z)
    cash_pnl, open_, eq_curve, loads = 0.0, [], [], []
    hist = []            # закрытые: (дата выхода, доходность)
    pause_until, skipped, cut, taken = "", 0, 0, 0
    prev_d = ""
    for d in days_all:
        # закрытия по дате выхода (до входов этого дня — освобождают место)
        still = []
        for z in open_:
            if z["d_out"] < d:
                cash_pnl += z["size"] * z["ret"]
                hist.append((z["d_out"], z["ret"], z["size"] * z["ret"]))
            else:
                still.append(z)
        open_ = still
        cap_now = 1 + cash_pnl
        if behave:
            last2 = [r_ for dd, r_, p in hist[-2:]]
            if len(last2) == 2 and all(r_ <= 0 for r_ in last2) and hist[-1][0] >= prev_d:
                pause_until = d       # 2 убытка подряд, последний — вчера или сегодня → сегодня без новых сделок
        for z in by_day.get(d, []):
            if behave and (pause_until == d or sum(p for dd, r_, p in hist if dd == d) <= -0.02 * cap_now):
                skipped += 1
                continue
            if mode == "fix":
                size = w * cap_now
            else:
                size = min(0.20, rk / z["risk"]) * cap_now if z["risk"] else w * cap_now
            if behave and hist and hist[-1][1] <= 0 and (pd.Timestamp(d) - pd.Timestamp(hist[-1][0])).days <= 2:
                size *= 0.5
            used = sum(o["size"] for o in open_)
            if cap is not None and used + size > cap * cap_now:
                free = cap * cap_now - used
                if free < size / 3:
                    skipped += 1
                    continue
                size, cut = free, cut + 1
            open_.append(dict(z, size=size))
            taken += 1
        # переоценка
        mtm = 0.0
        for z in open_:
            c = CL.get(z["t"], {}).get(d)
            if c and d >= z["d_in"]:
                mtm += z["size"] * (c / z["e"] - 1)
        eq_curve.append(1 + cash_pnl + mtm)
        prev_d = d
        loads.append(sum(o["size"] for o in open_) / max(1e-9, 1 + cash_pnl))
    for z in open_:
        cash_pnl += z["size"] * z["ret"]
    eq = pd.Series(eq_curve, index=pd.to_datetime(days_all))
    eq.iloc[-1] = 1 + cash_pnl
    dd = float((eq / eq.cummax() - 1).min())
    yrs = {int(y): float(g.iloc[-1] / (eq[eq.index.year < y].iloc[-1] if (eq.index.year < y).any() else 1.0) - 1) for y, g in eq.groupby(eq.index.year)}
    n_years = (eq.index[-1] - eq.index[0]).days / 365.25
    sel = eq[eq.index.year < SEL]
    tst = eq[eq.index.year >= SEL]
    cagr = lambda s, base: float((s.iloc[-1] / base) ** (365.25 / max(1, (s.index[-1] - s.index[0]).days)) - 1)  # noqa: E731
    return dict(final=float(eq.iloc[-1]), cagr=float(eq.iloc[-1] ** (1 / n_years) - 1), dd=dd, years=yrs, taken=taken, skipped=skipped, cut=cut,
                maxload=float(max(loads)), avgload=float(np.mean(loads)), cagr_sel=cagr(sel, 1.0), cagr_test=cagr(tst, float(sel.iloc[-1])),
                dd_test=float((tst / tst.cummax() - 1).min()), monthly={k.strftime("%Y-%m"): round(float(v), 4) for k, v in eq.resample("ME").last().items()})


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    E, R, FE, Dd, bars, cost = E29.load_all()
    F = E29.Filt(E, FE)
    rows = build(E, R, F, Dd, bars, cost)
    log(f"сделок с 5-мин путём: " + ", ".join(f"{s} {sum(1 for x in rows if x['strat'] == s)}" for s in OWN) + f"; {round(time.time() - t0)} с")
    V = variants(rows)
    out = {}
    log("===== 1) СТОП × ТЕЙК × СРОК: средняя сделка 2020–22 → 2023–26 (прибыльных, доля выходов по стопу) =====")
    best = {}
    for strat in OWN:
        res = []
        for key, vals in V.items():
            st = stats(vals, rows, strat)
            res.append((key, st))
        res.sort(key=lambda z: -(z[1]["sel"][0] if z[1]["sel"][0] == z[1]["sel"][0] else -9))
        cur = next(z for z in res if z[0] == ("нет", "как сейчас", "свой"))
        log(f"  {strat}: КАК СЕЙЧАС (без стопа, тейк как сейчас, свой срок): {cur[1]['sel'][0]:+.2%} ({cur[1]['sel'][1]}) → {cur[1]['test'][0]:+.2%} ({cur[1]['test'][1]}), "
            f"прибыльных {cur[1]['all'][2]:.0%}")
        for key, st in res[:8]:
            log(f"    {key[0]:>10} | {key[1]:>10} | {key[2]:>6}: {st['sel'][0]:+.2%} → {st['test'][0]:+.2%}; прибыльных {st['all'][2]:.0%}; "
                f"по стопу {st['stop_share']:.0%}" + (f"; средний R {st['R']:+.2f}" if st["R"] is not None else ""))
        # сводка по стопам и тейкам (среднее по остальным параметрам, весь период)
        for dim, names, idx in (("стоп", STOPS, 0), ("тейк", TAKES, 1)):
            line = []
            for nmv in names:
                vals = [st["sel"][0] for key, st in res if key[idx] == nmv and st["sel"][0] == st["sel"][0]]
                valt = [st["test"][0] for key, st in res if key[idx] == nmv and st["test"][0] == st["test"][0]]
                if vals:
                    line.append(f"{nmv} {np.mean(vals):+.2%}/{np.mean(valt):+.2%}")
            log(f"    по {dim}у (2020–22/2023–26): " + "; ".join(line))
        best[strat] = res[0][0]
        out[strat] = {"current": cur[1], "top": [(list(k), s) for k, s in res[:15]]}
    log("  ВЫБРАНО ПО 2020–2022: " + "; ".join(f"{s}: {k}" for s, k in best.items()))
    # 2) правила-фильтры
    log("===== 2) ПРАВИЛА ИЗ ЖУРНАЛА КАК ФИЛЬТРЫ (на выбранном выходе; 2020–22 → 2023–26) =====")
    flt = {"только по тренду": lambda x: x["trend"], "рынок не против (индекс > −1%)": lambda x: x["mkt"] > -0.01,
           "без догоняния (5 дн. ≤ 2 ATR)": lambda x: x["chase"] <= 2, "вход ≤ 1 ATR от рынка": lambda x: x["far"] <= 1}
    out["filters"] = {}
    for strat in OWN:
        vals = V[best[strat]]
        base = stats(vals, rows, strat)
        log(f"  {strat} без фильтров: {base['sel'][0]:+.2%} ({base['sel'][1]}) → {base['test'][0]:+.2%} ({base['test'][1]})")
        for fn, f in flt.items():
            a_ = stats(vals, rows, strat, f)
            b_ = stats(vals, rows, strat, lambda x, f=f: not f(x))
            ok = a_["sel"][0] > base["sel"][0] + 0.002 and a_["test"][0] > base["test"][0]
            log(f"    {fn}: проходят {a_['sel'][0]:+.2%} ({a_['sel'][1]}) → {a_['test'][0]:+.2%} ({a_['test'][1]}); отсеянные {b_['sel'][0]:+.2%} ({b_['sel'][1]}) → "
                f"{b_['test'][0]:+.2%} ({b_['test'][1]}) — {'ПОДТВЕРЖДЁН' if ok else 'не подтверждён'}")
            out["filters"][f"{strat} {fn}"] = dict(pass_=a_, rest=b_, ok=bool(ok))
    # 3) портфель
    log("===== 3) ПОРТФЕЛЬ С ПЕРЕОЦЕНКОЙ: размер × потолок загрузки (капитал, в год 2020–26 / 2020–22 / 2023–26, просадка) =====")
    CL = closes(Dd)
    days_all = sorted(set(d for t in CL.values() for d in t if "2020-01-01" <= d <= "2026-12-31"))
    days_all = [d for d in days_all if pd.Timestamp(d).weekday() < 5]
    cur_pick = {s: ("нет", "как сейчас", "свой") for s in OWN}
    out["portfolio"] = {}
    for pname, pick in (("выход как сейчас", cur_pick), ("выход по 2020–22", best)):
        has_stop = all(k[0] != "нет" for k in pick.values())
        grid = [("fix", w, None) for w in (0.025, 0.05, 0.075, 0.10, 0.15)] + ([("risk", 0.10, rk) for rk in (0.005, 0.01, 0.015)] if has_stop else [])
        for mode, w, rk in grid:
            for cap in (1.0, 1.5, 2.0, None):
                for behave in (False, True):
                    if behave and not (cap == 1.0 or cap == 1.5):
                        continue
                    r = portfolio(rows, V, pick, CL, days_all, mode=mode, w=w, rk=rk or 0.01, cap=cap, behave=behave)
                    nm = (f"{pname} | {'доля ' + format(w, '.1%') if mode == 'fix' else 'риск ' + format(rk, '.1%')} | "
                          f"потолок {'нет' if cap is None else format(cap, '.0%')}{' | правила после убытков' if behave else ''}")
                    out["portfolio"][nm] = r
                    log(f"  {nm}: ×{r['final']:.2f}, {r['cagr']:+.1%} в год ({r['cagr_sel']:+.1%} / {r['cagr_test']:+.1%}), просадка {r['dd']:.0%} "
                        f"(2023–26 {r['dd_test']:.0%}); сделок {r['taken']}, пропущено {r['skipped']}, урезано {r['cut']}; загрузка макс. {r['maxload']:.0%}, "
                        f"средняя {r['avgload']:.0%}")
                    log("     по годам: " + ", ".join(f"{y}: {v:+.1%}" for y, v in r["years"].items()))
                    log(f"КРИВАЯ {nm} | " + " ".join(f"{k}={v}" for k, v in r["monthly"].items()))
    json.dump(out, open(os.path.join(TR.MODELS, "exp30_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №30 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
