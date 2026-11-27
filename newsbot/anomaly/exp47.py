# -*- coding: utf-8 -*-
"""Проверка №47 (аудит, партия 3): (А) задержка котировок и исполнение; (Б) хвостовой риск загрузки.

Данные — как в №45/46: 5-мин 2020–2026, точные правила бота, полный состав (ушедшие), обвалы в расчёте.
(А) Исследования входят с первого 5-мин бара после часа сигнала (F2/F4 — по открытию; S1 — лимит, исполнен, если минимум бара ≤ цены лимита).
   Бот видит сигнал позже: снимки раз в ~5 мин (с ALGOPACK — в реальном времени), без ALGOPACK ISS отдаёт цены с задержкой 15 мин.
   Варианты: сдвиг входа 0 / 5 / 10 / 20 мин; исполнение S1 по минимуму бара (биржевой лимит) или по закрытию бара (как виртуальный счёт по снимкам).
   Выход: доля исполненных S1 и чем отличаются неисполненные (по размеру хода); сдвиг цены входа F2/F4 (средний, по часу сигнала); портфель.
(Б) Потолок загрузки 150% / 120% / 100%; «динамический» (100%, когда 20-дн. волатильность IMOEX выше 80-го процентиля прошлого года);
   предохранитель по просадке с переоценкой −20% / −15% (новые входы останавливаются на 20 торговых дней; открытые позиции не закрываются — как в боте).
   Выход: в год, просадка, худший день, худший год; бутстрэп по годам к базе.
Не является индивидуальной инвестиционной рекомендацией.
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp35 as E35  # noqa: E402
import exp40 as E40  # noqa: E402
import exp45 as E45  # noqa: E402
import train as TR  # noqa: E402

log = TR.log


def make_seq(orig, shift, fill):
    """seq_full со сдвигом входа на shift минут и исполнением лимита S1 по минимуму ('low') или закрытию ('close') бара."""
    import exp18 as E18

    def seq_full(r, Dd, bars, hold, lim):
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
        mm = (int(r.c) + 1) * 60 + 5 + shift
        t0 = f"{mm // 60:02d}:{mm % 60:02d}"
        if lim:
            e = r.p0 - 0.5 * (r.p0 - r.prev)
            col = 4 if fill == "low" else 5
            k0 = next((k for k, x in enumerate(seq) if x[0] == d0 and x[1] >= t0 and x[col] <= e), None)
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
    return seq_full


def trades_from(rows, V):
    out = []
    for v_, x in zip(V[("нет", "как сейчас", "свой")], rows):
        if v_ is not None:
            out.append(dict(strat=x["strat"], t=x["t"], d=x["d"].strftime("%Y-%m-%d"), d_in=x["d_in"], d_out=v_[2], ret=float(v_[0]), e=x["e"]))
    return out


def port(trades, CL, days, cap=1.5, capf=None, ks=None, halt_days=20):
    """Как exp40.port (одна позиция на бумагу), но с потолком cap (или capf(день)) и предохранителем ks (просадка с переоценкой)."""
    W, F2_LOAD, F2_OPEN = E40.W, E40.F2_LOAD, E40.F2_OPEN
    by = {}
    for z in sorted(trades, key=lambda z: (z["d_in"], 0 if z["strat"] != "F2" else 1, z["t"])):
        by.setdefault(z["d_in"], []).append(z)
    cash, op, eq, peak, halt = 0.0, [], [], 1.0, 0
    nh = 0
    for i, d in enumerate(days):
        op2 = []
        for z in op:
            if z["d_out"] < d:
                cash += z["size"] * z["ret"]
            else:
                op2.append(z)
        op = op2
        capn = 1 + cash
        cp = capf(d) if capf else cap
        if halt > 0:
            halt -= 1
        else:
            for z in by.get(d, []):
                if any(o["t"] == z["t"] for o in op):
                    continue
                used = sum(o["size"] for o in op) / capn
                w = W[z["strat"]]
                if used + w > cp + 1e-9:
                    continue
                if z["strat"] == "F2" and (used + w > min(F2_LOAD, cp) + 1e-9 or sum(1 for o in op if o["strat"] == "F2") >= F2_OPEN):
                    continue
                e = z.get("e") or CL.get(z["t"], {}).get(d)
                if not e:
                    continue
                op.append(dict(z, size=w * capn, e=e))
        mtm = 0.0
        for z in op:
            c = CL.get(z["t"], {}).get(d)
            if c and d >= z["d_in"]:
                mtm += z["size"] * (c / z["e"] - 1)
        v = 1 + cash + mtm
        eq.append(v)
        peak = max(peak, v)
        if ks is not None and halt == 0 and v / peak - 1 <= ks:
            halt, nh, peak = halt_days, nh + 1, v          # как /resume после паузы: пик заново
    return pd.Series(eq, index=pd.to_datetime(days)), nh


def summary(name, s, base_years=None, extra=""):
    m = E35.metr(s)
    wd = s.pct_change().dropna()
    line = (f"  {name}: {m['cagr']:+.2%} в год, просадка {m['dd']:.0%}, худший день {wd.min():+.1%} ({wd.idxmin().date()}), худший год {m['worst']:+.1%}{extra}")
    out = dict(cagr=m["cagr"], dd=m["dd"], worst_day=float(wd.min()), worst=m["worst"], years=m["years"])
    if base_years:
        ys = sorted(base_years)
        dif = np.array([m["years"].get(y, 0) - base_years.get(y, 0) for y in ys])
        rng = np.random.default_rng(47)
        bs = np.array([dif[rng.integers(0, len(dif), len(dif))].mean() for _ in range(3000)])
        line += f"; к базе {dif.mean():+.2%}/год [{np.percentile(bs, 5):+.2%}; {np.percentile(bs, 95):+.2%}]"
        out["vs"] = dict(mean=float(dif.mean()), lo90=float(np.percentile(bs, 5)), hi90=float(np.percentile(bs, 95)))
    log(line)
    return out


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    rep = {}
    S = E45.load_full(rep)
    T0, CL, days, E = S["T"], S["CL"], S["days"], S["E"]
    import exp29 as E29
    import exp30 as E30
    F = E29.Filt(S["E"], S["FE"])
    orig = E30.seq_full
    key_c = {(r.t, r.d.strftime("%Y-%m-%d")): (int(r.c), abs(r.p0 / r.prev - 1)) for r in E.itertuples()}
    # ---------------- (А) задержка
    log("===== (А) Задержка котировок и исполнение =====")
    VAR = [("0 мин, лимит по минимуму (исследования)", 0, "low"), ("5 мин, по минимуму (снимки, ALGOPACK)", 5, "low"),
           ("10 мин, по минимуму", 10, "low"), ("20 мин, по минимуму (ISS без ALGOPACK)", 20, "low"),
           ("0 мин, по закрытию бара (виртуальный счёт)", 0, "close"), ("20 мин, по закрытию бара", 20, "close")]
    TT, resA = {}, {}
    for nm, sh, fill in VAR:
        E30.seq_full = make_seq(orig, sh, fill)
        try:
            rows = E30.build(E, S["R"], F, S["Dd"], S["bars"], S["cost"])
            T = trades_from(rows, E30.variants(rows))
        finally:
            E30.seq_full = orig
        TT[nm] = T
        s, *_ = E40.port(T, CL, days, "V1")
        m = E35.metr(s)
        n = {k: sum(z["strat"] == k for z in T) for k in ("S1", "F2", "F4")}
        mean = {k: float(np.mean([z["ret"] for z in T if z["strat"] == k])) if n[k] else None for k in n}
        resA[nm] = dict(cagr=m["cagr"], dd=m["dd"], n=n, mean=mean, years=m["years"])
        log(f"  {nm}: {m['cagr']:+.2%} в год, просадка {m['dd']:.0%}; сделок S1 {n['S1']} ({mean['S1']:+.2%}), F2 {n['F2']} ({mean['F2']:+.2%}), F4 {n['F4']} ({mean['F4']:+.2%})")
    base_nm = VAR[0][0]
    b = {(z["strat"], z["t"], z["d"]): z for z in TT[base_nm]}
    for nm, *_ in VAR[1:]:
        o = {(z["strat"], z["t"], z["d"]): z for z in TT[nm]}
        s1b = [k for k in b if k[0] == "S1"]
        lost = [k for k in s1b if k not in o]
        fill = 1 - len(lost) / max(1, len(s1b))
        mv_l = [key_c.get((k[1], k[2]), (0, np.nan))[1] for k in lost]
        mv_k = [key_c.get((k[1], k[2]), (0, np.nan))[1] for k in s1b if k in o]
        rl = [b[k]["ret"] for k in lost]
        line = (f"  {nm}: S1 исполнено {fill:.0%} от исследований ({len(s1b) - len(lost)} из {len(s1b)})"
                + (f"; неисполненные: ход {np.nanmedian(mv_l):.1%} (у исполненных {np.nanmedian(mv_k):.1%}), их результат в исследованиях {np.mean(rl):+.2%}" if lost else ""))
        gaps, by_h = [], {}
        for k, z in b.items():
            if k[0] in ("F2", "F4") and k in o and z["e"]:
                g = o[k]["e"] / z["e"] - 1
                gaps.append(g)
                h = key_c.get((k[1], k[2]), (None, 0))[0]
                by_h.setdefault(h, []).append(g)
        if gaps:
            line += (f"; F2/F4 цена входа: средняя {np.mean(gaps):+.2%}, медиана {np.median(gaps):+.2%}, |сдвиг| > 1% у {np.mean(np.abs(gaps) > 0.01):.0%}; по часу сигнала: "
                     + ", ".join(f"{h}: {np.mean(v):+.2%} ({len(v)})" for h, v in sorted(by_h.items(), key=lambda x: (x[0] is None, x[0] or 0))))
        log(line)
        resA[nm]["s1_fill"] = fill
        resA[nm]["gap"] = float(np.mean(gaps)) if gaps else None
    rep["задержка"] = resA
    log(f"{round(time.time() - t0)} с")
    # ---------------- (Б) хвостовой риск
    log("===== (Б) Хвостовой риск: потолок загрузки и предохранитель =====")
    IX = TR.load_series("d", ["IMOEX"], "2018-01-01", index=("IMOEX",)).get("IMOEX", [])
    ix = pd.Series({r[0][:10]: float(r[4]) for r in IX}).sort_index()
    vol = ix.pct_change().rolling(20).std()
    thr = vol.rolling(250, min_periods=120).quantile(0.8).shift(1)
    hi = (vol > thr).reindex(days).ffill().fillna(False)
    log(f"  дней с высокой волатильностью IMOEX (динамический потолок 100%): {int(hi.sum())} из {len(days)}")
    resB = {}
    s, _ = port(T0, CL, days, 1.5)
    resB["потолок 150% (как сейчас)"] = summary("потолок 150% (как сейчас)", s)
    by0 = resB["потолок 150% (как сейчас)"]["years"]
    for nm, kw in (("потолок 120%", dict(cap=1.2)), ("потолок 100%", dict(cap=1.0)),
                   ("динамический: 100% при высокой волатильности IMOEX", dict(capf=lambda d: 1.0 if bool(hi.get(d, False)) else 1.5)),
                   ("150% + предохранитель −20% (пауза 20 дн.)", dict(cap=1.5, ks=-0.20)), ("150% + предохранитель −15%", dict(cap=1.5, ks=-0.15)),
                   ("100% + предохранитель −15%", dict(cap=1.0, ks=-0.15))):
        s, nh = port(T0, CL, days, **kw)
        resB[nm] = summary(nm, s, by0, extra=f", срабатываний предохранителя {nh}" if kw.get("ks") else "")
    rep["хвост"] = resB
    json.dump(rep, open(os.path.join(TR.MODELS, "exp47_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №47 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
