# -*- coding: utf-8 -*-
"""Проверка №36: сколько держать бумаги во второй стратегии (v3, половинный риск) — срок сделок по модулям на истории 2009–2026.

v3 = strategy.json + regime.js (как у бота), половинный размер (риск 0,5% на сделку, ≤ 17% капитала на позицию, загрузка v3 ≤ 50%).
По модулям (импульс лидеров / откат в тренде / шорт пробоя): число сделок в год, срок в торговых днях (медиана, 25–75%, среднее, максимум),
доля прибыльных, средний результат, вклад в прибыль, причины выхода; доля прибыли от сделок по сроку (≤5, 6–20, 21–60, 61–120, >120 дн.).
Не является индивидуальной инвестиционной рекомендацией.
"""
import json
import os
import subprocess
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import train as TR  # noqa: E402

log = TR.log
NAMES = {"mom": "импульс лидеров", "pull": "откат в тренде", "sbrk": "шорт пробоя"}


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    tick = TR.universe()
    Dd = TR.load_series("d", tick + ["IMOEX"], "2012-01-01", index=("IMOEX",))
    IX = Dd.pop("IMOEX")
    Dd = TR.stitch_renamed(Dd)
    Dd, IX = TR.backfill(Dd, IX)
    wd = lambda d: pd.Timestamp(d[:10]).weekday() < 5  # noqa: E731
    days = sorted({r[0][:10] for r in IX if wd(r[0])})
    sec = {t: [[r[0][:10], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)] for r in rows if r[4] and wd(r[0])] for t, rows in Dd.items()}
    sec = {t: v for t, v in sec.items() if len(v) >= 60}
    imo = [[r[0][:10], float(r[1]), float(r[2]), float(r[3]), float(r[4]), 0] for r in IX if wd(r[0])]
    p_in, p_out = os.path.join(TR.MODELS, "v3_in.json"), os.path.join(TR.MODELS, "v3_out.json")
    json.dump(dict(days=days, sec=sec, imoex=imo), open(p_in, "w"))
    log(f"бумаг {len(sec)}, дней {len(days)}; {round(time.time() - t0)} с")
    for scale in ("1", "0.5"):
        subprocess.run(["node", os.path.join(HERE, "v3run.js"), p_in, p_out, "2009-01-01", "1000000", scale], check=True)
        V = json.load(open(p_out))
        log(f"===== v3, размер ×{scale}: " + "; ".join(f"{k}: {v['cagr']:+.1%} в год, просадка {v['mdd']:.0%}, загрузка {v['expo']:.0%}" for k, v in V["per"].items()))
        T = pd.DataFrame(V["list"], columns=["fam", "side", "d0", "d1", "days", "ret", "r", "pnl", "reason", "open"])
        T = T[T.open == 0]
        yrs = (pd.Timestamp(days[-1]) - pd.Timestamp("2009-01-01")).days / 365.25
        tot = T.pnl.sum()
        log(f"  всего сделок {len(T)} ({len(T) / yrs:.0f} в год), срок: медиана {T.days.median():.0f} дн., среднее {T.days.mean():.0f}, 75% — до {T.days.quantile(.75):.0f}, "
            f"90% — до {T.days.quantile(.9):.0f}, максимум {T.days.max()}; прибыльных {(T.pnl > 0).mean():.0%}")
        for fam, g in T.groupby("fam"):
            rs = Counter(g.reason).most_common(5)
            win, los = g[g.pnl > 0], g[g.pnl <= 0]
            log(f"  {NAMES.get(fam, fam)} ({'шорт' if g.side.iloc[0] < 0 else 'покупка'}): {len(g)} сделок ({len(g) / yrs:.1f} в год), "
                f"срок медиана {g.days.median():.0f} дн. (25–75%: {g.days.quantile(.25):.0f}–{g.days.quantile(.75):.0f}), среднее {g.days.mean():.0f}, максимум {g.days.max()}; "
                f"прибыльные держали в среднем {win.days.mean():.0f} дн., убыточные {los.days.mean():.0f}; прибыльных {(g.pnl > 0).mean():.0%}, "
                f"средняя {g.ret.mean():+.2%}, {g.r.mean():+.2f}R; вклад в прибыль {g.pnl.sum() / tot:.0%}; выходы: " + ", ".join(f"{k} {v}" for k, v in rs))
        bins = [(1, 5), (6, 20), (21, 60), (61, 120), (121, 10000)]
        log("  прибыль по сроку сделки: " + "; ".join(
            f"{a}–{b if b < 10000 else '…'} дн.: {int(((T.days >= a) & (T.days <= b)).sum())} сд., вклад {T[(T.days >= a) & (T.days <= b)].pnl.sum() / tot:+.0%}"
            for a, b in bins))
        T["y"] = T.d1.str[:4]
        log("  по годам (сделок / средний срок): " + ", ".join(f"{y}: {len(g)}/{g.days.mean():.0f}" for y, g in T.groupby("y")))
    log("===== ИТОГ ПРОВЕРКИ №36 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
