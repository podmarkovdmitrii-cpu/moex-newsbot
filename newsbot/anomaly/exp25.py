# -*- coding: utf-8 -*-
"""Проверка №25: рост капитала по годам для правила сканера бота идей (по тренду, 3 случайных сигнала в день; стоп за уровнем / 2,5 ATR,
цель 5R, до 60 торговых дней) — портфель с ограничением позиций и без плеча.

Входы — каждый торговый день по ликвидным акциям (оборот ≥ 10 млн ₽), лонг и шорт по тренду (цена и наклон SMA50 в сторону сделки),
из сигналов дня случайно выбираются 3 (как в боте). Сделка открывается, только если есть свободный слот (не больше N позиций)
и свободные деньги: размер позиции = риск на сделку / расстояние до стопа (стоп стоит R% счёта), но не больше 100%/N капитала.
Результат сделки — по дневным свечам (гэп через стоп — по открытию, издержки 0,1% + 0,05% на стопе), прибыль учитывается в день выхода.
Варианты: N = 5 и 10 позиций, риск 0,5% и 1% на сделку; шорт — с теми же правилами (у брокера шорт может быть недоступен). Год — по дню выхода.
Не является индивидуальной инвестиционной рекомендацией.
"""
import json
import os
import sys
import time
import zlib

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp23 as E23  # noqa: E402
import exp24 as E24  # noqa: E402
import scan_core as SC  # noqa: E402
import train as TR  # noqa: E402

log = TR.log


def trades(D, ixs):
    from numpy.lib.stride_tricks import sliding_window_view
    rows = []
    H = SC.HOLD
    for t, df in D.items():
        B = SC.base_arrays(df, ixs)
        o, h, l, c = (df[k].to_numpy(float) for k in ("o", "h", "l", "c"))
        n = len(c)
        r1 = np.abs(np.diff(np.log(c)))
        bad = np.r_[False, r1 > 0.45] | (h / np.maximum(l, 1e-9) > 1.8)
        badc = np.cumsum(bad)
        pad = np.full(H, np.nan)
        win = [sliding_window_view(np.r_[x[1:], pad], H) for x in (o, h, l, c)]
        dates = df.d.values
        for side in (1, -1):
            idx = [i for i in range(260, n - 2) if B["A"][i] > 0 and B["val_med"][i] >= 1e7 and np.isfinite(B["d200"][i])
                   and B["tr"][i] * side > 0 and B["sl"][i] * side > 0 and badc[min(n - 1, i + H)] - badc[i - 1] == 0]
            if not idx:
                continue
            Wt = np.stack([np.stack([w[i] for w in win], 0) for i in idx]).astype(np.float32)
            S = np.array([SC.level_stop(h, l, c, i, B["A"][i], side)[0] for i in idx])
            R, days = E23.sim(c[idx], side, Wt, S, [(SC.TARGET_R, 1.0)], H, 0)
            for j, i in enumerate(idx):
                k = min(n - 1, i + int(days[j]))
                d0 = pd.Timestamp(dates[i])
                rows.append((t, d0, pd.Timestamp(dates[k]), side, float(R[j]), float(S[j] / c[i]),
                             (zlib.crc32(f"{t}{side}{d0.date()}".encode()) % 10007) / 10007))
    return pd.DataFrame(rows, columns=["t", "d0", "d1", "side", "R", "risk", "rnd"])


def portfolio(T, N, risk, sides=(1, -1), k=3):
    T = T[T.side.isin(sides)].copy()
    T["rank"] = T.groupby("d0").rnd.rank(ascending=False, method="first")
    T = T[T["rank"] <= k].sort_values(["d0", "rank"])
    cap, open_, pnl_by_day, taken = 1.0, [], {}, 0
    for d0, g in T.groupby("d0"):
        # закрыть вышедшие до d0 (прибыль — в день выхода)
        still = []
        for x in open_:
            if x["d1"] < d0:
                cap += x["pnl"]
                pnl_by_day[x["d1"]] = pnl_by_day.get(x["d1"], 0) + x["pnl"]
            else:
                still.append(x)
        open_ = still
        for _, r in g.iterrows():
            if len(open_) >= N:
                break
            used = sum(x["size"] for x in open_) / cap
            size = min(risk / r.risk, 1.0 / N, max(0.0, 1.0 - used))   # доля капитала (без плеча)
            if size <= 0.01:
                break
            open_.append(dict(d1=r.d1, size=size * cap, pnl=size * cap * r.R * r.risk))
            taken += 1
    for x in open_:
        cap += x["pnl"]
        pnl_by_day[x["d1"]] = pnl_by_day.get(x["d1"], 0) + x["pnl"]
    s = pd.Series(pnl_by_day).sort_index()
    eq = 1 + s.cumsum()
    yrs = {}
    for y, g in s.groupby(s.index.year):
        start = 1 + s[s.index.year < y].sum()
        yrs[int(y)] = float(g.sum() / start)
    dd = float((eq / eq.cummax() - 1).min())
    return yrs, dd, taken, float(eq.iloc[-1])


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    D, ixs = E24.load()
    T = trades(D, ixs)
    log(f"трендовых сигналов: {len(T)}; средний R {T.R.mean():+.3f}; {round(time.time() - t0)} с")
    out = {}
    log("===== РОСТ КАПИТАЛА ПО ГОДАМ (год — по дню выхода сделки; без плеча) =====")
    for sides, nm in (((1, -1), "лонг и шорт"), ((1,), "только лонг")):
        for N in (5, 10):
            for risk in (0.005, 0.01):
                yrs, dd, n, final = portfolio(T, N, risk, sides)
                y14 = [v for y, v in yrs.items() if 2010 <= y <= 2019]
                y20 = [v for y, v in yrs.items() if y >= 2020]
                key = f"{nm}, до {N} позиций, риск {risk:.1%}"
                out[key] = dict(years=yrs, dd=dd, n=n, final=final)
                log(f"  {key}: сделок {n}, в среднем за год 2010–2019 {np.mean(y14):+.1%}, 2020–2026 {np.mean(y20):+.1%}, "
                    f"лет в плюсе {sum(v > 0 for v in yrs.values())}/{len(yrs)}, макс. просадка {dd:.0%}")
                log("     " + ", ".join(f"{y}: {v:+.1%}" for y, v in yrs.items()))
    json.dump(out, open(os.path.join(TR.MODELS, "exp25_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №25 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
