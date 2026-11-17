# -*- coding: utf-8 -*-
"""Проверка №20: где лучше фиксировать прибыль — на заданном проценте или на уровнях. 5-минутные свечи ALGOPACK, 2020–2026.

Три способа входа (как на виртуальном счёте):
  S1 — аномальный рост, лимит на откате 50% хода в тот же день;
  F2 — аномальный рост, покупка сразу после часа сигнала;
  F4 — аномальное падение, покупка сразу (выкуп).
Выходы (у каждого — предел по сроку 5, 10 или 20 торговых дней, иначе закрытие последнего дня):
  процент от цены входа: +1 … +30%;
  доля хода сигнала (от вчерашнего закрытия до цены сигнала): 25 … 300%;
  ATR (средний дневной диапазон за 14 дней): 0,5 … 4;
  уровни (берётся первый уровень выше цены входа не ближе 0,3%, иначе сделка идёт до срока):
    максимум дня до сигнала, вчерашний максимум, 20-, 60- и 250-дневные максимумы, классические точки разворота дня R1/R2/R3,
    недельные R1/R2, расширения Фибоначчи хода 127% и 162%, круглые числа, «ближайшее сопротивление» из всех;
    для выкупа падения ещё — вчерашнее закрытие (возврат к началу падения) и EMA20;
  скользящий стоп от максимума: 3, 5, 8, 12% и 2–3 ATR (прибыль не ограничена);
  без тейка — только срок.
Тейк засчитывается по 5-минутным максимумам после входа (по уровню, без лучшей цены), скользящий стоп — по минимумам
(при гэпе — по цене открытия). Издержки 0,1/0,2% по ликвидности. Выбор лучшего — по 2020–2022 гг., проверка — 2023–2026 гг.
Кэш 5-минутных баров — тот же, что в проверке №18 (EXP_A18_DIR), докачиваются только недостающие дни.
"""
import datetime as dt
import gzip
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import exp9 as E9  # noqa: E402
import exp15 as E15  # noqa: E402
import exp18 as E18  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
FROM5 = int(os.environ.get("EXP_FROM5", "2020"))
SPLIT = int(os.environ.get("EXP_SPLIT", "2023"))
HOLDS = (5, 10, 20)
WAYS = {"S1 рост, откат 50%": (1, "lim"), "F2 рост, вход сразу": (1, "sig"), "F4 выкуп падения": (-1, "sig")}
PCT = [0.01, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10, 0.15, 0.20, 0.30]
MOVE = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
ATRK = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
TRAIL = [0.03, 0.05, 0.08, 0.12]
TRAIL_ATR = [2.0, 3.0]
LEVELS = ["макс. дня до сигнала", "вчерашний макс.", "20-дн. макс.", "60-дн. макс.", "250-дн. макс.", "R1 дня", "R2 дня", "R3 дня",
          "R1 недели", "R2 недели", "Фибо 127% хода", "Фибо 162% хода", "круглое число", "ближайшее сопротивление",
          "вчерашнее закрытие", "EMA20"]


def daily_info(Dd, t, d0):
    """Уровни на момент сигнала по дневным свечам до дня сигнала (ISS)."""
    df = CD.series(Dd.get(t, []))
    df = df[df.d < pd.Timestamp(d0)]
    if len(df) < 30:
        return None
    h, l, c = df.h.values, df.l.values, df.c.values
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = float(pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    P = (h[-1] + l[-1] + c[-1]) / 3
    w = df.set_index("d").resample("W-FRI").agg({"h": "max", "l": "min", "c": "last"}).dropna()
    w = w[w.index < pd.Timestamp(d0) - pd.Timedelta(days=pd.Timestamp(d0).weekday())]  # только закрытые недели
    lv = {"вчерашний макс.": h[-1], "20-дн. макс.": h[-20:].max(), "60-дн. макс.": h[-60:].max(), "250-дн. макс.": h[-250:].max(),
          "R1 дня": 2 * P - l[-1], "R2 дня": P + (h[-1] - l[-1]), "R3 дня": h[-1] + 2 * (P - l[-1]),
          "вчерашнее закрытие": c[-1], "EMA20": float(pd.Series(c).ewm(span=20, adjust=False).mean().iloc[-1])}
    if len(w):
        wh, wl, wc = w.h.iloc[-1], w.l.iloc[-1], w.c.iloc[-1]
        WP = (wh + wl + wc) / 3
        lv["R1 недели"], lv["R2 недели"] = 2 * WP - wl, WP + (wh - wl)
    return atr, lv


def round_above(p):
    step = 10 ** math.floor(math.log10(p)) / 2          # 100 → шаг 50; 7,3 → 0,5; 0,0123 → 0,005
    return math.floor(p / step + 1e-9) * step + step


def build_seq(r, Dd, bars, hold, lim):
    """Бары от входа до конца срока (очищенные, как в №18); (цена входа, массивы дат, open, high, low, close) или None."""
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
    S = seq[k0 + 1:]
    last = days[-1][0]
    S = [x for x in S if x[0] <= last]
    if not S:
        return None
    a = np.array([[x[2], x[3], x[4], x[5]] for x in S], float)
    return e, a, days


def exits(e, a, targets, trails, cost):
    """targets — {имя: уровень тейка (цена) или None}; trails — {имя: доля отката от максимума}. → {имя: доходность}."""
    o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    out = {}
    cm = np.maximum.accumulate(h)
    for nm, tp in targets.items():
        if tp is None or not np.isfinite(tp):
            out[nm] = c[-1] / e - 1 - cost
            continue
        hit = np.nonzero(h >= tp)[0]
        out[nm] = (tp if len(hit) else c[-1]) / e - 1 - cost
    for nm, frac in trails.items():
        peak = np.maximum(e, np.r_[e, cm[:-1]])           # максимум до текущего бара
        stop = peak * (1 - frac)
        hit = np.nonzero(l <= stop)[0]
        if len(hit):
            k = hit[0]
            px = min(stop[k], o[k])
        else:
            px = c[-1]
        out[nm] = px / e - 1 - cost
    return out


def stats(r):
    r = np.asarray([x for x in r if x == x])
    if not len(r):
        return dict(n=0)
    w, ls = r[r > 0], r[r <= 0]
    return dict(n=int(len(r)), mean=float(r.mean()), med=float(np.median(r)), win=float((r > 0).mean()),
                pf=float(w.sum() / max(1e-9, -ls.sum())), worst=float(r.min()))


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
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
    E = E15.events_a(X, H2, IX2, Dd)
    E = E[(E.lval >= 7) & (E.d.dt.year >= FROM5)].reset_index(drop=True)
    cost = E15.cost_of(E.lval.values)
    log("ликвидных сигналов", FROM5, "–:", len(E), "| рост", int((E.s > 0).sum()), "падение", int((E.s < 0).sum()))
    need = set()
    for i, r in E.iterrows():
        d0 = r.d.strftime("%Y-%m-%d")
        need.add((r.t, d0))
        for dd, _, _ in E18.trading_days(Dd, r.t, d0, max(HOLDS)):
            need.add((r.t, dd))
    bars = E18.load_bars(need, (os.environ.get("ALGOPACK_TOKEN") or "").strip())
    yr = E.d.dt.year.values
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), split=SPLIT, ways={})
    for way, (side, ent) in WAYS.items():
        R = {}          # имя правила → [доходность по сигналам] (NaN — нет сделки)
        idx = np.where(E.s.values == side)[0]
        ok_n = 0
        for i in idx:
            r = E.iloc[i]
            di = daily_info(Dd, r.t, r.d.strftime("%Y-%m-%d"))
            if not di:
                continue
            atr, lv = di
            mv = abs(r.p0 / r.prev - 1)
            for hold in HOLDS:
                sq = build_seq(r, Dd, bars, hold, ent == "lim")
                if sq is None:
                    continue
                e, a, _ = sq
                if hold == HOLDS[0]:
                    ok_n += 1
                T = {"без тейка": None}
                for p in PCT:
                    T[f"+{p:.0%}"] = e * (1 + p)
                for m in MOVE:
                    T[f"{m:.0%} хода"] = e * (1 + m * mv)
                for k in ATRK:
                    T[f"{k:g} ATR"] = e + k * atr
                L = dict(lv)
                L["макс. дня до сигнала"] = r.H0
                L["Фибо 127% хода"] = r.prev + 1.272 * (r.p0 - r.prev) if side > 0 else r.prev + 0.272 * (r.prev - r.p0)
                L["Фибо 162% хода"] = r.prev + 1.618 * (r.p0 - r.prev) if side > 0 else r.prev + 0.618 * (r.prev - r.p0)
                L["круглое число"] = round_above(e * 1.003)
                above = [v for k_, v in L.items() if k_ != "круглое число" and v and v >= e * 1.003]
                L["ближайшее сопротивление"] = min(above + [L["круглое число"]]) if above else L["круглое число"]
                for k_ in LEVELS:
                    v = L.get(k_)
                    T["уровень: " + k_] = v if v and v >= e * 1.003 else None
                TR_ = {f"скользящий стоп {x:.0%}": x for x in TRAIL}
                TR_.update({f"скользящий стоп {k:g} ATR": min(0.9, k * atr / e) for k in TRAIL_ATR})
                res = exits(e, a, T, TR_, cost[i])
                for nm, v in res.items():
                    R.setdefault(f"{nm} | до {hold} дн.", np.full(len(E), np.nan))[i] = v
        log(f"===================== {way}: сделок с 5-минутными данными {ok_n} =====================")
        tr, te = yr < SPLIT, yr >= SPLIT
        rows = []
        for nm, v in R.items():
            a_, b_, c_ = stats(v[tr]), stats(v[te]), stats(v)
            if b_.get("n", 0) >= 20 and a_.get("n", 0) >= 20:
                rows.append((nm, a_, b_, c_))
        fam = lambda nm: ("уровни" if nm.startswith("уровень") else "скользящий стоп" if nm.startswith("скользящий") else
                          "доля хода" if "хода" in nm.split(" | ")[0] else "ATR" if "ATR" in nm else "без тейка" if nm.startswith("без") else "процент")
        best = max(rows, key=lambda x: x[1]["mean"])
        log(f"  лучшее по 2020–{SPLIT - 1}: «{best[0]}» — тогда {best[1]['mean']:+.2%}/сд, на проверке {SPLIT}–2026: "
            f"{best[2]['mean']:+.2%}/сд, медиана {best[2]['med']:+.2%}, прибыльных {best[2]['win']:.0%}, PF {best[2]['pf']:.2f}, худшая {best[2]['worst']:+.1%} (n={best[2]['n']})")
        log(f"  лучшее в каждом семействе (выбор по 2020–{SPLIT - 1}, цифры — проверка {SPLIT}–2026):")
        fb = {}
        for f_ in ("процент", "доля хода", "ATR", "уровни", "скользящий стоп", "без тейка"):
            cand = [x for x in rows if fam(x[0]) == f_]
            if not cand:
                continue
            x = max(cand, key=lambda z: z[1]["mean"])
            fb[f_] = dict(rule=x[0], train=x[1], test=x[2], all=x[3])
            log(f"    {f_}: «{x[0]}» — {x[1]['mean']:+.2%} → {x[2]['mean']:+.2%}/сд, медиана {x[2]['med']:+.2%}, прибыльных {x[2]['win']:.0%}, "
                f"PF {x[2]['pf']:.2f}, худшая {x[2]['worst']:+.1%} (n={x[2]['n']})")
        log("  все уровни (весь период 2020–2026, срок как у лучшего уровня):")
        hb = fb.get("уровни", {}).get("rule", "| до 10 дн.").split(" | ")[1]
        for x in sorted([x for x in rows if fam(x[0]) == "уровни" and x[0].endswith(hb)], key=lambda z: -z[3]["mean"]):
            log(f"    {x[0]}: {x[3]['mean']:+.2%}/сд, прибыльных {x[3]['win']:.0%}, PF {x[3]['pf']:.2f} (n={x[3]['n']}) | до {SPLIT}: {x[1]['mean']:+.2%}, с {SPLIT}: {x[2]['mean']:+.2%}")
        log("  процент от входа (весь период, по срокам 5 / 10 / 20 дн.):")
        for p in PCT + [None]:
            nm = "без тейка" if p is None else f"+{p:.0%}"
            vals = [stats(R.get(f"{nm} | до {h} дн.", np.array([])))for h in HOLDS]
            log(f"    {nm}: " + " / ".join(f"{v['mean']:+.2%} ({v['win']:.0%})" if v.get("n") else "—" for v in vals))
        log("  топ-10 правил за весь период 2020–2026 (для справки, это «в выборке»):")
        for x in sorted(rows, key=lambda z: -z[3]["mean"])[:10]:
            log(f"    {x[0]}: {x[3]['mean']:+.2%}/сд, медиана {x[3]['med']:+.2%}, прибыльных {x[3]['win']:.0%} | до {SPLIT}: {x[1]['mean']:+.2%}, с {SPLIT}: {x[2]['mean']:+.2%}")
        out["ways"][way] = dict(best=dict(rule=best[0], train=best[1], test=best[2]), families=fb)
        json.dump(out, open(os.path.join(TR.MODELS, "exp20_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №20 =====", round(time.time() - t0), "с")
    for way, o in out["ways"].items():
        log(f"{way}: лучшее «{o['best']['rule']}» → проверка {o['best']['test']['mean']:+.2%}/сд; по семействам: "
            + "; ".join(f"{k} «{v['rule']}» {v['test']['mean']:+.2%}" for k, v in o["families"].items()))


if __name__ == "__main__":
    main()
