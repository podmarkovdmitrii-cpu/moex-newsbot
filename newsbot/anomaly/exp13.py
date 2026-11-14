# -*- coding: utf-8 -*-
"""Проверка №13: стратегия из №12 на всей истории с 2008 г. (годы 2008–2021 при выборе не использовались — честная проверка).

Часовые свечи до 2020 г. в кэше модели нет — докачиваем с ISS рыночные часовые свечи (без привязки к режиму торгов: до 2013 г.
основным был EQBR/EQNE) для всех бумаг с историей до 2020 г., кэш anomaly-data/h_old. Индекс IMOEX — так же.
Стратегии заранее зафиксированы (никакой подгонки на 2008–2021):
  S1 главная: аномалия-РОСТ → лимит на откате 50% хода в тот же день → тейк +25% хода, без стопа, выход не позже закрытия D+5;
  S2 то же, откат 38%;  S3 то же, тейк 50% хода;  S4 S1 + стоп 50% хода (для оценки риска);
  S5 шорт роста 3-го эшелона: против движения, сразу, тейк 50% хода, до закрытия дня;
  БАЗА: по движению, сразу, до закрытия дня (все аномалии).
По каждому году: сделок, средняя доходность сделки, доля прибыльных, PF, прирост капитала (до 10 сделок в день по 10%), просадка.
Чувствительность: издержки ×2. Оговорка: состав бумаг — те, что есть в данных (часть исключённых с биржи бумаг может отсутствовать).
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp9 as E9  # noqa: E402
import exp12 as E12  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
OLD_FROM = os.environ.get("EXP_H_FROM", "2008-01-01")
OLD_TILL = "2020-01-01"
BUDGET = float(os.environ.get("EXP_H_BUDGET_MIN", "150")) * 60

STRATS = {
    "S1 главная: рост, откат 50%, тейк 25% хода, до D+5": (("follow", "0.5", "0.25", None, 5), "все", "рост"),
    "S2 рост, откат 38%, тейк 25% хода, до D+5": (("follow", "0.382", "0.25", None, 5), "все", "рост"),
    "S3 рост, откат 50%, тейк 50% хода, до D+5": (("follow", "0.5", "0.5", None, 5), "все", "рост"),
    "S4 = S1 + стоп 50% хода": (("follow", "0.5", "0.25", "0.5", 5), "все", "рост"),
    "S5 шорт роста 3-го эшелона, сразу, тейк 50%, до закрытия дня": (("fade", "sig", "0.5", None, 0), "эшелон 3", "все"),
    "БАЗА: по движению, сразу, до закрытия дня": (("follow", "sig", None, None, 0), "все", "все"),
    # ликвидность: lval — десятичный логарифм медианного дневного оборота (7 = 10 млн ₽, 7,5 ≈ 30 млн ₽, 8 = 100 млн ₽)
    "S1 | оборот ≥ 10 млн ₽/день": (("follow", "0.5", "0.25", None, 5), "ликв 10", "рост"),
    "S1 | оборот ≥ 30 млн ₽/день": (("follow", "0.5", "0.25", None, 5), "ликв 30", "рост"),
    "S1 | оборот ≥ 100 млн ₽/день": (("follow", "0.5", "0.25", None, 5), "ликв 100", "рост"),
    "S5 | 3-й эшелон, оборот ≥ 10 млн ₽/день": (("fade", "sig", "0.5", None, 0), "эшелон 3 ликв 10", "все"),
    "S5 | 3-й эшелон, оборот ≥ 30 млн ₽/день": (("fade", "sig", "0.5", None, 0), "эшелон 3 ликв 30", "все"),
    "БАЗА | оборот ≥ 30 млн ₽/день": (("follow", "sig", None, None, 0), "ликв 30", "все"),
}


def fetch_old(tickers, index=("IMOEX",)):
    """Часовые свечи до 2020 г. по рынку (все режимы), кэш h_old; бюджет времени — EXP_H_BUDGET_MIN."""
    from common import candles
    base = os.environ.get("EXP_H_DIR") or os.path.join(TR.DATA, "h_old")
    os.makedirs(base, exist_ok=True)
    t_end = time.time() + BUDGET
    days = (dt.date.today() - dt.date.fromisoformat(OLD_FROM)).days + 1

    def one(t):
        p = os.path.join(base, t + ".json.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                return t, json.load(f), True
        if time.time() > t_end:
            return t, [], False
        idx = t in index
        path = f"engines/stock/markets/{'index' if idx else 'shares'}/securities/{t}/candles.json"
        try:
            B = candles(path, days, 60)
        except Exception as e:  # noqa: BLE001
            log("часовые", t, e)
            return t, [], False
        rows = [[b["t"][:13], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in B if b["t"][:10] < OLD_TILL]
        with gzip.open(p, "wt") as f:
            json.dump(rows, f, separators=(",", ":"))
        return t, rows, True
    out, done, cached = {}, 0, 0
    with cf.ThreadPoolExecutor(int(os.environ.get("EXP_H_WORKERS", "6"))) as ex:
        for k, (t, rows, ok) in enumerate(ex.map(one, tickers)):
            done += ok
            if rows:
                out[t] = rows
            if k % 25 == 0:
                log(f"часовые до 2020: {k}/{len(tickers)}, с данными {len(out)}")
    yrs = pd.Series([r[0][:4] for v in out.values() for r in v]).value_counts().sort_index()
    log("часовых баров до 2020 по годам:", ", ".join(f"{y}: {n}" for y, n in yrs.items()))
    log(f"бумаг скачано/из кэша {done} из {len(tickers)}; с данными {len(out)}")
    return out


def yearly(E, r, mask):
    rows = {}
    for y in sorted(set(E.d.dt.year)):
        m = mask & (E.d.dt.year.values == y)
        x = r[m]
        f = np.isfinite(x)
        if f.sum() < 5:
            continue
        a = E12.metrics(E, r, m) if f.sum() >= E12.MIN_N else None
        xt = x[f]
        rows[int(y)] = dict(n=int(f.sum()), mean=round(float(xt.mean()), 5), win=round(float((xt > 0).mean()), 3),
                            pf=round(float(xt[xt > 0].sum() / max(1e-9, -xt[xt < 0].sum())), 2),
                            cap=round(float(a["year"]), 4) if a else round(float(xt.sum() * 0.1), 4))
    return rows


def period(E, r, mask, y0, y1):
    m = mask & (E.d.dt.year.values >= y0) & (E.d.dt.year.values <= y1)
    return E12.metrics(E, r, m)


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    E12.MIN_N = 20
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    first = {t: v[0][0][:10] for t, v in Dd.items() if v}
    old_t = sorted(t for t, f in first.items() if f < OLD_TILL and t in set(X.t))
    log("бумаг с историей до 2020:", len(old_t))
    Ho = fetch_old(old_t + ["IMOEX"])
    IXo = Ho.pop("IMOEX", [])
    H2 = {}
    for t in set(Hh) | set(Ho):
        new = Hh.get(t, [])
        cut = new[0][0] if new else "9999"
        H2[t] = [r for r in Ho.get(t, []) if r[0] < cut] + new
    cut = IXH[0][0] if IXH else "9999"
    IX2 = [r for r in IXo if r[0] < cut] + IXH
    log("индекс по часам с", IX2[0][0] if IX2 else "—", "| бумаг с часовыми", len(H2))
    E = E12.events(X, H2, IX2, Dd)
    E = E[E.d >= pd.Timestamp(TR.FROM)].reset_index(drop=True)
    P = E12.paths(E)
    log("сигналов по годам:", ", ".join(f"{y}: {n}" for y, n in E.d.dt.year.value_counts().sort_index().items()))
    lv = E.lval.values
    fil = {"все": np.ones(len(E), bool), "эшелон 3": E.tier.values == 3, "ликв 10": lv >= 7, "ликв 30": lv >= 7.477, "ликв 100": lv >= 8,
           "эшелон 3 ликв 10": (E.tier.values == 3) & (lv >= 7), "эшелон 3 ликв 30": (E.tier.values == 3) & (lv >= 7.477)}
    log("доля сигналов по обороту: ≥10 млн", round(float((lv >= 7).mean()), 3), "≥30 млн", round(float((lv >= 7.477).mean()), 3),
        "≥100 млн", round(float((lv >= 8).mean()), 3))
    dr = {"все": np.ones(len(E), bool), "рост": E.s.values > 0}
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), n=int(len(E)), strategies={})
    periods = [(2008, 2013), (2014, 2019), (2020, 2021), (2022, 2024), (2025, 2026), (2008, 2021), (2008, 2026)]
    for cost_mult in (1.0, 2.0):
        E12.COST_T = {k: v * cost_mult for k, v in {1: 0.001, 2: 0.002, 3: 0.003}.items()}
        log(f"===== ИЗДЕРЖКИ ×{cost_mult:g}: {E12.COST_T} =====")
        for nm, (g, fn, dn) in STRATS.items():
            if cost_mult > 1 and not nm.startswith(("S1 главная", "S1 | оборот ≥ 30", "S5 | 3-й эшелон, оборот ≥ 30", "БАЗА")):
                continue
            r = E12.simulate(E, P, *g)
            m = fil[fn] & dr[dn]
            yr = yearly(E, r, m)
            pr = {f"{a}–{b}": period(E, r, m, a, b) for a, b in periods}
            out["strategies"][f"{nm} | издержки ×{cost_mult:g}"] = dict(years=yr, periods=pr)
            log(f"--- {nm} ---")
            log("   по годам: " + "; ".join(f"{y}: {v['n']} сд, {v['mean']:+.2%}, {v['win']:.0%}, кап {v['cap']:+.0%}" for y, v in yr.items()))
            for k, v in pr.items():
                if v:
                    log(f"   {k}: сделок {v['n']}, {v['mean']:+.2%}/сделку, прибыльных {v['win']:.0%}, PF {v['pf']}, капитал {v['year']:+.1%}/год, просадка {v['dd']:.0%}")
            pos = [y for y, v in yr.items() if v["cap"] > 0]
            log(f"   лет в плюсе: {len(pos)} из {len(yr)}; худший год: " + (min(((y, v['cap']) for y, v in yr.items()), key=lambda x: x[1]).__repr__() if yr else "—"))
            json.dump(out, open(os.path.join(TR.MODELS, "exp13_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №13 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
