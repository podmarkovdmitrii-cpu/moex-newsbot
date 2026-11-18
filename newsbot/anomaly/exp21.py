# -*- coding: utf-8 -*-
"""Проверка №21: математические и технические закономерности перед аномалиями + всё, что раньше давало хоть небольшой плюс, —
вместе с боевой моделью. Годы проверки 2021–2026, каждый год модель учится только на прошлых (как боевая).

Наборы признаков:
  боевые        — нынешняя модель аномалий (~110 признаков);
  М             — математика (newsbot/anomaly/math_feats.py): сжатие волатильности (полосы Боллинджера/Кельтнера, NR4/NR7,
                  внутренние бары, ATR5/ATR60, канал Дончиана), оценки волатильности по свечам (Паркинсон, Гарман–Класс,
                  EWMA/GARCH), форма распределения (асимметрия, эксцесс, MAX), бета/корреляция/идиосинкратический риск, память ряда
                  (автокорреляция, отношение дисперсий, перестановочная энтропия), круглые числа, календарь экспираций;
  ПРЕЖНИЕ       — всё, что проверяли раньше по отдельности: спред по свечам и влияние сделок (№6), ALGOPACK — покупки/продажи,
                  заявки, стакан, концентрация HI2, сигналы биржи (№6, №8), FUTOI (№7), теханализ Investing (№19);
  ВСЁ           — боевые + М + ПРЕЖНИЕ.
Контроль: те же боевые с другим seed (разброс от случайности) и боевые + столько же случайных признаков, сколько во «ВСЁ».
Метрики: доля аномалий среди 5 и 10 бумаг дня с наибольшей вероятностью, AUC, точность направления.
Плюс: какие математические закономерности сами по себе связаны с аномалиями (частота по квинтилям, 2012–2019 и 2020–2026).
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import exp3 as E3  # noqa: E402
import exp5 as E5  # noqa: E402
import exp9 as E9  # noqa: E402
import exp16 as E16  # noqa: E402
import exp19 as E19  # noqa: E402
import experiment as EX  # noqa: E402
import math_feats as MF  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
FIRST = int(os.environ.get("EXP_FIRST_TEST", "2021"))
NBAG = int(os.environ.get("EXP_NBAG", "3"))
NICE = {"mx_bbw": "ширина полос Боллинджера", "mx_bbw_rk": "ранг ширины полос за 120 дн.", "mx_kc_sq": "полосы внутри канала Кельтнера",
        "mx_sq_len": "дней сжатия подряд", "mx_nr4": "NR4", "mx_nr7": "NR7", "mx_inside_n": "внутренних баров подряд", "mx_atr_ratio": "ATR5/ATR60",
        "mx_don_w": "ширина канала Дончиана", "mx_don_rk": "ранг ширины Дончиана", "mx_park20": "волатильность Паркинсона", "mx_gk20": "волатильность Гарман–Класс",
        "mx_on_share": "доля ночных гэпов в волатильности", "mx_ewma_ratio": "EWMA/60-дн. волатильность", "mx_volvol": "волатильность волатильности",
        "mx_skew60": "асимметрия 60 дн.", "mx_kurt60": "эксцесс 60 дн.", "mx_max20": "макс. дневной рост за 20 дн.", "mx_min20": "макс. дневное падение за 20 дн.",
        "mx_beta60": "бета к индексу", "mx_corr60": "корреляция с индексом", "mx_idio60": "собственная волатильность", "mx_beta_ch": "изменение беты",
        "mx_ac1": "автокорреляция доходностей", "mx_vr5": "отношение дисперсий VR(5)", "mx_pe60": "перестановочная энтропия", "mx_pv_corr20": "связь цены и оборота",
        "mx_vol_slope20": "наклон оборота 20 дн.", "mx_round": "до круглого числа, ATR", "mx_to_expiry": "дней до экспирации", "mx_month_start": "первые дни месяца",
        "mx_dom": "день месяца"}


def lift_split(X, cols):
    """Частота аномалий по квинтилям признака (границы — по всему периоду) в 2012–2019 и 2020–2026; устойчивые — где Q5/Q1 одного знака."""
    base = float(X.y.mean())
    rows = []
    for c in cols:
        q = X[["d", c, "y"]].dropna()
        if len(q) < 5000 or q[c].nunique() < 3:
            continue
        if q[c].nunique() <= 2:
            b = (q[c] > q[c].min()).astype(int) * 4
        else:
            b = pd.qcut(q[c].rank(method="first"), 5, labels=False)
        q = q.assign(b=b.values)
        e = q[q.d.dt.year < 2020]
        l_ = q[q.d.dt.year >= 2020]
        pe = e.groupby("b").y.mean() / max(1e-9, e.y.mean())
        pl = l_.groupby("b").y.mean() / max(1e-9, l_.y.mean())
        if 0 not in pe or 4 not in pe or 0 not in pl or 4 not in pl:
            continue
        rows.append((c, pe, pl, (pe[4] - pe[0]), (pl[4] - pl[0])))
    rows.sort(key=lambda x: -min(abs(x[3]), abs(x[4])) * (np.sign(x[3]) == np.sign(x[4])))
    log(f"===== КАКИЕ ЗАКОНОМЕРНОСТИ САМИ ПО СЕБЕ СВЯЗАНЫ С АНОМАЛИЯМИ (во сколько раз чаще базы {base:.2%}, квинтили Q1…Q5) =====")
    out = {}
    for c, pe, pl, de, dl in rows:
        ok = np.sign(de) == np.sign(dl)
        log(f"  {'✓' if ok else '·'} {NICE.get(c, c)} [{c}]: 2012–2019 " + " ".join(f"{v:.2f}" for v in pe.values)
            + " | 2020–2026 " + " ".join(f"{v:.2f}" for v in pl.values))
        out[c] = dict(early=[round(float(v), 3) for v in pe.values], late=[round(float(v), 3) for v in pl.values], stable=bool(ok))
    return out


def dir_acc(X, feats, label):
    try:
        return E19.dir_acc(X, feats, label)
    except Exception as e:  # noqa: BLE001
        log("направление:", e)
        return {}


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), runs=[])
    path = os.path.join(TR.MODELS, "exp21_report.json")

    def save():
        json.dump(out, open(path, "w"), ensure_ascii=False, indent=1, default=str)
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    FD = CD.feature_cols(X)
    yr = int(X.d.dt.year.max())
    years = list(range(FIRST, yr + 1))
    # ---- М: математика
    MFr = MF.build(Dd, IX, CD.series)
    MC = E3._put(X, MFr, MF.COLS)
    log(f"математических признаков {len(MC)}, заполнено {X[MC].notna().mean().mean():.0%}; {round(time.time() - t0)} с")
    out["lift"] = lift_split(X[X.d.dt.year >= 2012], MC)
    save()
    # ---- ПРЕЖНИЕ
    import exp6 as E6
    P = EX.prices(Dd)
    prev = []
    for nm, fn in (("спред по свечам", lambda: E6.g_spread(X, P)), ("влияние сделок", lambda: E6.g_impact(X, P))):
        try:
            c = fn()
            prev += c
            log(f"ПРЕЖНИЕ: {nm} — {len(c)} признаков")
        except Exception as e:  # noqa: BLE001
            log(f"ПРЕЖНИЕ: {nm} не посчитан:", e)
    try:
        import algopack as AP
        A = AP.load()
        groups = E6.g_algopack(X, A) if len(A) else {}
        for g, c in groups.items():
            prev += c
            log(f"ПРЕЖНИЕ: {g} — {len(c)} признаков")
    except Exception as e:  # noqa: BLE001
        log("ПРЕЖНИЕ: ALGOPACK не посчитан:", e)
    try:
        import futoi as FU
        F = FU.load()
        c = E6.g_futoi(X, F) if len(F) else []
        prev += c
        log(f"ПРЕЖНИЕ: FUTOI — {len(c)} признаков")
    except Exception as e:  # noqa: BLE001
        log("ПРЕЖНИЕ: FUTOI не посчитан:", e)
    try:
        As = E19.shifted(E19.daily_ta(Dd))
        tac = [c for c in As.columns if c.startswith(("ta_", "taw_"))]
        prev += E3._put(X, As, tac)
        log(f"ПРЕЖНИЕ: теханализ Investing — {len(tac)} признаков")
    except Exception as e:  # noqa: BLE001
        log("ПРЕЖНИЕ: теханализ не посчитан:", e)
    prev = [c for c in dict.fromkeys(prev) if c in X.columns and X[c].notna().mean() > 0.02]
    ALL = MC + prev
    for c in ALL:  # память: ~700 тыс. строк × сотни признаков
        X[c] = X[c].astype(np.float32)
    rs = np.random.RandomState(21)
    NOISE = [f"шум{k}" for k in range(len(ALL))]
    for k in NOISE:
        X[k] = rs.normal(size=len(X)).astype(np.float32)
    log(f"признаков: боевых {len(FD)}, М {len(MC)}, ПРЕЖНИЕ {len(prev)}, ВСЁ {len(FD) + len(ALL)}; {round(time.time() - t0)} с")
    # ---- прогноз аномалий
    log("===== ПРОГНОЗ АНОМАЛИЙ, годы", years[0], "–", years[-1], "=====")
    base = E5.walk(X, FD, years, "боевые", nbag=NBAG)
    out["runs"].append(base)
    save()
    for nm, feats, seed in (("боевые, другой seed (разброс случайности)", FD, 100), ("боевые + М (математика)", FD + MC, 0),
                            ("боевые + ПРЕЖНИЕ", FD + prev, 0), ("ВСЁ: боевые + М + ПРЕЖНИЕ", FD + ALL, 0),
                            ("боевые + столько же случайных признаков", FD + NOISE, 0)):
        r = E5.walk(X, feats, years, nm, nbag=NBAG, seed0=seed, base=base)
        out["runs"].append(r)
        save()
    # ---- направление
    out["dir"] = {}
    for nm, feats in (("боевые", FD), ("боевые + М", FD + MC), ("ВСЁ", FD + ALL)):
        out["dir"][nm] = dir_acc(X, feats, nm)
        save()
    log("===== ИТОГ ПРОВЕРКИ №21 =====")
    for r in out["runs"][1:]:
        log(f"  {r['name']}: топ-5 дня {r['day5']:.2%} (Δ {r['d_day5']:+.2%}), топ-10 {r['day10']:.2%} (Δ {r['d_day10']:+.2%}), "
            f"AUC Δ {r['d_auc']:+.4f}, лучше в {r['yrs_better']} из {r['yrs']} лет; по годам Δтоп-5: "
            + ", ".join(f"{x['year']}: {x['day5'] - next(b['day5'] for b in base['years'] if b['year'] == x['year']):+.1%}" for x in r["years"]))
    log(f"  боевые: топ-5 дня {base['day5']:.2%}, топ-10 {base['day10']:.2%}, AUC {base['auc']:.4f}")
    for nm, d in out["dir"].items():
        if d:
            log(f"  направление ({nm}): {d['acc']:.1%}, при уверенном прогнозе {d['acc_conf']:.1%}")
    stable = [c for c, v in out["lift"].items() if v["stable"]]
    log(f"  устойчивых закономерностей (один знак в 2012–2019 и 2020–2026): {len(stable)} из {len(out['lift'])}")
    log("время", round(time.time() - t0), "с")
    save()


if __name__ == "__main__":
    main()
