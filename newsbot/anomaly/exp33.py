# -*- coding: utf-8 -*-
"""Проверка №33: почему в 2024–2025 так мало сделок сборной стратегии, хотя сигналов и 5-минутных свечей хватает (№32).

По годам 2020–2026 (сигналы внутри дня, оборот ≥ 10 млн ₽):
  • сколько сигналов роста и падения; сколько лимитов S1 исполнилось (откат 50% хода в тот же день);
  • модель-фильтр F2 / F4 (как у бота: модель года учится на прошлых годах, порог «верхние 30%» — по оценкам 3 прошлых лет):
    доля прошедших в году, медиана оценок года против порога (сдвиг оценок), средний результат прошедших и всех (5-мин свечи);
  • альтернативы порога: 70-й процентиль оценок прошлого года и скользящий за последние 250 сигналов до текущего (без заглядывания вперёд).
Не является индивидуальной инвестиционной рекомендацией.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp22 as E22  # noqa: E402
import exp29 as E29  # noqa: E402
import train as TR  # noqa: E402

log = TR.log


def main():
    t0 = time.time()
    E, R, FE, Dd, bars, cost = E29.load_all()
    F = E29.Filt(E, FE)
    yr = E.d.dt.year.values
    log(f"сигналов: {len(E)}; {round(time.time() - t0)} с")
    log("===== СИГНАЛЫ И ИСПОЛНЕНИЕ S1 ПО ГОДАМ =====")
    for Y in range(2020, 2027):
        up = np.where((yr == Y) & (E.s.values > 0))[0]
        dn = int(((yr == Y) & (E.s.values < 0)).sum())
        fill = 0
        for i in up:
            if E22.trade5(E.iloc[i], Dd, bars, True, "lim", "move", 0.25, 5, cost[i]):
                fill += 1
        log(f"  {Y}: рост {len(up)}, падение {dn}; лимит S1 исполнен {fill} ({fill / max(1, len(up)):.0%})")
    log("===== МОДЕЛЬ-ФИЛЬТР: ДОЛЯ ПРОШЕДШИХ И СДВИГ ОЦЕНОК (порог как у бота — по 3 прошлым годам) =====")
    for nm, sl, tp, hold, fs in (("F2", "РС", None, 10, "base"), ("F4", "ПС", ("m", 2.0), 20, "math")):
        r_ = R[(sl, tp, hold)]
        sg = E29.SLOTS[sl][2]
        ent = (True, "sig", None, None, 10) if nm == "F2" else (True, "sig", "move", 2.0, 20)
        allp = {}
        for Y in range(2017, 2027):
            allp[Y] = F.scores((sl, fs, tp, hold), r_, sg, Y)
        for Y in range(2020, 2027):
            p = allp[Y]
            ph = np.concatenate([allp[y] for y in range(Y - 3, Y)])
            ph = ph[np.isfinite(ph)]
            if not len(ph):
                continue
            thr3 = float(np.quantile(ph, 0.7))
            p1 = allp[Y - 1][np.isfinite(allp[Y - 1])]
            thr1 = float(np.quantile(p1, 0.7)) if len(p1) else thr3
            idx = np.where((yr == Y) & np.isfinite(p))[0]
            if not len(idx):
                continue
            # скользящий порог: 70-й процентиль последних 250 оценок до сигнала (по всем годам, только прошлое)
            hist = [(E.d.iloc[j], allp[int(yr[j])][j]) for y in range(Y - 3, Y + 1) for j in np.where((yr == y) & np.isfinite(allp[y]))[0]]
            hist.sort(key=lambda z: z[0])
            hd = [h[0] for h in hist]
            hv = np.array([h[1] for h in hist])
            res = {}
            for i in idx:
                x = E22.trade5(E.iloc[i], Dd, bars, *ent[:1], ent[1], ent[2], ent[3], ent[4], cost[i])
                res[i] = x[0] if x else None
            k = np.searchsorted(np.array(hd, dtype="datetime64[ns]"), np.datetime64(E.d.iloc[idx[0]]))

            def stat(mask_idx):
                v = [res[i] for i in mask_idx if res[i] is not None]
                return f"{len(mask_idx)} ({np.mean(v):+.2%})" if v else f"{len(mask_idx)} (—)"
            pass3 = [i for i in idx if p[i] >= thr3]
            pass1 = [i for i in idx if p[i] >= thr1]
            passr = []
            for i in idx:
                kk = np.searchsorted(np.array(hd, dtype="datetime64[ns]"), np.datetime64(E.d.iloc[i]))
                prev = hv[max(0, kk - 250):kk]
                if len(prev) >= 50 and p[i] >= np.quantile(prev, 0.7):
                    passr.append(i)
            log(f"  {nm} {Y}: сигналов {stat(list(idx))}; медиана оценок {np.median(p[idx]):+.2%}, порог 3 лет {thr3:+.2%}, прошлого года {thr1:+.2%}; "
                f"прошли: порог 3 лет {stat(pass3)}, прошлого года {stat(pass1)}, скользящий 250 {stat(passr)}")
    log("===== ИТОГ ПРОВЕРКИ №33 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
