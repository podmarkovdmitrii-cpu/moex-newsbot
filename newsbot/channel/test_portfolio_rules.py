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

# ---- B1-2: v3 в просадке −25% по реальному размеру (модель −50% × 0,5) → общая остановка, хотя сборная в нуле
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
