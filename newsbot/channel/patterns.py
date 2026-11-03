# -*- coding: utf-8 -*-
"""Паттерны «резкое движение → разворот» и «вынос стопов» для живого агента (channel/jobs.py pattern_scan).

Один и тот же паттерн считается в исследовании векторно (research/reversal.py, research/shakeout.py) и здесь — по последнему
закрытому бару. Агент-проверяющий (research/rev_verify.py) прогоняет ЭТОТ код на истории в точках событий исследования:
если живой код не находит те же события, правило не допускается к публикации.

Бар j — последний закрытый бар; решение по закрытию j, вход — по открытию следующего бара (в живом режиме ≈ текущая цена).
ATR(j) — среднее истинного диапазона баров j−14…j−1 (не меньше 10 значений); норма оборота — медиана 20 баров до окна движения.
Семейства:
  stop    — вынос стопов (без предшествующего движения): прокол экстремума N баров и закрытие обратно (research/shakeout.py);
  fade    — после резкого движения за W баров (|ΔC| ≥ thr·ATR, оборот ≥ vx×) — вход против движения, стоп за экстремумом окна;
  follow  — то же движение, вход по движению, стоп 1,5·ATR (follow_wide — 3·ATR);
  confirm — резкое движение за W баров до j, бар j закрылся против движения (≥ 0,5·ATR) → вход против движения («рост, затем падение»);
  shake   — резкое движение за W баров до j, бар j — вынос стопов против движения (прокол экстремума окна и закрытие обратно) → вход против
            движения: связка резкого движения и выноса стопов.
"""
import math


def _nan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def atr_at(bars, j):
    trs = []
    for k in range(max(1, j - 14), j):
        x, p = bars[k], bars[k - 1]
        if _nan(x["h"]) or _nan(p["c"]):
            continue
        trs.append(max(x["h"] - x["l"], abs(x["h"] - p["c"]), abs(x["l"] - p["c"])))
    return sum(trs) / len(trs) if len(trs) >= 10 else None


def med_vol(bars, end):
    """Медиана оборота баров end−20…end−1 (не меньше 15)."""
    vv = sorted(b["v"] for b in bars[max(0, end - 20):end] if not _nan(b["v"]))
    if len(vv) < 15:
        return None
    k = len(vv) // 2
    return vv[k] if len(vv) % 2 else (vv[k - 1] + vv[k]) / 2  # как медиана pandas/numpy


def _risk_ok(entry, stop, side):
    risk = (entry - stop) * side
    return entry > 0 and risk > 0 and 0.003 <= risk / entry <= 0.15


def detect(bars, fam, prm, side, j=None, check_risk=True):
    """Срабатывает ли паттерн на баре j (по умолчанию — последний). Возвращает dict(entry, stop, tgt, risk, info) или None."""
    j = len(bars) - 1 if j is None else j
    if j < 30:
        return None
    b = bars[j]
    if _nan(b["c"]):
        return None
    entry = b["c"]
    R = prm.get("R", 2.0)
    if fam == "stop":
        N = int(prm["N"])
        a, m = atr_at(bars, j), med_vol(bars, j)
        if a is None or m is None or j < N:
            return None
        rg = b["h"] - b["l"]
        if not (rg > 0 and rg >= prm["rng"] * a and b["v"] >= prm["vx"] * m and m >= prm["liq"]):
            return None
        prev = bars[j - N:j]
        if any(_nan(x["l"]) or _nan(x["h"]) for x in prev):
            return None
        if side > 0:
            ext = min(x["l"] for x in prev)
            ok = b["l"] < ext * (1 - prm["pen"]) and b["c"] >= b["l"] + prm["pos"] * rg and b["c"] > ext
            stop = b["l"] * 0.999
        else:
            ext = max(x["h"] for x in prev)
            ok = b["h"] > ext * (1 + prm["pen"]) and b["c"] <= b["h"] - prm["pos"] * rg and b["c"] < ext
            stop = b["h"] * 1.001
        info = dict(ext=ext, rg=rg / a, vx=b["v"] / m)
    else:
        W = int(prm["W"])
        e = j - 1 if fam in ("confirm", "shake") else j  # последний бар движения
        a_e, m_e = atr_at(bars, e), med_vol(bars, e - W + 1)
        if a_e is None or m_e is None or m_e < prm["liq"] or e - W < 0 or _nan(bars[e - W]["c"]):
            return None
        mv = (bars[e]["c"] - bars[e - W]["c"]) / a_e
        vr = sum(x["v"] for x in bars[e - W + 1:e + 1]) / (W * m_e)
        if not (abs(mv) >= prm["thr"] and vr >= prm["vx"]):
            return None
        d = 1 if mv > 0 else -1  # направление резкого движения
        win = bars[e - W + 1:j + 1]
        if fam == "fade" or fam == "confirm":
            if side != -d:
                return None
            if fam == "confirm":
                a_j = atr_at(bars, j)
                if a_j is None or (b["c"] - bars[j - 1]["c"]) * d > -0.5 * a_j:
                    return None
            stop = max(x["h"] for x in win) * 1.001 if side < 0 else min(x["l"] for x in win) * 0.999
        elif fam in ("follow", "follow_wide"):
            if side != d:
                return None
            stop = entry - side * (1.5 if fam == "follow" else 3.0) * a_e
        elif fam == "shake":
            if side != -d:
                return None
            rg = b["h"] - b["l"]
            prev = bars[e - W + 1:e + 1]
            if side > 0:
                ok = rg > 0 and b["l"] < min(x["l"] for x in prev) and b["c"] >= b["l"] + 0.6 * rg
                stop = b["l"] * 0.999
            else:
                ok = rg > 0 and b["h"] > max(x["h"] for x in prev) and b["c"] <= b["h"] - 0.6 * rg
                stop = b["h"] * 1.001
            if not ok:
                return None
        else:
            return None
        ok = True
        info = dict(move=mv, vr=vr, W=W)
    if not ok or (check_risk and not _risk_ok(entry, stop, side)):
        return None
    risk = (entry - stop) * side
    return dict(entry=entry, stop=stop, tgt=entry + side * R * risk if R < 50 else None, risk=risk / entry, info=info)
