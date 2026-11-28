# -*- coding: utf-8 -*-
"""Регрессионные тесты аудита, партия 1 (правила риск-менеджмента ↔ код), без сети:
B1-1 одна позиция на бумагу (сборная и v3); B1-2 порог −20% по всему счёту (сборная + v3); B1-3 переоценка позиции без свежей цены."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
D = tempfile.mkdtemp()
os.environ["STATE_DIR"] = os.environ["NEWS_STATE"] = D
import idea_scan as I  # noqa: E402

I._emergency = lambda t, side: None
I._cbr = lambda url: (_ for _ in ()).throw(OSError("без сети"))   # макрорежим в тесте не нужен
day = time.strftime("%Y-%m-%d", I.msk())


class PW:
    S = {}
    out = []

    @staticmethod
    def say(uid, txt, **k):
        PW.out.append(txt)

    @staticmethod
    def save():
        pass

    @staticmethod
    def tlabel(t):
        return t


def v3(open_=(), equity=1_000_000):
    json.dump(dict(made=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), equity=equity, capital=1_000_000, scale=0.5,
                   open=[dict(t=t, d0=day, side=1, entry=100, stop=97, w=0.08, pnl=0.0) for t in open_], recent=[]),
              open(os.path.join(D, "signals.json"), "w"))


def sig(strat, t, **k):
    return dict(dict(strat=strat, t=t, day=day, sig_at=day + "T11:00", top=True, exc=0.07, move=0.07, p0=100.0, entry=100.0, tp=110.0,
                     last_day=day, score=0.05), **k)


def decide(st):
    PW.out.clear()
    I._ctx(PW)
    I._signals(PW, st, day, 1, True)
    return dict(PW.S["inlim"])


# ---- B1-1: S1 и F2 по одной бумаге в один день → берётся только S1
v3()
PW.S = {}
st = {"orders": [dict(strat="S1", t="AAA", day=day, sig_at=day + "T11:00", limit=95.0, move=0.07, exc=0.07, p0=100.0)],
      "trades": [sig("F2", "AAA")]}
r = decide(st)
assert r[f"S1:AAA:{day}"] is True and r[f"F2:AAA:{day}"] is False, r
assert any("уже есть позиция" in t for t in PW.out), PW.out
# F4 при открытой S1 (позавчера) → вне лимита
PW.S = {"inlim": {"S1:BBB:2026-01-05": True}}
st = {"orders": [], "trades": [dict(sig("S1", "BBB", day="2026-01-05"), entry=100.0), sig("F4", "BBB", exc=-0.08)]}
r = decide(st)
assert r[f"F4:BBB:{day}"] is False, r
# по бумаге открыта позиция v3 → вне лимита; другая бумага — в лимите
v3(open_=("CCC",))
PW.S = {}
st = {"orders": [], "trades": [sig("F4", "CCC", exc=-0.08), sig("F4", "DDD", exc=-0.08)]}
r = decide(st)
assert r[f"F4:CCC:{day}"] is False and r[f"F4:DDD:{day}"] is True, r
print("B1-1 OK: одна позиция на бумагу (S1 > F2, F4 при открытой S1, позиция v3)")

# ---- B1-2: v3 в просадке −25% по реальному размеру (модель −50% × 0,5) → общая остановка, хотя сборная в нуле (режим «halt»)
I.KS_DD_MODE = "halt"
PW.S = {"v3_base": {"day": day, "eq": 1_000_000}}
v3(equity=1_000_000)
rs = I.risk_state(PW, {"trades": [], "orders": []}, day)
assert not rs["halted"], rs
v3(equity=500_000)
rs = I.risk_state(PW, {"trades": [], "orders": []}, day)
assert rs["halted"] and "сборная + v3" in rs["why"], rs
I.resume(PW)
rs = I.risk_state(PW, {"trades": [], "orders": []}, day)
assert not rs["halted"], rs
print("B1-2 OK: порог −20% по всему счёту (сборная + v3), /resume сбрасывает общий пик")

# ---- аудит №47: режим по умолчанию «alert» — при −20% одно сообщение, входы не блокируются; эпизод заканчивается, когда просадка < 10%
I.KS_DD_MODE = "alert"
PW.S = {"v3_base": {"day": day, "eq": 1_000_000}}
v3(equity=1_000_000)
I.risk_state(PW, {"trades": [], "orders": []}, day)
v3(equity=500_000)
PW.out.clear()
assert I.guard(PW, 1) is True and any("остановитесь и разберитесь" in t.lower() for t in PW.out), PW.out
rs = I.risk_state(PW, {"trades": [], "orders": []}, day)
assert not rs["halted"] and "сборная + v3" in rs["dd_alert"], rs
rs = I.risk_state(PW, {"trades": [], "orders": []}, day)
assert rs.get("dd_alert") and not rs.get("alert_new"), "повторного сообщения в том же эпизоде нет"
v3(equity=1_000_000)
rs = I.risk_state(PW, {"trades": [], "orders": []}, day)
assert not rs.get("dd_alert") and "dd_alert" not in PW.S["ks"], "просадка ушла — эпизод закрыт"
print("№47 OK: просадка −20% — сигнал без блокировки входов, одно сообщение на эпизод")

# ---- B1-3: позиция без свежей цены — по последней известной и в списке; без цены вообще — по цене входа
v3()
PW.S = {"inlim": {}}
old = "2026-01-05"
st = {"orders": [], "trades": [dict(sig("F4", "EEE", day=old), entry=100.0), dict(sig("F4", "FFF", day=old), entry=100.0)],
      "last_px": {"EEE": 60.0}, "px_t": {"EEE": old}, "px_at": day + "T12:00"}
rs = I.risk_state(PW, st, day)
assert abs(rs["mtm"] - 0.05 * (60 / 100 - 1)) < 1e-9, rs["mtm"]
assert ("EEE", old) in rs["stale"] and ("FFF", None) in rs["stale"], rs["stale"]
print("B1-3 OK: переоценка по последней цене, позиции без свежей цены помечены:", rs["stale"])

# ---- B1-4: размер — доля × (1 + результат закрытых до дня входа), как в исследованиях №27/30/31; до SIZING_SINCE — по-старому
I.SIZING_SINCE = "2026-02-01"
PW.S = {"inlim": {}}
st = {"orders": [], "trades": [
    dict(sig("S1", "OLD", day="2026-01-10"), entry=100.0, entry_at="2026-01-10T11:00", exit=120.0, exit_day="2026-01-12", ret=20.0),  # +100% капитала
    dict(sig("F4", "NEW", day="2026-02-10"), entry=100.0, entry_at="2026-02-10T11:00"),
    dict(sig("F4", "LOSS", day="2026-02-11"), entry=100.0, entry_at="2026-02-11T11:00", exit=50.0, exit_day="2026-02-12", ret=-10.0),
    dict(sig("F4", "LATE", day="2026-02-13"), entry=100.0, entry_at="2026-02-13T11:00")]}
sz = I._build_sizes(st, {})
assert abs(sz["S1:OLD:2026-01-10"] - 0.05) < 1e-12, sz             # до даты перехода — от начального капитала
assert abs(sz["F4:NEW:2026-02-10"] - 0.10) < 1e-12, sz             # капитал ×2 → размер ×2
assert abs(sz["F4:LOSS:2026-02-11"] - 0.10) < 1e-12, sz
# после убытка −1,0 (0,10 × −10): капитал 1 + 1 − 1 = 1 → размер снова 0,05
assert abs(sz["F4:LATE:2026-02-13"] - 0.05) < 1e-12, sz
rs = I.risk_state(PW, st, "2026-02-13")
assert abs(rs["eq"] - (1.0 - 1.0)) < 1e-9, rs["eq"]                # кривая — суммы «размер × результат» (в долях начального капитала)
st2 = {"orders": [], "trades": [dict(sig("S1", "HALF", day="2026-02-01"), entry=100.0, entry_at="2026-02-01T11:00", exit=50.0,
                                     exit_day="2026-02-02", ret=-10.0), dict(sig("F4", "X", day="2026-02-05"), entry=100.0, entry_at="2026-02-05T11:00")]}
sz = I._build_sizes(st2, {})
assert abs(sz["F4:X:2026-02-05"] - 0.025) < 1e-12, sz             # капитал ×0,5 → размер ×0,5
print("B1-4 OK: размер от текущего капитала по закрытым (×2 → ×2, ×0,5 → ×0,5), до даты перехода — по-старому")

# ---- B1-4, переходная сделка: открыта до SIZING_SINCE, закрыта после — размер не пересчитывается, результат входит в базу
I.SIZING_SINCE = "2026-02-01"
st3 = {"orders": [], "trades": [
    dict(sig("F4", "TRN", day="2026-01-25"), entry=100.0, entry_at="2026-01-25T11:00", exit=300.0, exit_day="2026-02-03", ret=20.0),
    dict(sig("F4", "AFT", day="2026-02-05"), entry=100.0, entry_at="2026-02-05T11:00")]}
sz = I._build_sizes(st3, {})
assert abs(sz["F4:TRN:2026-01-25"] - 0.05) < 1e-12, sz             # размер переходной сделки — по старому правилу, не задним числом
assert abs(sz["F4:AFT:2026-02-05"] - 0.10) < 1e-12, sz             # её результат (+0,05 × 20 = +1) вошёл в базу новой сделки
print("B1-4 OK: переходная сделка — размер не пересчитан, результат вошёл в базу")


# ---- таблица смен правил: каждая сделка помечается правилами дня входа; новая смена не переписывает старые
I.SIZING_SINCE, I.PER_TICKER_SINCE = "2026-11-25", "2026-11-25"
assert I.rules_at("2026-11-20") == {"sizing": "initial", "per_ticker": "any", "ks_dd": "halt", "s1_fill": "snapshot"}
assert I.rules_at("2026-11-25") == {"sizing": "current", "per_ticker": "one", "ks_dd": "halt", "s1_fill": "snapshot"}
assert I.rules_at("2026-11-28") == {"sizing": "current", "per_ticker": "one", "ks_dd": "alert", "s1_fill": "low"}
I.RULE_EXTRA = [("2026-12-10", "per_ticker", "s1f2")]
assert I.rules_at("2026-12-01")["per_ticker"] == "one" and I.rules_at("2026-12-10")["per_ticker"] == "s1f2"
assert I.sizing_base({"day": "2026-11-24"}) == "initial" and I.sizing_base({"day": "2026-12-11"}) == "current"
I.RULE_EXTRA = []
print("таблица правил: ok")

# ---- живой период: исполнения S1 по способу и эпизоды −20% с даты новых правил
PW.S = {"ks": {"dd_episodes": [{"start": "2026-12-01", "dd": -0.21}, {"start": "2026-10-01", "dd": -0.2}]}}
st = {"trades": [dict(strat="S1", t="A", day="2026-12-02", entry=1.0, fill="минимум"), dict(strat="S1", t="B", day="2026-12-03", entry=1.0, fill="снимок"),
                 dict(strat="S1", t="C", day="2026-11-20", entry=1.0), dict(strat="F4", t="D", day="2026-12-03", entry=1.0)]}
line = I.live_period_line(PW, st)
assert "S1 исполнено 2 (по минимуму цены 1, по снимку 1)" in line and "эпизодов просадки" in line and line.endswith(": 1"), line
print("живой период: ok —", line)
