# -*- coding: utf-8 -*-
"""Регрессионный тест аудита A-1: в день дивидендной отсечки бумага не даёт сигналов виртуального счёта (S1 / F4 / F2),
а в обычный день тот же гэп даёт сигнал F4. Запуск: python newsbot/channel/test_exdiv.py (без сети: dohod.ru и состояние подменяются)."""
import datetime as dt
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["STATE_DIR"] = os.environ["NEWS_STATE"] = tempfile.mkdtemp()

import common  # noqa: E402

common.STATE = os.environ["STATE_DIR"]
import divs  # noqa: E402
import paper_anom as PA  # noqa: E402

day = dt.date(2026, 7, 20)                                     # понедельник; SBER: реестр 20.07.2026 в divhist.json
divs.upcoming = lambda days=60: [dict(t="XDIV", amount=50.0, yld=0.12, reg="2026-07-21", last_buy="2026-07-17", declared=True)]
ex = divs.ex_dates(day)
assert "SBER" in ex and ex["SBER"]["src"] == "divhist", ex      # из истории
assert "XDIV" in ex and ex["XDIV"]["src"] == "dohod.ru", ex      # объявленная выплата: последний день покупки — пятница 17.07
assert "GAZP" not in ex
assert divs.ex_dates(dt.date(2026, 7, 21)).get("SBER") is None  # на следующий день — уже не отсечка

PA._filters = lambda: {"F4": dict(thr=-1.0, tp=2.0, hold=20), "F2": dict(thr=-1.0, tp=None, hold=10)}
PA._features = lambda *a, **k: ({}, 0.0)
PA._score = lambda rec, feat: 0.05
now = dt.datetime(2026, 7, 20, 11, 0)
feats = {t: dict(tier=1, lval=8, sd60=0.01) for t in ("SBER", "XDIV", "GAZP")}
md = [dict(SECID="SBER", LAST=280.0, PREVPRICE=315.0, LASTTOPREVPRICE=-11.1, VALTODAY=1e9),
      dict(SECID="XDIV", LAST=360.0, PREVPRICE=410.0, LASTTOPREVPRICE=-12.2, VALTODAY=1e9),
      dict(SECID="GAZP", LAST=110.0, PREVPRICE=124.0, LASTTOPREVPRICE=-11.3, VALTODAY=1e9)]
PA.scan(md, 0.0, now, feats, dict(ex=ex))
st = common.load_json(PA.FILE, {})
got = {x["t"] for x in st.get("trades", []) + st.get("orders", [])}
assert "SBER" not in got and "XDIV" not in got, got              # отсечка — сигналов нет
assert "GAZP" in got, got                                        # обычный день — F4 есть
print("OK: отсечка блокирует сигналы, обычный гэп даёт F4;", sorted(got), "пропущены:", sorted(st.get("exdiv_skip", {})))
