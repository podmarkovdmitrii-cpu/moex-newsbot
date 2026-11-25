# -*- coding: utf-8 -*-
"""Регрессионный тест формата вечерней сводки и отчёта за день (аудит: «мелочи» форматирования ломались при изменении логики).
Фиксированные данные → проверка обязательных разделов и форм записи чисел. Без сети."""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
D = tempfile.mkdtemp()
os.environ["STATE_DIR"] = os.environ["NEWS_STATE"] = D
os.environ["CHANNEL_STATE"] = os.path.join(D, "channel")
os.makedirs(os.environ["CHANNEL_STATE"])
import common  # noqa: E402

common.STATE = os.environ["CHANNEL_STATE"]
import divs  # noqa: E402
import idea_scan as I  # noqa: E402

divs.upcoming = lambda days=60: []
I._cbr = lambda url: (_ for _ in ()).throw(OSError("без сети"))
day = I.time.strftime("%Y-%m-%d", I.msk())
st = {"orders": [], "trades": [
    dict(strat="F4", t="AAA", day="2026-11-20", sig_at="2026-11-20T11:00", top=True, entry=100.0, entry_at="2026-11-20T11:00", tp=120.0,
         last_day="2026-11-21", exc=-0.08, move=0.08, overdue_since="2026-11-24"),
    dict(strat="F4", t="BBB", day="2026-11-20", sig_at="2026-11-20T11:00", top=True, entry=100.0, entry_at="2026-11-20T11:00", tp=120.0,
         last_day="2030-01-01", exc=-0.08, move=0.08, splits=[dict(day="2026-11-24", k=0.1)]),
    dict(strat="S1", t="CCC", day="2026-11-19", sig_at="2026-11-19T11:00", entry=50.0, entry_at="2026-11-19T12:00", exit=55.0, exit_day=day,
         ret=0.1, exit_why="тейк", limit=50.0)],
    "last_px": {"AAA": 95.0, "BBB": 101.0}, "px_t": {"AAA": "2026-11-20", "BBB": day}}
json.dump(st, open(os.path.join(os.environ["CHANNEL_STATE"], "paper_anom.json"), "w"))


class PW:
    S = {"capital": 1234567, "inlim": {"F4:AAA:2026-11-20": True, "F4:BBB:2026-11-20": True, "S1:CCC:2026-11-19": True}}

    @staticmethod
    def say(*a, **k):
        pass

    @staticmethod
    def save():
        pass

    @staticmethod
    def tlabel(t):
        return t


s = I.summary_text(PW)
for need in ("📋 <b>Сводка", "💰 <b>Портфель в цифрах</b> (капитал 1 234 567 ₽)", ", v3 ", "<b>1. Сигналы", "<b>2. Закрыто за день",
             "<b>3. Открытые позиции", "<b>4. На следующий торговый день", "<b>5. Риск</b>", "Весь счёт (сборная + v3", "5в. Требует внимания",
             "⏱ F4 AAA: срок выхода", "✂️ F4 BBB", "Нет свежей цены", "Не является индивидуальной инвестиционной рекомендацией"):
    assert need in s, (need, s)
assert not re.search(r"\d,\d{3}\b", re.sub(r"[+-]?\d+\.\d+%", "", s)), "запятая — разделитель тысяч"
assert "  ₽" not in s and "₽₽" not in s
assert len(s) <= 3800, len(s)   # запас до лимита сообщения Telegram (4096); эталон менять только при намеренной смене формата
r = I.report_text(PW)
for need in ("📊 <b>Отчёт за день", "<b>1. Результат портфеля</b> (капитал 1 234 567 ₽)", "<b>2. Сделки дня</b>", "<b>3. По стратегиям</b>",
             "<b>4. Риск</b>", "<b>5. Вывод</b>", "Не является индивидуальной инвестиционной рекомендацией"):
    assert need in r, (need, r)
assert not re.search(r"\d,\d{3}\b", re.sub(r"[+-]?\d+\.\d+%", "", r))
print("OK: формат сводки и отчёта — разделы, суммы в рублях, длина", len(s), "/", len(r))
