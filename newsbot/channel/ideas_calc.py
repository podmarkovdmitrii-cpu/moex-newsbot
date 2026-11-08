# -*- coding: utf-8 -*-
"""Итог закрытой идеи — одна формула для канала (#итоги_идей) и бота для Пульса.

Приоритет — итог, который автор сам написал при закрытии («✅ Тейк взят +6,7%», «❌ Стоп −3,1%»): это фактический
результат его сделки. Если процента в итоге нет — расчёт по уровням: доли тейков из плана, остаток — по стопу на момент
закрытия. R — результат в рисках идеи (расстояние от входа до первоначального стопа).
"""
import re

PCT = re.compile(r"([+\-−–])?\s*(\d+(?:[.,]\d+)?)\s*%")
NEG = re.compile(r"❌|⛔|🔴|стоп|убыт|минус", re.I)
POS = re.compile(r"✅|🟢|🏁|тейк|прибыл|плюс", re.I)


def shares_of(v):
    sh = [t.get("share") for t in ((v.get("plan") or {}).get("takes") or [])]
    if len(sh) != len(v["takes"]) or any(x is None for x in sh):
        sh = [1 / len(v["takes"])] * len(v["takes"])
    return sh


def risk_frac(v):
    """Риск идеи в долях цены входа (по первоначальному стопу)."""
    E = v["entry"]
    if v.get("risk_pct"):
        return v["risk_pct"] / 100
    s0 = v.get("stop0") or v["stop"]
    return abs(E - s0) / E if s0 != E else None


def by_levels(v):
    E, side = v["entry"], v["side"]
    sh, hit = shares_of(v), set(v.get("hit") or [])
    pct = sum(sh[i] * (v["takes"][i] - E) * side / E for i in hit if i < len(sh))
    rest = 1 - sum(sh[i] for i in hit if i < len(sh))
    if rest > 1e-6 and v.get("closed"):
        pct += rest * (v["stop"] - E) * side / E
    kind = "тейк" if rest <= 1e-6 else ("безубыток" if abs(v["stop"] - E) < 1e-9 and not hit else "частично" if hit else "стоп")
    return pct, kind


def stated_pct(v, guess_sign=1):
    """Процент из итога автора или None. Знак: явный «+/−», иначе по словам (стоп/убыток — минус), иначе guess_sign."""
    txt = v.get("result") or ""
    m = PCT.search(txt)
    if not m:
        return None
    x = float(m.group(2).replace(",", ".")) / 100
    if m.group(1):
        return -x if m.group(1) in "-−–" else x
    if NEG.search(txt) and not POS.search(txt):
        return -x
    if POS.search(txt) and not NEG.search(txt):
        return x
    return x if guess_sign >= 0 else -x


def result(v):
    """{'pct', 'R', 'kind', 'src'} для закрытой (или частично исполненной) идеи; src: 'автор' | 'уровни'."""
    lv, kind = by_levels(v)
    sp = stated_pct(v, 1 if lv >= 0 else -1) if v.get("closed") else None
    pct, src = (sp, "автор") if sp is not None else (lv, "уровни")
    pct = 0.0 if abs(pct) < 0.0005 else pct
    if sp is not None:
        if abs(sp) < 0.0005:
            kind = "безубыток"
        elif kind in ("стоп", "безубыток") and sp > 0:
            kind = "частично" if v.get("hit") else "в плюс"
        elif kind == "тейк" and sp < 0:
            kind = "стоп"
    rf = risk_frac(v)
    return {"pct": pct, "R": pct / rf if rf else None, "kind": kind, "src": src}


def record(ideas):
    """Сводка по всем закрытым идеям: число, в плюс, сумма %, сумма R, средний R, дата первой идеи."""
    rows = [(iid, v, result(v)) for iid, v in ideas.items() if v.get("closed")]
    if not rows:
        return None
    Rs = [r["R"] for _, _, r in rows if r["R"] is not None]
    return {"n": len(rows), "wins": sum(1 for _, _, r in rows if r["pct"] > 0), "losses": sum(1 for _, _, r in rows if r["pct"] < 0),
            "sum_pct": sum(r["pct"] for _, _, r in rows), "sum_R": sum(Rs), "avg_R": sum(Rs) / len(Rs) if Rs else None,
            "first_t": min(int(v.get("t") or 0) for _, v, _ in rows if v.get("t")) if any(v.get("t") for _, v, _ in rows) else None,
            "rows": rows}
