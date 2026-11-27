# -*- coding: utf-8 -*-
"""Регрессионный тест фильтра сбоев исследований (exp15.bad_path, аудит №42/45/46).
Настоящий обвал (как SBER 24.02.2022: −55% за день, цена не вернулась) остаётся в расчёте при EXP_BADPATH=real (по умолчанию)
и отбрасывается старым правилом (bad_path_strict / EXP_BADPATH=strict). Сбой (цена вернулась) и дробление отбрасываются всегда.
Не является индивидуальной инвестиционной рекомендацией."""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "anomaly"))
os.environ.pop("EXP_BADPATH", None)
import exp15 as E  # noqa: E402

assert E.BADPATH_MODE == "real", "по умолчанию — новый фильтр"
# SBER 21–25.02.2022 (закрытия): 239 → 207 → 132 (−36%) → 132; внутри 24.02 минимум 89 (−57% от входа) — бар max/min > 2
crash = dict(c0=239.0, nh=[245.0, 216.0, 155.0], nl=[220.0, 89.0, 125.0], nc=[207.0, 132.0, 132.0])
glitch = dict(c0=100.0, nh=[101.0, 52.0, 101.0], nl=[99.0, 49.0, 99.0], nc=[100.0, 50.0, 100.0])
split = dict(c0=100.0, nh=[101.0, 51.0, 51.0], nl=[99.0, 49.0, 49.0], nc=[100.0, 50.0, 50.0])
normal = dict(c0=100.0, nh=[103.0, 104.0], nl=[98.0, 99.0], nc=[101.0, 102.0])
assert E.bad_path_strict(**crash) and not E.bad_path(**crash), "обвал: старое правило отбрасывает, новое — оставляет"
assert E.classify_path(crash["c0"], crash["nc"]) == "обвал"
assert E.bad_path(**glitch) and E.classify_path(glitch["c0"], glitch["nc"]) == "сбой"
assert E.bad_path(**split) and E.classify_path(split["c0"], split["nc"]) == "дробление?"
assert not E.bad_path(**normal) and not E.bad_path_strict(**normal)
os.environ["EXP_BADPATH"] = "strict"
E = importlib.reload(E)
assert E.bad_path(**crash), "EXP_BADPATH=strict — старое правило"
os.environ.pop("EXP_BADPATH", None)
print("фильтр сбоев: ok (обвал остаётся, сбой/дробление отбрасываются, strict воспроизводит старое)")
