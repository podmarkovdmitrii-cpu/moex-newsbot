# -*- coding: utf-8 -*-
"""Регрессионные тесты аудита, партия 2 (события на открытой позиции), без сети:
B2-1 срок вышел без торгов → позиция не закрывается «в никуда», помечается; после возобновления торгов — закрытие по сроку;
B2-2 сплит/консолидация → цены позиции пересчитываются, ложного убытка нет; B2-3 смена тикера → учёт продолжается, ключ сделки прежний;
B2-4 отсечка на открытой позиции видна (дивиденд в результат не входит, как в проверках)."""
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
import idea_scan as I  # noqa: E402
import paper_anom as PA  # noqa: E402

PA._filters = lambda: {}
divs.upcoming = lambda days=60: []


def run(st, md, now):
    common.save_json(PA.FILE, st)
    PA.scan(md, 0.0, now, {}, {})
    return common.load_json(PA.FILE, {})


def tr(t, **k):
    return dict(dict(strat="F4", t=t, day="2026-11-02", sig_at="2026-11-02T11:00", entry=100.0, entry_at="2026-11-02T11:00", tp=150.0,
                     last_day="2026-11-20", tier=1, move=0.05, p0=100.0, prev=105.0), **k)


# ---- B2-1: срок 20.11, торгов нет 23.11 и 24.11 → не закрыта, помечена; 25.11 торги есть → закрыта по сроку с пометкой
st = {"orders": [], "trades": [tr("HALT")], "seen": {}, "last_px": {"HALT": 90.0}}
st = run(st, [], dt.datetime(2026, 11, 23, 11, 0))
x = st["trades"][0]
assert x.get("exit") is None and x.get("overdue_since") == "2026-11-23", x
st = run(st, [], dt.datetime(2026, 11, 24, 11, 0))
assert st["trades"][0]["overdue_since"] == "2026-11-23"
st = run(st, [dict(SECID="HALT", LAST=80.0, PREVPRICE=88.0, LASTTOPREVPRICE=-9.1)], dt.datetime(2026, 11, 25, 11, 0))
x = st["trades"][0]
assert x["exit"] == 88.0 and x["exit_why"] == "срок (после остановки торгов)", x
print("B2-1 OK: срок без торгов — пометка, а не закрытие «в никуда»; после возобновления — закрытие по сроку")

# ---- B2-2: сплит 1:10 — последняя цена 300 → 30: цены позиции ÷10, результат не −90%
st = {"orders": [], "trades": [tr("SPLT", entry=280.0, tp=330.0, last_day="2026-12-20")], "seen": {}, "last_px": {"SPLT": 300.0}}
st = run(st, [dict(SECID="SPLT", LAST=30.5, PREVPRICE=30.0, LASTTOPREVPRICE=1.7)], dt.datetime(2026, 11, 25, 11, 0))
x = st["trades"][0]
assert abs(x["entry"] - 28.0) < 1e-9 and abs(x["tp"] - 33.0) < 1e-9 and x["splits"][0]["k"] == 0.1, x
assert x.get("exit") is None
assert x["splits"][0]["auto"] is True and x["splits"][0]["kind"] == "сплит"
# консолидация 10:1 — цена ×10: цены позиции ×10
st = {"orders": [], "trades": [tr("CONS", entry=10.0, tp=12.0, last_day="2026-12-20")], "seen": {}, "last_px": {"CONS": 11.0}}
st = run(st, [dict(SECID="CONS", LAST=111.0, PREVPRICE=110.0, LASTTOPREVPRICE=0.9)], dt.datetime(2026, 11, 25, 11, 0))
x = st["trades"][0]
assert abs(x["entry"] - 100.0) < 1e-9 and x["splits"][0]["kind"] == "консолидация" and x["splits"][0]["auto"], x
# гэп +250% после остановки (коэффициент 3,6 — не «круглый») → НЕ пересчитано, требует решения
st = {"orders": [], "trades": [tr("GAP", last_day="2026-12-20")], "seen": {}, "last_px": {"GAP": 100.0}}
st = run(st, [dict(SECID="GAP", LAST=360.0, PREVPRICE=100.0, LASTTOPREVPRICE=260.0)], dt.datetime(2026, 11, 25, 11, 0))
x = st["trades"][0]
assert x["entry"] == 100.0 and x["splits"][0]["auto"] is False, x
# обычное падение −30% — не сплит
st = {"orders": [], "trades": [tr("DROP", last_day="2026-12-20")], "seen": {}, "last_px": {"DROP": 100.0}}
st = run(st, [dict(SECID="DROP", LAST=70.0, PREVPRICE=100.0, LASTTOPREVPRICE=-30.0)], dt.datetime(2026, 11, 25, 11, 0))
assert st["trades"][0]["entry"] == 100.0 and not st["trades"][0].get("splits")
print("B2-2 OK: сплит и консолидация пересчитываются, гэп с некруглым коэффициентом — нет (требует решения), обычное падение — нет")

# ---- B2-3: смена тикера YNDX → YDEX: позиция под новым тикером, ключ сделки прежний (решение «в лимите» не теряется)
st = {"orders": [], "trades": [tr("YNDX", last_day="2026-12-20")], "seen": {}, "last_px": {}}
st = run(st, [dict(SECID="YDEX", LAST=101.0, PREVPRICE=100.0, LASTTOPREVPRICE=1.0)], dt.datetime(2026, 11, 25, 11, 0))
x = st["trades"][0]
assert x["t"] == "YDEX" and x["t_old"] == "YNDX", x
assert I._trade_key(x) == "F4:YNDX:2026-11-02"
print("B2-3 OK: смена тикера — учёт под новым тикером, ключ сделки прежний")

# ---- B2-4: отсечка между входом и сегодня видна (SBER, реестр 20.07.2026 в divhist.json)
assert divs.ex_between("SBER", "2026-07-10", "2026-07-25") and not divs.ex_between("SBER", "2026-07-21", "2026-07-25")
print("B2-4 OK: отсечка на открытой позиции находится")

# ---- аудит №47: исполнение лимита S1 как на бирже — снимок выше лимита, но минимум с момента выставления дошёл → исполнено «по минимуму»
o = dict(strat="S1", t="LIMT", day="2026-11-25", sig_at="2026-11-25T11:05", p0=110.0, prev=100.0, move=0.1, tier=1, limit=105.0, exc=0.1, wkd=False)
md = [dict(SECID="LIMT", LAST=107.0, PREVPRICE=100.0, LASTTOPREVPRICE=7.0)]
PA._low_since = lambda t, day, since: (104.5, "11:40") if t == "LIMT" and since == "11:05" else None
PA.S1_FILL = "snapshot"
st = run({"orders": [dict(o)], "trades": [], "seen": {}}, md, dt.datetime(2026, 11, 25, 12, 0))
assert st["orders"] and not st["trades"], "строгий учёт: по снимку не исполнено"
PA.S1_FILL = "low"
st = run({"orders": [dict(o)], "trades": [], "seen": {}}, md, dt.datetime(2026, 11, 25, 12, 0))
x = st["trades"][0]
assert x["fill"] == "минимум" and x["entry"] == 105.0 and x["entry_at"] == "2026-11-25T11:40" and not st["orders"], x
md2 = [dict(SECID="LIMT", LAST=104.0, PREVPRICE=100.0, LASTTOPREVPRICE=4.0)]
st = run({"orders": [dict(o)], "trades": [], "seen": {}}, md2, dt.datetime(2026, 11, 25, 12, 0))
assert st["trades"][0]["fill"] == "снимок"
print("№47 OK: лимит S1 исполняется по минимуму цены с момента выставления (как биржевая заявка); строгий режим — только по снимку")
