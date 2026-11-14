# -*- coding: utf-8 -*-
"""Виртуальный счёт: стратегия из проверок №12–13 на живых сигналах бота — без реальных сделок и без публикаций.

Правила те же, что в проверках (newsbot/anomaly/exp12.py, exp13.py):
  сигнал   — бумага впервые за день стала ценовой аномалией ВВЕРХ между 10:00 и 18:00 МСК: изменение к индексу от вчерашнего
             закрытия ≥ порога (эшелон 1/2/3 → 4/6/8%, но не меньше 3σ дневных движений — tier и sd60 из утреннего прогноза);
  фильтр   — ликвидность: медианный дневной оборот ≥ 10 млн ₽ (lval ≥ 7 из утреннего прогноза);
  вход     — лимит на откате 50% хода от вчерашнего закрытия, действует до конца того же дня (включая вечернюю сессию);
  выход    — тейк +25% хода от цены входа, иначе закрытие 5-го торгового дня после сигнала; стопа нет;
  издержки — 0,1/0,2/0,3% за сделку по эшелону; капитал — до 10 сделок в день по 10%.
Цены — снимки котировок раз в ~5 минут (как у поиска аномалий), поэтому исполнение лимита и тейка проверяется по последней цене
(осторожнее, чем по часовым максимумам/минимумам в проверке). Закрытие D+5 берётся как PREVPRICE следующего торгового дня.
Состояние — paper_anom.json в папке состояния паблика."""
import datetime as dt

from common import load_json, log, save_json

FILE = "paper_anom.json"
COST = {1: 0.001, 2: 0.002, 3: 0.003}
THR = {1: 0.04, 2: 0.06, 3: 0.08}
HOLD = 5
PER_DAY = 10


def _wd_after(d, n):
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def scan(md, ixc, now, feats):
    """Вызывается из jobs.anomaly() на каждом проходе. md — котировки акций (SECID, LAST, PREVPRICE, LASTTOPREVPRICE),
    ixc — изменение индекса за день (доля), feats — {тикер: признаки утреннего прогноза (tier, sd60, lval)}."""
    st = load_json(FILE, {"orders": [], "trades": [], "seen": {}, "started": now.isoformat(timespec="minutes")})
    today = now.date().isoformat()
    hm = now.strftime("%H:%M")
    px = {}
    for r in md:
        if not r.get("LAST"):
            continue
        r = dict(r)
        if not r.get("PREVPRICE") and r.get("LASTTOPREVPRICE") is not None:  # вчерашнее закрытие из изменения за день
            r["PREVPRICE"] = float(r["LAST"]) / (1 + float(r["LASTTOPREVPRICE"]) / 100)
        px[r["SECID"]] = r
    changed = False
    # 1) закрыть позиции, у которых истёк срок: закрытие D+5 = PREVPRICE в первый торговый день после него
    for tr in st["trades"]:
        if tr.get("exit") or not tr.get("entry"):
            continue
        r = px.get(tr["t"])
        if not r:
            continue
        if today > tr["last_day"] and r.get("PREVPRICE"):
            _close(tr, float(r["PREVPRICE"]), "срок", tr["last_day"])
            changed = True
    # 2) тейк по текущей цене (позиции, открытые не в этот же проход)
    for tr in st["trades"]:
        if tr.get("exit") or not tr.get("entry"):
            continue
        r = px.get(tr["t"])
        if r and tr["entry_at"] < now.isoformat(timespec="minutes") and float(r["LAST"]) >= tr["tp"]:
            _close(tr, tr["tp"], "тейк", today)
            changed = True
    # 3) исполнение лимитов сегодняшнего дня; вчерашние неисполненные — снять
    keep = []
    for o in st["orders"]:
        if o["day"] != today:
            o["status"] = "не исполнена"
            st["trades"].append(dict(o, entry=None))
            changed = True
            continue
        r = px.get(o["t"])
        if r and float(r["LAST"]) <= o["limit"]:
            e = o["limit"]
            tp = e * (1 + 0.25 * o["move"])
            st["trades"].append(dict(o, entry=e, entry_at=now.isoformat(timespec="minutes"), tp=round(tp, 6),
                                     last_day=_wd_after(now.date(), HOLD).isoformat()))
            log("виртуальный счёт: куплено", o["t"], "по", round(e, 4), "тейк", round(tp, 4))
            changed = True
        else:
            keep.append(o)
    st["orders"] = keep
    # 4) новые сигналы
    if now.weekday() < 5 and "10:00" <= hm < "18:00":
        n_today = sum(1 for x in st["orders"] + st["trades"] if x.get("day") == today)
        for t, r in px.items():
            if n_today >= PER_DAY:
                break
            f = feats.get(t) or {}
            if st["seen"].get(t) == today or f.get("tier") is None or f.get("lval") is None:
                continue
            prev = r.get("PREVPRICE")
            if not prev:
                continue
            chg = (r.get("LASTTOPREVPRICE") or 0) / 100
            exc = chg - ixc
            tier = int(f["tier"])
            thr = max(THR.get(tier, 0.08), 3 * float(f.get("sd60") or 0))
            if abs(exc) < thr:
                continue
            st["seen"][t] = today                     # первый раз за день — дальше по бумаге сигналов нет
            changed = True
            if exc <= 0 or float(f["lval"]) < 7:
                continue                              # только рост и только ликвидные (≥ 10 млн ₽ в день)
            p0, prev = float(r["LAST"]), float(prev)
            move = abs(p0 / prev - 1)
            o = dict(t=t, day=today, sig_at=now.isoformat(timespec="minutes"), p0=p0, prev=prev, move=round(move, 5), tier=tier,
                     limit=round(p0 - 0.5 * (p0 - prev), 6), exc=round(exc, 4))
            st["orders"].append(o)
            n_today += 1
            log(f"виртуальный счёт: сигнал {t} {chg:+.1%} (к индексу {exc:+.1%}), лимит {o['limit']:.4f} на откате 50%")
    st["seen"] = {k: v for k, v in st["seen"].items() if v >= (now.date() - dt.timedelta(days=3)).isoformat()}
    if changed:
        save_json(FILE, st)
    return st


def _close(tr, price, why, day):
    tr["exit"], tr["exit_why"], tr["exit_day"] = round(price, 6), why, day
    tr["ret"] = round(price / tr["entry"] - 1 - COST.get(tr["tier"], 0.003), 5)
    log(f"виртуальный счёт: продано {tr['t']} ({why}) по {price:.4f}, результат {tr['ret']:+.2%}")


def report(st=None):
    """Сводка: сигналы, исполнение, закрытые сделки, средний результат, доля прибыльных, PF, капитал при 10% на сделку."""
    if not st:  # из workflow «Паблик» состояние бота лежит в NEWS_STATE/channel
        import os
        for base in (None, os.path.join(os.environ.get("NEWS_STATE") or "", "channel")):
            st = load_json(FILE, {}, base)
            if st.get("trades") or st.get("orders"):
                break
    T = st.get("trades") or []
    done = [x for x in T if x.get("exit")]
    open_ = [x for x in T if x.get("entry") and not x.get("exit")]
    sig = len(T) + len(st.get("orders") or [])
    filled = sum(1 for x in T if x.get("entry"))
    out = dict(started=st.get("started"), signals=sig, filled=filled, open=len(open_), closed=len(done))
    if done:
        r = [x["ret"] for x in done]
        win = [x for x in r if x > 0]
        loss = [x for x in r if x <= 0]
        out.update(mean=round(sum(r) / len(r), 5), win=round(len(win) / len(r), 3),
                   pf=round(sum(win) / max(1e-9, -sum(loss)), 2) if loss else None, capital=round(sum(r) * 0.1, 4),
                   tp_share=round(sum(1 for x in done if x["exit_why"] == "тейк") / len(done), 3))
    log("ВИРТУАЛЬНЫЙ СЧЁТ (стратегия из проверок №12–13):", out)
    for x in done[-10:]:
        log(f"   {x['day']} {x['t']}: вход {x['entry']:.4f}, выход {x['exit']:.4f} ({x['exit_why']}, {x['exit_day']}), {x['ret']:+.2%}")
    for x in open_:
        log(f"   открыта {x['t']}: вход {x['entry']:.4f} ({x['entry_at']}), тейк {x['tp']:.4f}, срок до {x['last_day']}")
    return out
