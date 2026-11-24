# -*- coding: utf-8 -*-
"""Виртуальный счёт: стратегии из проверок №12–18 на живых сигналах бота — без реальных сделок и без публикаций.

S1 (проверки №12–15):
  сигнал   — бумага впервые за день стала ценовой аномалией ВВЕРХ между 10:00 и 18:00 МСК: изменение к индексу от вчерашнего
             закрытия ≥ порога (эшелон 1/2/3 → 4/6/8%, но не меньше 3σ дневных движений — tier и sd60 из утреннего прогноза);
  фильтр   — ликвидность: медианный дневной оборот ≥ 10 млн ₽ (lval ≥ 7 из утреннего прогноза);
  вход     — лимит на откате 50% хода от вчерашнего закрытия, действует до конца того же дня (включая вечернюю сессию);
             ограничения числа сигналов в день и открытых позиций нет (PAPER_PER_DAY=0);
  выход    — тейк +25% хода от цены входа, иначе закрытие 5-го торгового дня после сигнала; стопа нет.
F4 и F2 (проверки №17–18, модель-фильтр по ≈100 признакам, файлы filter_R4.pkl / filter_R2.pkl из newsbot/anomaly/filter_fit.py):
  F4 — аномальное ПАДЕНИЕ ликвидной бумаги: покупка сразу по текущей цене, тейк +200% хода, иначе закрытие 20-го торгового дня;
  F2 — аномальный РОСТ ликвидной бумаги: покупка сразу, закрытие 10-го торгового дня; без тейка и стопа.
  В учёт идут ВСЕ такие сигналы; у каждого записана оценка модели и признак «верхние 30%» (оценка ≥ порога из обучения) —
  в отчёте сравниваются отобранные моделью и все (как в проверке №18).
Издержки — 0,1/0,2/0,3% за сделку по эшелону. Цены — снимки котировок раз в ~5 минут, исполнение лимита и тейка — по последней цене
(осторожнее, чем по максимумам/минимумам в проверках). Закрытие дня срока берётся как PREVPRICE следующего торгового дня.
Состояние — paper_anom.json в папке состояния паблика."""
import datetime as dt
import os
import pickle

from common import load_json, log, save_json

FILE = "paper_anom.json"
COST = {1: 0.001, 2: 0.002, 3: 0.003}
THR = {1: 0.04, 2: 0.06, 3: 0.08}
HOLD = 5
PER_DAY = int(os.environ.get("PAPER_PER_DAY", "0"))   # 0 — без ограничения числа сигналов S1 в день (раньше 10)
_MODELS = {}


def _wd_after(d, n):
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _filters():
    """Модели-фильтры из папки моделей аномалий (ANOM_MODELS): {"F4": запись, "F2": запись}; пусто — фильтров нет."""
    if "loaded" in _MODELS:
        return _MODELS["loaded"]
    out = {}
    base = os.environ.get("ANOM_MODELS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "anomaly", "models")
    for key, nm in (("F4", "filter_R4.pkl"), ("F2", "filter_R2.pkl")):
        p = os.path.join(base, nm)
        if os.path.exists(p):
            try:
                out[key] = pickle.load(open(p, "rb"))
            except Exception as e:  # noqa: BLE001
                log("виртуальный счёт: фильтр", nm, "не загружен:", e)
    _MODELS["loaded"] = out
    return out


def _score(rec, feat):
    import numpy as np
    import pandas as pd
    row = {k: (np.nan if feat.get(k) is None else float(feat.get(k))) for k in rec["feats"]}
    return float(rec["model"].predict(pd.DataFrame([row], columns=rec["feats"]))[0])


def _features(t, s, r, f, now, ixc, exc, thr, st, ctx):
    """Признаки сигнала в тот же момент, что в проверке №17: дневные из утреннего прогноза + внутри дня."""
    today = now.date().isoformat()
    last, prev = float(r["LAST"]), float(r["PREVPRICE"])
    feat = {k: v for k, v in f.items() if isinstance(v, (int, float)) or v is None}
    mv = last / prev - 1
    o = r.get("OPEN")
    feat.update(c=now.hour, acx=abs(exc) / thr, ixr_c=s * ixc, cvol=(r.get("VALTODAY") or 0) / 10 ** float(f["lval"]),
                gap_sh=max(-2.0, min(3.0, (float(o) / prev - 1) / mv)) if o and mv else None,
                p_m=f.get("p_y"), pdir=s * (f["p_dir_up"] - 0.5) if f.get("p_dir_up") is not None else None)
    nh = (ctx.get("news") or {}).get(t, [])
    feat["nw_day"] = sum(1 for h in nh if h < now.hour + 1)
    sec = (ctx.get("secs") or {}).get(t, "?")
    wv = st.setdefault("wave", {}).setdefault(today, [])
    feat["wave"] = sum(1 for x in wv if x[1] == sec and x[2] == s and sec != "?")
    wv.append([t, sec, s])
    AL = ctx.get("al")
    if AL is not None:
        fp = st.setdefault("fp3", {}).setdefault(today, {})
        if t not in fp:
            b = v = 0.0
            d = now.date()
            for _ in range(3):
                d -= dt.timedelta(days=1)
                while d.weekday() >= 5:
                    d -= dt.timedelta(days=1)
                x = AL.day_flows([t], day=d.isoformat()).get(t)
                if x:
                    b, v = b + x["vb"], v + x["vs"]
            fp[t] = (b - v) / (b + v) if b + v > 0 else None
        feat["fp3"] = s * fp[t] if fp[t] is not None else None
        if now.hour >= 11:
            x = AL.window_flows([t], "10:00", "11:00").get(t)
            feat["fe"] = s * (x[0] - x[1]) / (x[0] + x[1]) if x and x[0] + x[1] > 0 else None
    return feat, mv


def scan(md, ixc, now, feats, ctx=None):
    """Вызывается из jobs.anomaly() на каждом проходе. md — котировки акций (SECID, LAST, OPEN, PREVPRICE, LASTTOPREVPRICE, VALTODAY),
    ixc — изменение индекса за день (доля), feats — {тикер: признаки утреннего прогноза}, ctx — {"secs", "news", "al"} для фильтров."""
    ctx = ctx or {}
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
    # 1) закрыть позиции, у которых истёк срок: закрытие последнего дня = PREVPRICE в первый торговый день после него
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
        if tr.get("exit") or not tr.get("entry") or not tr.get("tp"):
            continue
        r = px.get(tr["t"])
        if r and tr["entry_at"] < now.isoformat(timespec="minutes") and float(r["LAST"]) >= tr["tp"]:
            _close(tr, tr["tp"], "тейк", today)
            changed = True
    # 3) исполнение лимитов S1 сегодняшнего дня; вчерашние неисполненные — снять
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
    wkd = now.weekday() >= 5
    if (not wkd and "10:00" <= hm < "18:00") or (wkd and os.environ.get("WEEKEND_TRADING", "1") != "0" and "10:00" <= hm < "18:30"):
        n_today = sum(1 for x in st["orders"] + st["trades"] if x.get("day") == today and x.get("strat", "S1") == "S1")
        flt = _filters()
        exd = ctx.get("ex") or {}
        for t, r in px.items():
            f = feats.get(t) or {}
            if st["seen"].get(t) == today or f.get("tier") is None or f.get("lval") is None:
                continue
            if t in exd:                              # день дивидендной отсечки: гэп — не движение (как в исследованиях, core_daily)
                if st.setdefault("exdiv_skip", {}).get(t) != today:
                    st["exdiv_skip"][t] = today
                    changed = True
                    log(f"виртуальный счёт: {t} — день дивидендной отсечки, сигналы не считаются ({exd[t].get('src')})")
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
            if float(f["lval"]) < 7:
                continue                              # только ликвидные (≥ 10 млн ₽ в день)
            s = 1.0 if exc > 0 else -1.0
            p0, prev = float(r["LAST"]), float(prev)
            move = abs(p0 / prev - 1)
            # F4 / F2: модель-фильтр, покупка сразу
            key = "F2" if s > 0 else "F4"
            rec = flt.get(key)
            if rec:
                try:
                    feat, mv = _features(t, s, r, f, now, ixc, exc, thr, st, ctx)
                    sc = _score(rec, feat)
                    top = rec.get("thr") is not None and sc >= rec["thr"]
                    tp = p0 * (1 + rec["tp"] * move) if rec.get("tp") else None
                    st["trades"].append(dict(strat=key, t=t, day=today, sig_at=now.isoformat(timespec="minutes"), p0=p0, prev=prev,
                                             move=round(move, 5), tier=tier, exc=round(exc, 4), entry=p0, entry_at=now.isoformat(timespec="minutes"),
                                             tp=round(tp, 6) if tp else None, last_day=_wd_after(now.date(), int(rec["hold"])).isoformat(),
                                             score=round(sc, 5), thr=rec.get("thr"), top=bool(top), wkd=wkd))
                    log(f"виртуальный счёт {key}: {t} {chg:+.1%} (к индексу {exc:+.1%}), куплено по {p0:.4f}; оценка модели {sc:+.2%}"
                        f" — {'верхние 30%' if top else 'ниже порога'}")
                except Exception as e:  # noqa: BLE001
                    log(f"виртуальный счёт {key}: {t} — оценка не посчитана:", e)
            # S1: только рост, лимит на откате 50%
            if s < 0 or (PER_DAY and n_today >= PER_DAY):
                continue
            o = dict(strat="S1", t=t, day=today, sig_at=now.isoformat(timespec="minutes"), p0=p0, prev=prev, move=round(move, 5), tier=tier,
                     limit=round(p0 - 0.5 * (p0 - prev), 6), exc=round(exc, 4), wkd=wkd)
            st["orders"].append(o)
            n_today += 1
            log(f"виртуальный счёт S1: сигнал {t} {chg:+.1%} (к индексу {exc:+.1%}), лимит {o['limit']:.4f} на откате 50%")
    # последние цены открытых позиций — для переоценки и аварийной остановки по открытым (idea_scan.risk_state)
    lp = {tr["t"]: float(px[tr["t"]]["LAST"]) for tr in st["trades"] if tr.get("entry") and not tr.get("exit") and tr["t"] in px}
    if lp:
        st["last_px"] = dict(st.get("last_px") or {}, **lp)
        st["px_at"] = now.isoformat(timespec="minutes")
        changed = True
    st["seen"] = {k: v for k, v in st["seen"].items() if v >= (now.date() - dt.timedelta(days=3)).isoformat()}
    for k in ("wave", "fp3"):
        st[k] = {d: v for d, v in (st.get(k) or {}).items() if d == today}
    if changed:
        save_json(FILE, st)
    return st


def _close(tr, price, why, day):
    tr["exit"], tr["exit_why"], tr["exit_day"] = round(price, 6), why, day
    tr["ret"] = round(price / tr["entry"] - 1 - COST.get(tr["tier"], 0.003), 5)
    log(f"виртуальный счёт {tr.get('strat', 'S1')}: продано {tr['t']} ({why}) по {price:.4f}, результат {tr['ret']:+.2%}")


def _summary(T, label):
    done = [x for x in T if x.get("exit")]
    open_ = [x for x in T if x.get("entry") and not x.get("exit")]
    out = dict(name=label, signals=len(T), filled=sum(1 for x in T if x.get("entry")), open=len(open_), closed=len(done))
    if done:
        r = [x["ret"] for x in done]
        win = [x for x in r if x > 0]
        loss = [x for x in r if x <= 0]
        out.update(mean=round(sum(r) / len(r), 5), win=round(len(win) / len(r), 3),
                   pf=round(sum(win) / max(1e-9, -sum(loss)), 2) if loss else None, capital=round(sum(r) * 0.1, 4),
                   tp_share=round(sum(1 for x in done if x["exit_why"] == "тейк") / len(done), 3))
    return out


def report(st=None):
    """Сводка по стратегиям: S1; F4 и F2 — все сигналы и отобранные моделью (верхние 30%)."""
    if not st:  # из workflow «Паблик» состояние бота лежит в NEWS_STATE/channel
        for base in (None, os.path.join(os.environ.get("NEWS_STATE") or "", "channel")):
            st = load_json(FILE, {}, base)
            if st.get("trades") or st.get("orders"):
                break
    T = st.get("trades") or []
    res = {}
    for key, label in (("S1", "S1: рост, откат 50%, тейк 25% хода, до 5 дн."), ("F4", "F4: выкуп падения, тейк 200% хода, до 20 дн."),
                       ("F2", "F2: рост, держать 10 дн.")):
        TT = [x for x in T if x.get("strat", "S1") == key]
        if key == "S1":
            TT += [dict(o, entry=None) for o in st.get("orders") or [] if o.get("strat", "S1") == "S1"]
            res[key] = _summary(TT, label)
        else:
            res[key] = _summary(TT, label + " — все сигналы")
            res[key + "_top"] = _summary([x for x in TT if x.get("top")], label + " — отобранные моделью")
    log("ВИРТУАЛЬНЫЙ СЧЁТ (с", st.get("started"), "):")
    for k, v in res.items():
        log("  ", v)
    for x in [x for x in T if x.get("exit")][-12:]:
        log(f"   {x.get('strat', 'S1')} {x['day']} {x['t']}: вход {x['entry']:.4f}, выход {x['exit']:.4f} ({x['exit_why']}, {x['exit_day']}), {x['ret']:+.2%}"
            + (f", оценка {x['score']:+.2%}{' ★' if x.get('top') else ''}" if x.get("score") is not None else ""))
    for x in [x for x in T if x.get("entry") and not x.get("exit")]:
        log(f"   открыта {x.get('strat', 'S1')} {x['t']}: вход {x['entry']:.4f} ({x['entry_at']}), тейк {x.get('tp')}, срок до {x['last_day']}"
            + (f", оценка {x['score']:+.2%}{' ★' if x.get('top') else ''}" if x.get("score") is not None else ""))
    return res
