# -*- coding: utf-8 -*-
"""Агент-проверяющий для лучшей стратегии «резкое движение → разворот / продолжение» (research/reversal.py).

  1. Живой код = код исследования: живой детектор channel/patterns.py прогоняется на истории, обрезанной на баре события
     (без будущих данных), — он должен найти не меньше 95% событий выбранного правила.
  2. Перестановочный тест: случайный вход у тех же бумаг, та же сторона, тот же риск, те же цель и горизонт (p ≤ 0,05).
  3. Стабильность: результат после издержек положителен не меньше чем в 60% лет.
Итог: research-stats/rev_verify.json и stats.json["reversal"] — его читает живой агент (jobs.py pattern_scan).
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
OUT = os.environ.get("RESEARCH_DIR") or os.path.join(HERE, "out")


def log(*a):
    print(time.strftime("%H:%M:%S"), "[проверка разворотов]", *a, flush=True)


def live_code_check(P, rows, fam, prm, side, k=150):
    import patterns as PT
    smp = rows.sample(min(k, len(rows)), random_state=7)
    ok = 0
    for _, r in smp.iterrows():
        j, t = int(r.j), r.t
        o, h, l, c, v = (P[x][t].values[:j + 1] for x in ("o", "h", "l", "c", "v"))
        bars = [dict(o=float(o[q]), h=float(h[q]), l=float(l[q]), c=float(c[q]), v=float(v[q]) if v[q] == v[q] else float("nan")) for q in range(j + 1)]
        ok += int(PT.detect(bars, fam, prm, side, check_risk=False) is not None)
    return ok / len(smp) if len(smp) else 0.0


def main():
    import shake_verify as SV
    import shakeout as SH
    import study as S
    try:
        rv = json.load(open(os.path.join(OUT, "reversal.json")))
    except Exception:  # noqa: BLE001
        log("reversal.json нет — проверять нечего")
        return
    P, _ = S.load_panel()
    Ph = None
    res = {}
    for tf in ("d", "h"):
        if tf not in rv:
            continue
        res[tf] = {"board": rv[tf].get("board"), "patterns": rv[tf].get("patterns")}
        for side_name, side in (("up", 1), ("down", -1)):
            dec = (rv[tf].get(side_name) or {}).get("decision") or {}
            if not dec.get("on"):
                res[tf][side_name] = dict(on=False, verified=False, reasons=[dec.get("why") or "правило не выбрано"], oos=dec.get("oos"))
                log(f"{tf} {side_name}: стратегии нет — {dec.get('why')}")
                continue
            rows = pd.read_csv(os.path.join(OUT, f"rev_rows_{tf}_{side_name}.csv"))
            if tf == "h" and Ph is None:
                Ph = SH.hourly_panel()
            PP = P if tf == "d" else Ph
            prm, fam = dec["params"], dec["fam"]
            reasons = []
            lc = live_code_check(PP, rows, fam, prm, side)
            if lc < 0.95:
                reasons.append(f"живой детектор находит лишь {lc:.0%} событий исследования (расхождение кода или заглядывание в будущее)")
            real, pv = SV.permutation(PP, rows, prm, side)
            if pv > 0.05:
                reasons.append(f"не лучше случайного входа с тем же риском (p={pv:.2f})")
            by = rows.groupby("year").r.agg(["mean", "size"])
            by = by[by["size"] >= 5]
            frac = float((by["mean"] > 0).mean()) if len(by) else 0.0
            if frac < 0.6:
                reasons.append(f"результат положителен лишь в {frac:.0%} лет")
            res[tf][side_name] = dict(on=True, verified=not reasons, reasons=reasons, fam=fam, params=prm, news=dec["news"], prob=dec.get("prob"), oos=dec.get("oos"),
                                      perm_p=round(pv, 3), years_pos=round(frac, 2), live_code=round(lc, 3),
                                      hist=dict(n=int(len(rows)), mean=round(float(rows.r.mean()), 5), prob=round(float(rows.hit.mean()), 3)))
            log(f"{tf} {side_name}: {fam} {prm}, новости={dec['news']} → {'ПОДТВЕРЖДЕНО' if not reasons else 'не подтверждено: ' + '; '.join(reasons)}")
    json.dump(res, open(os.path.join(OUT, "rev_verify.json"), "w"), ensure_ascii=False, default=float)
    sp = os.path.join(OUT, "stats.json")
    try:
        st = json.load(open(sp))
    except Exception:  # noqa: BLE001
        st = {}
    st["reversal"] = {tf: {k: v for k, v in d.items() if k in ("up", "down")} for tf, d in res.items()}
    json.dump(st, open(sp, "w"), ensure_ascii=False, default=float)
    n_ok = sum(1 for tf in res for k in ("up", "down") if (res[tf].get(k) or {}).get("verified"))
    log("ИТОГ: подтверждённых стратегий", n_ok, "— живой агент публикует идеи только по ним")


if __name__ == "__main__":
    main()
