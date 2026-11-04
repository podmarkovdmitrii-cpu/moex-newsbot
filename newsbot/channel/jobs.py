# -*- coding: utf-8 -*-
"""
Паблик MOEX: публикации по расписанию (GitHub Actions, workflow channel.yml).

  morning  — утренний торговый план: сигналы и сетапы совмещённой стратегии v2 с графиками, режим рынка, ОФЗ, новости;
  flows    — перевес покупателей и продавцов по 30 самым ликвидным акциям и крупные сделки (≥ 50 млн ₽ одной сделкой);
  ofz      — потоки денег в ОФЗ по срокам, изменение доходностей, индекс RGBI;
  pulse    — новые посты самых читаемых авторов Т-Пульса со ссылками;
  evening  — итоги дня: индекс, сектора, лидеры роста и падения, модельный портфель;
  weekly   — понедельничная статья: базовые активы, их долгосрочный потенциал и всё нужное трейдеру (PDF + пост).

Запуск: python channel/jobs.py <режим> [--dry-run] [--demo]. Без режима — по расписанию (переменная SCHED) или по времени МСК.
"""
import html
import datetime as dt
import json
import math
import os
import re
import statistics as st
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (plural, DISCLAIMER, MSK as MSKTZ, BG, BLUE, DEMO, DN, FG, GRID, NEWS_STATE, ORANGE, OUT, UP, VIOLET, Publisher, bln, candle_chart, currency_candles, ema, esc, fmt, hbars,  # noqa: E402
                    http_get, index_candles, candles, iss, load_json, log, now_msk, pc, plt, save_json, share_candles, style, table,
                    FUT, FX, FUT_CODE, fut_continuous, fut_fronts, fut_candles, tf_bars, atr, TF_RU)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(os.path.join(HERE, "data.json"), encoding="utf-8"))
DIVS = json.load(open(os.path.join(HERE, "..", "divhist.json"), encoding="utf-8"))
SEC_OF = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8"))["secOf"]
CBR_DAYS = ["2026-10-23", "2026-12-18", "2027-02-12", "2027-03-20", "2027-04-24", "2027-06-11", "2027-07-23", "2027-09-10", "2027-10-22", "2027-12-17"]
FAM_RU = {"mom": "импульс", "pull": "откат к EMA 20", "sbrk": "шорт пробоя"}
SEC_RU = {"bank": "банки", "oil": "нефтегаз", "metal": "металлурги", "retail": "ритейл", "tech": "IT и связь", "power": "энергетика", "chem": "химия", "devel": "девелоперы", "transport": "транспорт"}
SCHED = {"40 3 * * 1-5": "morning", "30 5 * * 1": "calendar_week", "35 16 * * 5": "weekly", "5 16 * * 5": "poll", "50 4 * * *": "nav", "*/30 7-15 * * 1-5": "flows", "50 15 * * 1-5": "flows",
         "45 7-15 * * 1-5": "ofz", "10 16 * * 1-5": "ofz", "25 16 * * 1-5": "evening",
         "20 3 * * 1-5": "trader_prep", "20 4 * * 1-5": "trader_open", "20 7 * * 1-5": "trader_open", "45 16 * * 5": "trader_week",
         "15 9 * * 1-5": "stock_review", "30 16 * * 0": "weekend_review", "55 15 * * 1-5": "strong_moves"}
PUB = Publisher()
DISCLAIMER_TXT = DISCLAIMER


def wd_ru(d):
    return ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][d.weekday()]


def next_cbr():
    t = now_msk().date().isoformat()
    for d in CBR_DAYS:
        if d >= t:
            return d
    return None


# =============================================================================================
# Перевес покупателей и продавцов (агрессор сделки по данным ISS) + крупные сделки
# =============================================================================================
def liquid_shares(n=30):
    if DEMO:
        return [dict(SECID=t, VALTODAY=1e9, LAST=100, LASTTOPREVPRICE=0) for t in ["SBER", "GAZP", "LKOH", "YDEX", "T", "ROSN", "VTBR", "GMKN", "NVTK", "PLZL"]]
    js = iss("engines/stock/markets/shares/boards/TQBR/securities.json", **{"iss.only": "marketdata,securities", "marketdata.columns": "SECID,LAST,LASTTOPREVPRICE,VALTODAY,OPEN,HIGH,LOW,SYSTIME",
                                                                              "securities.columns": "SECID,SHORTNAME,PREVPRICE,DECIMALS,INSTRID"})
    # на TQBR теперь торгуются и биржевые фонды (INSTRID IFTF и др.) — оставляем только акции и расписки (EQIN)
    names = {r["SECID"]: r for r in table(js, "securities") if (r.get("INSTRID") or "EQIN") == "EQIN"}
    md = [dict(r, NAME=names[r["SECID"]].get("SHORTNAME", r["SECID"])) for r in table(js, "marketdata") if r.get("VALTODAY") and r["SECID"] in names]
    md.sort(key=lambda r: -(r["VALTODAY"] or 0))
    return md[:n]


def fetch_trades(secid, market, last=None, max_pages=150):
    """Сделки дня по бумаге с признаком агрессора (B — покупка по рынку, S — продажа). Инкрементально от номера last."""
    out, start = [], 0
    for _ in range(max_pages):
        p = {"iss.only": "trades", "trades.columns": "TRADENO,TRADETIME,PRICE,VALUE,BUYSELL,BOARDID", "limit": 5000}
        if last:
            p.update(tradeno=last, next_trade=1)
        else:
            p["start"] = start
        t0 = time.time()
        rows = table(iss(f"engines/stock/markets/{market}/securities/{secid}/trades.json", **p), "trades")
        if time.time() - t0 > 15:
            log(f"сделки {secid}: страница {len(rows)} строк за {time.time() - t0:.0f} с — Мосбиржа отвечает медленно")
        out += rows
        if len(rows) < 5000:
            break
        if last:
            last = rows[-1]["TRADENO"]
        else:
            start += len(rows)
    return out


def demo_trades(secid, last):
    import random
    rnd = random.Random(hash(secid) % 97 + (last or 0))
    base = 100.0
    rows = []
    for k in range(400):
        rows.append(dict(TRADENO=(last or 0) + k + 1, TRADETIME=f"{10 + k // 60:02d}:{k % 60:02d}:00", PRICE=base, VALUE=rnd.lognormvariate(11, 1.6), BUYSELL=rnd.choice("BBS" if hash(secid) % 2 else "BSS"), BOARDID="TQBR"))
    rows.append(dict(TRADENO=(last or 0) + 999, TRADETIME="14:31:05", PRICE=base, VALUE=120e6, BUYSELL="B", BOARDID="TQBR"))
    return rows


def turnover_avg(tickers, days=20):
    """Средний дневной оборот за 20 торговых дней по истории торгов (кэш по датам)."""
    cache = load_json("turnover.json", {})
    if DEMO:
        return {t: 1e9 for t in tickers}
    d = now_msk().date()
    have = sorted(cache)
    need, k = [], 0
    while len(need) + len([x for x in have if x < d.isoformat()]) < days + 5 and k < 45:
        k += 1
        day = d - dt.timedelta(days=k)
        if day.weekday() < 5 and day.isoformat() not in cache:
            need.append(day.isoformat())
    for ds in need[:8]:
        rows, start = [], 0
        try:
            while True:
                part = table(iss("history/engines/stock/markets/shares/boards/TQBR/securities.json", date=ds, start=start, **{"iss.only": "history", "history.columns": "SECID,VALUE"}), "history")
                rows += part
                if len(part) < 100:
                    break
                start += len(part)
        except Exception as e:  # noqa: BLE001
            log("история оборотов", ds, e)
            continue
        cache[ds] = {r["SECID"]: r["VALUE"] for r in rows if r.get("VALUE")}
    cache = {k: v for k, v in cache.items() if k >= (d - dt.timedelta(days=60)).isoformat()}
    save_json("turnover.json", cache)
    dates = [x for x in sorted(cache) if cache[x]][-days:]
    return {t: (st.mean([cache[x].get(t, 0) for x in dates]) if dates else None) for t in tickers}


def fetch_fut_trades(secid, last=None, max_pages=150):
    out, start = [], 0
    for _ in range(max_pages):
        p = {"iss.only": "trades", "trades.columns": "TRADENO,TRADETIME,PRICE,QUANTITY,BUYSELL", "limit": 5000}
        if last:
            p.update(tradeno=last, next_trade=1)
        else:
            p["start"] = start
        rows = table(iss(f"engines/futures/markets/forts/securities/{secid}/trades.json", **p), "trades")
        out += rows
        if len(rows) < 5000:
            break
        if last:
            last = rows[-1]["TRADENO"]
        else:
            start += len(rows)
    return out


def flows():
    """Перевес покупателей/продавцов и сделки китов: акции (1–3 эшелон по обороту) и сырьевые фьючерсы. Каждые 30 минут."""
    now = now_msk()
    today = now.date().isoformat()
    stt = load_json("flows.json", {})
    if stt.get("date") != today:
        stt = {"date": today, "tk": {}}
    md_all = liquid_shares(2000)  # все акции TQBR — для общего объёма денег за день
    md = md_all[:int(os.environ.get("FLOWS_N", "60"))]
    avg = turnover_avg([r["SECID"] for r in md_all])
    try:
        fut = fut_fronts()
    except Exception as e:  # noqa: BLE001
        log("фьючерсы", e)
        fut = {}
    items = [("share", r["SECID"], r) for r in md] + [("fut", f["secid"], f) for f in fut.values()]
    n_ok, n_fail, t_start = 0, 0, time.time()
    budget = float(os.environ.get("FLOWS_BUDGET_S", 420))
    for kind, t, r in items:
        if time.time() - t_start > budget:
            # лента сделок отдаётся слишком медленно: пост с опозданием в 10+ минут уже не «текущий» — пропускаем слот
            log(f"ленты сделок грузятся дольше {budget / 60:.0f} мин (готово {n_ok} из {len(items)}) — пост потоков пропущен")
            save_json("flows.json", stt)  # загруженное сохраняем: следующий слот догрузит остальное быстрее
            return
        if n_fail >= 3 and not n_ok:
            # Мосбиржа не отдаёт ленту сделок этому серверу (каждая бумага — ~100 с тайм-аутов): не ждём час, пост пропускаем
            log("ленты сделок недоступны (3 бумаги подряд) — пост потоков пропущен, следующий слот через 30 минут")
            return
        s = stt["tk"].setdefault(t, {"last": None, "vb": 0.0, "vs": 0.0, "big": [], "pb": 0.0, "ps": 0.0})
        s["pb"], s["ps"] = s["vb"], s["vs"]  # на момент прошлого поста — для перевеса за последние 30 минут
        if kind == "share":
            a20 = avg.get(t) or 0
            thr = float(os.environ.get("BIG_TRADE", "50e6")) if a20 > 3e9 else max(5e6, min(50e6, 0.05 * a20 if a20 else 20e6))
        else:
            thr = 500e6 if r.get("asset") in FX else 30e6  # валютные фьючерсы торгуются на десятки миллиардов — китом считаем от 500 млн
        try:
            if DEMO:
                rows = demo_trades(t, s["last"])
            elif kind == "share":
                rows = fetch_trades(t, "shares", s["last"])
            else:
                rows = fetch_fut_trades(t, s["last"])
        except Exception as e:  # noqa: BLE001
            log("сделки", t, e)
            n_fail += 1
            continue
        n_ok += 1
        k = (r["step"] / r["minstep"]) if kind == "fut" and r.get("minstep") else 1
        for x in rows:
            if kind == "share" and x.get("BOARDID") not in (None, "TQBR"):
                continue
            v = x.get("VALUE") or (x.get("PRICE", 0) * x.get("QUANTITY", 0) * k)
            if x.get("BUYSELL") == "B":
                s["vb"] += v
            elif x.get("BUYSELL") == "S":
                s["vs"] += v
            if v >= thr:
                s["big"].append([x["TRADETIME"], x["PRICE"], v, x.get("BUYSELL"), 1])
        if rows:
            s["last"] = rows[-1]["TRADENO"]
        time.sleep(0.05)

    def stat(kind, t, r):
        s = stt["tk"].get(t)
        if not s or s["vb"] + s["vs"] <= 0:
            return None
        tot = s["vb"] + s["vs"]
        wb, ws = s["vb"] - s.get("pb", 0), s["vs"] - s.get("ps", 0)
        lab = t if kind == "share" else r["asset"]
        return dict(t=t, lab=lab, kind=kind, name=r.get("NAME") or r.get("name") or t, imb=(s["vb"] - s["vs"]) / tot, tot=tot,
                    wimb=(wb - ws) / (wb + ws) if wb + ws > 0 else None, wtot=wb + ws,
                    chg=((r.get("LASTTOPREVPRICE") or 0) / 100) if kind == "share" else (r.get("chg") or 0),
                    x=(tot / avg[t]) if kind == "share" and avg.get(t) else None, big=[b for b in s["big"] if len(b) > 4 and b[4]])
    res = [z for z in (stat(k_, t, r) for k_, t, r in items) if z]
    for t, s in stt["tk"].items():  # киты показаны — больше не повторяем
        for b in s["big"]:
            if len(b) > 4:
                b[4] = 0
        s["big"] = s["big"][-40:]
    save_json("flows.json", stt)
    if not res:
        log("нет данных по сделкам")
        return
    sh = [x for x in res if x["kind"] == "share"]
    fu = [x for x in res if x["kind"] == "fut"]
    allb = sum(x["tot"] * (1 + x["imb"]) / 2 for x in sh)
    allt = sum(x["tot"] for x in sh) or 1
    key = lambda x: x["wimb"] if x["wimb"] is not None and x["wtot"] > 0.02 * x["tot"] else x["imb"]
    buyers = sorted([x for x in sh if key(x) > 0], key=lambda x: -key(x) * min(1.5, (x["x"] or 1)))[:6]
    sellers = sorted([x for x in sh if key(x) < 0], key=lambda x: key(x) * min(1.5, (x["x"] or 1)))[:6]
    bigs_all = [(x["lab"], x["kind"], *b[:4]) for x in res for b in x["big"]]
    bigs = sorted(bigs_all, key=lambda z: -z[4])[:10]
    hm = now.strftime("%H:%M")

    buyers, sellers = buyers[:4], sellers[:4]

    oi_map = stock_oi() if not DEMO else {}
    flow_by = {x["t"]: key(x) for x in sh}
    news_cls = all_clusters()

    def why(t, chg, x_=None):
        need_r5 = (chg or 0) >= 0.03 or (flow_by.get(t) or 0) < 0  # для продавцов — всегда: фиксация прибыли / продолжение распродажи
        b = behavior(chg, flow=flow_by.get(t), x=x_, oi=oi_map.get(t), r5=ret5(t) if need_r5 else None, ndir=news_dir(t, cls=news_cls))
        return ("\n   ↳ <i>" + esc("; ".join(b)) + "</i>") if b else ""

    def row(x):
        k_ = key(x)
        per = "за 30 мин" if x["wimb"] is not None and x["wtot"] > 0.02 * x["tot"] else "за день"
        xx = f" · оборот <b>{fmt(x['x'], 1)}×</b> к норме" if x["x"] and x["x"] >= 1.3 else ""
        return f"<b>${x['lab']}</b> {pc(k_, 0)} {per}{xx} · цена <code>{pc(x['chg'], 1)}</code>" + why(x["t"], x["chg"], x["x"])
    share_b = allb / allt
    mood = "покупатели сильнее" if share_b >= 0.53 else "продавцы сильнее" if share_b <= 0.47 else "силы равны"
    # общий объём денег за день во всех акциях и его сравнение с обычным на это время сессии
    mins = (now.hour * 60 + now.minute) - 600
    frac = max(0.08, min(1.0, mins / 530))
    tot_all = sum(r.get("VALTODAY") or 0 for r in md_all)
    avg_all = sum(v for v in (avg.get(r["SECID"]) for r in md_all) if v)
    norm_x = tot_all / (avg_all * frac) if avg_all else None
    net = allb - (allt - allb)
    # резкие изменения оборота по бумагам (к среднему за 20 дней с поправкой на время дня)
    vol = []
    for r in md_all:
        a20 = avg.get(r["SECID"]) or 0
        if a20 < 20e6 or not r.get("VALTODAY"):
            continue
        vol.append((r["SECID"], r["VALTODAY"] / (a20 * frac), r["VALTODAY"], (r.get("LASTTOPREVPRICE") or 0) / 100, a20))
    vup = sorted([v for v in vol if v[1] >= 2.5], key=lambda v: -v[1])[:6]
    vdn = sorted([v for v in vol if v[1] <= 0.35 and v[4] >= 150e6], key=lambda v: v[1])[:5]
    txt = [f"⚖️ <b>Покупатели vs продавцы</b> · {now.strftime('%d.%m')}, обновлено в {hm} МСК\n<i>🔄 Пост обновляется каждые 30 минут до конца торгов</i>",
           f"<blockquote>💰 <b>Деньги в акциях за день: {bln(tot_all)}</b>" + (f" ({fmt(norm_x, 1)}× к обычному на это время)" if norm_x else "") +
           f"\nПокупки по рынку {pc(share_b, 0, False)} · чистый {'приток' if net >= 0 else 'отток'} <b>{'+' if net >= 0 else '−'}{bln(abs(net))}</b> — {mood}</blockquote>", "",
           "🟢 <b>Перевес покупок</b>", *([row(x) for x in buyers] or ["<i>нет бумаг с заметным перевесом</i>"]),
           "", "🔴 <b>Перевес продаж</b>", *([row(x) for x in sellers] or ["<i>нет бумаг с заметным перевесом</i>"])]
    # куда могут дойти: самые сильные потоки, подтверждённые оборотом, новостями и графиком
    tg_rows = []
    if os.environ.get("FLOW_TARGETS", "1") != "0":
        cls = all_clusters()
        vxm = {v[0]: v[1] for v in vol}
        for x in buyers[:3] + sellers[:3]:
            k_ = key(x)
            if abs(k_) < 0.25:
                continue
            side = 1 if k_ > 0 else -1
            r_ = level_target(x["t"], side, flow=k_, vx=vxm.get(x["t"]), ndir=news_dir(x["t"], cls=cls), chg=x["chg"] or 0)
            if r_ and r_["pts"] >= 1:
                tg_rows.append(r_)
            time.sleep(0.05)
        tg_rows.sort(key=lambda r: -r["pts"])
        tg_rows = tg_rows[:4]
        if tg_rows:
            save_targets(tg_rows)
    if tg_rows:
        txt += ["", "🎯 <b>Куда могут дойти</b> <i>(оценка на 1–5 дней: потоки + новости + уровни графика)</i>"] + [target_line(r) for r in tg_rows]
    # психология толпы и смежные бумаги (статистика — research/, проверка — research/verify.py)
    try:
        crowd = crowd_scan(md_all, avg, frac, news_cls)
    except Exception as e:  # noqa: BLE001
        log("толпа", e)
        crowd = []
    if crowd:
        txt += ["", "🧠 <b>Психология толпы</b> <i>(движение без новостей о компании)</i>"]
        for x in crowd:
            line, sig = crowd_line(x)
            txt.append(line + (("\n" + sig) if sig else ""))
    try:
        spill = spill_scan(md_all, avg, frac, ix_day_change())
    except Exception as e:  # noqa: BLE001
        log("смежные", e)
        spill = []
    if spill:
        txt += ["", "🔗 <b>Смежные бумаги</b> <i>(ещё не отреагировали на рывок лидера)</i>"]
        for line, sig in spill:
            txt.append(line + (("\n" + sig) if sig else ""))
    if vup or vdn:
        txt += ["", "📊 <b>Резкие изменения оборота</b> <i>(к среднему за 20 дней на это время)</i>"]
        shown = {x["t"] for x in buyers + sellers}
        txt += [f"🟢▲ <b>${t}</b> ×{fmt(x, 1)} · {bln(v)} · цена <code>{pc(c, 1)}</code>" + ("" if t in shown else why(t, c, x)) for t, x, v, c, _ in vup]
        txt += [f"🔴▼ <b>${t}</b> ×{fmt(x, 2)} — оборот почти иссяк · {bln(v)}" for t, x, v, c, _ in vdn]
    if fu:
        fl = []
        for x in sorted([x for x in fu if x["lab"] not in FX], key=lambda x: -x["tot"])[:5] + [x for x in fu if x["lab"] in FX]:
            w = f"{pc(x['wimb'], 0)} за 30 мин" if x["wimb"] is not None and x["wtot"] > 0 else f"{pc(x['imb'], 0)} за день"
            fl.append(f"{esc(FUT[x['lab']][0])}: {w} · {bln(x['tot'])} · цена {pc(x['chg'], 1)}")
        txt += ["", "<blockquote expandable>🛢 <b>Сырьё и валюта (фьючерсы)</b>\n" + "\n".join(fl) + "</blockquote>"]
    if bigs:
        # одинаковые сделки одной минуты (заявка, разбитая на части) — одной строкой
        agg = {}
        for t, k_, tm, p_, v, side in bigs_all:
            g = agg.setdefault((t, k_, tm[:5], side), [0.0, 0, p_])
            g[0] += v
            g[1] += 1
        top = sorted(agg.items(), key=lambda z: -z[1][0])[:6]
        wl = [f"{'$' + t if k_ == 'share' else esc(FUT[t][0])} {tm} — {'🟢 покупка' if side == 'B' else '🔴 продажа'} <b>{bln(v)}</b> по {fmt(p_, 2)}" + (f" ({n_} сделки)" if n_ > 1 else "")
              for (t, k_, tm, side), (v, n_, p_) in top]
        txt += ["", "<blockquote expandable>🐋 <b>Киты за 30 минут</b> — крупнейшие сделки\n" + "\n".join(wl) + "</blockquote>"]
    txt += ["", how_to_read(), "#потоки"]
    rows = [(x["lab"], key(x), None) for x in sorted(buyers + sellers[::-1], key=lambda x: -key(x))]
    img = hbars(rows, os.path.join(OUT, "flows.png"), f"Перевес покупок (+) и продаж (−), {now.strftime('%d.%m')} {hm} МСК", fmt_v=lambda v: pc(v, 0))
    PUB.post("\n".join(txt), [(img, f"Перевес покупателей и продавцов на {hm} МСК")], live="flows")
    save_json("flows_last.json", dict(time=now.isoformat(), tot_all=tot_all, net=net, vol_up=[v[0] for v in vup], buyers=[(x["lab"], key(x)) for x in buyers], sellers=[(x["lab"], key(x)) for x in sellers], share=allb / allt))


# =============================================================================================
# Поведение денег в бумаге: кто и зачем двигает (по цене, перевесу сделок, обороту и открытому интересу фьючерса)
# =============================================================================================
FUT2STOCK = {"SBRF": "SBER", "SBPR": "SBERP", "GAZR": "GAZP", "SNGR": "SNGS", "SNGP": "SNGSP", "MTSI": "MTSS", "NOTKM": "NVTK", "TRNF": "TRNFP", "TATP": "TATNP"}


def stock_oi():
    """Изменение открытого интереса по фьючерсам на акции за день: {тикер акции: (доля, контракты)}."""
    try:
        js = iss("engines/futures/markets/forts/securities.json", **{"iss.only": "securities,marketdata", "securities.columns": "SECID,ASSETCODE,PREVOPENPOSITION",
                                                                     "marketdata.columns": "SECID,OPENPOSITION"})
    except Exception as e:  # noqa: BLE001
        log("открытый интерес", e)
        return {}
    now_ = {r["SECID"]: r.get("OPENPOSITION") for r in table(js, "marketdata")}
    agg = {}
    for r in table(js, "securities"):
        a = r.get("ASSETCODE")
        cur, prev = now_.get(r["SECID"]), r.get("PREVOPENPOSITION")
        if not a or cur is None or not prev:
            continue
        g = agg.setdefault(FUT2STOCK.get(a, a), [0, 0])
        g[0] += cur
        g[1] += prev
    return {t: (c / p - 1, c - p) for t, (c, p) in agg.items() if p >= 1000}


def behavior(chg, flow=None, x=None, oi=None, r5=None, ndir=0):
    """Короткое объяснение, что может стоять за движением денег. Возвращает список из 0–2 фраз."""
    out = []
    chg = chg or 0
    if oi and abs(oi[0]) >= 0.03 and abs(chg) >= 0.005:
        o = pc(oi[0], 0)
        if chg > 0 and oi[0] < 0:
            out.append(f"похоже на закрытие шортов: цена растёт, а открытый интерес по фьючерсу {o} — рост во многом за счёт откупа коротких позиций и может быстро выдохнуться")
        elif chg > 0:
            out.append(f"новые покупки: растут и цена, и открытый интерес по фьючерсу ({o}) — в бумагу заходят новые деньги")
        elif oi[0] > 0:
            out.append(f"новые шорты: цена падает, а открытый интерес по фьючерсу {o} — продавцы наращивают короткие позиции")
        else:
            out.append(f"закрытие лонгов: снижаются и цена, и открытый интерес ({o}) — фиксация прибыли, давление может ослабнуть")
    if flow is not None:
        if flow >= 0.2 and chg <= -0.005:
            out.append("покупки по рынку не поднимают цену — крупный продавец стоит лимитными заявками (поглощение)")
        elif flow <= -0.2 and chg >= 0.005:
            out.append("продажи по рынку не роняют цену — кто-то крупный выкупает предложение лимитками")
    if not out and r5 is not None and r5 <= -0.05 and chg >= 0.03 and (flow is None or flow > 0):
        out.append(f"резкий отскок после падения ({pc(r5, 0)} за 5 дней) — часть покупок может быть закрытием шортов")
    # продавцы — те же объяснения зеркально (раньше у строк с перевесом продаж пояснений почти не было)
    if not out and r5 is not None and r5 >= 0.05 and chg <= 0 and (flow is not None and flow < 0):
        out.append(f"фиксация прибыли после роста ({pc(r5, 0)} за 5 дней) — продают те, кто покупал раньше")
    if not out and r5 is not None and r5 <= -0.07 and chg <= -0.01 and (flow is not None and flow < 0):
        out.append(f"продажи продолжаются после падения ({pc(r5, 0)} за 5 дней) — держатели выходят из бумаги, разворота по потоку пока нет")
    if ndir and flow is not None and abs(flow) >= 0.15 and len(out) < 2:
        if flow < 0 and ndir < 0:
            out.append("продают вслед за негативной новостью о компании")
        elif flow > 0 and ndir > 0:
            out.append("покупают вслед за позитивной новостью о компании")
        elif flow < 0 and ndir > 0:
            out.append("продают несмотря на позитивную новость — «продажа на факте» или крупный держатель выходит")
        elif flow > 0 and ndir < 0:
            out.append("покупают несмотря на негативную новость — рынок считает её учтённой в цене")
    if x and x >= 2.5:
        if chg <= -0.04 and (flow is None or flow < 0):
            out.append("обвал на большом объёме — возможны вынужденные продажи (маржин-коллы)")
        elif -0.04 < chg <= -0.015 and (flow is not None and flow < 0) and len(out) < 2:
            out.append("продажи на объёме выше обычного — крупный участник сокращает позицию")
        elif abs(chg) < 0.01 and len(out) < 2:
            out.append("большой оборот почти без движения цены — похоже на перекладку крупного пакета или борьбу за уровень")
        elif chg >= 0.05 and ndir <= 0 and len(out) < 2:
            out.append("сильный импульс на большом объёме без новости в ту же сторону — такие движения часто частично откатываются")
    if not out and flow is not None and abs(flow) >= 0.2:
        if flow < 0:
            out.append("продажи по рынку без заметной новости" + (" и цена уступает — давление продавцов реальное" if chg <= -0.005 else " — пока цену держат покупатели лимитными заявками"))
        else:
            out.append("покупки по рынку без заметной новости" + (" и цена растёт — спрос реальный" if chg >= 0.005 else " — пока цену держат продавцы лимитными заявками"))
    return out[:2]


def ret5(t):
    try:
        B = tf_bars(t, "1d", "share")
        return B[-1]["c"] / B[-6]["c"] - 1 if len(B) > 6 else None
    except Exception:  # noqa: BLE001
        return None


# =============================================================================================
# Предположительный уровень: потоки + новости + технический анализ (уровни графика, тренд, ATR)
# =============================================================================================
NEWS_POS = {"div_rec", "buyback", "results_up", "guidance_up", "analyst_up", "production_up", "cbr_cut", "geo_peace"}
NEWS_NEG = {"div_cut", "spo", "results_down", "guidance_down", "analyst_down", "sanctions", "legal", "tax", "production_down", "cbr_hike", "geo_escal"}


def news_dir(t, hours=12, cls=None):
    """Направление свежих новостей о бумаге по смыслу события (не по статистике реакции): +1, −1 или 0."""
    since, sc = time.time() - hours * 3600, 0
    for c in cls if cls is not None else all_clusters():
        tk, ty = ((c.get("key") or "|") + "|").split("|")[:2]
        if tk != t or c.get("ts", 0) < since or c.get("rumorOnly"):
            continue
        sc += 1 if ty in NEWS_POS else -1 if ty in NEWS_NEG else 0
    return (sc > 0) - (sc < 0)


def level_target(t, side, kind="share", flow=0.0, vx=None, ndir=0, chg=0.0):
    """Куда цена может дойти за 1–5 дней в сторону потока: ближайший значимый уровень графика (локальные экстремумы 120 дней)
    не ближе 0,5 и не дальше 4 дневных ATR; нет уровня — 2 ATR. Отмена — ближайший уровень с другой стороны или 1 ATR."""
    try:
        B = tf_bars(t, "1d", kind)
    except Exception as e:  # noqa: BLE001
        log("уровни", t, e)
        return None
    if len(B) < 40 or not side:
        return None
    last = B[-1]["c"]
    a = atr(B[-15:]) or last * 0.02
    c = [b["c"] for b in B]
    e20, e50 = ema(c, 20)[-1], ema(c, 50)[-1]
    trend = 1 if e20 > e50 and last > e50 else -1 if e20 < e50 and last < e50 else 0
    H = B[-121:-1]
    piv = []
    for i in range(3, len(H) - 3):
        w = H[i - 3:i + 4]
        if H[i]["h"] == max(b["h"] for b in w):
            piv.append((H[i]["h"], H[i]["t"][:10]))
        if H[i]["l"] == min(b["l"] for b in w):
            piv.append((H[i]["l"], H[i]["t"][:10]))
    ahead = sorted([p for p in piv if 0.5 * a <= (p[0] - last) * side <= 4 * a], key=lambda p: abs(p[0] - last))
    behind = sorted([p for p in piv if 0.4 * a <= (last - p[0]) * side <= 2.5 * a], key=lambda p: abs(p[0] - last))
    stop = behind[0][0] if behind else last - side * a
    risk = abs(last - stop)
    # цель имеет смысл, только если путь до неё не короче расстояния до отмены
    ahead = [p for p in ahead if abs(p[0] - last) >= 0.8 * risk]
    if ahead:
        tgt = ahead[0][0]
        touches = sum(1 for p in piv if abs(p[0] - tgt) <= 0.3 * a)
        why = f"{'сопротивление' if side > 0 else 'поддержка'} {ahead[0][1][8:10]}.{ahead[0][1][5:7]}" + (f", уровень проверялся {touches} {plural(touches, 'раз', 'раза', 'раз')}" if touches >= 2 else "")
    else:
        tgt = last + side * max(2 * a, risk)
        why = "значимых уровней рядом нет — оценка по дневной волатильности"
    pts, pro, con = 0, [], []
    if abs(flow) >= 0.35:
        pts += 1
        pro.append(f"поток {pc(flow, 0)}")
    if vx and vx >= 1.5:
        pts += 1
        pro.append(f"оборот {fmt(vx, 1)}×")
    if trend == side:
        pts += 1
        pro.append("тренд в ту же сторону")
    elif trend == -side:
        pts -= 1
        con.append("против тренда")
    if ndir == side:
        pts += 1
        pro.append("новости в ту же сторону")
    elif ndir == -side:
        pts -= 1
        con.append("новости против")
    if chg * side < -0.01:
        pts -= 1
        con.append("цена пока идёт против потока")
    conf = "высокая" if pts >= 3 else "средняя" if pts == 2 else "низкая"
    return dict(t=t, side=side, last=last, tgt=tgt, stop=stop, move=tgt / last - 1, why=why, conf=conf, pts=pts, pro=pro, con=con, atr=a)


def target_line(x, name=None):
    d = dec_of(x["last"])
    return (f"<b>{name or '$' + x['t']}</b> {'🟢' if x['side'] > 0 else '🔴'} → <code>{fmt(x['tgt'], d)}</code> ({pc(x['move'], 1)}) · уверенность <b>{x['conf']}</b>"
            f"\n   <i>{esc(x['why'])}; {', '.join(x['pro'] + x['con']) or 'без подтверждений'}; отмена за <code>{fmt(x['stop'], d)}</code></i>")


def dec_of(x):
    return 0 if x >= 1000 else 1 if x >= 100 else 2 if x >= 2 else 4


def save_targets(rows):
    """Журнал прогнозов уровня — для честной статистики попаданий в итогах дня."""
    st_ = load_json("targets.json", [])
    now = time.time()
    for x in rows:
        st_.append(dict(t=x["t"], kind=x.get("kind", "share"), side=x["side"], last=x["last"], tgt=x["tgt"], stop=x["stop"], ts=now, conf=x["conf"]))
    save_json("targets.json", [r for r in st_ if now - r["ts"] < 20 * 86400][-400:])


def targets_review(days=5):
    """Сколько прогнозов уровня за последние дни дошли до цели раньше отмены (по дневным свечам)."""
    st_ = load_json("targets.json", [])
    now = time.time()
    done, hit, miss = set(), 0, 0
    for r in st_:
        if now - r["ts"] > days * 86400 or now - r["ts"] < 3600:
            continue
        key = (r["t"], r["side"], round(r["tgt"], 4))
        if key in done:
            continue
        done.add(key)
        try:
            B = tf_bars(r["t"], "1d", r.get("kind") or "share")
        except Exception:  # noqa: BLE001
            continue
        d0 = dt.datetime.fromtimestamp(r["ts"], MSKTZ).date().isoformat()
        res = None
        for b in [b for b in B if b["t"][:10] >= d0]:
            up, dn = b["h"], b["l"]
            if r["side"] > 0:
                if dn <= r["stop"]:
                    res = 0
                    break
                if up >= r["tgt"]:
                    res = 1
                    break
            else:
                if up >= r["stop"]:
                    res = 0
                    break
                if dn <= r["tgt"]:
                    res = 1
                    break
        if res == 1:
            hit += 1
        elif res == 0:
            miss += 1
    return hit, miss


# =============================================================================================
# Потоки в ОФЗ
# =============================================================================================
BUCKETS = [(0, 2, "до 2 лет"), (2, 5, "2–5 лет"), (5, 8, "5–8 лет"), (8, 99, "от 8 лет")]


def ofz_history_turnover(days=20):
    cache = load_json("ofz_hist.json", {})
    if DEMO:
        return 40e9
    d = now_msk().date()
    k = 0
    while len([x for x in cache if x < d.isoformat()]) < days and k < 40:
        k += 1
        day = d - dt.timedelta(days=k)
        ds = day.isoformat()
        if day.weekday() >= 5 or ds in cache:
            continue
        rows, start = [], 0
        try:
            while True:
                part = table(iss("history/engines/stock/markets/bonds/boards/TQOB/securities.json", date=ds, start=start, **{"iss.only": "history", "history.columns": "SECID,VALUE"}), "history")
                rows += part
                if len(part) < 100:
                    break
                start += len(part)
        except Exception as e:  # noqa: BLE001
            log("ОФЗ история", ds, e)
            break
        cache[ds] = sum(r["VALUE"] or 0 for r in rows)
    cache = {k: v for k, v in cache.items() if k >= (d - dt.timedelta(days=70)).isoformat()}
    save_json("ofz_hist.json", cache)
    vals = [cache[x] for x in sorted(cache) if cache[x]][-days:]
    return st.mean(vals) if vals else None


def ofz():
    now = now_msk()
    today = now.date().isoformat()
    if DEMO:
        bonds = [dict(SECID=f"SU26{k:03d}RMFS0", NAME=f"ОФЗ 26{k:03d}", VAL=2e9 * (k % 5 + 1), DUR=0.5 + k * 0.7, Y=0.14 + (k % 7) * 0.003) for k in range(18)]
    else:
        js = iss("engines/stock/markets/bonds/boards/TQOB/securities.json", **{"iss.only": "securities,marketdata,marketdata_yields", "securities.columns": "SECID,SHORTNAME,MATDATE",
                                                                              "marketdata.columns": "SECID,VALTODAY,LAST", "marketdata_yields.columns": "SECID,EFFECTIVEYIELD,DURATION"})
        sec = {r["SECID"]: r for r in table(js, "securities")}
        yl = {r["SECID"]: r for r in table(js, "marketdata_yields")}
        bonds = []
        for r in table(js, "marketdata"):
            y = yl.get(r["SECID"]) or {}
            if not r.get("VALTODAY") or not y.get("DURATION"):
                continue
            bonds.append(dict(SECID=r["SECID"], NAME=(sec.get(r["SECID"]) or {}).get("SHORTNAME", r["SECID"]), VAL=r["VALTODAY"], DUR=y["DURATION"] / 365, Y=(y.get("EFFECTIVEYIELD") or 0) / 100))
    agg = {b[2]: dict(vb=0.0, vs=0.0, val=0.0, yv=0.0, yw=0.0) for b in BUCKETS}
    for b in bonds:
        lab = next(x[2] for x in BUCKETS if x[0] <= b["DUR"] < x[1])
        a = agg[lab]
        a["val"] += b["VAL"]
        # доходность — только по классическим ОФЗ-ПД (26xxx/25xxx/24xxx); ИН (52xxx) дают реальную доходность, флоатеры (29xxx) — не по кривой
        if re.match(r"SU2[456]", b["SECID"]) and 0.03 < b["Y"] < 0.40:
            a["yv"] += b["Y"] * b["VAL"]
            a["yw"] += b["VAL"]
        try:
            rows = demo_trades(b["SECID"], None) if DEMO else fetch_trades(b["SECID"], "bonds", None, 40)
        except Exception as e:  # noqa: BLE001
            log("ОФЗ сделки", b["SECID"], e)
            continue
        for x in rows:
            if x.get("BOARDID") not in (None, "TQOB"):
                continue  # только основной режим: сделки РЕПО и переговорные (PSOB и др.) раздували «продажи» в разы больше оборота
            v = x.get("VALUE") or 0
            if x.get("BUYSELL") == "B":
                a["vb"] += v
            elif x.get("BUYSELL") == "S":
                a["vs"] += v
        time.sleep(0.05)
    tot = sum(a["val"] for a in agg.values())
    avg = ofz_history_turnover()
    hist = load_json("ofz_yields.json", {})
    hist[today] = {k: (a["yv"] / a["yw"] if a["yw"] else None) for k, a in agg.items()}
    hist = {k: v for k, v in hist.items() if k >= (now.date() - dt.timedelta(days=40)).isoformat()}
    save_json("ofz_yields.json", hist)
    past = [d for d in sorted(hist) if d < today]
    ref = hist[past[-5]] if len(past) >= 5 else (hist[past[0]] if past else None)
    try:
        rg = index_candles("RGBI", 30)
        rg1 = rg[-1]["c"] / rg[-2]["c"] - 1
        rg5 = rg[-1]["c"] / rg[-6]["c"] - 1
    except Exception as e:  # noqa: BLE001
        log("RGBI", e)
        rg1 = rg5 = None
    lines = []
    for lab in [b[2] for b in BUCKETS]:
        a = agg[lab]
        net = a["vb"] - a["vs"]
        y = hist[today][lab]
        dy = (y - ref[lab]) if ref and ref.get(lab) and y else None
        lines.append(dict(lab=lab, net=net, val=a["val"], y=y, dy=dy, share=(a["vb"] / (a["vb"] + a["vs"])) if a["vb"] + a["vs"] else None))
    longnet = sum(x["net"] for x in lines[2:])
    shortnet = sum(x["net"] for x in lines[:2])
    dylong = st.mean([x["dy"] for x in lines[2:] if x["dy"] is not None]) if any(x["dy"] is not None for x in lines[2:]) else None
    thr = 0.03 * sum(x["val"] for x in lines[2:])
    if abs(longnet) < thr and abs(shortnet) < 0.03 * sum(x["val"] for x in lines[:2]):
        concl = "Потоки сбалансированы — заметного перекоса покупателей или продавцов нет."
    elif longnet > thr and (dylong is None or dylong < 0) and (rg1 is None or rg1 >= -0.001):
        concl = "Деньги идут в длинные ОФЗ, доходности снижаются — рынок закладывает снижение ключевой ставки. Обычно это поддерживает акции роста, девелоперов и банки."
    elif longnet < -thr and (dylong is None or dylong > 0) and (rg1 is None or rg1 <= 0.001):
        concl = "Длинные ОФЗ продают, доходности растут — рынок закладывает более жёсткую политику ЦБ. Для акций это фон осторожности."
    elif shortnet > 0.03 * sum(x["val"] for x in lines[:2]) and longnet <= 0:
        concl = "Покупают в основном короткие выпуски — деньги паркуются без ставки на снижение ключевой."
    else:
        concl = "Потоки смешанные, явного направления нет."
    mins = (now.hour * 60 + now.minute) - 600
    frac = max(0.1, min(1.0, mins / 530))
    xa = f" ({fmt(tot / (avg * frac), 1)}× к обычному обороту {'на это время' if frac < 1 else 'за день'})" if avg else ""
    txt = [f"🏦 <b>Потоки в ОФЗ</b> · {now.strftime('%d.%m')}, {'середина дня' if now.hour < 16 else 'итоги дня'}, обновлено в {now.strftime('%H:%M')} МСК\n<i>🔄 Пост обновляется в течение дня</i>",
           f"<blockquote><b>Вывод:</b> {concl}</blockquote>",
           f"Оборот гособлигаций: <b>{bln(tot)}</b>{xa}." + (f" Индекс RGBI <code>{pc(rg1, 2)}</code> за день, <code>{pc(rg5, 2)}</code> за 5 дней." if rg1 is not None else ""), ""]
    for x in lines:
        if x["val"] < 50e6 and abs(x["net"]) < 5e6:
            continue  # в этом сроке почти не торговали — строка без смысла
        dy = f" · доходность {fmt(x['y'] * 100, 2)}%" + (f" ({'+' if x['dy'] >= 0 else '−'}{fmt(abs(x['dy']) * 1e4, 0)} б.п. за неделю)" if x["dy"] is not None else "") if x["y"] else ""
        flow = "без перекоса" if abs(x["net"]) < 0.01 * x["val"] else f"{'🟢 покупки' if x['net'] > 0 else '🔴 продажи'} <b>{bln(abs(x['net']))}</b>"
        txt.append(f"<b>{x['lab']}</b>: {flow} из {bln(x['val'])}{dy}")
    txt += ["", how_to_read(), "#ОФЗ"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.8), dpi=110, facecolor=BG)
    for a in (a1, a2):
        style(a)
        a.tick_params(labelsize=11)
    labs = [x["lab"] for x in lines]
    a1.bar(labs, [x["net"] / 1e9 for x in lines], color=[UP if x["net"] >= 0 else DN for x in lines], zorder=3)
    a1.set_title("Чистые покупки по рынку, млрд ₽", color="#ffffff", fontsize=13, fontweight="bold", loc="left")
    a1.axhline(0, color="#787b86", linewidth=0.8)
    a2.plot(labs, [(x["y"] or 0) * 100 for x in lines], color=BLUE, marker="o", label="сегодня")
    if ref:
        a2.plot(labs, [((ref.get(lab) or 0) * 100) or None for lab in labs], color=ORANGE, marker="o", linestyle="--", label="неделю назад")
    a2.set_title("Доходность ОФЗ по сроку, %", color="#ffffff", fontsize=13, fontweight="bold", loc="left")
    leg = a2.legend(fontsize=8, facecolor=BG, edgecolor=GRID)
    for t in leg.get_texts():
        t.set_color(FG)
    fig.tight_layout()
    img = os.path.join(OUT, "ofz.png")
    fig.savefig(img, facecolor=BG)
    plt.close(fig)
    PUB.post("\n".join(txt), [(img, "Потоки в ОФЗ и кривая доходности")], live="ofz")
    save_json("ofz_last.json", dict(date=today, concl=concl, tot=tot, x=(tot / avg if avg else None), rg5=rg5, lines=lines))


# =============================================================================================
# Т-Пульс: посты самых читаемых авторов
# =============================================================================================
# Пульс: только сделки сильных трейдеров (по истории заявленных сделок 2019–2026) и лента для подтверждения новостей.
# Отдельные посты «что пишут авторы» убраны: сами по себе мнения из Пульса значения не имеют.
P_BUY = re.compile(r"\b(купил|купила|покупаю|докупил|докупила|докупаю|добрал|добрала|набираю|набрал|беру|взял|взяла|вошел|вошёл|вошла|захожу|открыл лонг|в лонг|лонг|long|подбираю|усреднил)\b", re.I)
P_SELL = re.compile(r"\b(продал|продала|продаю|скинул|скинула|сбросил|зафиксировал|зафиксировала|фиксирую|вышел|вышла|закрыл лонг|шорт|в шорт|short|шортанул|сократил|разгрузил)\b", re.I)
PULSE_WF = "проверка по годам: лучшие авторы прошлых лет давали +0,29% к индексу в 2023, около нуля в 2024–2025 и +0,20% в 2026"


def pulse_stance(tx):
    b, s_ = bool(P_BUY.search(tx)), bool(P_SELL.search(tx))
    return 1 if b and not s_ else -1 if s_ and not b else 0


def pulse_posts(nick, cache, limit=12):
    pid = cache["ids"].get(nick)
    if not pid:
        js = json.loads(http_get(f"https://www.tbank.ru/api/invest-gw/social/v1/profile/nickname/{nick}", timeout=15, tries=1))
        pid = (js.get("payload") or {}).get("id")
        if not pid:
            return []
        cache["ids"][nick] = pid
    js = json.loads(http_get(f"https://www.tbank.ru/api/invest-gw/social/v1/profile/{pid}/post?limit={limit}", timeout=15, tries=1))
    out = []
    for p in (js.get("payload") or {}).get("items", []):
        try:
            when = dt.datetime.fromisoformat((p.get("inserted") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        cont = p.get("content") or {}
        tx = html.unescape(re.sub(r"\s+", " ", cont.get("text") or p.get("text") or "")).strip()
        ins = (cont.get("instruments") or []) + (p.get("instruments") or [])
        tks = sorted({i.get("ticker") for i in ins if i and i.get("ticker") and re.fullmatch(r"[A-Z0-9]{2,7}", i.get("ticker"))})
        out.append(dict(id=p.get("id"), ts=when.timestamp(), tx=tx, tks=tks))
    return out


def share_last(t):
    try:
        js = iss(f"engines/stock/markets/shares/boards/TQBR/securities/{t}.json", **{"iss.only": "marketdata", "marketdata.columns": "SECID,LAST"})
        r = table(js, "marketdata")
        return r[0].get("LAST") if r else None
    except Exception:  # noqa: BLE001
        return None


def pulse_eval(st_):
    """Итог отслеживаемых сделок через 5 торговых сессий: доходность бумаги минус индекс (в сторону сделки)."""
    now = time.time()
    try:
        ix = {b["t"]: b["c"] for b in index_candles("IMOEX", 40)}
    except Exception:  # noqa: BLE001
        return
    for d in st_["deals"]:
        if d.get("res") is not None or now - d["ts"] < 8 * 86400:
            continue
        try:
            B = share_candles(d["t"], 40)
        except Exception:  # noqa: BLE001
            continue
        day = dt.datetime.fromtimestamp(d["ts"], MSKTZ).date().isoformat()
        j = next((k for k, b in enumerate(B) if b["t"] > day), None)
        if j is None or j + 5 > len(B):
            continue
        e, x = B[j]["o"], B[j + 4]["c"]
        i0, i1 = ix.get(B[j - 1]["t"]) if j else None, ix.get(B[j + 4]["t"])
        d["res"] = d["side"] * ((x / e - 1) - ((i1 / i0 - 1) if i0 and i1 else 0))
        time.sleep(0.05)


def pulse_deals():
    now = now_msk()
    st_ = load_json("pulse_deals.json", {})
    st_.setdefault("ids", {})
    st_.setdefault("seen", [])
    st_.setdefault("deals", [])
    if st_.get("day") != now.date().isoformat():
        st_["day"], st_["n"] = now.date().isoformat(), 0
    top = {a["nick"]: a for a in DATA.get("pulse_top", [])}
    crowd = {a["nick"]: a for a in DATA["pulse"][:40]}
    feed = [x for x in load_json("pulse_feed.json", []) if time.time() - x["ts"] < 48 * 3600]
    seen = set(st_["seen"])
    new_deals, fails = [], 0
    # API Пульса ограничивает частоту: за запуск — не больше ~14 авторов и 100 секунд, по кругу; при 429 — сразу стоп до следующего запуска
    allp = list(top) + [n for n in list(crowd)[:16] if n not in top]
    cur = st_.get("cursor", 0) % len(allp)
    order = allp[cur:] + allp[:cur]
    t_start, done = time.time(), 0
    for nick in order[:int(os.environ.get("PULSE_PER_RUN", "14"))]:
        if fails >= 2 or time.time() - t_start > 100:
            break
        try:
            posts = pulse_posts(nick, st_)
            fails = 0
            done += 1
        except Exception as e:  # noqa: BLE001
            log("Пульс", nick, e)
            done += 1
            if getattr(e, "code", 0) == 429:
                log("Пульс: лимит запросов API — продолжу в следующий запуск")
                break
            fails += 1
            continue
        for p in posts:
            if p["id"] in seen or time.time() - p["ts"] > 6 * 3600:
                continue
            seen.add(p["id"])
            stance = pulse_stance(p["tx"])
            feed.append(dict(nick=nick, top=nick in top, id=p["id"], ts=p["ts"], tks=p["tks"][:3], st=stance, tx=p["tx"][:300]))
            if nick in top and stance and 1 <= len(p["tks"]) <= 2 and len(p["tx"]) >= 20:
                for t in p["tks"]:
                    if any(d["nick"] == nick and d["t"] == t and d["side"] == stance and p["ts"] - d["ts"] < 86400 for d in st_["deals"]):
                        continue
                    new_deals.append(dict(nick=nick, t=t, side=stance, ts=p["ts"], id=p["id"], tx=p["tx"]))
        time.sleep(2.5)
    st_["cursor"] = cur + done
    st_["seen"] = list(seen)[-3000:]
    save_json("pulse_feed.json", feed)
    try:
        pulse_eval(st_)
    except Exception as e:  # noqa: BLE001
        log("Пульс: оценка сделок", e)
    posted = []
    for d in new_deals:
        price = share_last(d["t"])
        if not price:
            continue  # не акция Мосбиржи (фьючерс, фонд, облигация) — не отслеживаем
        d["price"] = price
        posted.append(d)
    room = int(os.environ.get("PULSE_DEALS_PER_DAY", "8")) - st_["n"]
    posted = posted[:max(0, min(3, room))]
    for d in posted:
        st_["deals"].append({k: d[k] for k in ("nick", "t", "side", "ts", "id", "price")})
    st_["deals"] = st_["deals"][-600:]
    save_json("pulse_deals.json", st_)
    log("Пульс: постов в ленте", len(feed), "| новых сделок сильных трейдеров", len(new_deals), "| к публикации", len(posted))
    if not posted:
        return
    if os.environ.get("PULSE_POSTS", "0") != "1":
        # Пульс — только подтверждение новостей (строка «👥 Пульс за 12 ч» в посте новости), отдельных постов нет
        log("Пульс: отдельные посты выключены (PULSE_POSTS=0) — сделки идут в ленту подтверждения новостей")
        return
    st_["n"] += 1
    save_json("pulse_deals.json", st_)
    cls = all_clusters()
    txt = ["🧑‍💼 <b>Сделки сильных трейдеров Пульса</b>", ""]
    for d in posted:
        a = top[d["nick"]]
        live = [x["res"] for x in st_["deals"] if x["nick"] == d["nick"] and x.get("res") is not None]
        link = f"https://www.tbank.ru/invest/social/profile/{d['nick']}/{d['id']}/"
        txt.append(f"👤 <b>{esc(d['nick'])}</b> — {'🟢 ПОКУПКА' if d['side'] > 0 else '🔴 ПРОДАЖА/ШОРТ'} <b>${d['t']}</b> (цена сейчас {fmt(d['price'], 2)})")
        txt.append(f"История его заявленных сделок 2019–2026: {a['n']}, в среднем {pc(a['mean'] / 100, 2)} к индексу за 5 дней, в плюс {pc(a['hit'], 0, False)}"
                   + (f"; доходность портфеля за 12 мес. {fmt(a['y12'], 0)}%" if a.get("y12") is not None else "") + ".")
        if live:
            txt.append(f"С начала отслеживания в канале: {len(live)} сделок, в среднем {pc(st.mean(live), 2)} к индексу за 5 дней.")
        txt.append(f"«{esc(cut(d['tx'], 220))}» — <a href=\"{link}\">пост в Пульсе</a>")
        nn = [c for c in news_pick(cls, time.time() - 36 * 3600, tks={d['t']}, min_pct=0.3, k=1)]
        if nn:
            c = nn[0]
            agree = ((c.get("wt") or {}).get("dir") or (1 if (c.get("score") or 0) > 0 else -1)) == d["side"]
            txt.append(("✅ Совпадает с новостным фоном: " if agree else "⚠️ Против новостного фона: ") + news_line(c, short=True))
        else:
            txt.append("Свежих весомых новостей по бумаге нет — сделка по собственной идее автора.")
        txt.append("")
    txt.append(f"<i>Авторы отобраны по истории заявленных сделок (не меньше 40, устойчивый перевес к индексу). Преимущество даже у лучших невелико, а {PULSE_WF}. Это наблюдение за чужими сделками, не сигнал.</i>")
    txt.append("#пульс_сделки " + " ".join("#" + t for t in dict.fromkeys(d["t"] for d in posted)))
    PUB.post("\n".join(txt))


def pulse():
    """Старый режим «что пишут авторы» отключён: Пульс используется только для сделок сильных трейдеров и подтверждения новостей."""
    pulse_deals()


# =============================================================================================
# Утренний торговый план (совмещённая стратегия v2)
# =============================================================================================
NOISE = re.compile(r"\(про [^)]{2,25}\):|Идея:|Тикер:|Стоп-приказ|Потенциал идеи|Друзья,|Топ[- ]\d*\s*(дивидендн|акци)|подписывайтесь|#реклама|розыгрыш|Краткий обзор", re.I)
TRUST_CH = {"interfaxonline", "ifax_go", "if_market_news", "tass_agency", "rbc_news", "ria_novosti", "prime1", "vedomosti", "kommersant", "bloomberg_ru", "forbesrussia",
            "thebell_io", "cbrstocks", "centralbank_russia", "minfin", "MID_Russia", "kremlinnews"}
COMPANY_TY = re.compile(r"(div_|buyback|spo|ipo|results_|guidance_|analyst_|legal|production_|mna|delist_|management)")
RU_REL = re.compile(r"росси|рф\b|рубл|мосбирж|imoex|ммвб|офз|банк росси|\bцб\b|минфин|набиуллин|силуанов|кремл|путин|песков|госдум|правительств|санкц|нефт|brent|urals|газ|опек|экспорт|импорт|украин|переговор|бюджет|ндпи|налог|инфляц|ключев", re.I)
DIGEST = re.compile(r"Что произошло за|Главное за|Главное к|Главные новости|к этому часу|новости к утру|новости к вечеру|Итоги дня|Итоги недели|дайджест|#крипто|биткоин|#BTC|Утренний обзор|Вечерний обзор|Краткий обзор|обзор (экономической |новостной )?повестки|к вечеру индекс", re.I)


SECTORS = [("oil", "🛢 Нефть и газ", "MOEXOG"), ("bank", "🏦 Банки и финансы", "MOEXFN"), ("metal", "⛏ Металлы и добыча", "MOEXMM"), ("tech", "💻 IT и связь", "MOEXIT"),
           ("retail", "🛒 Ритейл", "MOEXCN"), ("consumer", "🧺 Потребительский сектор", "MOEXCN"), ("power", "⚡️ Электроэнергетика", "MOEXEU"), ("chem", "🧪 Химия и удобрения", "MOEXCH"),
           ("devel", "🏗 Девелоперы", "MOEXRE"), ("transport", "✈️ Транспорт", "MOEXTN"), ("industry", "🏭 Машиностроение", None), ("other", "📦 Прочие компании", None)]


def prev_wd(d):
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def next_wd(d):
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def ddmm(s):
    return f"{s[8:10]}.{s[5:7]}" if s else "—"


def clean_tx(tx):
    s = html.unescape(html.unescape(tx or ""))
    s = re.sub(r"https?://\S+|#\w+|@\w+", " ", s)
    s = re.sub(r"Канал [^.\n]{0,60}«Максе?»\.?", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^[^\wА-Яа-яЁё$«\"(]+", "", s)


def cut(s, n):
    if len(s) <= n:
        return s
    s = s[:n]
    return s[:s.rfind(" ")].rstrip(",;:—- ") + "…"


def eq_set():
    if DEMO:
        return None
    try:
        js = iss("engines/stock/markets/shares/boards/TQBR/securities.json", **{"iss.only": "securities", "securities.columns": "SECID,INSTRID"})
        return {r["SECID"] for r in table(js, "securities") if (r.get("INSTRID") or "EQIN") == "EQIN"}
    except Exception as e:  # noqa: BLE001
        log("список акций", e)
        return None


def hist_day(ds):
    rows, start = [], 0
    while True:
        part = table(iss("history/engines/stock/markets/shares/boards/TQBR/securities.json", date=ds, start=start,
                         **{"iss.only": "history", "history.columns": "SECID,SHORTNAME,CLOSE,LEGALCLOSEPRICE,VALUE"}), "history")
        rows += part
        if len(part) < 100:
            break
        start += len(part)
    return rows


def day_moves():
    """Изменения цен акций за последний завершённый торговый день (история торгов Мосбиржи): (дата, {SECID: dict(name, close, chg, val)})."""
    if DEMO:
        import random
        rnd = random.Random(7)
        return prev_wd(now_msk().date()).isoformat(), {t: dict(name=t, close=100, chg=rnd.uniform(-0.05, 0.05), val=rnd.uniform(5e6, 5e9)) for t in sec_of_cache()}
    d = now_msk().date()
    found, k = [], 0
    while len(found) < 2 and k < 12:
        k += 1
        day = d - dt.timedelta(days=k)
        if day.weekday() >= 5:
            continue
        try:
            rows = [r for r in hist_day(day.isoformat()) if r.get("CLOSE") or r.get("LEGALCLOSEPRICE")]
        except Exception as e:  # noqa: BLE001
            log("история торгов", day, e)
            rows = []
        if rows:
            found.append((day.isoformat(), rows))
    if len(found) < 2:
        return None, {}
    (d1, r1), (_, r0) = found
    eq = eq_set()
    prev = {r["SECID"]: r.get("CLOSE") or r.get("LEGALCLOSEPRICE") for r in r0}
    out = {}
    for r in r1:
        c, p = r.get("CLOSE") or r.get("LEGALCLOSEPRICE"), prev.get(r["SECID"])
        if c and p and (eq is None or r["SECID"] in eq):
            out[r["SECID"]] = dict(name=r.get("SHORTNAME") or r["SECID"], close=c, chg=c / p - 1, val=r.get("VALUE") or 0)
    return d1, out


def all_clusters():
    S = load_json("newsbot.json", {}, NEWS_STATE)
    return list((S.get("cl") or {}).values())


def cl_weight(c):
    w = c.get("wt")
    return w["W"] if w else abs(c.get("score") or 0) * 1e-3


def news_pick(cls, since, tks=None, market=False, min_pct=0.5, k=5):
    """Новости после since (unix), по тикерам tks или рыночные; самые весомые первыми. Дайджесты и слабые типы — мимо."""
    out, seen = [], set()
    for c in sorted(cls, key=lambda c: -cl_weight(c)):
        if c.get("ts", 0) < since:
            continue
        tk, ty = ((c.get("key") or "|") + "|").split("|")[:2]
        if tk == "РЫНОК" and COMPANY_TY.match(ty or ""):
            continue  # тип события компании у «рынка в целом» — ошибка разметки (чужое IPO, обыски в НБУ)
        if market and tk != "РЫНОК":
            continue
        if tks is not None and tk not in tks:
            continue
        w = c.get("wt")
        if w and w["pct"] < min_pct:
            continue
        if not w and abs(c.get("score") or 0) < 0.3:
            continue
        raw = clean_tx(c["first"].get("tx"))
        if DIGEST.search(raw) or NOISE.search(c["first"].get("tx") or "") or c["first"]["link"] in seen or len(raw) < 25:
            continue
        if tk == "РЫНОК" and not (c.get("official") or (not c.get("rumorOnly") and any(ch in TRUST_CH for ch in (c.get("srcs") or {}))) or (w and w["pct"] >= 0.9)):
            continue  # «рынок» — только из надёжных источников
        seen.add(c["first"]["link"])
        out.append(c)
        if len(out) >= k:
            break
    return out


def news_line(c, n=230, short=False):
    tk = (c.get("key") or "|").split("|")[0]
    w = c.get("wt") or {}
    s = c.get("score") or 0
    mark = "🟢" if s > 0 else "🔴" if s < 0 else "⚪"
    head = cut(clean_tx(c["first"].get("tx")), 110 if short else n)
    who = "$" + tk if tk != "РЫНОК" else "Рынок"
    tm = dt.datetime.fromtimestamp(c.get("ts", 0), MSKTZ).strftime("%H:%M")
    rumor = " <i>(не подтверждено)</i>" if c.get("rumorOnly") else ""
    line = f"{mark} <b>{who}</b>, {tm}: {esc(head)}{rumor} — <a href=\"{c['first']['link']}\">{esc(src_name(c['first'].get('ch', '')))}</a>"
    if w and not short:
        lab = "высокий" if w["pct"] >= 0.9 else "повышенный" if w["pct"] >= 0.75 else "средний"
        line += (f". Вес: {lab} (сильнее {round(w['pct'] * 100)}% новостей); по истории реакция {'индекса' if w.get('lvl') == 'рынок' else 'бумаги к индексу'} за 5 дней "
                 f"≈ {pc(w['E'] * (w.get('dir') or 1), 1)} (случаев: {w['n']})")
    return line


def src_name(ch):
    return {"rbc_news": "РБК", "interfaxonline": "Интерфакс", "tass_agency": "ТАСС", "ria_novosti": "РИА Новости", "markettwits": "MarketTwits", "kommersant": "Коммерсантъ",
            "vedomosti": "Ведомости", "if_market_news": "ИФ Market News", "bloomberg_ru": "Bloomberg (рус.)", "forbesrussia": "Forbes", "thebell_io": "The Bell"}.get(ch, ch)


def run20(t, ixd):
    """Доходность бумаги к индексу за 20 торговых дней."""
    try:
        B = share_candles(t, 45)
    except Exception:  # noqa: BLE001
        return None
    if len(B) < 22:
        return None
    a, b = B[-21], B[-1]
    i0, i1 = ixd.get(a["t"]), ixd.get(b["t"])
    return (b["c"] / a["c"] - 1) - ((i1 / i0 - 1) if i0 and i1 else 0)


def div_item(r, s, mkt_share, cur, cls, today):
    t = r["t"]
    lb = r.get("last_buy")
    dleft = (dt.date.fromisoformat(lb) - today).days if lb else None
    head = (f"💰 <b>${t}</b> ({esc(r['name'])}) — {esc(r['period'])}: {fmt(r['amount'], 2)} ₽ на акцию, доходность {pc(r['yld'], 1, False)}. "
            f"Последний день покупки {ddmm(lb)}" + (f" ({'сегодня' if dleft == 0 else 'завтра' if dleft == 1 else 'через ' + str(dleft) + ' дн.'})" if dleft is not None else "") +
            f", закрытие реестра {ddmm(r['reg'])}.")
    parts = [head]
    share = (s or {}).get("share")
    if s:
        parts.append("История: " + divs_describe(t, s) + ".")
    else:
        parts.append(f"История отсечек по бумаге короткая; в среднем по рынку гэп в день отсечки ≈ {round((mkt_share or 0.8) * 100)}% дивиденда.")
    g = (share if share is not None else (mkt_share or 0.8)) * r["yld"]
    parts.append(f"Ожидаемый гэп в день отсечки ≈ {pc(-abs(g), 1)} цены.")
    if cur is not None:
        txt = f"Сейчас за 20 дней бумага {pc(cur, 1)} к индексу"
        if s and cur > s["run"] + 0.03:
            txt += " — сильнее обычного дивидендного ралли: заметная часть выплаты, вероятно, уже в цене"
        elif s and dleft is not None and dleft <= 25 and cur < s["run"] - 0.02 and s["run_up"] >= 0.55:
            txt += f" — обычного дивидендного ралли пока нет (исторически в {round(s['run_up'] * 100)}% случаев бумага перед отсечкой обгоняла индекс)"
        parts.append(txt + ".")
    nc = [c for c in cls if (c.get("key") or "|").split("|")[0] == t and time.time() - c.get("ts", 0) < 7 * 86400]
    nc = sorted(nc, key=lambda c: -cl_weight(c))[:2]
    if nc:
        parts.append("Новости по бумаге за неделю: " + " ".join(news_line(c, short=True) for c in nc))
        neg = [c for c in nc if (c.get("score") or 0) < 0 and (c.get("wt") or {}).get("pct", 0) >= 0.7]
        pos = [c for c in nc if (c.get("score") or 0) > 0]
        if neg:
            parts.append("⚠️ <b>Вывод:</b> весомый негативный новостной фон может перевесить дивидендный фактор — дивиденд не защищает от падения цены.")
        elif pos:
            parts.append("<b>Вывод:</b> новости поддерживают бумагу вместе с дивидендным фактором.")
    else:
        parts.append("Значимых новостей по бумаге за неделю не было — в цене в основном дивидендный фактор.")
    return " ".join(parts)


def divs_describe(t, s):
    import divs
    return divs.describe(t, s)


def sector_trend(code):
    if not code:
        return None
    try:
        B = index_candles(code, 160)
    except Exception as e:  # noqa: BLE001
        log("отраслевой индекс", code, e)
        return None
    if len(B) < 60:
        return None
    c = [b["c"] for b in B]
    e20, e50 = ema(c, 20)[-1], ema(c, 50)[-1]
    tr = 1 if c[-1] > e50 and e20 > e50 else -1 if c[-1] < e50 and e20 < e50 else 0
    return dict(last=c[-1], d1=c[-1] / c[-2] - 1, m1=c[-1] / c[-22] - 1, tr=tr, day=B[-1]["t"])


def morning():
    now = now_msk()
    today = now.date()
    sig = load_json("signals.json", None, NEWS_STATE)
    fam = DATA["fam"]
    secs = sec_of_cache()
    imgs = []
    # индекс и режим
    ixd = {}
    try:
        ix = index_candles("IMOEX", 420)
        ixd = {b["t"]: b["c"] for b in ix}
        c = [b["c"] for b in ix]
        e50, e200 = ema(c, 50)[-1], ema(c, 200)[-1]
        ch1 = ix[-1]["c"] / ix[-2]["c"] - 1
        imgs.append((candle_chart(ix, os.path.join(OUT, "m_imoex.png"), "Индекс МосБиржи · режим рынка по EMA 50", emas=((50, BLUE, "EMA 50"), (200, ORANGE, "EMA 200")), n=160, dec=0),
                     "Индекс МосБиржи и средние: режим рынка определяется по EMA 50"))
        ixt = f"IMOEX {fmt(ix[-1]['c'], 0)} ({pc(ch1, 2)} за последний день), {'выше' if ix[-1]['c'] > e50 else 'ниже'} EMA 50, {'выше' if ix[-1]['c'] > e200 else 'ниже'} EMA 200."
    except Exception as e:  # noqa: BLE001
        log("IMOEX", e)
        ixt = ""
    ofzl = load_json("ofz_last.json", None)
    fl = load_json("flows_last.json", None)
    since_d = prev_wd(today)
    since = dt.datetime(since_d.year, since_d.month, since_d.day, 19, 0, tzinfo=MSKTZ).timestamp()
    cls = all_clusters()
    dday, moves = day_moves()
    # дивиденды
    try:
        import divs
        upc = divs.upcoming(60)
        dst = divs.history_stats()
    except Exception as e:  # noqa: BLE001
        log("дивиденды", e)
        upc, dst = [], {}
    shares_all = [v["share"] for v in dst.values() if v.get("share") is not None]
    mkt_share = st.median(shares_all) if shares_all else None
    # сигналы стратегии
    plan = []
    if sig:
        for q in sig.get("pend", []):
            plan.append(dict(q, kind="signal"))
        held = {q["t"] for q in sig.get("pend", [])}
        for w in sig.get("watch", []):
            if w["t"] in held or (w["kind"] == "mom" and (w.get("ext") or 0) > 0.10):
                continue
            plan.append(dict(w, fam=w["kind"], kind="watch"))

    def plan_line(p):
        return plan_line0(p) + scen_short(p)

    def scen_short(p):
        if not p.get("entry") or not p.get("stop"):
            return ""
        sc = trade_scen(p["t"], p["side"], p["entry"], p["stop"], p.get("take"), 20 if p.get("fam") == "mom" else 10)
        if not sc:
            return ""
        return (f". 🎲 Модель: цель раньше стопа {sc['p_take']:.0%}, стоп раньше цели {sc['p_stop']:.0%}, по времени {sc['p_time']:.0%}"
                + (" (стоп меньше дневного хода — легко выбить)" if sc.get("stop_atr") and sc["stop_atr"] < 1 else ""))

    def plan_line0(p):
        f = fam.get(p["fam"], {})
        if p["kind"] == "signal":
            return (f"🎯 <b>${p['t']}</b> — сигнал на открытие: {'покупка' if p['side'] > 0 else 'шорт'} ({FAM_RU.get(p['fam'], p['fam'])}) ≈ {fmt(p['entry'], 2)}, стоп {fmt(p['stop'], 2)}"
                    + (f", цель {fmt(p['take'], 2)}" if p.get("take") else "") + (f". На истории: прибыльных {pc(f['win'], 0, False)}, средний результат {fmt(f['avgR'], 2)}R" if f else ""))
        return (f"👀 <b>${p['t']}</b> — {'лонг' if p['side'] > 0 else 'шорт'} при условии: {esc(p.get('note') or '')}" + (f"; стоп {fmt(p['stop'], 2)}" if p.get("stop") else "")
                + (f", цель {fmt(p['take'], 2)}" if p.get("take") else ""))

    title = f"Торговый план на {today.strftime('%d.%m.%Y')}, {wd_ru(today)}"
    B = []
    B.append(("p", f"<i>План собран в {now.strftime('%H:%M')} МСК: режим рынка, ночные новости с весом по исторической реакции рынка, сигналы стратегии, дивиденды и разбор по секторам. "
                   f"Цены — за торги {ddmm(dday) if dday else 'прошлого дня'}.</i>"))
    # рынок в целом
    B.append(("h3", "📊 Рынок в целом"))
    mk = []
    if sig:
        mk.append(f"<b>Режим рынка:</b> {esc(sig.get('regimeText', ''))}. {ixt}")
    elif ixt:
        mk.append("<b>Индекс:</b> " + ixt)
    if ofzl:
        mk.append(f"<b>ОФЗ:</b> {esc(ofzl['concl'])}")
    if fl:
        bs = ", ".join(f"${t} {pc(v, 0)}" for t, v in fl["buyers"][:3])
        ss = ", ".join(f"${t} {pc(v, 0)}" for t, v in fl["sellers"][:3])
        if bs or ss:
            mk.append("<b>Перевес в конце прошлой сессии</b> (доля покупок минус продаж по рынку): " + "; ".join(x for x in (("покупатели — " + bs) if bs else "", ("продавцы — " + ss) if ss else "") if x))
    cbr = next_cbr()
    if cbr:
        dd = (dt.date.fromisoformat(cbr) - today).days
        mk.append(f"<b>Заседание Банка России по ставке:</b> {ddmm(cbr)}" + (" — сегодня" if dd == 0 else f" (через {dd} дн.)" if dd <= 14 else ""))
    if today.weekday() == 2:
        mk.append("<b>Среда:</b> аукционы Минфина по ОФЗ — спрос на них влияет на доходности и банковский сектор.")
    B.append(("ul", mk))
    top = news_pick(cls, since, k=6)
    B.append(("h3", "🌙 Главное за ночь"))
    B.append(("ul", [news_line(c) for c in top] if top else ["Весомых новостей за ночь не было — рынок откроется на старых драйверах."]))
    pdl = [d for d in (load_json("pulse_deals.json", {}, os.path.join(NEWS_STATE, "channel")).get("deals") or []) if d["ts"] >= since]
    if pdl:
        ptop = {a["nick"]: a for a in DATA.get("pulse_top", [])}
        B.append(("h4", "🧑‍💼 Сделки сильных трейдеров Пульса за ночь и вчера"))
        B.append(("ul", [f"<b>{esc(d['nick'])}</b>: {'покупка' if d['side'] > 0 else 'продажа/шорт'} <b>${d['t']}</b> по ≈{fmt(d['price'], 2)}"
                         + (f" (история: {ptop[d['nick']]['n']} сделок, в среднем {pc(ptop[d['nick']]['mean'] / 100, 2)} к индексу за 5 дн.)" if d["nick"] in ptop else "") for d in pdl[-8:]]))
    mnews = news_pick(cls, since, market=True, k=4)
    mnews = [c for c in mnews if c not in top]
    if mnews:
        B.append(("h4", "Новости для всего рынка"))
        B.append(("ul", [news_line(c) for c in mnews]))
    # прогноз аномалий на день
    ap = None
    try:
        ap = anom_today_any()
    except Exception as e:  # noqa: BLE001
        log("прогноз аномалий", e)
    arows = {k: v for k, v in ((ap or {}).get("rows") or {}).items() if v.get("p_y") is not None}
    atop = sorted(arows, key=lambda t: -(arows[t].get("p_y") or 0))[:12]
    if atop:
        base = sum(arows[t].get("p_y") or 0 for t in arows) / max(1, len(arows))
        B.append(("h3", "🔮 Вероятность аномалий сегодня"))
        B.append(("p", f"<i>Модель оценивает шанс, что бумага сегодня резко отклонится от индекса (порог эшелона, 4–8%) или наберёт оборот в 4–6 раз выше обычного. "
                       f"В среднем по {len(arows)} бумагам — {pc(base, 1, False)}. Обучена на 2013–2026 и проверена на годах, которых не видела: у 5% бумаг с самым высоким прогнозом аномалия случалась в 8 раз чаще среднего. "
                       + anom_dir_note("daily_dir", "утром") + "</i>"))
        B.append(("ul", [f"<b>${t}</b> — <b>{arows[t]['p_y']:.0%}</b> (ценовая {arows[t].get('p_y_px') or 0:.0%}, {anom_dir_word(arows[t])})"
                         + (": " + "; ".join(arows[t].get("why") or []) if arows[t].get("why") else "") for t in atop]))
    # дивидендный календарь
    if upc:
        B.append(("h3", "💰 Дивидендный календарь (объявленные выплаты, 60 дней)"))
        B.append(("ul", [f"<b>${r['t']}</b> — {fmt(r['amount'], 2)} ₽ ({pc(r['yld'], 1, False)}), последний день покупки {ddmm(r.get('last_buy'))}, реестр {ddmm(r['reg'])}"
                         for r in sorted(upc, key=lambda r: r.get("last_buy") or r["reg"])[:20]]))
        B.append(("p", "<i>Разбор каждой выплаты — с исторической реакцией бумаги на отсечки и новостями по ней — в разделе её сектора ниже.</i>"))
    # сектора
    by_sec = {}
    for t in set(moves) | {p["t"] for p in plan} | {r["t"] for r in upc}:
        by_sec.setdefault(secs.get(t, "other"), set()).add(t)
    sect_out = []
    for key, name, code in SECTORS:
        tks = by_sec.get(key, set())
        if key == "other":
            tks = {t for t in tks if (moves.get(t) or {}).get("val", 0) >= 50e6 or t in {p["t"] for p in plan} or t in {r["t"] for r in upc}}
        if not tks and not code:
            continue
        blocks, score = [], 0
        tr = sector_trend(code)
        if tr:
            blocks.append(("p", f"Отраслевой индекс {code}: {fmt(tr['last'], 0)} ({pc(tr['d1'], 1)} за день, {pc(tr['m1'], 1)} за месяц) — "
                                f"{ {1: 'восходящий тренд (выше EMA 20 и EMA 50)', -1: 'нисходящий тренд (ниже EMA 20 и EMA 50)', 0: 'без выраженного тренда'}[tr['tr']] }."))
        mv = sorted([(t, moves[t]) for t in tks if t in moves and moves[t]["val"] >= 10e6], key=lambda x: -x[1]["chg"])
        if mv:
            ups = [f"${t} {pc(m['chg'], 1)}" for t, m in mv[:3] if m["chg"] > 0]
            dns = [f"${t} {pc(m['chg'], 1)}" for t, m in mv[::-1][:3] if m["chg"] < 0]
            vsum = sum(m["val"] for _, m in mv)
            blocks.append(("p", "Прошлый день: " + ("лидеры — " + ", ".join(ups) if ups else "роста не было") + "; " + ("аутсайдеры — " + ", ".join(dns) if dns else "падения не было") + f". Оборот сектора {bln(vsum)}."))
        nn = news_pick(cls, since, tks=tks, k=4)
        if nn:
            blocks += [("h4", "Новости за ночь"), ("ul", [news_line(c) for c in nn])]
            score += 3 * len(nn)
        pl = [p for p in plan if p["t"] in tks]
        if pl:
            blocks += [("h4", "Сигналы и сетапы стратегии"), ("ul", [plan_line(p) for p in pl])]
            score += 4 * len(pl)
        dv = [r for r in upc if r["t"] in tks]
        if dv:
            items = []
            for r in sorted(dv, key=lambda r: r.get("last_buy") or r["reg"])[:4]:
                items.append(div_item(r, dst.get(r["t"]), mkt_share, run20(r["t"], ixd) if ixd else None, cls, today))
            blocks += [("h4", "Дивиденды"), ("ul", items)]
            score += 3 * len(dv)
        # фокус сектора: какие бумаги и почему
        focus = {}
        for p in pl:
            focus.setdefault(p["t"], []).append("сигнал стратегии" if p["kind"] == "signal" else "сетап стратегии")
        for t_ in tks:
            pa = (arows.get(t_) or {}).get("p_y") or 0
            if pa >= 0.15:
                focus.setdefault(t_, []).append(f"вероятность аномалии {pa:.0%}")
        for t_ in tks:
            if (arows.get(t_) or {}).get("reg_n"):
                focus.setdefault(t_, []).append("региональный инцидент в регионе активов")
        for d in pdl:
            if d["t"] in tks:
                focus.setdefault(d["t"], []).append(f"сделка сильного трейдера Пульса ({'покупка' if d['side'] > 0 else 'продажа'})")
        for c in nn:
            focus.setdefault(c["key"].split("|")[0], []).append("весомая новость, по истории реакция обычно " + ("вверх" if ((c.get("wt") or {}).get("dir") or (c.get("score") or 0)) > 0 else "вниз"))
        for r in dv:
            if r.get("last_buy") and 0 <= (dt.date.fromisoformat(r["last_buy"]) - today).days <= 10:
                focus.setdefault(r["t"], []).append("скоро отсечка")
        for t, m in mv[:2] + mv[-2:]:
            if abs(m["chg"]) >= 0.04:
                focus.setdefault(t, []).append(f"сильное движение {pc(m['chg'], 1)}")
        if focus:
            blocks.append(("p", "🔎 <b>Фокус сектора сегодня:</b> " + "; ".join(f"${t} — {', '.join(dict.fromkeys(v))}" for t, v in sorted(focus.items(), key=lambda x: -len(x[1]))[:5]) + "."))
            score += len(focus)
        if len(blocks) <= 1 and not focus:
            if not tr:
                continue
        score += (sum(m["val"] for _, m in mv) / 1e11) if mv else 0
        sect_out.append((score - (100 if key == "other" else 0), name, blocks))
    sect_out.sort(key=lambda x: -x[0])
    if sect_out:
        B.append(("hr",))
        B.append(("h3", "🧭 Разбор по секторам"))
        B.append(("p", "<i>Сектора — по убыванию количества событий: сигналы, ночные новости, дивиденды, сильные движения.</i>"))
        for _, name, blocks in sect_out:
            B.append(("h3", name))
            B += blocks
    if sig and sig.get("open"):
        B.append(("hr",))
        B.append(("h3", "📂 Модельный портфель"))
        it = [f"${p['t']} {'лонг' if p['side'] > 0 else 'шорт'} с {ddmm(p['d0'])} по {fmt(p['entry'], 2)} · сейчас {pc(p['pnl'], 1)} · стоп {fmt(p['stop'], 2)}" for p in sig["open"]]
        eq = sig.get("equity") or 0
        it.append(f"Модельный счёт: {fmt(eq, 0)} ₽ ({pc(eq / sig['capital'] - 1, 1)} с {ddmm(sig['start'])}.{sig['start'][:4]}), сделок {sig['trades']}, прибыльных {sig['wins']}.")
        B.append(("ul", it))
    B.append(("hr",))
    B.append(("h3", "Как читать план"))
    B.append(("ul", [f"Стратегия «{esc((sig or {}).get('strategy', 'совмещённая v2'))}»: риск 1% на сделку, сигналы — по закрытию прошлого дня, вход на открытии.",
                     "Вес новости — насколько сильно рынок исторически реагировал на такие новости (2017–2026): процентиль среди всех типов, средняя реакция за 5 дней и число случаев.",
                     "Дивиденды: «гэп» — на сколько цена снижалась в день отсечки (в долях дивиденда); «закрытие гэпа» — за сколько дней цена возвращалась к уровню до отсечки; ралли — как бумага обгоняла индекс за 20 дней до отсечки.",
                     "Только публичные данные: Мосбиржа, dohod.ru, открытые Telegram-каналы. Статистика прошлого не гарантирует будущих результатов.",
                     DISCLAIMER_TXT]))
    # анонс в Telegram
    tz = [f"☀️ <b>{title}</b>", ""]
    if sig:
        tz.append(f"<b>Режим рынка:</b> {esc(sig.get('regimeText', ''))}. {ixt}")
    elif ixt:
        tz.append(ixt)
    if top:
        tz += ["", "🌙 <b>Главное за ночь:</b>"] + [news_line(c, short=True) for c in top[:3]]
    if sig and sig.get("pend"):
        tz += ["", "🎯 <b>Сигналы на открытие:</b> " + ", ".join(f"${q['t']} {'покупка' if q['side'] > 0 else 'шорт'}" for q in sig["pend"])]
    near = [r for r in upc if r.get("last_buy") and 0 <= (dt.date.fromisoformat(r["last_buy"]) - today).days <= 14]
    if near:
        tz += ["💰 <b>Скоро отсечки:</b> " + ", ".join(f"${r['t']} ({pc(r['yld'], 1, False)}, купить до {ddmm(r['last_buy'])})" for r in sorted(near, key=lambda r: r["last_buy"])[:4])]
    if atop:
        tz += ["🔮 <b>Вероятные аномалии:</b> " + ", ".join(f"${t} {arows[t]['p_y']:.0%} " + anom_dir_word(arows[t]).split(' (')[0] for t in atop[:5])]
    if sect_out:
        tz += ["🧭 <b>В фокусе секторов:</b> " + ", ".join(n.split(" ", 1)[1] for _, n, _ in [x for x in sect_out if not x[1].startswith("📦")][:4])]
    tz += ["", "{LINK}", "#план_дня"]
    # графики сделок — альбомом к анонсу
    for k, p in enumerate(plan[:7]):
        try:
            bars = share_candles(p["t"], 300)
        except Exception as e:  # noqa: BLE001
            log("свечи", p["t"], e)
            continue
        side = p["side"]
        lines = [(p.get("entry"), BLUE, "Вход" if p["kind"] == "signal" else "Триггер", "--"), (p.get("stop"), DN, "Стоп", "-")]
        if p.get("take"):
            lines.append((p["take"], UP, "Цель", "-"))
        emas = {"mom": ((20, BLUE, "EMA 20"), (150, ORANGE, "EMA 150")), "pull": ((20, BLUE, "EMA 20"), (100, ORANGE, "EMA 100"))}.get(p["fam"], ((50, BLUE, "EMA 50"), (200, ORANGE, "EMA 200")))
        f = fam.get(p["fam"], {})
        note = (p.get("note") or "; ".join((p.get("why") or [])[:2])) + (f". Модуль «{FAM_RU.get(p['fam'])}» на истории: прибыльных {pc(f['win'], 0, False)}, средний результат {fmt(f['avgR'], 2)}R, держали в среднем {fmt(f['days'], 0)} дн." if f else "")
        ttl = f"{p['t']} · {FAM_RU.get(p['fam'], p['fam'])} · {'ЛОНГ' if side > 0 else 'ШОРТ'} · {'сигнал на открытие' if p['kind'] == 'signal' else 'ждём условия'}"
        path = candle_chart(bars, os.path.join(OUT, f"m_{k}_{p['t']}.png"), ttl, lines=lines, emas=emas, note=note, mark="СИГНАЛ" if p["kind"] == "signal" else "ЖДЁМ")
        imgs.append((path, f"${p['t']}: {FAM_RU.get(p['fam'], '')}, {'лонг' if side > 0 else 'шорт'}"))
    import article
    # торговый план — обычным постом; отдельная ежедневная статья — обзор одной бумаги (режим stock_review)
    with open(os.path.join(OUT, f"plan_{today.isoformat()}.html"), "w", encoding="utf-8") as f_:  # полная версия плана — только для проверки
        f_.write(f"<meta charset='utf-8'><h1>{html.escape(title)}</h1>" + article.to_html(B))
    PUB.post("\n".join(x for x in tz if x != "{LINK}"), imgs[:1])
    try:
        commod_plan()
    except Exception as e:  # noqa: BLE001
        log("сырьё", e)


COMMOD_MAIN = ["BR", "NG", "TTF", "GOLD", "SILV", "PLT", "PLD", "COPPER", "ALUM", "NICKEL", "WHEAT", "Si", "CNY", "Eu", "ED", "UCNY"]


def commod_state(a, f):
    """Трендовый анализ сырья как для акций: EMA 20/50, 20-дневный канал, ATR, идея по тренду с уровнями."""
    B = fut_continuous(a, 200) if a in FUT_CODE else fut_candles(f["secid"], 200)
    if len(B) < 40:
        return None
    c = [b["c"] for b in B]
    e20, e50 = ema(c, 20)[-1], ema(c, 50)[-1]
    last = f.get("last") or c[-1]
    a14 = atr(B[-15:])
    hi20, lo20 = max(b["h"] for b in B[-21:-1]), min(b["l"] for b in B[-21:-1])
    trend = 1 if last > e50 and e20 > e50 else -1 if last < e50 and e20 < e50 else 0
    r1m = c[-1] / c[-22] - 1 if len(c) > 22 else None
    idea = None
    if trend and a14:
        side = trend
        dist = 1.5 * a14
        idea = dict(side=side, entry=last, stop=last - side * dist, take=last + side * 2 * dist)
    brk = "пробой 20-дневного максимума" if last > hi20 else "пробой 20-дневного минимума" if last < lo20 else None
    return dict(a=a, f=f, last=last, e20=e20, e50=e50, trend=trend, r1m=r1m, atr=a14, hi20=hi20, lo20=lo20, idea=idea, brk=brk)


def commod_plan():
    fr = fut_fronts(COMMOD_MAIN)
    rows = []
    for a in COMMOD_MAIN:
        f = fr.get(a)
        if not f:
            continue
        try:
            x = commod_state(a, f)
        except Exception as e:  # noqa: BLE001
            log("сырьё", a, e)
            x = None
        if x:
            rows.append(x)
        time.sleep(0.1)
    if not rows:
        return
    now = now_msk()
    txt = [f"🛢 <b>Сырьё и валюта: план на {now.strftime('%d.%m')}</b> (фьючерсы Мосбиржи, ближайший контракт)", ""]
    rows = [x for x in rows if x["a"] not in FX] + [x for x in rows if x["a"] in FX]
    fx_head = False
    for x in rows:
        if x["a"] in FX and not fx_head:
            txt += ["", "💱 <b>Валютные пары</b>"]
            fx_head = True
        f = x["f"]
        tr = {1: "🟢 восходящий тренд", -1: "🔴 нисходящий тренд", 0: "⚪ боковик"}[x["trend"]]
        line = f"<b>{esc(f['name'])}</b> ({f['secid']}): {fmt(x['last'], 2)} {f['unit']} · {pc(f['chg'], 1)} за день" + (f" · {pc(x['r1m'], 1)} за месяц" if x["r1m"] is not None else "") + f"\n  {tr}" + (f", {x['brk']}" if x["brk"] else "")
        if x["idea"]:
            i = x["idea"]
            line += f"\n  Идея по тренду: {'ЛОНГ' if i['side'] > 0 else 'ШОРТ'} от {fmt(i['entry'], 2)}, стоп {fmt(i['stop'], 2)}, тейк {fmt(i['take'], 2)}"
        rel = FUT[x["a"]][2]
        if rel and x["trend"]:
            if x["a"] in FX:
                line += f"\n  {'Рубль слабеет' if x['trend'] > 0 else 'Рубль крепнет'} — {'плюс' if x['trend'] > 0 else 'минус'} для экспортёров: {' '.join('$' + r for r in rel[:4])}"
            else:
                line += f"\n  Для акций: {' '.join('$' + r for r in rel[:4])} — {'попутный ветер' if x['trend'] > 0 else 'встречный ветер'}"
        if x["trend"]:
            try:
                lt = level_target(x["f"]["secid"], x["trend"], kind="fut", ndir=0, chg=x["f"].get("chg") or 0)
                if lt:
                    d = dec_of(lt["last"])
                    line += f"\n  🎯 Предположительный уровень: <code>{fmt(lt['tgt'], d)}</code> ({pc(lt['move'], 1)}) — {esc(lt['why'])}; отмена за <code>{fmt(lt['stop'], d)}</code>"
            except Exception as e:  # noqa: BLE001
                log("уровень", x["a"], e)
        txt.append(line)
    txt += ["", "<i>Тренд — по EMA 20/50 дневных свечей склеенных контрактов; стоп — 1,5 дневного ATR, тейк — 2 риска. Предположительный уровень — ближайший значимый уровень графика по тренду (оценка, не прогноз). "
            "Фьючерсы — плечевой инструмент: гарантийное обеспечение и вариационная маржа. Цены Si и Eu — в рублях за 1000 единиц валюты.</i>", "#сырьё #валюта #фьючерсы"]
    imgs = []
    for x in sorted(rows, key=lambda x: -(x["f"].get("val") or 0))[:4]:
        try:
            bars = tf_bars(x["f"]["secid"], "4h", "fut")
            lines = []
            if x["idea"]:
                i = x["idea"]
                lines = [(i["entry"], BLUE, "Вход", "--"), (i["stop"], DN, "Стоп", "-"), (i["take"], UP, "Тейк", "-")]
            p = candle_chart(bars, os.path.join(OUT, f"c_{x['a']}.png"), f"{x['f']['name']} ({x['f']['secid']}) · свечи 4 часа", lines=lines,
                             emas=((20, BLUE, "EMA 20"), (50, ORANGE, "EMA 50")), n=90)
            if p:
                imgs.append((p, f"{x['f']['name']}: свечи 4 часа"))
        except Exception as e:  # noqa: BLE001
            log("график сырья", x["a"], e)
    PUB.post("\n".join(txt), imgs)


# =============================================================================================
# Итоги дня
# =============================================================================================
SECT_IX = [("MOEXOG", "Нефть и газ"), ("MOEXFN", "Финансы"), ("MOEXMM", "Металлы и добыча"), ("MOEXCN", "Потребительский"), ("MOEXEU", "Электроэнергетика"),
           ("MOEXTL", "Телеком"), ("MOEXCH", "Химия"), ("MOEXRE", "Недвижимость"), ("MOEXTN", "Транспорт"), ("MOEXIT", "IT")]


def evening():
    now = now_msk()
    md = liquid_shares(60)
    try:
        js = iss("engines/stock/markets/index/securities.json", **{"iss.only": "marketdata", "marketdata.columns": "SECID,CURRENTVALUE,LASTCHANGEPRC"})
        ixm = {r["SECID"]: r for r in table(js, "marketdata")}
    except Exception as e:  # noqa: BLE001
        log("индексы", e)
        ixm = {}
    im = ixm.get("IMOEX") or {}
    secs = [(name, (ixm.get(s) or {}).get("LASTCHANGEPRC")) for s, name in SECT_IX if (ixm.get(s) or {}).get("LASTCHANGEPRC") is not None]
    up = sorted(md, key=lambda r: -(r.get("LASTTOPREVPRICE") or 0))[:5]
    dn = sorted(md, key=lambda r: (r.get("LASTTOPREVPRICE") or 0))[:5]
    tot = sum(r["VALTODAY"] or 0 for r in md)
    sig = load_json("signals.json", None, NEWS_STATE)
    S = load_json("newsbot.json", {}, NEWS_STATE)
    txt = [f"🌙 <b>Итоги дня — {now.strftime('%d.%m')}</b>", ""]
    if im:
        txt.append(f"Индекс МосБиржи: {fmt(im.get('CURRENTVALUE'), 0)} ({pc((im.get('LASTCHANGEPRC') or 0) / 100, 2)}). Оборот 60 крупнейших акций: {bln(tot)}.")
    if secs:
        best, worst = max(secs, key=lambda x: x[1]), min(secs, key=lambda x: x[1])
        txt.append(f"Лучший сектор — {best[0]} ({pc(best[1] / 100, 2)}), худший — {worst[0]} ({pc(worst[1] / 100, 2)}).")
    txt += ["", "📈 <b>Лидеры роста</b>: " + ", ".join(f"${r['SECID']} {pc((r.get('LASTTOPREVPRICE') or 0) / 100, 1)}" for r in up),
            "📉 <b>Лидеры падения</b>: " + ", ".join(f"${r['SECID']} {pc((r.get('LASTTOPREVPRICE') or 0) / 100, 1)}" for r in dn)]
    try:
        th_, tm_ = targets_review(5)
        if th_ + tm_ >= 3:
            txt.append(f"🎯 <b>Уровни из потоков за 5 дней</b>: дошли до цели {th_} из {th_ + tm_} (остальные — сработала отмена); незакрытые не считаются.")
    except Exception as e:  # noqa: BLE001
        log("проверка уровней", e)
    fl = load_json("flows_last.json", None)
    if fl and fl.get("time", "")[:10] == now.date().isoformat():
        txt.append("⚖️ <b>Перевес к закрытию</b>: покупатели — " + ", ".join(f"${t}" for t, _ in fl["buyers"][:3]) + "; продавцы — " + ", ".join(f"${t}" for t, _ in fl["sellers"][:3]))
    if sig:
        eq = sig.get("equity") or 0
        txt.append(f"💼 <b>Модельный портфель</b>: {fmt(eq, 0)} ₽ ({pc(eq / sig['capital'] - 1, 1)} с начала), открыто позиций: {len(sig.get('open') or [])}.")
        if sig.get("pend"):
            txt.append("🔔 Сигналы на завтра: " + ", ".join(f"${q['t']} {'покупка' if q['side'] > 0 else 'шорт'}" for q in sig["pend"]))
    pubc = (S.get("pub") or {}).get("count")
    if pubc:
        txt.append(f"📰 Опубликовано новостей и заявлений за день: {pubc}.")
    try:
        im_ = (ixm.get("IMOEX") or {}).get("LASTCHANGEPRC")
        sc = anom_score_day(liquid_shares(400), (im_ or 0) / 100)
        if sc:
            rec, exp, hist = sc
            last = hist["days"][-20:]
            th = sum(len(d["hit"]) for d in last)
            tt = sum(len(d["top"]) for d in last)
            txt.append(f"🔮 <b>Прогноз аномалий</b>: из 10 бумаг утреннего топа аномалией стали {len(rec['hit'])}" + (f" ({' '.join('$' + t for t in rec['hit'])})" if rec["hit"] else "")
                       + f", модель ожидала ≈{exp:.1f}" + (f"; ранних предупреждений {len(rec['warn'])}, сбылось {len(rec['whit'])}" if rec["warn"] else "")
                       + (f". За {len(last)} дн.: {th} из {tt} ({th / tt:.0%})." if len(last) >= 3 and tt else ".")
                       + (f" Направление угадано: {rec['dir_ok']} из {rec['dir_n']}." if rec.get("dir_n") else ""))
    except Exception as e:  # noqa: BLE001
        log("итоги прогноза аномалий", e)
    try:
        tmr, focus, notes = watch_tomorrow(md, now)
    except Exception as e:  # noqa: BLE001
        log("на завтра", e)
        tmr, focus, notes = None, {}, []
    # вероятные аномалии на следующий торговый день (по данным сегодняшнего дня; утром прогноз уточняется)
    nxt = None
    try:
        nxt = anom_next(now)
    except Exception as e:  # noqa: BLE001
        log("прогноз аномалий на завтра", e)
    if nxt and nxt.get("rows"):
        nr = nxt["rows"]
        ntop = sorted(nr, key=lambda t: -(nr[t].get("p_y") or 0))[:8]
        tmr = tmr or dt.date.fromisoformat(nxt["date"])
        for t in ntop:
            if (nr[t].get("p_y") or 0) >= 0.15:
                focus.setdefault(t, []).insert(0, f"вероятность аномалии {nr[t]['p_y']:.0%}, {anom_dir_word(nr[t])}")
        txt += ["", f"🔮 <b>Вероятные аномалии на {dt.date.fromisoformat(nxt['date']).strftime('%d.%m')}</b> (модель; утром в плане дня — уточнение с ночными новостями):"]
        for t in ntop:
            r = nr[t]
            txt.append(f"• <b>${t}</b> — {r['p_y']:.0%} (ценовая {r.get('p_y_px') or 0:.0%}), {anom_dir_word(r)}" + (": " + "; ".join((r.get("why") or [])[:3]).replace("вчера", "сегодня") if r.get("why") else ""))
        base = sum((r.get("p_y") or 0) for r in nr.values()) / max(1, len(nr))
        txt.append(f"<i>В среднем по {len(nr)} бумагам — {pc(base, 1, False)}. " + anom_dir_note("daily_dir", "утром") + "</i>")
    try:
        nm = news_movers(md, now, (nxt or {}).get("rows"))
    except Exception as e:  # noqa: BLE001
        log("новости-движители", e)
        nm = []
    if nm:
        txt += ["", "⚡️ <b>Новости, которые могут вызвать аномальное движение завтра</b> <i>(весомые новости дня, реакция цены на которые ещё не отыграна)</i>"] + nm
    if focus or notes:
        txt += ["", f"👀 <b>На что обратить внимание завтра, {tmr.strftime('%d.%m')} ({wd_ru(tmr)}):</b>"]
        for t, why in sorted(focus.items(), key=lambda x: -len(x[1]))[:10]:
            txt.append(f"• <b>${t}</b> — " + "; ".join(dict.fromkeys(why)))
        txt += notes
    try:
        sm = load_json("strong_today.json", {})
        if sm.get("day") == now.date().isoformat() and sm.get("lines"):
            ln = list(sm["lines"])
            ln[0] = ln[0].replace("📣 <b>Очень резкие движения дня</b>", "📣 <b>Очень резкие движения дня</b>")
            txt += [""] + ln
    except Exception as e:  # noqa: BLE001
        log("резкие движения в итогах:", e)
    txt.append("#итоги_дня")
    img = hbars([(n, v / 100, None) for n, v in sorted(secs, key=lambda x: -x[1])], os.path.join(OUT, "evening.png"), f"Отраслевые индексы МосБиржи за {now.strftime('%d.%m')}", fmt_v=lambda v: pc(v, 2)) if secs else None
    PUB.post("\n".join(txt), [(img, "Отраслевые индексы за день")] if img else None)


# =============================================================================================
# Понедельничная статья: базовые активы и их долгосрочный потенциал
# =============================================================================================
ASSETS_IX = [("IMOEX", "Индекс МосБиржи", "index"), ("RGBI", "Гособлигации (RGBI)", "index")] + [(s, n, "index") for s, n in SECT_IX] + \
            [("CNYRUB_TOM", "Юань, ₽", "fx"), ("GLDRUB_TOM", "Золото, ₽/г", "fx")]
STOCKS = "SBER GAZP LKOH ROSN NVTK GMKN YDEX T PLZL TATN SNGSP VTBR MGNT CHMF PHOR NLMK ALRS MOEX MTSS X5 IRAO OZON HEAD AFLT".split()


def top_stocks(n=50):
    """Самые торгуемые акции (1–3 эшелон) по среднему обороту за 20 дней; фонды (IFTF) исключены."""
    if DEMO:
        return STOCKS
    try:
        js = iss("engines/stock/markets/shares/boards/TQBR/securities.json", **{"iss.only": "securities", "securities.columns": "SECID,INSTRID"})
        eq = [r["SECID"] for r in table(js, "securities") if (r.get("INSTRID") or "EQIN") == "EQIN"]
        avg = turnover_avg(eq)
        out = [t for t in sorted(eq, key=lambda t: -(avg.get(t) or 0)) if avg.get(t)][:n]
        return out or STOCKS
    except Exception as e:  # noqa: BLE001
        log("список акций", e)
        return STOCKS


def metrics(bars):
    c = [b["c"] for b in bars]
    if len(c) < 260:
        return None
    e50, e200 = ema(c, 50)[-1], ema(c, 200)[-1]
    rets = [c[i] / c[i - 1] - 1 for i in range(len(c) - 60, len(c))]
    hi = max(c[-252:])
    return dict(last=c[-1], r1m=c[-1] / c[-22] - 1, r3m=c[-1] / c[-64] - 1, r12m=c[-1] / c[-253] - 1, dd=c[-1] / hi - 1, vol=st.pstdev(rets) * (252 ** 0.5),
                trend=1 if c[-1] > e200 and e50 > e200 else -1 if c[-1] < e200 and e50 < e200 else 0)


def div_yield(t, price):
    d0 = (now_msk().date() - dt.timedelta(days=365)).isoformat()
    s = sum(v for d, v in DIVS.get(t, []) if d >= d0)
    return s / price if price else None


def score_asset(m, kind, dy=None, mom_rank=None, news=0.0):
    sc, why = 0.0, []
    if m["trend"] == 1:
        sc += 1
        why.append("восходящий тренд (цена и EMA 50 выше EMA 200)")
    elif m["trend"] == -1:
        sc -= 1
        why.append("нисходящий тренд (цена и EMA 50 ниже EMA 200)")
    else:
        why.append("боковой тренд")
    if mom_rank is not None:
        if mom_rank <= 0.33:
            sc += 1
            why.append("в верхней трети по доходности за 12 месяцев")
        elif mom_rank >= 0.67:
            sc -= 1
            why.append("в нижней трети по доходности за 12 месяцев")
    if dy is not None:
        if dy >= 0.10:
            sc += 1
            why.append(f"высокая дивидендная доходность {pc(dy, 1, False)}")
        elif dy <= 0.03:
            sc -= 0.5
            why.append("дивиденды малы или отсутствуют")
    if m["dd"] <= -0.35 and m["trend"] >= 0:
        sc += 0.5
        why.append(f"на {pc(-m['dd'], 0, False)} ниже годового максимума при неотрицательном тренде — потенциал восстановления")
    if m["vol"] >= 0.45:
        sc -= 0.5
        why.append(f"высокая волатильность {pc(m['vol'], 0, False)} годовых")
    if news >= 0.3:
        sc += 0.5
        why.append("позитивный новостной фон за 3 недели")
    elif news <= -0.3:
        sc -= 0.5
        why.append("негативный новостной фон за 3 недели")
    lab = "сильные позиции" if sc >= 2 else "умеренно позитивно" if sc >= 0.5 else "нейтрально" if sc > -0.5 else "слабые позиции"
    return sc, lab, why


def weekly():
    now = now_msk()
    S = load_json("newsbot.json", {}, NEWS_STATE)
    newsb = {}
    for e in S.get("newsEv") or []:
        newsb[e["t"]] = newsb.get(e["t"], 0) + e["v"]
    rows = []
    for sid, name, kind in ASSETS_IX:
        try:
            bars = currency_candles(sid, 420) if kind == "fx" else index_candles(sid, 420)
            m = metrics(bars)
            if m:
                rows.append(dict(id=sid, name=name, kind=kind, m=m, bars=bars))
        except Exception as e:  # noqa: BLE001
            log("актив", sid, e)
    for a in ["BR", "NG", "GOLD", "SILV", "COPPER", "WHEAT"]:
        try:
            bars = fut_continuous(a, 420)
            m = metrics(bars)
            if m:
                rows.append(dict(id=a, name=FUT[a][0], kind="fut", m=m, bars=bars))
        except Exception as e:  # noqa: BLE001
            log("сырьё", a, e)
    stocks = []
    for t in top_stocks(int(os.environ.get("WEEKLY_N", "50"))):
        try:
            bars = share_candles(t, 420)
            m = metrics(bars)
            if m:
                stocks.append(dict(id=t, name=t, kind="share", m=m, bars=bars, dy=div_yield(t, m["last"]), news=newsb.get(t, 0)))
        except Exception as e:  # noqa: BLE001
            log("акция", t, e)
        time.sleep(0.1)
    order = sorted(stocks, key=lambda x: -x["m"]["r12m"])
    for k, x in enumerate(order):
        x["rank"] = k / max(1, len(order) - 1)
    for x in rows:
        x["sc"], x["lab"], x["why"] = score_asset(x["m"], x["kind"])
    for x in stocks:
        x["sc"], x["lab"], x["why"] = score_asset(x["m"], "share", x["dy"], x["rank"], x["news"])
    # графики
    imgs = []
    allr = [(x["name"], x["m"]["r12m"], None) for x in sorted(rows, key=lambda x: -x["m"]["r12m"])]
    imgs.append((hbars(allr, os.path.join(OUT, "w_assets.png"), "Базовые активы: доходность за 12 месяцев", fmt_v=lambda v: pc(v, 1)), "Индексы, сектора, юань и золото за 12 месяцев"))
    sr = [(f"{x['id']} ({x['lab']})", x["sc"], UP if x["sc"] >= 0.5 else DN if x["sc"] <= -0.5 else "#787b86") for x in sorted(stocks, key=lambda x: -x["sc"])]
    imgs.append((hbars(sr, os.path.join(OUT, "w_scores.png"), "Акции: оценка долгосрочной позиции (тренд, импульс, дивиденды, новости)", fmt_v=lambda v: fmt(v, 1)), "Сводная оценка по акциям"))
    fig, ax = plt.subplots(figsize=(10, 5), dpi=110, facecolor=BG)
    style(ax)
    for x in stocks:
        if x["dy"] is None:
            continue
        col = UP if x["sc"] >= 0.5 else DN if x["sc"] <= -0.5 else "#9e9e9e"
        ax.scatter(x["m"]["r12m"] * 100, x["dy"] * 100, s=60, color=col, zorder=3)
        ax.annotate(x["id"], (x["m"]["r12m"] * 100, x["dy"] * 100), xytext=(4, 3), textcoords="offset points", color=FG, fontsize=8)
    ax.set_xlabel("Доходность за 12 месяцев, %", color=FG)
    ax.set_ylabel("Дивиденды за 12 месяцев, % к цене", color=FG)
    ax.set_title("Импульс и дивиденды: зелёные — сильные позиции, красные — слабые", color="#ffffff", loc="left", fontsize=11)
    fig.tight_layout()
    p3 = os.path.join(OUT, "w_scatter.png")
    fig.savefig(p3, facecolor=BG)
    plt.close(fig)
    imgs.append((p3, "Импульс и дивиденды по акциям"))
    ix = next((x for x in rows if x["id"] == "IMOEX"), None)
    if ix:
        imgs.append((candle_chart(ix["bars"], os.path.join(OUT, "w_imoex.png"), "Индекс МосБиржи: 1 год, EMA 50 и EMA 200", emas=((50, BLUE, "EMA 50"), (200, ORANGE, "EMA 200")), n=250, dec=0), "Индекс МосБиржи за год"))
    # текст
    strong = [x for x in sorted(stocks, key=lambda x: -x["sc"]) if x["sc"] >= 1.5][:6]
    weak = [x for x in sorted(stocks, key=lambda x: x["sc"]) if x["sc"] <= -0.5][:6]

    def aline(x):
        m = x["m"]
        return f"{x['name']}: {pc(m['r1m'], 1)} за месяц, {pc(m['r12m'], 1)} за год, от максимума {pc(m['dd'], 0)}; {x['lab']}"
    txt = [f"📚 <b>Базовые активы: долгосрочный взгляд — неделя {now.strftime('%d.%m')}</b>", ""]
    if ix:
        txt.append("<b>Рынок:</b> " + aline(ix) + ".")
    rg = next((x for x in rows if x["id"] == "RGBI"), None)
    if rg:
        txt.append("<b>Облигации:</b> " + aline(rg) + ".")
    fx = [x for x in rows if x["kind"] == "fx"]
    for x in fx:
        txt.append(f"<b>{x['name']}:</b> {fmt(x['m']['last'], 2)} ({pc(x['m']['r1m'], 1)} за месяц, {pc(x['m']['r12m'], 1)} за год).")
    fu = [x for x in rows if x["kind"] == "fut"]
    if fu:
        txt.append("<b>Сырьё (фьючерсы, склейка контрактов):</b> " + "; ".join(f"{x['name']} {pc(x['m']['r1m'], 1)} за месяц, {pc(x['m']['r12m'], 1)} за год — {x['lab']}" for x in sorted(fu, key=lambda x: -x["sc"])) + ".")
    sect = sorted([x for x in rows if x["id"].startswith("MOEX") and x["id"] != "IMOEX"], key=lambda x: -x["sc"])
    if sect:
        txt.append("<b>Сильнейшие сектора по оценке тренда:</b> " + ", ".join(f"{x['name']} ({pc(x['m']['r3m'], 1)} за 3 мес.)" for x in sect[:3]) + ".")
        txt.append("<b>Слабейшие:</b> " + ", ".join(f"{x['name']} ({pc(x['m']['r3m'], 1)} за 3 мес.)" for x in sect[-3:][::-1]) + ".")
    txt.append("")
    if strong:
        txt.append("🟢 <b>Сильные позиции</b>:")
        txt += [f"• ${x['id']} — {'; '.join(x['why'][:3])}" for x in strong]
    if weak:
        txt.append("🔴 <b>Слабые позиции</b>:")
        txt += [f"• ${x['id']} — {'; '.join(x['why'][:3])}" for x in weak]
    txt += ["", "{LINK}", "#неделя #долгосрок"]
    import article
    article.publish(PUB, f"Базовые активы: долгосрочный взгляд — неделя {now.strftime('%d.%m.%Y')}", weekly_blocks(now, rows, stocks), "\n".join(txt), imgs,
                    fname=f"weekly_{now.strftime('%Y-%m-%d')}")


def weekly_blocks(now, rows, stocks):
    """Статья недели для Telegraph (без таблиц: Telegraph их не поддерживает — каждая строка таблицы становится пунктом списка)."""
    tr = {1: "вверх", -1: "вниз", 0: "боковик"}
    B = [("p", "Индексы, сектора, облигации, юань, золото и 24 крупнейшие акции Мосбиржи. Оценка строится по прозрачным правилам из цифр; "
               "это не прогноз и не индивидуальная инвестиционная рекомендация."),
         ("h3", "1. Индексы, сектора, валюта и золото")]
    B.append(("ul", [f"<b>{esc(x['name'])}</b> — {fmt(x['m']['last'], 2)}; 1 мес. {pc(x['m']['r1m'], 1)}, 3 мес. {pc(x['m']['r3m'], 1)}, 12 мес. {pc(x['m']['r12m'], 1)}; "
                     f"от максимума {pc(x['m']['dd'], 0)}; волатильность {pc(x['m']['vol'], 0, False)}; тренд {tr[x['m']['trend']]} → <b>{esc(x['lab'])}</b>" for x in rows]))
    B.append(("h3", "2. Акции: долгосрочная позиция"))
    for x in sorted(stocks, key=lambda x: -x["sc"]):
        m = x["m"]
        B.append(("h4", f"${x['id']} — {fmt(x['sc'], 1)} ({esc(x['lab'])})"))
        B.append(("p", f"{SEC_RU.get(SEC_OF.get(x['id'], ''), '—')}; 1 мес. {pc(m['r1m'], 1)}, 12 мес. {pc(m['r12m'], 1)}, от максимума {pc(m['dd'], 0)}; "
                       f"дивиденды за 12 мес. {pc(x['dy'], 1, False) if x['dy'] is not None else '—'}; тренд {tr[m['trend']]}. "
                       + ("Почему: " + esc("; ".join(x["why"][:3])) if x.get("why") else "")))
    B.append(("h3", "3. Методика и что нужно знать трейдеру"))
    B.append(("ul", ["Тренд: цена и EMA 50 выше EMA 200 — восходящий (+1), ниже — нисходящий (−1).",
                     "Импульс: место бумаги по доходности за 12 месяцев среди 24 крупнейших акций; верхняя треть +1, нижняя −1.",
                     "Дивиденды: сумма выплат за 12 месяцев к текущей цене; от 10% — +1, до 3% — −0,5.",
                     "Потенциал восстановления: бумага более чем на 35% ниже годового максимума при неотрицательном тренде (+0,5). Волатильность от 45% годовых — −0,5.",
                     "Новостной фон: сумма весов новостей по бумаге за 3 недели (веса — средняя историческая реакция цены на такой тип новости).",
                     "Оценка 2 и выше — «сильные позиции», 0,5…2 — «умеренно позитивно», −0,5…0,5 — «нейтрально», ниже — «слабые позиции».",
                     "Ограничения: оценка не учитывает отчётность, долговую нагрузку и не раскрытые ещё корпоративные события. Цены — Мосбиржа, дивиденды — dohod.ru."]))
    B.append(("p", "<i>" + esc(DISCLAIMER) + "</i>"))
    return B


def weekly_pdf(now, rows, stocks, imgs):
    try:
        import matplotlib
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as e:
        log("PDF не собран:", e)
        return None
    fdir = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    pdfmetrics.registerFont(TTFont("DV", os.path.join(fdir, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DVB", os.path.join(fdir, "DejaVuSans-Bold.ttf")))
    H1 = ParagraphStyle("h1", fontName="DVB", fontSize=17, leading=21, spaceAfter=6)
    H2 = ParagraphStyle("h2", fontName="DVB", fontSize=12.5, leading=16, spaceBefore=8, spaceAfter=4, textColor=colors.HexColor("#1a3d7c"))
    TX = ParagraphStyle("tx", fontName="DV", fontSize=9, leading=12.5)
    CL = ParagraphStyle("cl", fontName="DV", fontSize=7.4, leading=9.2)
    W = A4[0] - 3 * cm

    def tbl(data, widths):
        t = Table([[Paragraph(str(c), CL) for c in r] for r in data], colWidths=[W * w for w in widths], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3d7c")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                               ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9ccd6")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f6fa")])]))
        return t

    def img(p):
        iw, ih = ImageReader(p).getSize()
        return Image(p, width=W, height=W * ih / iw)
    path = os.path.join(OUT, f"Bazovye_aktivy_{now.strftime('%Y-%m-%d')}.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.4 * cm, bottomMargin=1.4 * cm, title="Базовые активы — недельный обзор")
    E = [Paragraph(f"Базовые активы: долгосрочный взгляд — неделя {now.strftime('%d.%m.%Y')}", H1),
         Paragraph("Индексы, сектора, облигации, юань, золото и 24 крупнейшие акции Мосбиржи. Оценка строится по прозрачным правилам из цифр; "
                   "это не прогноз и не индивидуальная инвестиционная рекомендация.", TX), Spacer(1, 6)]
    E.append(Paragraph("1. Индексы, сектора, валюта и золото", H2))
    data = [["Актив", "Цена", "1 мес.", "3 мес.", "12 мес.", "От макс.", "Волат.", "Тренд", "Оценка"]]
    for x in rows:
        m = x["m"]
        data.append([x["name"], fmt(m["last"], 2), pc(m["r1m"], 1), pc(m["r3m"], 1), pc(m["r12m"], 1), pc(m["dd"], 0), pc(m["vol"], 0, False), {1: "вверх", -1: "вниз", 0: "боковик"}[m["trend"]], x["lab"]])
    E.append(tbl(data, [0.2, 0.1, 0.08, 0.08, 0.09, 0.09, 0.08, 0.1, 0.18]))
    E.append(Spacer(1, 4))
    E.append(img(imgs[0][0]))
    E.append(PageBreak())
    E.append(Paragraph("2. Акции: долгосрочная позиция", H2))
    data = [["Бумага", "Сектор", "1 мес.", "12 мес.", "От макс.", "Дивиденды 12 мес.", "Тренд", "Оценка", "Почему"]]
    for x in sorted(stocks, key=lambda x: -x["sc"]):
        m = x["m"]
        data.append([x["id"], SEC_RU.get(SEC_OF.get(x["id"], ""), "—"), pc(m["r1m"], 1), pc(m["r12m"], 1), pc(m["dd"], 0), pc(x["dy"], 1, False) if x["dy"] is not None else "—",
                     {1: "вверх", -1: "вниз", 0: "боковик"}[m["trend"]], f"{fmt(x['sc'], 1)} — {x['lab']}", "; ".join(x["why"][:3])])
    E.append(tbl(data, [0.07, 0.09, 0.07, 0.07, 0.07, 0.09, 0.07, 0.13, 0.34]))
    for p, _ in imgs[1:]:
        E.append(Spacer(1, 4))
        E.append(img(p))
    E.append(PageBreak())
    E.append(Paragraph("3. Методика и что нужно знать трейдеру", H2))
    for s in ["Тренд: цена и EMA 50 выше EMA 200 — восходящий (+1), ниже — нисходящий (−1). Именно трендовый фильтр лежит в основе совмещённой стратегии канала.",
              "Импульс: место бумаги по доходности за 12 месяцев среди 24 крупнейших акций; верхняя треть +1, нижняя −1. На истории 2008–2026 лидеры импульса продолжали опережать рынок чаще, чем отстающие.",
              "Дивиденды: сумма выплат за 12 месяцев к текущей цене; от 10% — +1, до 3% — −0,5. Дивиденды после отсечки уже вычтены из цены.",
              "Потенциал восстановления: бумага более чем на 35% ниже годового максимума при неотрицательном тренде (+0,5). Высокая волатильность (от 45% годовых) — −0,5.",
              "Новостной фон: сумма весов новостей по бумаге за 3 недели (веса — средняя историческая реакция цены на такой тип новости).",
              "Оценка 2 и выше — «сильные позиции», 0,5…2 — «умеренно позитивно», −0,5…0,5 — «нейтрально», ниже — «слабые позиции».",
              "Ограничения: оценка не учитывает финансовую отчётность, долговую нагрузку и корпоративные события, о которых ещё не сообщено. Цены — Мосбиржа, дивиденды — dohod.ru."]:
        E.append(Paragraph("• " + s, TX))
    doc.build(E)
    return path


# =============================================================================================
def vk_keys():
    """Какие ключи ВК заданы: пользователя или сообщества (картинки на стену грузит только ключ пользователя)."""
    for name, tok in (("VK_GROUP_TOKEN", PUB.vk_token), ("VK_USER_TOKEN", PUB.vk_user)):
        if not tok:
            continue
        try:
            u = PUB._vk("users.get", tok)
            if u:
                log(name, "— ключ ПОЛЬЗОВАТЕЛЯ:", (u[0].get("first_name", "") + " " + u[0].get("last_name", "")).strip(), "(подходит для картинок)")
            else:
                log(name, "— ключ СООБЩЕСТВА (картинки им загрузить нельзя)")
        except Exception as e:  # noqa: BLE001
            kind = "ключ СООБЩЕСТВА (картинки им загрузить нельзя)" if "group auth" in str(e) or " 27 " in str(e) else "ошибка"
            log(name, "—", kind, "|", e)


def telegraph_check():
    """Проверка TELEGRAPH_TOKEN: аккаунт, автор, число страниц. Ничего не публикует."""
    import article
    if not article.TOKEN:
        log("TELEGRAPH_TOKEN не задан — статьи пойдут сообщениями Telegram")
        return
    try:
        a = article.account_info()
        log("Telegraph: токен рабочий — аккаунт", a.get("short_name"), "| автор", a.get("author_name") or "—", "| страниц", a.get("page_count"))
    except Exception as e:  # noqa: BLE001
        log("Telegraph: токен НЕ работает —", e)


def tg_check():
    """Проверка бота паблика: кто он, в каких каналах админ и может ли публиковать. Ничего не публикует."""
    telegraph_check()
    if not os.environ.get("TG_PUBLIC_TOKEN"):
        log("секрет TG_PUBLIC_TOKEN не задан — паблик будет писать от основного бота (TG_TOKEN)")
    try:
        me = PUB._tg("getMe")
    except Exception as e:  # noqa: BLE001
        log("бот не отвечает — проверьте токен:", e)
        return
    log("бот паблика: @" + me.get("username", "?"), "id", me.get("id"))
    try:
        ups = PUB._tg("getUpdates", {"allowed_updates": json.dumps(["my_chat_member", "channel_post"]), "limit": 100})
    except Exception as e:  # noqa: BLE001
        ups = []
        log("getUpdates:", e)
    seen = {}
    for u in ups:
        m = u.get("my_chat_member") or {}
        ch = m.get("chat") or (u.get("channel_post") or {}).get("chat") or {}
        if ch.get("id"):
            seen[ch["id"]] = (ch.get("title"), ch.get("username"), (m.get("new_chat_member") or {}).get("status"))
    for cid, (title, un, stt) in seen.items():
        log("бота добавили в чат:", title, "@" + un if un else "(без публичной ссылки)", "id", cid, "статус", stt or "?")
    if not seen:
        log("в последних событиях бот ни в один канал не добавлялся (или события старше суток)")
    chat = PUB.tg_chat
    if not chat:
        log("переменная TG_PUBLIC_CHAT не задана — укажите @ссылку канала или его id из строки выше")
        return
    try:
        info = PUB._tg("getChat", {"chat_id": chat})
        mem = PUB._tg("getChatMember", {"chat_id": chat, "user_id": me["id"]})
        ok = mem.get("status") == "creator" or (mem.get("status") == "administrator" and mem.get("can_post_messages", True))
        log("канал:", info.get("title"), "| статус бота:", mem.get("status"), "| может публиковать:", "ДА" if ok else "НЕТ — дайте право «Публикация сообщений»")
    except Exception as e:  # noqa: BLE001
        log("канал", chat, "недоступен боту:", e, "— добавьте бота администратором канала")
    vk_keys()


def test_post():
    """Пробный пост для проверки подключения Telegram-канала и сообщества ВК."""
    log("Telegram:", PUB.tg_chat or "TG_PUBLIC_CHAT не задан", "| ВК:", ("сообщество " + PUB.vk_group) if PUB.vk_group else "VK_GROUP_ID не задан",
        "| ключ сообщества:", "есть" if PUB.vk_token else "нет", "| ключ администратора:", "есть" if PUB.vk_user else "нет")
    vk_keys()
    img = None
    try:
        import card
        src = os.path.join(OUT, "test_card.json")
        img = os.path.join(OUT, "test_card.png")
        json.dump({"t": "РЫНОК", "label": "Проверка связи", "caption": "Паблик подключён: графики и картинки на месте", "sign": 1,
                   "fact": "Пробная картинка: так выглядят иллюстрации к новостям"}, open(src, "w", encoding="utf-8"), ensure_ascii=False)
        card.main(src, img)
    except Exception as e:  # noqa: BLE001
        log("картинка для проверки не сделана:", e)
        img = None
    PUB.post(f"✅ <b>Паблик подключён</b>\nПроверка связи {now_msk().strftime('%d.%m.%Y %H:%M')} МСК: текст и картинка. Здесь будут план дня, потоки денег, ОФЗ, Пульс, итоги и важные новости Мосбиржи.",
             [(img, "Пробная иллюстрация")] if img else None, ai_lead=False)
    log("готово — проверьте канал и сообщество")


def aitest():
    """Проверка шлюза ИИ: ничего не публикует, только пишет в журнал ответ модели и итог проверки."""
    import ai
    log("шлюз:", ai.BASE or "не задан", "| ключ:", "есть" if ai.KEY else "НЕТ (секрет LLM_API_KEY)", "| модели:", ", ".join(ai.MODELS))
    sample = ("🌙 <b>Итоги дня — пример</b>\nИндекс МосБиржи: 2 450 (+1,00%). Оборот 60 крупнейших акций: 32,9 млрд ₽.\n"
              "Лидеры роста: $SNGS +3,0%, $NVTK +3,0%. Лидеры падения: $MDMG −2,7%, $VKCO −0,5%.\n"
              "Перевес к закрытию: покупатели — $SNGS, $VKCO; продавцы — $MDMG, $AFLT.")
    log("все модели шлюза:", ", ".join(ai.list_models()) or "— (шлюз не отдаёт список)")
    st = {}
    out = ai.with_lead(sample, st, esc)
    log("состояние:", json.dumps(st, ensure_ascii=False))
    print("----- РЕЗУЛЬТАТ -----\n" + out, flush=True)
    log("ИТОГ:", "вступление получено и прошло проверку" if out != sample else "вступления нет — см. сообщения [ИИ] выше")


# =============================================================================================
# Прогноз аномалий (модели anomaly/train.py): на день — до открытия, внутри дня — ранние признаки
# =============================================================================================
ANOM_DIR = os.path.join(HERE, "..", "anomaly")
AN_KEEP = ["p_y", "p_y_px", "p_y_up", "p_y_dn", "p_dir_up", "tier", "sd60", "sd20", "an20", "an250", "since_an", "vr1", "nw_n", "nw_w", "nw_wmax", "ix_r1", "ix_sd20", "lval",
           "ema20d", "dhi20", "dlo20", "month", "dow", "to_ex", "absx1", "h_ev_ret", "brk20", "sq"]


def anom_live():
    sys.path.insert(0, ANOM_DIR)
    import live
    return live


def anom_pre(force=False, day=None):
    """Вероятности аномалий на сегодня по 250 самым торгуемым акциям; результат — anom_today.json (состояние)."""
    now = now_msk()
    today = day or now.date().isoformat()
    if dt.date.fromisoformat(today).weekday() >= 5:
        return None
    cur = load_json("anom_today.json", {})
    if cur.get("date") == today and not force:
        return cur
    try:
        live = anom_live()
    except Exception as e:  # noqa: BLE001
        log("прогноз аномалий: модуль недоступен", e)
        return None
    if not live.ready():
        log("прогноз аномалий: модели ещё не обучены (workflow «Модель аномалий»)")
        return None
    try:
        import divs
        upc = divs.upcoming(60)
    except Exception:  # noqa: BLE001
        upc = []
    tick = top_stocks(int(os.environ.get("ANOM_N", "250")))
    impact = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8"))
    X = live.daily_probs(tick, today, DIVS, upc, NEWS_STATE, impact)
    rows = {}
    for t, r in X.iterrows():
        keys = [k for k in r.index if k not in ("d", "t", "y", "y_px", "y_up", "y_dn", "y_vol", "x", "vr", "ex")]
        d = {}
        for k in set(keys) | set(AN_KEEP):
            v = r.get(k)
            try:
                d[k] = None if pd_isna(v) else round(float(v), 6)
            except (TypeError, ValueError):
                d[k] = None
        d["why"] = live.reasons({k: (v if v is not None else 0) for k, v in d.items()})
        rows[t] = d
    out = dict(date=today, rows=rows, trained=live.meta().get("trained"))
    if not day:
        save_json("anom_today.json", out)
    log("прогноз аномалий на день:", len(rows), "бумаг; топ:", ", ".join(f"{t} {rows[t]['p_y']:.0%}" for t in sorted(rows, key=lambda t: -rows[t]["p_y"])[:5]))
    return out


def anom_test():
    """Проверка без публикации: прогноз на ближайший торговый день, топ-20 в журнал."""
    d = now_msk().date()
    day = d if d.weekday() < 5 else next_wd(d)
    out = anom_pre(force=True, day=day.isoformat())
    if not out:
        log("прогноза нет")
        return
    rows = out["rows"]
    log("модель обучена:", out.get("trained"), "| бумаг:", len(rows), "| средняя вероятность:", round(sum(r["p_y"] for r in rows.values()) / len(rows), 4))
    for t in sorted(rows, key=lambda t: -rows[t]["p_y"])[:20]:
        r = rows[t]
        log(f"{t}: {r['p_y']:.1%} ценовая {r['p_y_px'] or 0:.1%} — {anom_dir_word(r)} — {'; '.join(r['why'])}")


def pd_isna(v):
    try:
        return v is None or (isinstance(v, float) and math.isnan(v))
    except Exception:  # noqa: BLE001
        return True


def anom_today_any():
    """Прогноз на сегодня: свой (состояние) или новостного бота (его кэш), иначе посчитать."""
    today = now_msk().date().isoformat()
    for base in (None, os.path.join(NEWS_STATE, "channel")):
        cur = load_json("anom_today.json", {}, base)
        if cur.get("date") == today:
            return cur
    return anom_pre()


def anom_dir_word(r):
    """Куда вероятнее пойдёт бумага, если аномалия случится: P(вверх | аномалия) из модели направления."""
    pu = r.get("p_dir_up")
    if pu is None:
        up, dn = r.get("p_y_up") or 0, r.get("p_y_dn") or 0
        if up + dn < 0.01:
            return "направление неясно"
        pu = up / (up + dn)
    if abs(pu - 0.5) < 0.1:
        return f"направление неясно (вверх {pu:.0%})"
    return f"⬆️ вверх {pu:.0%}" if pu > 0.5 else f"⬇️ вниз {1 - pu:.0%}"


def anom_next(now=None):
    """Прогноз аномалий на следующий торговый день по данным сегодняшней сессии (для итогов дня)."""
    now = now or now_msk()
    day = next_wd(now.date()).isoformat()
    cur = load_json("anom_next.json", {})
    if cur.get("date") == day and cur.get("made", "")[:10] == now.date().isoformat():
        return cur
    out = anom_pre(force=True, day=day)
    if out:
        out["made"] = now.isoformat()
        save_json("anom_next.json", out)
    return out


# =============================================================================================
# Сценарии сделки: что вероятнее всего будет с позицией после входа (модель anomaly/scenario.py)
# =============================================================================================
def anom_features(t):
    """Признаки бумаги на утро: прогноз дня (сегодня) или вечерний прогноз на следующий торговый день."""
    today = now_msk().date().isoformat()
    for base in (None, os.path.join(NEWS_STATE, "channel")):
        for name in ("anom_today.json", "anom_next.json"):
            cur = load_json(name, {}, base)
            if cur.get("date", "") >= prev_wd(now_msk().date()).isoformat() and t in (cur.get("rows") or {}):
                return cur["rows"][t]
    return None


def trade_scen(t, side, entry, stop, take=None, hz=10):
    try:
        feat = anom_features(t)
        if not feat:
            return None
        return anom_live().scenario(feat, side, entry, stop, take, hz) | {"beta": feat.get("beta"), "reg_n": feat.get("reg_n"), "reg_n5": feat.get("reg_n5"), "side": side}
    except Exception as e:  # noqa: BLE001
        log("сценарии сделки", t, e)
        return None


def scen_text(sc, t=None):
    """Строки поста: вероятности сценариев + предупреждения из разбора ошибок модели."""
    if not sc:
        return ""
    out = [f"🎲 <b>Сценарии после входа</b> (модель по истории 2013–2026, горизонт {sc['hz']} торг. дн.): "
           f"цель раньше стопа — <b>{sc['p_take']:.0%}</b> · стоп раньше цели — <b>{sc['p_stop']:.0%}</b> · ни то ни другое, выход по времени — {sc['p_time']:.0%}. "
           f"Ожидаемый результат ≈ {'+' if sc['exp_R'] >= 0 else '−'}{fmt(abs(sc['exp_R']), 2)}R."]
    if sc.get("stop_atr") and sc["stop_atr"] == sc["stop_atr"] and sc["stop_atr"] < 1:
        out.append(f"⚠️ Стоп ближе обычного дневного хода бумаги ({fmt(sc['stop_atr'], 1)} ATR): по истории такие стопы чаще выбивает шумом.")
    if sc.get("beta") and sc["beta"] >= 1.3:
        out.append(f"⚠️ Бумага ходит сильнее рынка (бета {fmt(sc['beta'], 1)}): в двух из трёх ошибочных сделок на истории позицию выбивало движение индекса.")
    if t:
        rl = reg_lines(t, 36)
        if rl:
            out.append("📍 Региональные события в регионах активов компании: " + "; ".join(rl[:2]))
        cl = ctx_lines(t, sc.get("side") or 1)
        if cl:
            out.append("<b>Фон для сделки</b> (публичные данные):\n" + "\n".join(cl))
    return "\n".join(out)


def _state_json(name):
    for base in (None, os.path.join(NEWS_STATE, "channel")):
        v = load_json(name, None, base)
        if v:
            return v
    return {}


def ctx_lines(t, side):
    """Фон для сделки: решение ЦБ, ОФЗ, потоки денег в бумагу, сделки сильных трейдеров Пульса, раскрытые сделки инсайдеров."""
    out, now = [], now_msk()
    try:
        cb = json.load(open(os.path.join(HERE, "..", "cbr_dates.json")))
        nxt = next((d for d in cb if d >= now.date().isoformat()), None)
        if nxt:
            dd = (dt.date.fromisoformat(nxt) - now.date()).days
            out.append(f"🏦 Решение ЦБ по ставке — {nxt[8:10]}.{nxt[5:7]} ({'сегодня' if dd == 0 else f'через {dd} дн.'})"
                       + ("; на истории сделки стратегии, открытые за 3–7 дней до заседания, чаще закрывались убытком (выборка ~30 сделок — оценка грубая)" if 3 <= dd <= 7 else ""))
    except Exception as e:  # noqa: BLE001
        log("фон: ЦБ", e)
    try:
        rg = index_candles("RGBI", 20)
        if len(rg) >= 6:
            r5 = rg[-1]["c"] / rg[-6]["c"] - 1
            out.append(f"📉 ОФЗ (RGBI) за 5 дней: {pc(r5, 1)} — " + ("деньги уходят из ОФЗ, для лонгов фон хуже (на истории такие лонги почти ничего не приносили, риск уменьшен)" if r5 < -0.01
                       else "деньги идут в ОФЗ, ставки на рынке снижаются" if r5 > 0.01 else "спокойно"))
    except Exception as e:  # noqa: BLE001
        log("фон: ОФЗ", e)
    fl = _state_json("flows_last.json")
    if fl.get("time", "") >= (now - dt.timedelta(hours=8)).isoformat()[:13]:
        for lab, v in (fl.get("buyers") or []) + (fl.get("sellers") or []):
            if lab == t:
                out.append(f"💸 Потоки по ${t} (ISS, последние 30 мин): перевес {'покупок' if v > 0 else 'продаж'} {pc(v, 0)}"
                           + (" — совпадает со сделкой" if v * side > 0 else " — против сделки"))
    pdl = [d for d in (_state_json("pulse_deals.json").get("deals") or []) if d.get("t") == t and d.get("ts", 0) >= time.time() - 5 * 86400]
    if pdl:
        b = sum(1 for d in pdl if d["side"] > 0); sl = len(pdl) - b
        out.append(f"👥 Сильные трейдеры Пульса за 5 дней по ${t}: покупок {b}, продаж {sl}"
                   + (" — в ту же сторону" if (b - sl) * side > 0 else " — в другую сторону" if (b - sl) * side < 0 else ""))
    lp = os.path.join(NEWS_STATE, "news_log.jsonl")
    if os.path.exists(lp):
        ins = []
        for line in open(lp, encoding="utf-8"):
            if '"@INS"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if t in (e.get("tks") or []) and e.get("ts", 0) >= time.time() - 30 * 86400:
                ins.append(e)
        if ins:
            b = sum(1 for e in ins if e.get("buy"))
            out.append(f"💼 Раскрытые сделки инсайдеров и крупных акционеров за 30 дней: покупок {b}, продаж {len(ins) - b}")
    return out


def reg_lines(t, hours=24):
    """Свежие региональные инциденты (журнал бота) в регионах, где у компании ключевые активы."""
    try:
        sys.path.insert(0, ANOM_DIR)
        import regional as RG
    except Exception:  # noqa: BLE001
        return []
    regs = set(RG.COMP.get(t, []))
    if not regs:
        return []
    lp = os.path.join(NEWS_STATE, "news_log.jsonl")
    out, since = [], time.time() - hours * 3600
    if os.path.exists(lp):
        for line in open(lp, encoding="utf-8"):
            if '"@REG"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            hit = [g for g in e.get("regs") or [] if g in regs]
            ts = float(e.get("ts") or 0)
            ts = ts / 1000 if ts > 1e11 else ts  # на случай миллисекунд
            if ts >= since and hit:
                w = dt.datetime.fromtimestamp(ts, MSKTZ).strftime("%d.%m %H:%M")
                out.append(f"{', '.join(RG.RU.get(g, g) for g in hit)} — {esc(cut(clean_tx(e.get('tx', '')), 110))} ({w})")
    return out[-3:]


def level_cli():
    """python jobs.py level ТИКЕР СТОРОНА → LEVEL_JSON: предположительный уровень с учётом последних потоков (для постов новостей)."""
    a = [x for x in sys.argv[1:] if not x.startswith("--")][1:]
    t, side = a[0], int(a[1])
    fl = _state_json("flows_last.json")
    flow = 0.0
    if fl.get("time", "") >= (now_msk() - dt.timedelta(hours=8)).isoformat()[:13]:
        for lab, v in (fl.get("buyers") or []) + (fl.get("sellers") or []):
            if lab == t:
                flow = v
    r = level_target(t, side, flow=flow, ndir=side)
    if r:
        r["pro"] = [p_ for p_ in r["pro"] if not p_.startswith("новости")] + ["смысл новости"]
        r["text"] = target_line(r).replace("\n   ", " — ")
    print("LEVEL_JSON " + json.dumps(r or {}, ensure_ascii=False, default=float), flush=True)


def trade_prob_cli():
    """python jobs.py trade_prob ТИКЕР СТОРОНА ВХОД СТОП [ТЕЙК] [ГОРИЗОНТ] → JSON + готовый текст (для бота на Node)."""
    a = [x for x in sys.argv[1:] if not x.startswith("--")][1:]
    t, side, entry, stop = a[0], int(a[1]), float(a[2]), float(a[3])
    take = float(a[4]) if len(a) > 4 and a[4] not in ("", "0", "null", "None") else None
    hz = int(a[5]) if len(a) > 5 else 10
    sc = trade_scen(t, side, entry, stop, take, hz)
    txt = scen_text(sc, t)
    print("SCEN_JSON " + json.dumps(dict(sc or {}, text=txt, notes=txt.split("\n", 1)[1] if "\n" in txt else ""), ensure_ascii=False, default=float), flush=True)


def anom_dir_note(name, when):
    try:
        a = anom_live().dir_acc(name)
    except Exception:  # noqa: BLE001
        a = {}
    if not a:
        return ""
    return (f"Направление ({when}) на проверке угадывалось в {a['acc']:.0%} случаев (если всегда говорить «вверх» — {a['base_up']:.0%})"
            + (f", при уверенном прогнозе — в {a['acc_conf']:.0%}" if a.get("acc_conf") else "") + ".")


def anom_intraday(stt, md, ixc, now):
    import pandas as pd
    """Ранние признаки: бумаги, которые с высокой вероятностью станут аномалией до конца дня. Раз в 15 минут, 11:00–18:45."""
    hm = now.strftime("%H:%M")
    if not ("11:00" <= hm <= "18:45") or time.time() - stt.get("ai_ts", 0) < int(os.environ.get("ANOM_CHECK_MIN", "15")) * 60 - 30:
        return
    stt["ai_ts"] = time.time()
    if stt.get("wn", 0) >= int(os.environ.get("ANOM_EARLY_PER_DAY", "2")):
        return
    today = anom_today_any()
    if not today or not today.get("rows"):
        return
    live = anom_live()
    if not os.path.exists(os.path.join(live.MODELS, "intra_y.pkl")):
        return
    td = pd.DataFrame(today["rows"]).T
    for k in live.CI.DCOLS + ["p_y"]:
        if k not in td.columns:
            td[k] = float("nan")
    td = td.astype({k: float for k in live.CI.DCOLS + ["p_y"]})
    # оборот час назад — из почасовых снимков
    hs = stt.setdefault("hsnap", {})
    key_now = str(now.hour)
    if key_now not in hs:
        hs[key_now] = {r["SECID"]: r.get("VALTODAY") or 0 for r in md}
    prev_val = hs.get(str(now.hour - 1), {})
    nh = {}
    S = load_json("newsbot.json", {}, NEWS_STATE)
    for c in (S.get("cl") or {}).values():
        w = dt.datetime.fromtimestamp(c.get("ts", 0), MSKTZ)
        if w.date() == now.date():
            nh.setdefault(c["key"].split("|")[0], []).append(w.hour + w.minute / 60)
    F = live.intraday(md, ixc, now.hour - 1 if now.minute < 50 else now.hour, td, prev_val, nh)
    if F.empty:
        return
    warn = stt.setdefault("warn", {})
    th = float(os.environ.get("ANOM_WARN_P", "0.5")), float(os.environ.get("ANOM_WARN_PX", "0.3"))
    cand = [r for _, r in F.iterrows() if (r.p >= th[0] or r.p_px >= th[1]) and r.t not in warn and r.t not in stt.get("sent", {})]
    if not cand or stt.get("wn", 0) >= int(os.environ.get("ANOM_WARN_PER_DAY", "6")):
        return
    cand = cand[:3]
    txt = ["🔮 <b>Ранние признаки аномалии</b> — модель оценивает, что эти бумаги могут резко двинуться или взорваться по обороту до конца дня", ""]
    for r in cand:
        dd = today["rows"].get(r.t, {})
        vol_typ = f", оборот уже ×{fmt(r.cvol_typ, 1)} к обычному на это время" if r.cvol_typ == r.cvol_typ and r.cvol_typ >= 1.5 else ""
        pu = float(r.p_dir_up)
        dirw = f"⬆️ скорее вверх ({pu:.0%})" if pu >= 0.6 else f"⬇️ скорее вниз ({1 - pu:.0%})" if pu <= 0.4 else f"направление неясно (вверх {pu:.0%})"
        txt.append(f"• <b>${r.t}</b> — вероятность аномалии <b>{r.p:.0%}</b> (ценовой {r.p_px:.0%}), {dirw}; сейчас {pc(r.cx, 1)} к индексу{vol_typ}"
                   + (f"; новостей за день: {int(r.nw_day)}" if r.nw_day else "") + (f"; утренний прогноз был {dd.get('p_y', 0):.0%}" if dd.get("p_y") else ""))
        warn[r.t] = dict(p=round(float(r.p), 3), px=round(float(r.p_px), 3), ts=time.time(), cx=round(float(r.cx), 4), up=round(pu, 3))
    txt += ["", "<i>Аномалия — движение цены к индексу сильнее порога эшелона или оборот в 4–6 раз выше обычного. Вероятность — по модели, обученной на истории 2013–2026 и проверенной на годах, которых она не видела. "
            + anom_dir_note("intra_dir", "внутри дня") + " Это не сигнал на сделку.</i>",
            "#прогноз_аномалий " + " ".join("#" + r.t for r in cand)]
    stt["wn"] = stt.get("wn", 0) + 1
    PUB.post("\n".join(txt))


def anom_score_day(md, ixc):
    """Итоги прогнозов дня: какие из утреннего топа и ранних предупреждений стали аномалией."""
    today = anom_today_any()
    if not today:
        return None
    rows = today["rows"]
    real, xs = set(), {}
    for r in md:
        t = r["SECID"]
        d = rows.get(t)
        if not d or d.get("sd60") is None:
            continue
        tier = d.get("tier") or 3
        thr = max({1: 0.04, 2: 0.06}.get(int(tier), 0.08), 3 * d["sd60"])
        x = (r.get("LASTTOPREVPRICE") or 0) / 100 - ixc
        xs[t] = x
        val = r.get("VALTODAY") or 0
        med = 10 ** (d.get("lval") or 12)
        if abs(x) >= thr or (val / med >= (6 if tier == 3 else 4) and val >= 30e6):
            real.add(t)
    topm = sorted(rows, key=lambda t: -(rows[t].get("p_y") or 0))[:10]
    an = load_json("anomaly.json", {}, os.path.join(NEWS_STATE, "channel"))
    warn = an.get("warn", {}) if an.get("date") == today["date"] else {}
    exp = sum(rows[t]["p_y"] for t in topm)
    hist = load_json("anom_track.json", {"days": []})
    def dir_ok(t, pu):
        return pu is not None and abs(pu - 0.5) >= 0.1 and t in xs and xs[t] != 0 and (xs[t] > 0) == (pu > 0.5)
    hit = [t for t in topm if t in real]
    whit = [t for t in warn if t in real]
    dm = [t for t in hit if rows[t].get("p_dir_up") is not None and abs(rows[t]["p_dir_up"] - 0.5) >= 0.1]
    dw = [t for t in whit if abs((warn[t].get("up") or 0.5) - 0.5) >= 0.1]
    rec = dict(date=today["date"], top=topm, hit=hit, warn=list(warn), whit=whit, n_real=len(real),
               dir_n=len(dm) + len(dw), dir_ok=sum(dir_ok(t, rows[t]["p_dir_up"]) for t in dm) + sum(dir_ok(t, warn[t]["up"]) for t in dw))
    hist["days"] = [d for d in hist["days"] if d["date"] != rec["date"]][-120:] + [rec]
    save_json("anom_track.json", hist)
    return rec, exp, hist


# =============================================================================================
# Аномалии цен и объёмов (запускается из новостного бота каждые ~5 минут)
# =============================================================================================
def sec_of():
    """Сектора бумаг — из словаря новостного бота (nlp.js), чтобы не вести два списка."""
    try:
        txt = open(os.path.join(HERE, "..", "nlp.js"), encoding="utf-8").read()
        m = re.search(r"const SEC_OF = (\{.*?\});", txt)
        return json.loads(m.group(1)) if m else {}
    except Exception:  # noqa: BLE001
        return {}


SEC_NAME = {"bank": "банки", "oil": "нефтегаз", "metal": "металлурги", "retail": "ритейл", "tech": "IT и связь", "power": "энергетика", "chem": "химия",
            "devel": "девелоперы", "transport": "транспорт", "consumer": "потребсектор", "industry": "машиностроение"}
FUT_TH = {"BR": (2.5, 1.2), "NG": (5, 2.5), "TTF": (6, 3), "GOLD": (2, 1), "SILV": (3, 1.5), "PLT": (3.5, 1.8), "PLD": (3.5, 1.8), "COPPER": (2.5, 1.2),
          "NICKEL": (3, 1.5), "ALUM": (2.5, 1.2), "ZINC": (3, 1.5), "WHEAT": (3, 1.5), "SUGAR": (3, 1.5), "COCOA": (4, 2), "COFFEE": (4, 2)}


def recent_trades(t, kind="share", n=500):
    """Последние сделки (свежие первыми) — кто двигал цену: перевес и крупнейшая сделка."""
    path = f"engines/stock/markets/shares/boards/TQBR/securities/{t}/trades.json" if kind == "share" else f"engines/futures/markets/forts/securities/{t}/trades.json"
    cols = "TRADENO,TRADETIME,PRICE,VALUE,QUANTITY,BUYSELL" if kind == "share" else "TRADENO,TRADETIME,PRICE,QUANTITY,BUYSELL"
    try:
        return table(iss(path, reversed=1, limit=n, **{"iss.only": "trades", "trades.columns": cols}), "trades")
    except Exception as e:  # noqa: BLE001
        log("последние сделки", t, e)
        return []


def news_for(tks, hours=6, types=None):
    S = load_json("newsbot.json", {}, NEWS_STATE)
    now = time.time()
    out = []
    for c in (S.get("cl") or {}).values():
        if now - c.get("ts", 0) > hours * 3600:
            continue
        tk, ty = (c.get("key") or "|").split("|")[:2]
        if tk in tks or (types and tk == "РЫНОК" and any(ty.startswith(x) for x in types)):
            out.append(c)
    out.sort(key=lambda c: -c.get("ts", 0))
    return out[:2]


def ix_weights():
    """Веса бумаг в индексе МосБиржи (доли), обновляются раз в день."""
    w = load_json("ix_weights.json", {})
    today = now_msk().date().isoformat()
    if w.get("date") == today or DEMO:
        return w.get("w", {})
    try:
        rows, start = [], 0
        while True:
            part = table(iss("statistics/engines/stock/markets/index/analytics/IMOEX.json", start=start, limit=100, **{"iss.only": "analytics"}), "analytics")
            rows += part
            if len(part) < 100:
                break
            start += len(part)
        ww = {(r.get("secids") or r.get("ticker")): (r.get("weight") or 0) / 100 for r in rows}
        if ww:
            save_json("ix_weights.json", dict(date=today, w=ww))
            return ww
    except Exception as e:  # noqa: BLE001
        log("веса индекса", e)
    return w.get("w", {})


def index_markets():
    js = iss("engines/stock/markets/index/securities.json", **{"iss.only": "marketdata", "marketdata.columns": "SECID,CURRENTVALUE,LASTCHANGEPRC,HIGH,LOW,VALTODAY"})
    return {r["SECID"]: r for r in table(js, "marketdata")}


def index_move(stt, md):
    """Заметное движение индекса МосБиржи (каждый новый 1% за день или рывок от 0,7% за ~30 минут) — пост о бумагах, которые его двигают."""
    try:
        ixm = index_markets()
    except Exception as e:  # noqa: BLE001
        log("индекс: нет данных", e)
        return False
    im = ixm.get("IMOEX") or {}
    cur = im.get("CURRENTVALUE")
    if not cur:
        return False
    chg = (im.get("LASTCHANGEPRC") or 0) / 100
    s = stt.setdefault("ix", {"lv": 0, "hist": [], "jump_ts": 0})
    now = time.time()
    s["hist"] = [h for h in s["hist"] if now - h[0] < 3600] + [[now, cur]]
    lv = int(abs(chg) * 100) * (1 if chg > 0 else -1)
    reason, jump = None, None
    if abs(lv) >= 1 and (s["lv"] == 0 or (lv > 0) != (s["lv"] > 0) or abs(lv) > abs(s["lv"])):
        reason = "level"
    else:
        old = [h for h in s["hist"] if 25 * 60 <= now - h[0] <= 40 * 60]
        if old:
            jump = cur / old[0][1] - 1
            if abs(jump) >= float(os.environ.get("IX_JUMP", "0.007")) and now - s.get("jump_ts", 0) > 3600:
                reason = "jump"
    if not reason:
        return False
    W = ix_weights()
    up = (jump if reason == "jump" else chg) > 0
    rows = [dict(t=r["SECID"], chg=(r.get("LASTTOPREVPRICE") or 0) / 100, val=r.get("VALTODAY") or 0) for r in md]
    for r in rows:
        r["con"] = W.get(r["t"], 0) * r["chg"]
    drivers = sorted([r for r in rows if r["t"] in W], key=lambda r: -r["con"] if up else r["con"])
    liquid = [r for r in rows if r["val"] >= 50e6]
    tops = sorted(liquid, key=lambda r: -r["chg"])
    head = (f"📊 <b>Индекс МосБиржи {pc(chg, 2)}</b> — " + (f"{'рост' if up else 'снижение'} больше {abs(lv)}% за день" if reason == "level" else f"рывок {pc(jump, 1)} за полчаса"))
    txt = [head, f"IMOEX {fmt(cur, 0)}, диапазон дня {fmt(im.get('LOW'), 0)}–{fmt(im.get('HIGH'), 0)}, оборот по бумагам индекса {bln(im.get('VALTODAY') or 0)}.", ""]
    if drivers:
        txt.append(f"⚙️ <b>Кто {'тянет индекс вверх' if up else 'тянет индекс вниз'}</b> (вклад в процентных пунктах = вес в индексе × изменение цены):")
        txt += [f"• ${r['t']} {pc(r['chg'], 1)} — вклад {'+' if r['con'] > 0 else '−'}{fmt(abs(r['con']) * 100, 2)} п.п. индекса (вес {pc(W[r['t']], 1, False)})" for r in drivers[:5] if (r["con"] > 0) == up and r["con"]]
        against = [r for r in drivers[::-1][:3] if r["con"] and (r["con"] > 0) != up]
        if against:
            txt.append("Против рынка: " + ", ".join(f"${r['t']} {pc(r['chg'], 1)}" for r in against))
        txt.append("")
    if tops:
        txt.append("📈 <b>Сильнее всех выросли:</b> " + ", ".join(f"${r['t']} {pc(r['chg'], 1)}" for r in tops[:6] if r["chg"] > 0))
        txt.append("📉 <b>Сильнее всех упали:</b> " + ", ".join(f"${r['t']} {pc(r['chg'], 1)}" for r in tops[::-1][:6] if r["chg"] < 0))
        n_up = sum(1 for r in liquid if r["chg"] > 0)
        share = n_up / len(liquid)
        breadth = share if up else 1 - share
        txt.append(f"Ширина рынка: {'растут' if up else 'снижаются'} {round(breadth * 100)}% ликвидных акций — " +
                   ("движение широкое, участвует почти весь рынок." if breadth >= 0.7 else "движение держится на части бумаг, остальной рынок не подтверждает." if breadth < 0.55 else "движение умеренно широкое."))
    secs = [(name, (ixm.get(c) or {}).get("LASTCHANGEPRC")) for c, name in SECT_IX if (ixm.get(c) or {}).get("LASTCHANGEPRC") is not None]
    if secs:
        secs.sort(key=lambda x: -x[1])
        worst = [x for x in secs[::-1] if x not in secs[:3]][:3]
        txt.append("🏭 Отраслевые индексы: сильнее всех — " + ", ".join(f"{n} {pc(v / 100, 1)}" for n, v in secs[:3]) + ("; слабее всех — " + ", ".join(f"{n} {pc(v / 100, 1)}" for n, v in worst) if worst else ""))
    cls = all_clusters()
    nn = news_pick(cls, now - 4 * 3600, market=True, min_pct=0.3, k=4) + news_pick(cls, now - 4 * 3600, tks={r["t"] for r in drivers[:3]}, min_pct=0.3, k=3)

    def fits(c):
        # причина движения — только новость о российском рынке, чьё направление совпадает с движением индекса
        tx = clean_tx(c["first"].get("tx"))
        if (c.get("key") or "").startswith("РЫНОК") and not RU_REL.search(tx):
            return False
        d = (c.get("wt") or {}).get("dir") or (1 if (c.get("score") or 0) > 0 else -1 if (c.get("score") or 0) < 0 else 0)
        return d == 0 or (d > 0) == up
    nn = [c for c in nn if fits(c)]
    if nn:
        txt += ["", "📰 <b>Возможные причины — свежие новости в ту же сторону:</b>"] + [news_line(c, short=True) for c in nn[:3]]
    else:
        txt += ["", "📰 Весомых новостей за 4 часа нет — вероятно, движение за счёт потоков крупных участников, внешнего фона (нефть, рубль) или технических уровней."]
    txt += ["", "<i>Пост выходит, когда индекс за день проходит очередной 1% или делает рывок от 0,7% за полчаса.</i>", "#индекс #рынок"]
    import card as cardmod
    src, img = os.path.join(OUT, "ix_move.json"), os.path.join(OUT, "ix_move.png")
    json.dump({"t": "РЫНОК", "kind": "index", "tf": "1h", "label": "движение индекса", "sign": 1 if up else -1,
               "caption": "Рынок пошёл вверх — кто ведёт" if up else "Рынок под давлением — кто тянет вниз",
               "fact": "Лидеры движения: " + ", ".join(f"{r['t']} {pc(r['chg'], 1)}" for r in drivers[:4])}, open(src, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        cardmod.main(src, img)
    except Exception as e:  # noqa: BLE001
        log("картинка индекса", e)
        img = None
    PUB.post("\n".join(txt), [(img, "Индекс МосБиржи: дневные свечи")] if img and os.path.exists(img) else None)
    if reason == "level":
        s["lv"] = lv
    else:
        s["jump_ts"] = now
    return True


def watch_tomorrow(md, now):
    """«На что обратить внимание завтра»: конкретные бумаги и почему."""
    tmr = next_wd(now.date())
    focus, notes = {}, []

    def add(t, why):
        focus.setdefault(t, []).append(why)
    # дивиденды
    try:
        import divs
        upc = divs.upcoming(60)
        dst = divs.history_stats()
    except Exception as e:  # noqa: BLE001
        log("дивиденды", e)
        upc, dst = [], {}
    for r in upc:
        if r.get("last_buy") == tmr.isoformat():
            add(r["t"], f"последний день покупки под дивиденд {fmt(r['amount'], 2)} ₽ ({pc(r['yld'], 1, False)})")
        if r.get("last_buy") == now.date().isoformat():
            s = dst.get(r["t"]) or {}
            g = (s.get("share") if s.get("share") is not None else 0.8) * r["yld"]
            add(r["t"], f"дивидендный гэп (отсечка): ожидаемо около {pc(-abs(g), 1)}" + (f", гэп исторически закрывался в среднем за {fmt(s['close_med'], 0)} торг. дн." if s.get("close_med") is not None else ""))
    # сигналы стратегии
    sig = load_json("signals.json", None, NEWS_STATE)
    for q in (sig or {}).get("pend", []):
        add(q["t"], f"сигнал стратегии на открытие: {'покупка' if q['side'] > 0 else 'шорт'} ≈ {fmt(q['entry'], 2)}, стоп {fmt(q['stop'], 2)}")
    # аномалии дня
    an = load_json("anomaly.json", {}, os.path.join(NEWS_STATE, "channel")) or load_json("anomaly.json", {})
    if an.get("date") == now.date().isoformat():
        for t, v in (an.get("sent") or {}).items():
            if re.fullmatch(r"[A-Z0-9]{2,6}", t) and not re.search(r"[FGHJKMNQUVXZ]\d$", t):
                add(t, f"аномалия сегодня ({pc(v[1], 1)} за день) — проверить, продолжится ли движение")
    # весомые новости дня
    t0 = dt.datetime(now.year, now.month, now.day, tzinfo=MSKTZ).timestamp()
    for c in news_pick(all_clusters(), t0, min_pct=0.75, k=12):
        tk = c["key"].split("|")[0]
        if tk != "РЫНОК" and not any(w.startswith("новость") for w in focus.get(tk, [])):
            d = (c.get("wt") or {}).get("dir") or (1 if (c.get("score") or 0) > 0 else -1)
            add(tk, f"новость с высоким весом: «{esc(cut(clean_tx(c['first'].get('tx')), 90))}» — по истории после таких новостей бумага чаще шла {'вверх' if d > 0 else 'вниз'}")
    # пробои 20-дневных уровней среди самых торгуемых
    for r in sorted(md, key=lambda r: -(r.get("VALTODAY") or 0))[:40]:
        t = r["SECID"]
        try:
            B = share_candles(t, 45)
        except Exception:  # noqa: BLE001
            continue
        if len(B) < 22 or B[-1]["t"] != now.date().isoformat():
            continue
        hi, lo = max(b["h"] for b in B[-21:-1]), min(b["l"] for b in B[-21:-1])
        if B[-1]["c"] > hi:
            add(t, f"закрылась выше 20-дневного максимума {fmt(hi, 2)} — пробой вверх")
        elif B[-1]["c"] < lo:
            add(t, f"закрылась ниже 20-дневного минимума {fmt(lo, 2)} — пробой вниз")
        time.sleep(0.05)
    # события рынка
    cbr = next_cbr()
    if cbr and 0 <= (dt.date.fromisoformat(cbr) - tmr).days <= 1:
        notes.append(f"🏦 Заседание Банка России по ставке — {ddmm(cbr)}: чувствительны банки, девелоперы, ОФЗ и бумаги с высоким долгом.")
    if tmr.weekday() == 2:
        notes.append("🏛 Среда — аукционы Минфина по ОФЗ, вечером Росстат публикует недельную инфляцию.")
    nxt = [r for r in upc if r.get("last_buy") and tmr.isoformat() < r["last_buy"] <= (tmr + dt.timedelta(days=7)).isoformat()]
    if nxt:
        notes.append("💰 Отсечки в ближайшую неделю: " + ", ".join(f"${r['t']} (купить до {ddmm(r['last_buy'])}, {pc(r['yld'], 1, False)})" for r in nxt[:6]))
    return tmr, focus, notes


def anomaly():
    now = now_msk()
    hm = now.strftime("%H:%M")
    if now.weekday() >= 5 or not ("10:05" <= hm <= "23:50"):
        log("аномалии: вне торговой сессии")
        return
    today = now.date().isoformat()
    stt = load_json("anomaly.json", {})
    if stt.get("date") != today:
        stt = {"date": today, "snap": {}, "sent": {}, "n": 0}
    md = liquid_shares(400)
    avg = turnover_avg([r["SECID"] for r in md])
    rank = {t: i for i, t in enumerate(sorted(avg, key=lambda t: -(avg[t] or 0)))}
    try:
        ix = index_candles("IMOEX", 5)
        ixc = ix[-1]["c"] / ix[-2]["c"] - 1
    except Exception:  # noqa: BLE001
        ixc = 0.0
    try:
        fut = fut_fronts()
    except Exception as e:  # noqa: BLE001
        log("фьючерсы", e)
        fut = {}
    secs = sec_of()
    mins = (now.hour * 60 + now.minute) - 600
    frac = max(0.06, min(1.0, mins / 530)) if mins < 530 else min(1.25, 1 + (mins - 540) / 1700)
    try:
        index_move(stt, md)
    except Exception as e:  # noqa: BLE001
        log("движение индекса", e)
    exset = set()
    try:
        import divs
        pw = prev_wd(now.date()).isoformat()
        exset = {r["t"] for r in divs.upcoming(60) if r.get("last_buy") == pw}
    except Exception:  # noqa: BLE001
        pass
    found = []
    by_sec = {}
    for r in md:
        by_sec.setdefault(secs.get(r["SECID"], "?"), []).append((r.get("LASTTOPREVPRICE") or 0) / 100)
    for r in md:
        t, last, val = r["SECID"], r.get("LAST"), r.get("VALTODAY") or 0
        if not last:
            continue
        chg = (r.get("LASTTOPREVPRICE") or 0) / 100
        tier = 1 if rank.get(t, 999) < 15 else 2 if rank.get(t, 999) < 50 else 3
        prev = stt["snap"].get(t)
        jump = last / prev[0] - 1 if prev and prev[0] else 0
        a20 = avg.get(t) or 0
        vx = val / (a20 * frac) if a20 else None
        exc = chg - ixc
        why = []
        if t in exset:
            exc, jump = 0.0, 0.0  # день отсечки: падение на размер дивиденда — не аномалия
        if abs(exc) >= (4 if tier == 1 else 6 if tier == 2 else 8) / 100 and val >= (100e6 if tier == 1 else 10e6):
            why.append(f"цена {pc(chg, 1)} за день при индексе {pc(ixc, 1)}")
        if abs(jump) >= (2 if tier == 1 else 3.5 if tier == 2 else 5) / 100 and val >= 5e6:
            why.append(f"рывок {pc(jump, 1)} за последние минуты")
        if vx and vx >= (4 if tier < 3 else 6) and val >= (300e6 if tier == 1 else 50e6 if tier == 2 else 30e6):
            why.append(f"оборот {fmt(vx, 1)}× к обычному на это время ({bln(val)})")
        stt["snap"][t] = [last, val]
        if why:
            lvl = max(abs(exc), abs(jump)) + (vx or 0) / 100
            old = stt["sent"].get(t)
            if old and lvl < old[0] * 1.5 and (old[1] > 0) == (chg > 0):
                continue
            found.append(dict(t=t, kind="share", name=r.get("NAME", t), last=last, chg=chg, exc=exc, jump=jump, vx=vx, val=val, tier=tier, why=why, lvl=lvl, sec=secs.get(t)))
    for a, f in fut.items():
        t, last = f["secid"], f.get("last")
        if not last:
            continue
        th, tj = FUT_TH.get(a, (3, 1.5))
        prev = stt["snap"].get(t)
        jump = last / prev[0] - 1 if prev and prev[0] else 0
        stt["snap"][t] = [last, f.get("val") or 0]
        why = []
        if abs(f["chg"]) >= th / 100:
            why.append(f"цена {pc(f['chg'], 1)} за день")
        if abs(jump) >= tj / 100:
            why.append(f"рывок {pc(jump, 1)} за последние минуты")
        if why:
            lvl = max(abs(f["chg"]), abs(jump))
            old = stt["sent"].get(t)
            if old and lvl < old[0] * 1.5 and (old[1] > 0) == (f["chg"] > 0):
                continue
            found.append(dict(t=t, kind="fut", asset=a, name=f["name"], last=last, chg=f["chg"], exc=f["chg"], jump=jump, vx=None, val=f.get("val"), tier=1, why=why, lvl=lvl))
    try:
        anom_intraday(stt, md, ixc, now)
    except Exception as e:  # noqa: BLE001
        log("ранние признаки аномалий", e)
    found.sort(key=lambda x: -x["lvl"])
    posted = 0
    for x in found:
        if posted >= 3 or stt["n"] >= int(os.environ.get("ANOMALY_PER_DAY", "3")):
            break
        try:
            anomaly_post(x, md, by_sec, ixc)
            posted += 1
            stt["n"] += 1
            stt["sent"][x["t"]] = [x["lvl"], x["chg"]]
        except Exception as e:  # noqa: BLE001
            log("аномалия", x["t"], e)
    save_json("anomaly.json", stt)
    log("аномалий найдено", len(found), "опубликовано", posted)


def anomaly_post(x, md, by_sec, ixc):
    t, up = x["t"], (x["exc"] if x["kind"] == "share" else x["chg"]) > 0
    lab = "$" + t if x["kind"] == "share" else f"{FUT[x['asset']][0]} ({t})"
    txt = [f"🚨 <b>Аномалия: {lab} — {'резкий рост' if up else 'резкое падение'}" + (" на большом объёме" if x.get("vx") and x["vx"] >= 4 else "") + "</b>",
           "<blockquote>" + "; ".join(x["why"]) + ".</blockquote>", ""]
    # кто двигал: последние сделки
    tr = recent_trades(t, x["kind"])
    if tr:
        k = 1
        if x["kind"] == "fut":
            f = fut_fronts([x["asset"]]).get(x["asset"]) or {}
            k = (f.get("step") or 1) / (f.get("minstep") or 1)
        vals = [(r.get("VALUE") or r.get("PRICE", 0) * r.get("QUANTITY", 0) * k, r.get("BUYSELL"), r) for r in tr]
        vb = sum(v for v, s_, _ in vals if s_ == "B")
        vs = sum(v for v, s_, _ in vals if s_ == "S")
        big = max(vals, key=lambda z: z[0])
        if vb + vs:
            n_ = len(tr)
            if x["kind"] == "share":
                try:
                    bh = behavior(x["chg"], flow=(vb - vs) / (vb + vs), x=x.get("vx"), oi=stock_oi().get(t), r5=ret5(t) if x["chg"] >= 0.03 else None, ndir=news_dir(t))
                except Exception as e:  # noqa: BLE001
                    log("поведение", t, e)
                    bh = []
            else:
                bh = []
            txt.append(f"👥 <b>Кто двигал:</b> {'покупатели' if vb >= vs else 'продавцы'} — перевес <b>{pc((vb - vs) / (vb + vs), 0)}</b> в последних {n_} {plural(n_, 'сделке', 'сделках', 'сделках')}; "
                       f"крупнейшая — {'покупка' if big[1] == 'B' else 'продажа'} на {bln(big[0])} в {str(big[2].get('TRADETIME', ''))[:5]}.")
            if bh:
                txt.append("🔎 <b>Что может стоять за движением:</b> " + esc("; ".join(bh)) + ".")
            txt.append("")
    # причины
    if x["kind"] == "share":
        cl = news_for([t])
    else:
        cl = news_for(FUT[x["asset"]][2], types={"BR": ["oil_", "geo_", "sanctions"], "NG": ["oil_", "geo_", "sanctions"], "TTF": ["oil_", "geo_", "sanctions"],
                                                     "GOLD": ["geo_", "cbr_"], "SILV": ["geo_"], "WHEAT": ["geo_", "tax"],
                                                     "Si": ["ruble_", "cbr_", "sanctions", "oil_", "geo_"], "CNY": ["ruble_", "cbr_", "sanctions", "oil_", "geo_"],
                                                     "Eu": ["ruble_", "cbr_", "sanctions", "geo_"], "ED": ["geo_"], "UCNY": ["geo_"]}.get(x["asset"]))
    if cl:
        txt.append("📰 <b>Возможная причина — свежие новости:</b>")
        for c in cl:
            head = cut(clean_tx(c["first"].get("tx")), 170)
            txt.append(f"• {esc(head)} — <a href=\"{c['first']['link']}\">{esc(src_name(c['first'].get('ch', '')))}</a>")
    elif x["kind"] == "fut":
        exp = (fut_fronts([x["asset"]]).get(x["asset"]) or {}).get("exp")
        txt.append("📰 <b>Возможная причина:</b> в публичных новостях за 6 часов объяснения нет — вероятно, движение мировых цен на это сырьё, крупный участник или приближение экспирации контракта"
                   + (f" ({exp[8:10]}.{exp[5:7]})" if exp else "") + ". Проверьте зарубежные котировки.")
    else:
        txt.append("📰 <b>Возможная причина:</b> в публичных новостях за 6 часов объяснения нет — так бывает при выходе крупного участника, перед корпоративными событиями (отчётность, дивиденды, допэмиссия) или из-за слухов. Осторожно: такие движения часто частично откатываются.")
    if x["kind"] == "share":
        rl = reg_lines(t, 24)
        if rl:
            txt.append("📍 Региональные события там, где у компании активы: " + "; ".join(rl[:2]))
    # контекст и влияние
    if x["kind"] == "share":
        sec = x.get("sec")
        peers = by_sec.get(sec, []) if sec else []
        pm = st.mean(peers) if len(peers) >= 3 else None
        ctx = f"Индекс МосБиржи {pc(ixc, 1)}" + (f", сектор «{SEC_NAME.get(sec, sec)}» в среднем {pc(pm, 1)}" if pm is not None else "")
        own = pm is None or abs(x["chg"] - pm) > abs(pm)
        txt.append(ctx + (" — движение индивидуальное, не всего рынка." if own else " — двигается весь сектор."))
        eff = []
        if sec:
            peers_t = [r["SECID"] for r in md if sec_of_cache().get(r["SECID"]) == sec and r["SECID"] != t][:4]
            if peers_t:
                eff.append(f"бумаги сектора ({' '.join('$' + p for p in peers_t)}) — {'подтягиваются вверх' if up else 'под давлением'}, если причина отраслевая")
        if x["tier"] == 1:
            eff.append(f"индекс МосБиржи — бумага из числа самых торгуемых, её движение {'поддерживает' if up else 'тянет вниз'} индекс")
        txt.append("На что может повлиять: " + ("; ".join(eff) if eff else "в основном на саму бумагу — во 2–3 эшелоне такие рывки редко передаются рынку") + ".")
    else:
        rel = FUT[x["asset"]][2]
        if rel:
            txt.append(f"На что может повлиять: {' '.join('$' + r for r in rel)} — {'позитивно' if up else 'негативно'} для выручки экспортёров этого сырья; для рубля — {'поддержка' if up and x['asset'] in ('BR', 'NG', 'TTF') else 'давление' if x['asset'] in ('BR', 'NG', 'TTF') else 'влияние слабое'}.")
    # контекст — в разворачиваемую цитату, чтобы главное читалось сразу
    k0 = next((i for i, l in enumerate(txt) if l.startswith(("Индекс МосБиржи", "На что может повлиять", "📍"))), len(txt))
    ctx_l = [l for l in txt[k0:] if l]
    txt = txt[:k0] + ["", "<blockquote expandable>📊 <b>Контекст</b>\n" + "\n".join(ctx_l + ["<i>Аномалия — движение цены или оборота намного сильнее обычного. Это повод присмотреться, а не готовый сигнал на сделку.</i>"]) + "</blockquote>",
                      "#аномалия" + (" #" + t if x["kind"] == "share" else "")]
    import card as cardmod
    src = os.path.join(OUT, f"an_{t}.json")
    img = os.path.join(OUT, f"an_{t}.png")
    json.dump({"t": t, "kind": x["kind"], "tf": "1h", "name": lab, "label": "аномалия", "sign": 1 if up else -1,
               "caption": ("Кто-то очень торопится купить" if up else "Кто-то очень торопится продать") if (x.get("vx") or 0) >= 4 else ("Резкий рывок вверх" if up else "Резкий обвал"),
               "fact": "; ".join(x["why"])}, open(src, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        cardmod.main(src, img)
    except Exception as e:  # noqa: BLE001
        log("картинка аномалии", e)
        img = None
    PUB.post("\n".join(txt), [(img, f"{lab}: дневные свечи")] if img and os.path.exists(img) else None)


_SEC = None


def sec_of_cache():
    global _SEC
    if _SEC is None:
        _SEC = sec_of()
    return _SEC


# =============================================================================================
# Исследования на истории (research/study.py + проверка research/verify.py): толпа, смежные бумаги, выходные
# =============================================================================================
RESEARCH_DIR = os.environ.get("RESEARCH_STATS") or os.path.join(HERE, "..", "research", "out")
SEC_RU2 = dict(SEC_RU, consumer="потребсектор", industry="машиностроение")
TYPE_RU = {"div_rec": "дивиденды", "div_cut": "отказ от дивидендов", "buyback": "обратный выкуп", "spo": "допэмиссия", "ipo": "IPO", "results_up": "рост прибыли/выручки",
           "results_down": "снижение прибыли", "guidance_up": "прогноз повышен", "guidance_down": "прогноз понижен", "analyst_up": "аналитики повысили оценку",
           "analyst_down": "аналитики понизили оценку", "sanctions": "санкции", "legal": "суды, штрафы, обыски", "tax": "налоги", "production_up": "рост добычи/продаж",
           "production_down": "снижение добычи/продаж", "mna": "сделка M&A", "delist_redom": "делистинг/редомициляция", "management": "смена руководства",
           "geo_escal": "эскалация", "geo_peace": "переговоры", "oil_up": "нефть дорожает", "oil_down": "нефть дешевеет", "tone": "тон новости"}
_RS = None


def research():
    global _RS
    if _RS is None:
        _RS = {}
        for p in (os.path.join(RESEARCH_DIR, "stats.json"),):
            try:
                _RS = json.load(open(p, encoding="utf-8"))
                break
            except Exception:  # noqa: BLE001
                pass
    return _RS


_SEC_FULL = None


def sector(t):
    """Сектор бумаги по словарю бота (nlp.js SEC_OF) — шире, чем impact.json."""
    global _SEC_FULL
    if _SEC_FULL is None:
        try:
            src = open(os.path.join(HERE, "..", "nlp.js"), encoding="utf-8").read()
            _SEC_FULL = json.loads(re.search(r"const SEC_OF = (\{.*?\});", src).group(1))
        except Exception:  # noqa: BLE001
            _SEC_FULL = dict(SEC_OF)
    return _SEC_FULL.get(t) or SEC_OF.get(t)


def company_news(t, since_ts, cls=None):
    """Новости о компании (кластеры бота с этим тикером) после since_ts."""
    out = []
    for c in cls if cls is not None else all_clusters():
        tk = (c.get("key") or "|").split("|")[0]
        if tk == t and c.get("ts", 0) >= since_ts:
            out.append(c)
    return out


def ix_day_change():
    try:
        js = iss("engines/stock/markets/index/securities/IMOEX.json", **{"iss.only": "marketdata", "marketdata.columns": "SECID,LASTCHANGEPRC"})
        r = table(js, "marketdata")
        return (r[0].get("LASTCHANGEPRC") or 0) / 100 if r else 0.0
    except Exception as e:  # noqa: BLE001
        log("индекс", e)
        return 0.0


def stat_txt(s, sign=1, h="5 дней"):
    if not s or not s.get("n"):
        return "истории мало"
    return f"{pc(s['mean'] * sign, 1)} к индексу за {h}, в {round((s['hit'] if sign > 0 else 1 - s['hit']) * 100)}% случаев (выборка {s['n']})"


def dir_txt(s, h="5 дней", base="к индексу"):
    """Статистика «по направлению движения»: плюс — движение продолжалось, минус — откатывалось."""
    if not s or not s.get("n"):
        return "истории мало"
    m = s["mean"]
    if m >= 0:
        return f"движение продолжалось — в среднем ещё на {pc(m, 1, False)} {base} за {h}, в {round(s['hit'] * 100)}% случаев (выборка {s['n']})"
    return f"движение откатывалось — в среднем на {pc(-m, 1, False)} {base} за {h}, в {round((1 - s['hit']) * 100)}% случаев (выборка {s['n']})"


CROWD_DEF = {"win": 3, "thr": 0.06, "vx": 2.0, "liq": 30e6}


def crowd_cfg(side):
    """Параметры «толпы» для стороны: подтверждённое правило обучения на разных промежутках 2008–2026 или стандарт (для справки)."""
    R = (research().get("crowd") or {}).get("up" if side > 0 else "down") or {}
    p = R.get("params") if R.get("verified") and R.get("params") else None
    return dict(p or research().get("crowd_params") or CROWD_DEF), R


def crowd_check(t, cls=None, frac=1.0):
    """Психология толпы по бумаге сейчас: движение к индексу за окно правила, оборот к норме, есть ли новости о компании.
    Окно, порог и оборот — из правила, выбранного обучением на истории с 2008 г. (своё для роста и для падения)."""
    try:
        B = share_candles(t, 60)
        I = {b["t"]: b["c"] for b in index_candles("IMOEX", 60)}
    except Exception as e:  # noqa: BLE001
        log("толпа: свечи", t, e)
        return None
    B = [b for b in B if dt.date.fromisoformat(b["t"][:10]).weekday() < 5]
    if len(B) < 30 or B[-1]["t"] not in I:
        return None
    today = B[-1]["t"][:10] == now_msk().date().isoformat()
    best = None
    for side in (1, -1):
        prm, R = crowd_cfg(side)
        w = int(prm["win"])
        if B[-1 - w]["t"] not in I or len(B) < w + 22:
            continue
        e = (B[-1]["c"] / B[-1 - w]["c"] - 1) - (I[B[-1]["t"]] / I[B[-1 - w]["t"]] - 1)
        if (1 if e > 0 else -1) != side:
            continue
        vols = [b["v"] for b in B[-w:]]
        if today and frac < 1:
            vols[-1] = vols[-1] / max(frac, 0.1)
        base = sorted(b["v"] for b in B[-w - 20:-w])
        vx = (sum(vols) / w) / base[len(base) // 2] if base and base[len(base) // 2] else 0
        since = dt.datetime.fromisoformat(B[-w]["t"][:10]).replace(hour=0, tzinfo=MSKTZ).timestamp() - 6 * 3600  # с вечера дня перед окном
        news = [c for c in company_news(t, since, cls) if (c.get("key") or "").split("|")[-1] != "tone"]
        best = dict(t=t, e3=e, win=w, vx=vx, news=len(news), dir=side, crowd=abs(e) >= prm["thr"] and vx >= prm["vx"] and not news, last=B[-1]["c"],
                    rule=bool(R.get("verified")))
    return best


def crowd_line(x, with_signal=True):
    """Текст про толпу и (если правило подтверждено обучением на разных промежутках и проверяющим агентом) сигнал."""
    R = (research().get("crowd") or {}).get("up" if x["dir"] > 0 else "down") or {}
    h = int(R.get("h") or 5)
    s_all = R.get("stat") or (R.get("all") or {}).get("f5")
    s_news = (R.get("with_news") or {}).get("f5")
    w = x.get("win", 3)
    head = f"🧠 <b>${x['t']}</b> {pc(x['e3'], 1)} к индексу за {w} {'дня' if w < 5 else 'дней'}, оборот {fmt(x['vx'], 1)}× к норме — <b>новостей о компании нет</b>"
    if not R:
        return head + ". Движение держится на настроении участников; статистика по таким случаям ещё считается.", None
    mode, ok = R.get("mode"), R.get("verified")
    o = R.get("oos") or {}
    oos = (f" Проверка на разных промежутках истории 2008–2026 (правило подбиралось на прошлом, проверялось на следующем годе): "
           f"после издержек {pc(o['mean'], 1)} на сделку, плюс в {round(o.get('pos_years', 0) * 100)}% лет") if o.get("n") else ""
    kind = "рывков" if x["dir"] > 0 else "падений"
    if ok and mode == "continue":
        why = f"Толпа сильнее здравого смысла: на истории после таких {kind} без новостей {dir_txt(s_all, h=f'{h} дн.')}." + oos
        side = x["dir"]
    elif ok and mode == "reverse":
        why = f"На истории после таких {kind} без новостей {dir_txt(s_all, h=f'{h} дн.')} — эмоции выдыхались." + oos
        side = -x["dir"]
    else:
        why = (f"На истории после таких {kind} без новостей {dir_txt(s_all)}, но обучение на разных промежутках 2008–2026 и независимая проверка "
               f"не нашли устойчивого преимущества — сигнала нет")
        side = 0
    cmp_ = f" Для сравнения, когда были новости: {dir_txt(s_news)}." if s_news and s_news.get("n") else ""
    line = head + ". " + why.rstrip(".") + "." + cmp_
    sig = None
    if with_signal and side:
        lt = level_target(x["t"], side, ndir=0, chg=0)
        if lt:
            sig = (f"   🎯 {'ЛОНГ' if side > 0 else 'ШОРТ'} по {'импульсу' if side == x['dir'] else 'откату'}: цель <code>{fmt(lt['tgt'], dec_of(lt['last']))}</code> ({pc(lt['move'], 1)}), "
                   f"отмена за <code>{fmt(lt['stop'], dec_of(lt['last']))}</code>, горизонт до {h} дн.")
    return line, sig


def crowd_scan(md_all, avg, frac, cls, limit=10):
    prm = research().get("crowd_params") or {"liq": 30e6}
    seen = load_json("crowd_seen.json", {})
    today = now_msk().date().isoformat()
    seen = {k: v for k, v in seen.items() if v == today}
    cand = [r for r in md_all if (avg.get(r["SECID"]) or 0) >= prm.get("liq", 30e6) and abs((r.get("LASTTOPREVPRICE") or 0) / 100) >= 0.015 and r["SECID"] not in seen]
    cand = sorted(cand, key=lambda r: -abs(r.get("LASTTOPREVPRICE") or 0))[:limit]
    out = []
    for r in cand:
        x = crowd_check(r["SECID"], cls, frac)
        if x and x["crowd"]:
            out.append(x)
            seen[x["t"]] = today
        time.sleep(0.05)
    save_json("crowd_seen.json", seen)
    return sorted(out, key=lambda x: -abs(x["e3"]))[:3]


def spill_scan(md_all, avg, frac, ix_chg):
    """Смежные бумаги: лидер сегодня резко обогнал индекс на большом обороте — какие соседи ещё не отреагировали.
    Давать ли сигнал — решает обучение на разных промежутках 2008–2026 (research/walkforward.py) и проверяющий агент:
    отдельно для рывков вверх и вниз. Если лучший вариант на истории — «не давать», бот пишет только справку."""
    live = research().get("spill_live") or {}
    pairs_info = research().get("spill") or []
    by = {r["SECID"]: r for r in md_all}
    chg = lambda t: (by[t].get("LASTTOPREVPRICE") or 0) / 100 - ix_chg if t in by else None
    out, used = [], set()
    for d, dn in ((1, "up"), (-1, "down")):
        L = live.get(dn) or {}
        on = bool(L.get("on"))
        prm = (L.get("params") if on else None) or research().get("spill_params") or {"thr": 0.04, "vx": 2.5, "liq": 50e6, "lag": 0.3}
        leaders, n_all = [], 0
        for r in md_all:
            t, a = r["SECID"], avg.get(r["SECID"]) or 0
            e = chg(t)
            if a >= prm["liq"] and e is not None and e * d >= prm["thr"] and (r.get("VALTODAY") or 0) / (a * frac) >= prm["vx"]:
                n_all += 1
                if sector(t):
                    leaders.append((t, e))
        if not leaders or n_all > 6:  # 7+ бумаг сразу — движение всего рынка, а не цепочка
            continue
        for t, e in sorted(leaders, key=lambda z: -abs(z[1]))[:2]:
            s1 = sector(t)
            lag = prm.get("lag", 0.3)
            lagging = lambda u: chg(u) is not None and (chg(u) * d < lag * abs(e) if lag > 0 else chg(u) * d <= 0)
            if on:
                scope = L.get("scope")
                okp = {tuple(p) for p in (L.get("pairs") or [])}
                fit = (lambda u: sector(u) == s1) if scope == "same" else (lambda u: (s1, sector(u)) in okp) if scope == "pairs" else (lambda u: bool(sector(u)))
                prio = [q[1] for q in (L.get("top_peers") or []) if q[0] == s1]
                peers = [u for u in by if u != t and u not in used and fit(u) and (avg.get(u) or 0) >= 30e6 and lagging(u)]
                peers.sort(key=lambda u: (prio.index(u) if u in prio else 99, -(avg.get(u) or 0)))
                peers = peers[:3]
                if not peers:
                    continue
                used.update(peers)
                st, o, h = L.get("stat") or {}, L.get("oos") or {}, int(L.get("h") or 3)
                where = {"same": "бумаги того же сектора", "pairs": "бумаги связанных секторов", "all": "отставшие бумаги рынка"}.get(scope, "соседи")
                line = (f"🔗 За <b>${t}</b> ({pc(e, 1)} к индексу) на истории {'подтягивались' if d > 0 else 'проседали'} {where}. Отстали сегодня: "
                        + ", ".join(f"${u} ({pc(chg(u), 1)})" for u in peers)
                        + (f". С 2008 г.: {pc(st['mean'], 1)} к индексу за {h} дн. в среднем, в {round(st['hit'] * 100)}% случаев (выборка {st['n']})" if st.get("n") else "")
                        + (f"; проверка на разных промежутках — после издержек {pc(o['mean'], 1)} на сделку, плюс в {round(o.get('pos_years', 0) * 100)}% лет" if o.get("n") else ""))
                sig = None
                lt = level_target(peers[0], d, ndir=0, chg=chg(peers[0]) or 0)
                if lt:
                    sig = (f"   🎯 {'ЛОНГ' if d > 0 else 'ШОРТ'} ${peers[0]} вслед за ${t}: цель <code>{fmt(lt['tgt'], dec_of(lt['last']))}</code> ({pc(lt['move'], 1)}), "
                           f"отмена за <code>{fmt(lt['stop'], dec_of(lt['last']))}</code>, горизонт до {h} дн.")
                out.append((line + ".", sig))
            else:
                ps = sorted([p for p in pairs_info if p["s1"] == s1 and p["dir"] == d], key=lambda p: -(p["train"].get("mean") or 0))[:1]
                peers = [u for u in by if u != t and u not in used and ps and sector(u) == ps[0]["s2"] and (avg.get(u) or 0) >= 30e6 and lagging(u)][:3]
                if not peers:
                    continue
                used.update(peers)
                out.append((f"🔗 За <b>${t}</b> ({pc(e, 1)} к индексу) отстали: " + ", ".join(f"${u} ({pc(chg(u), 1)})" for u in peers)
                            + f". Справка, не сигнал: обучение на разных промежутках истории 2008–2026 не нашло устойчивого преимущества в том, чтобы "
                            + f"покупать отставших соседей после {'рывка' if d > 0 else 'падения'} лидера — лучший вариант на истории «не входить».", None))
    return out


def news_movers(md, now, nr=None):
    """Вечер: новости дня о компаниях, которые могут дать аномальное движение завтра (реакция ещё не отыграна)."""
    cls = all_clusters()
    start = dt.datetime(now.year, now.month, now.day, 12, 0, tzinfo=MSKTZ).timestamp()
    by = {r["SECID"]: r for r in md}
    best = {}
    for c in cls:
        tk, ty = ((c.get("key") or "|") + "|").split("|")[:2]
        w = c.get("wt") or {}
        if tk in ("", "РЫНОК") or ty == "tone" or c.get("ts", 0) < start or w.get("pct", 0) < 0.75:
            continue
        raw = clean_tx(c["first"].get("tx"))
        if DIGEST.search(raw) or NOISE.search(c["first"].get("tx") or ""):
            continue
        if tk not in best or w.get("W", 0) > (best[tk].get("wt") or {}).get("W", 0):
            best[tk] = c
    rows = []
    for tk, c in best.items():
        w = c["wt"]
        ty = c["key"].split("|")[1]
        d = 1 if ty in NEWS_POS else -1 if ty in NEWS_NEG else (w.get("dir") or 0)
        ch = (by[tk].get("LASTTOPREVPRICE") or 0) / 100 if tk in by else None
        E = w.get("E") or 0
        late = dt.datetime.fromtimestamp(c["ts"], MSKTZ).strftime("%H:%M") >= "18:40"
        priced = ch is not None and d and ch * d >= max(0.6 * E, 0.01)
        if priced:
            continue
        p_an = (nr or {}).get(tk, {}).get("p_y")
        rows.append((w.get("W", 0) + (0.01 if late else 0) + (p_an or 0) * 0.01, tk, ty, c, ch, late, p_an, d, E))
    rows.sort(key=lambda r: -r[0])
    out = []
    for _, tk, ty, c, ch, late, p_an, d, E in rows[:6]:
        tm = dt.datetime.fromtimestamp(c["ts"], MSKTZ).strftime("%H:%M")
        why = ("вышла после основной сессии — рынок отреагирует завтра" if late else
               f"сегодня цена {pc(ch, 1)}, реакция ещё не отыграна" if ch is not None else "реакции цены пока нет")
        out.append(f"• <b>${tk}</b> — {TYPE_RU.get(ty, ty)}, {tm}: {esc(cut(clean_tx(c['first'].get('tx')), 110))} "
                   f"<i>({why}; на истории такие новости давали {pc(E * (d or 1), 1)} к индексу за 5 дней" + (f"; модель аномалий: {p_an:.0%}" if p_an else "") + ")</i>")
    return out


# ---------------------------------------------------------------------------------------------
# Торги выходного дня (сессии Мосбиржи; их же показывают Т-Инвестиции): итоги и прогноз на понедельник — воскресенье вечером
# ---------------------------------------------------------------------------------------------
def weekend_review():
    now = now_msk()
    sat = now.date() - dt.timedelta(days=(now.date().weekday() - 5) % 7)
    md = liquid_shares(120)
    avg = turnover_avg([r["SECID"] for r in md])
    rows = []
    for r in md:
        t = r["SECID"]
        try:
            B = share_candles(t, 12)
        except Exception as e:  # noqa: BLE001
            log("выходные", t, e)
            continue
        fri = [b for b in B if dt.date.fromisoformat(b["t"][:10]) < sat and dt.date.fromisoformat(b["t"][:10]).weekday() < 5]
        we = [b for b in B if sat <= dt.date.fromisoformat(b["t"][:10]) <= sat + dt.timedelta(days=1)]
        if not fri or not we:
            continue
        F = fri[-1]["c"]
        hi, lo, c = max(b["h"] for b in we), min(b["l"] for b in we), we[-1]["c"]
        up, dn = hi / F - 1, lo / F - 1
        rows.append(dict(t=t, F=F, c=c, ret=c / F - 1, wmax=up if abs(up) >= abs(dn) else dn, up=up, dn=dn, val=sum(b["v"] for b in we), avg=avg.get(t) or 0))
        time.sleep(0.03)
    if not rows:
        log("выходные: торгов не было или данные недоступны")
        return
    rows = [x for x in rows if x["val"] >= 1e6]
    tot = sum(x["val"] for x in rows)
    W = research().get("weekend") or {}
    ver = W.get("verify") or {}
    cls = all_clusters()
    since = dt.datetime(sat.year, sat.month, sat.day, tzinfo=MSKTZ).timestamp() - 5 * 3600
    mon = sat + dt.timedelta(days=2)

    def forecast(x):
        b = next((b for b in W.get("buckets") or [] if b["lo"] < x["ret"] <= b["hi"]), None)
        news = [c for c in company_news(x["t"], since, cls) if (c.get("key") or "").split("|")[-1] != "tone"]
        parts = []
        if news:
            n0 = news[0]
            parts.append(f"новость за выходные: {TYPE_RU.get(n0['key'].split('|')[1], '')} — рынок основной сессии может отыграть её сильнее")
        if b and b.get("mon_dir", {}).get("n", 0) >= 30:
            s = b["mon_dir"]
            v_ = ver.get("strong" if abs(x["ret"]) >= 0.015 else "all", {})
            parts.append(f"на истории после такого хода выходных в понедельник {dir_txt(s, 'день', 'от цены выходных')}"
                         + ("" if v_.get("verified") else " — устойчивость не подтверждена, прогноз направления не даём"))
            if b.get("beyond") is not None:
                parts.append(f"в {round(b['beyond'] * 100)}% случаев понедельник уходил дальше экстремума выходных")
        return "; ".join(parts)
    movers = sorted(rows, key=lambda x: -abs(x["wmax"]))[:8]
    txt = [f"🗓 <b>Торги выходного дня: итоги и что ждать в понедельник</b> · {sat.strftime('%d.%m')}–{(sat + dt.timedelta(days=1)).strftime('%d.%m')}",
           f"<blockquote>💰 Деньги в акциях за выходные: <b>{bln(tot)}</b> по {len(rows)} бумагам · обычный будний день — {bln(sum(x['avg'] for x in rows))}</blockquote>", "",
           "📈 <b>Максимальное движение за выходные</b> <i>(к закрытию пятницы; в скобках — где бумага закрыла выходные)</i>"]
    for x in movers:
        f_ = forecast(x)
        txt.append(f"{'🟢▲' if x['wmax'] > 0 else '🔴▼'} <b>${x['t']}</b> до {pc(x['wmax'], 1)} ({pc(x['ret'], 1)} на закрытии) · оборот {bln(x['val'])}" + (f"\n   ↳ <i>{esc(f_)}</i>" if f_ else ""))
    if W.get("n"):
        txt += ["", f"<blockquote expandable>📊 <b>Что показывает история выходных сессий</b> ({W.get('weekends')} выходных, {W.get('first', '')[:7]}–{W.get('last', '')[:7]})\n"
                + f"Медианный ход бумаги за выходные — {pc(W['move']['abs_wret_med'], 1, False)}, максимальный внутри выходных — {pc(W['move']['abs_wmax_med'], 1, False)}.\n"
                + "\n".join(f"Выходные {b['name']}: в понедельник по направлению выходных {pc(b['mon_dir'].get('mean') or 0, 2)}, попаданий {round((b['mon_dir'].get('hit') or 0) * 100)}% (n={b['mon_dir'].get('n', 0)})" for b in W.get("buckets") or [])
                + f"\nНезависимая проверка: {('продолжение подтверждено' if (ver.get('all') or {}).get('effect') == 'continue' else 'откат подтверждён' if (ver.get('all') or {}).get('effect') == 'reverse' else 'устойчивого перевеса нет')}.</blockquote>"]
    txt += ["", f"<i>Торги выходного дня — сессии Мосбиржи по субботам и воскресеньям (их же транслируют Т-Инвестиции); ликвидность ниже будней, поэтому цены выходных шумнее. Понедельник — {mon.strftime('%d.%m')}.</i>",
            "#выходные #прогноз"]
    img = hbars([(f"${x['t']}", x["wmax"], None) for x in movers], os.path.join(OUT, "weekend.png"), f"Максимальное движение за выходные {sat.strftime('%d.%m')}–{(sat + dt.timedelta(days=1)).strftime('%d.%m')}, к пятнице")
    PUB.post("\n".join(txt), [(img, "Торги выходного дня")] if img else None)


# ---------------------------------------------------------------------------------------------
# Ежедневный обзор одной бумаги (статья Telegraph) — отдельно от торгового плана
# ---------------------------------------------------------------------------------------------
def pick_review(md, avg):
    st = load_json("reviews.json", {"done": {}})
    now = time.time()
    done = {t for t, ts in st["done"].items() if now - ts < 21 * 86400}
    cls = all_clusters()
    fl = load_json("flows_last.json", {})
    flow = {lab: abs(v) for lab, v in (fl.get("buyers") or []) + (fl.get("sellers") or [])}
    an = (load_json("anom_today.json", {}) or {}).get("rows") or {}
    best, score = None, -1
    for r in md[:80]:
        t = r["SECID"]
        if t in done:
            continue
        nw = sum((c.get("wt") or {}).get("W", 0) for c in company_news(t, now - 36 * 3600, cls))
        x = (r.get("VALTODAY") or 0) / (avg.get(t) or 1e18)
        sc = nw * 200 + flow.get(t, 0) + min(x, 5) * 0.2 + (an.get(t, {}).get("p_y") or 0) + abs((r.get("LASTTOPREVPRICE") or 0) / 100) * 5
        if sc > score:
            best, score = t, sc
    return best


def stock_review(ticker=None):
    import article
    now = now_msk()
    md = liquid_shares(200)
    by = {r["SECID"]: r for r in md}
    avg = turnover_avg([r["SECID"] for r in md[:80]])
    t = ticker or pick_review(md, avg)
    if not t:
        log("обзор: бумага не выбрана")
        return
    B = share_candles(t, 420)
    I = index_candles("IMOEX", 420)
    if len(B) < 60:
        log("обзор: мало истории", t)
        return
    c = [b["c"] for b in B]
    last = c[-1]
    r_ = lambda k: c[-1] / c[-1 - k] - 1 if len(c) > k else None
    ytd = next((b["c"] for b in B if b["t"][:4] == str(now.year)), c[0])
    hi52, lo52 = max(b["h"] for b in B[-252:]), min(b["l"] for b in B[-252:])
    e20, e50, e200 = ema(c, 20)[-1], ema(c, 50)[-1], ema(c, 200)[-1] if len(c) >= 200 else None
    a14 = atr(B[-15:]) or 0
    name = (by.get(t) or {}).get("NAME") or t
    sec = sector(t)
    trend = "восходящий" if last > e50 > (e200 or 0) else "нисходящий" if last < e50 and (e200 is None or e50 < e200) else "боковой"
    Im = {b["t"]: b["c"] for b in I}
    common_ = [b for b in B[-121:] if b["t"] in Im]
    beta = None
    if len(common_) > 60:
        rs = np.diff(np.log([b["c"] for b in common_]))
        ri = np.diff(np.log([Im[b["t"]] for b in common_]))
        beta = float(np.cov(rs, ri)[0, 1] / np.var(ri)) if np.var(ri) > 0 else None
    cls = all_clusters()
    news = news_pick(cls, time.time() - 7 * 86400, tks={t}, min_pct=0.0, k=8)
    up = level_target(t, 1)
    dn = level_target(t, -1)
    cr = crowd_check(t, cls)
    imp = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8"))
    reac = sorted([(k.split("|")[1], v) for k, v in imp["comp"].items() if k.startswith(t + "|") and v.get("n", 0) >= 20 and not k.split("|")[1].startswith("tone")],
                  key=lambda z: -abs(z[1]["a5"]))[:4]
    try:
        import divs as DV
        upc = [r for r in DV.upcoming(120) if r["t"] == t]
    except Exception:  # noqa: BLE001
        upc = []
    dh = (DIVS.get(t) or [])[-3:]
    peers = [u for u in by if u != t and sector(u) == sec][:6]
    x_today = (by.get(t, {}).get("VALTODAY") or 0) / ((avg.get(t) or 0) * max(0.1, (now.hour * 60 + now.minute - 600) / 530)) if avg.get(t) else None
    title = f"{name} (${t}): обзор на {now.strftime('%d.%m.%Y')}"
    facts = [f"{name} ({t}), сектор {SEC_RU2.get(sec, sec or '—')}. Цена {fmt(last, dec_of(last))} ₽: за день {pc(r_(1), 1)}, неделю {pc(r_(5), 1)}, месяц {pc(r_(21), 1)}, 3 месяца {pc(r_(63), 1)}, с начала года {pc(last / ytd - 1, 1)}.",
             f"Тренд {trend}: цена {'выше' if last > e50 else 'ниже'} EMA 50" + (f", {'выше' if last > e200 else 'ниже'} EMA 200" if e200 else "") + f". До максимума за год {pc(hi52 / last - 1, 1)}, до минимума {pc(lo52 / last - 1, 1)}. Дневной ход (ATR) {pc(a14 / last, 1, False)}." + (f" Бета к индексу {fmt(beta, 2)}." if beta else "")]
    if news:
        facts.append("Новости недели: " + "; ".join(cut(clean_tx(n['first'].get('tx')), 120) for n in news[:3]))
    lead = None
    try:
        import ai
        st_ = load_json("ai.json", {})
        lead = ai.lead("\n".join(facts), st_)
        save_json("ai.json", st_)
    except Exception as e:  # noqa: BLE001
        log("ИИ-резюме обзора:", e)
    Bk = [("p", f"<i>Обзор собран в {now.strftime('%H:%M')} МСК по данным Мосбиржи, новостям из ленты бота и истории реакций бумаги 2017–2026. Не является индивидуальной инвестиционной рекомендацией.</i>")]
    if lead:
        Bk += [("h3", "Коротко"), ("p", esc(lead))]
    Bk += [("h3", "📊 Цена и тренд"), ("ul", [esc(f) for f in facts[:2]])]
    lv = []
    if up:
        lv.append(f"Вверх: ближайшее сопротивление <b>{fmt(up['tgt'], dec_of(last))}</b> ({pc(up['move'], 1)}) — {esc(up['why'])}")
    if dn:
        lv.append(f"Вниз: ближайшая поддержка <b>{fmt(dn['tgt'], dec_of(last))}</b> ({pc(dn['move'], 1)}) — {esc(dn['why'])}")
    if lv:
        Bk += [("h3", "📐 Уровни"), ("ul", lv)]
    vol = []
    if x_today:
        vol.append(f"Оборот сегодня {bln(by[t].get('VALTODAY') or 0)} — {fmt(x_today, 1)}× к обычному на это время (среднее за 20 дней {bln(avg.get(t) or 0)}).")
    fl = load_json("flows_last.json", {})
    for lab, v in (fl.get("buyers") or []) + (fl.get("sellers") or []):
        if lab == t:
            vol.append(f"Последний замер потоков: перевес {'покупок' if v > 0 else 'продаж'} {pc(v, 0)} за 30 минут.")
    oi = stock_oi().get(t) if not DEMO else None
    bh = behavior((by.get(t, {}).get("LASTTOPREVPRICE") or 0) / 100, x=x_today, oi=oi)
    if oi:
        vol.append(f"Открытый интерес по фьючерсу на акцию за день: {pc(oi[0], 1)}.")
    if bh:
        vol.append("Что может стоять за движением: " + esc("; ".join(bh)) + ".")
    if cr:
        vol.append(f"Психология толпы: за {cr.get('win', 3)} дн. {pc(cr['e3'], 1)} к индексу, оборот {fmt(cr['vx'], 1)}× к норме, новостей о компании {'нет' if not cr['news'] else cr['news']}" +
                   (" — движение держится на настроении участников." if cr["crowd"] else "."))
    if vol:
        Bk += [("h3", "💸 Деньги и поведение участников"), ("ul", vol)]
    if news:
        Bk += [("h3", "📰 Новости за неделю"), ("ul", [news_line(n) for n in news[:6]])]
    if reac:
        Bk += [("h3", "🧾 Как бумага реагировала на новости (2017–2026)"),
               ("ul", [f"{TYPE_RU.get(k, k)}: {pc(v['a5'], 1)} к индексу за 5 дней, обгоняла индекс в {round(v['up5'] * 100)}% случаев (выборка {v['n']})" for k, v in reac])]
    dv = []
    for r in upc:
        dv.append(f"Объявлено: {fmt(r['amount'], 2)} ₽ ({pc(r['yld'], 1, False)}), последний день покупки {ddmm(r.get('last_buy'))}.")
    if dh:
        dv.append("Последние выплаты: " + ", ".join(f"{d_[0]} — {fmt(d_[1], 2)} ₽" for d_ in dh))
    if dv:
        Bk += [("h3", "💰 Дивиденды"), ("ul", dv)]
    if peers:
        Bk += [("h3", f"🏭 Сектор «{SEC_RU2.get(sec, sec)}» сегодня"), ("ul", [f"${u}: {pc((by[u].get('LASTTOPREVPRICE') or 0) / 100, 1)}" for u in peers])]
    sc = []
    if up and dn:
        sc.append(f"Позитивный: закрепление выше {fmt(c[-1] + a14, dec_of(last))} открывает путь к {fmt(up['tgt'], dec_of(last))}.")
        sc.append(f"Негативный: уход ниже {fmt(dn['stop'] if dn.get('stop') else c[-1] - a14, dec_of(last))} — риск движения к {fmt(dn['tgt'], dec_of(last))}.")
    sc.append("Риски: общий фон рынка (бета " + (fmt(beta, 2) if beta else "—") + "), решения ЦБ, новости компании; уровни — ориентиры, а не прогноз.")
    Bk += [("h3", "🧭 Сценарии"), ("ul", sc)]
    imgs = []
    try:
        lines = []
        if up:
            lines.append((up["tgt"], UP, "Сопротивление", "--"))
        if dn:
            lines.append((dn["tgt"], DN, "Поддержка", "--"))
        p = candle_chart(B, os.path.join(OUT, f"rev_{t}.png"), f"${t} · {name} · дневные свечи", lines=lines, emas=((20, BLUE, "EMA 20"), (50, ORANGE, "EMA 50")), n=140, dec=dec_of(last))
        if p:
            imgs.append((p, f"${t}: дневные свечи и уровни"))
    except Exception as e:  # noqa: BLE001
        log("график обзора", e)
    teaser = [f"🔬 <b>Обзор дня: {esc(name)} (${t})</b>", "",
              f"<blockquote>Цена <b>{fmt(last, dec_of(last))} ₽</b> · день {pc(r_(1), 1)} · месяц {pc(r_(21), 1)} · с начала года {pc(last / ytd - 1, 1)}\nТренд {trend}"
              + (f" · сопротивление {fmt(up['tgt'], dec_of(last))}" if up else "") + (f" · поддержка {fmt(dn['tgt'], dec_of(last))}" if dn else "") + "</blockquote>"]
    if lead:
        teaser += ["", f"🧠 <i>{esc(lead)}</i>"]
    teaser += ["", "Внутри: тренд и уровни, деньги и поведение участников, новости недели, реакция бумаги на новости за 2017–2026, дивиденды, сектор и сценарии.", "", "{LINK}", f"#обзор_дня #{t}"]
    article.publish(PUB, title, Bk, "\n".join(teaser), imgs, fname=f"review_{t}")
    st = load_json("reviews.json", {"done": {}})
    st["done"][t] = time.time()
    save_json("reviews.json", st)


def main():
    mode = next((a for a in sys.argv[1:] if not a.startswith("--")), None) or os.environ.get("MODE") or SCHED.get(os.environ.get("SCHED", "").strip())
    if not mode:
        h = now_msk().strftime("%H:%M")
        mode = "morning" if "06:00" <= h < "08:30" else "pulse"
    if mode.endswith("_dry"):  # проверка без публикации: всё собирается, текст — в журнал
        PUB.dry = True
        PUB.sink = open(os.path.join(OUT, "posts.txt"), "a", encoding="utf-8")
        mode = mode[:-4]
    log("режим", mode, "(без отправки)" if PUB.dry else "")
    {"morning": morning, "flows": flows, "ofz": ofz, "pulse": pulse, "evening": evening, "weekly": weekly, "aitest": aitest, "test_post": test_post, "tg_check": tg_check, "anomaly": anomaly, "commod": commod_plan, "pulse_deals": pulse_deals, "anom_pre": anom_pre, "anom_test": anom_test, "trade_prob": trade_prob_cli, "level": level_cli, "stock_review": stock_review, "weekend_review": weekend_review, "anom_next": anom_next, "telegraph_check": telegraph_check, "shakeout_scan": shakeout_scan, "pattern_scan": shakeout_scan, "strong_moves": strong_moves, "nav": nav, "calendar_week": calendar_week, "poll": poll,
     "trader_prep": lambda: trader_mod().prep(PUB), "trader_open": lambda: trader_mod().open_(PUB), "trader_week": lambda: trader_mod().week(PUB)}[mode]()


# =============================================================================================
# Агент «вынос стопов»: сканирует все акции Мосбиржи (TQBR — все они доступны в Т-Инвестициях) на дневных и часовых барах.
# Правила и вероятность исполнения — из research/shakeout.py (обучение на разных промежутках 2008–2026 с новостями) и
# research/shake_verify.py (агент-проверяющий). Идею на вход бот пишет ТОЛЬКО по подтверждённым правилам.
# =============================================================================================
def shakeout_scan():
    """Каждые ~10 минут (из bot.js): часовые бары — после закрытия часа; дневные — один раз вечером (18:45–19:30 МСК)."""
    import patterns as PT
    live = []
    for src, fam0 in (("shakeout", "stop"), ("reversal", None)):
        rules = research().get(src) or {}
        for tf in ("d", "h"):
            for side_name, r in (rules.get(tf) or {}).items():
                if isinstance(r, dict) and r.get("verified"):
                    live.append((tf, side_name, dict(r, fam=fam0 or r.get("fam"))))
    if not live:
        log("вынос стопов / резкие движения: подтверждённых правил нет — идей не публикуем")
        return
    now = now_msk()
    if now.weekday() >= 5 and not os.environ.get("SHAKE_WEEKEND"):
        return
    seen = load_json("shake_seen.json", {})
    seen = {k: v for k, v in seen.items() if v >= (now - dt.timedelta(days=3)).isoformat()}
    md = liquid_shares(2000)
    cls = all_clusters()
    posts = []
    for tf, side_name, R in live:
        side = 1 if side_name == "up" else -1
        prm = R["params"]
        if tf == "d" and not (now.hour == 18 and now.minute >= 45 or now.hour == 19 and now.minute <= 30):
            continue
        for r in md:
            t = r["SECID"]
            if (r.get("VALTODAY") or 0) < (prm["liq"] if tf == "d" else prm["liq"] * 5):
                continue
            try:
                if tf == "d":
                    B = share_candles(t, 150)
                    bars = [dict(o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"], t=b["t"]) for b in B]
                    if not bars or bars[-1]["t"][:10] != now.date().isoformat():
                        continue
                else:
                    B = candles(f"engines/stock/markets/shares/boards/TQBR/securities/{t}/candles.json", 12, 60)
                    bars = [dict(o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"], t=b["t"]) for b in B]
                    if bars and bars[-1]["t"][:13] >= now.strftime("%Y-%m-%d %H"):
                        bars = bars[:-1]  # текущий час ещё не закрыт
                    if not bars or bars[-1]["t"][:10] != now.date().isoformat():
                        continue
            except Exception as e:  # noqa: BLE001
                log("вынос: свечи", t, e)
                continue
            key = f"{tf}|{R['fam']}|{side_name}|{t}|{bars[-1]['t'][:13]}"
            if key in seen or any(k.startswith(f"{tf}|") and k.endswith(f"|{t}|{bars[-1]['t'][:13]}") for k in seen):
                continue
            x = PT.detect(bars, R["fam"], prm, side)
            if not x:
                continue
            since = time.time() - 30 * 3600
            nws = [c for c in company_news(t, since, cls) if (c.get("key") or "").split("|")[-1] != "tone"]
            if R.get("news") == "no" and nws or R.get("news") == "with" and not nws:
                continue
            seen[key] = now.isoformat()
            posts.append((tf, side, t, x, R, len(nws)))
            time.sleep(0.05)
    save_json("shake_seen.json", seen)
    for tf, side, t, x, R, n_news in posts[:3]:
        prm, o = R["params"], R.get("oos") or {}
        d = dec_of(x["entry"])
        fam, inf = R["fam"], x.get("info") or {}
        bar = "день" if tf == "d" else "час"
        unit = "дн." if tf == "d" else "ч"
        hz = f"{prm['Hb']} {'торговых дней' if tf == 'd' else 'часовых баров'}"
        prob = R.get("prob") or (R.get("hist") or {}).get("prob")
        up_ = side > 0
        if fam == "stop":
            head = f"🪤 <b>Вынос {'лонгистов (стопы покупателей)' if up_ else 'шортистов (стопы продавцов)'}: ${t}</b>"
            why = (f"За {bar} цена {'проколола минимум' if up_ else 'проколола максимум'} последних {prm['N']} {unit} (<code>{fmt(inf.get('ext', 0), d)}</code>), "
                   f"сработали стопы, но к закрытию бара вернулась {'вверх' if up_ else 'вниз'}. Размах бара {fmt(inf.get('rg', 0), 1)}× обычного, оборот {fmt(inf.get('vx', 0), 1)}× к норме")
        else:
            mvdir = "рост" if inf.get("move", 0) > 0 else "падение"
            head = {"fade": f"↩️ <b>Резкий {mvdir} — ставка на разворот: ${t}</b>", "follow": f"🚀 <b>Резкий {mvdir} — ставка на продолжение: ${t}</b>", "follow_wide": f"🚀 <b>Резкий {mvdir} — ставка на продолжение: ${t}</b>",
                    "confirm": f"↩️ <b>{'Рост, затем разворот вниз' if not up_ else 'Падение, затем разворот вверх'}: ${t}</b>",
                    "shake": f"🪤 <b>Резкое {mvdir} и вынос стопов против него: ${t}</b>"}[fam]
            why = (f"За {inf.get('W')} {unit} — {mvdir} на {fmt(abs(inf.get('move', 0)), 1)} × средний ход {'дня' if tf == 'd' else 'часа'} (ATR), оборот {fmt(inf.get('vr', 0), 1)}× к норме"
                   + ({"confirm": "; последний бар закрылся против движения", "shake": "; затем цена проколола экстремум движения, выбила стопы и вернулась"}.get(fam, "")))
        tg = f" · цель <code>{fmt(x['tgt'], d)}</code> ({pc(prm['R'] * x['risk'] * 1, 1)})" if x.get("tgt") else " · выход по времени"
        txt = [head + f" · {'🟢 идея на рост' if up_ else '🔴 идея на снижение'}",
               why + (f"; новостей о компании за сутки: {n_news}" if n_news else "; новостей о компании за сутки нет") + ".",
               "",
               f"<blockquote>🎯 <b>{'ЛОНГ' if up_ else 'ШОРТ'} ${t}</b>\nВход ≈ <code>{fmt(x['entry'], d)}</code> · стоп <code>{fmt(x['stop'], d)}</code> ({pc(-x['risk'], 1)})"
               f"{tg}\nГоризонт: {hz}</blockquote>",
               (f"📊 <b>Вероятность исполнения цели раньше стопа: ~{round((prob or 0) * 100)}%</b> " if x.get("tgt") else "📊 ")
               + f"<i>(на истории вне выборки: правило подбиралось на прошлых годах и проверялось на следующих; {o.get('n', 0)} сделок, "
               f"в среднем {pc(o.get('mean', 0), 2)} на сделку после издержек, плюс в {round((o.get('pos_years') or 0) * 100)}% лет)</i>",
               "Правило подтверждено агентом-проверяющим: результат лучше случайного входа с тем же риском. Стоп обязателен, размер позиции — небольшой."]
        PUB.post("\n".join(txt), ai_lead=False)
        log("паттерн", R["fam"], ": идея", tf, t, "рост" if side > 0 else "снижение")

def strong_moves():
    """Вечер (18:55 МСК): очень резкие движения дня (≥ 4,5 обычного дневного хода за 1–3 дня) — пост-наблюдение с честной
    статистикой из research/reversal.py: что бывало дальше на истории 2008–2026. Не сигнал: как стратегия это не прошло проверку."""
    import patterns as PT
    obs = research().get("reversal_obs") or {}
    if not obs:
        log("резкие движения: статистики ещё нет")
        return
    now = now_msk()
    seen = load_json("strong_seen.json", {})
    if seen.get("day") == now.date().isoformat():
        return
    cls = all_clusters()
    found = []
    for r in liquid_shares(2000):
        t = r["SECID"]
        if (r.get("VALTODAY") or 0) < 30e6:
            continue
        try:
            B = share_candles(t, 90)
        except Exception as e:  # noqa: BLE001
            log("резкие движения: свечи", t, e)
            continue
        bars = [dict(o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"]) for b in B]
        if len(bars) < 40 or B[-1]["t"][:10] != now.date().isoformat():
            continue
        j = len(bars) - 1
        a = PT.atr_at(bars, j)
        if not a:
            continue
        for W in (1, 2, 3):
            m = PT.med_vol(bars, j - W + 1)
            if not m or m < 30e6:
                continue
            mv = (bars[j]["c"] - bars[j - W]["c"]) / a
            vr = sum(x["v"] for x in bars[j - W + 1:j + 1]) / (W * m)
            if abs(mv) >= 4.5 and vr >= 1.0:
                d = "up" if mv > 0 else "down"
                st = (obs.get(f"W{W}_vx{2 if vr >= 2 else 1}_{d}") or {}).get("f5")
                if st and st.get("n", 0) >= 30:
                    since = time.time() - 30 * 3600
                    n_news = len([c for c in company_news(t, since, cls) if (c.get("key") or "").split("|")[-1] != "tone"])
                    found.append(dict(t=t, W=W, mv=mv, vr=vr, chg=bars[j]["c"] / bars[j - W]["c"] - 1, d=d, st=st, news=n_news))
                break
        time.sleep(0.03)
    seen = {"day": now.date().isoformat()}
    save_json("strong_seen.json", seen)
    if not found:
        log("резкие движения: сегодня движений ≥ 4,5 ATR нет")
        return
    found.sort(key=lambda z: -abs(z["mv"]))
    txt = [f"📣 <b>Очень резкие движения дня</b> · {now.strftime('%d.%m')} <i>(наблюдение, не сигнал)</i>",
           "Бумаги, которые за 1–3 дня прошли больше 4,5 обычных дневных ходов. Что бывало дальше в таких случаях на истории Мосбиржи 2008–2026:", ""]
    for z in found[:6]:
        s_ = z["st"]
        kind = "роста" if z["d"] == "up" else "падения"
        sg = 1 if z["d"] == "up" else -1
        cont = s_.get("cont") or 0
        nw = s_.get("with_news") if z["news"] else s_.get("no_news")
        nw_txt = (f"; когда {'были' if z['news'] else 'не было'} новостей о компании — {pc(nw['mean'] * sg, 1)} в {round((nw.get('cont') or 0) * 100)}% случаев (выборка {nw['n']})"
                  if nw and nw.get("n", 0) >= 30 else "")
        txt.append(f"{'🟢' if z['d'] == 'up' else '🔴'} <b>${z['t']}</b> {pc(z['chg'], 1)} за {z['W']} {'день' if z['W'] == 1 else 'дня'} "
                   f"({fmt(abs(z['mv']), 1)} обычного хода), оборот {fmt(z['vr'], 1)}× к норме · новостей о компании за сутки: {z['news'] or 'нет'}")
        txt.append(f"   ↳ <i>после такого {kind} в {round(cont * 100)}% случаев движение продолжалось ещё 5 торговых дней: в среднем "
                   f"{pc(s_['mean'] * sg, 1)} (медиана {pc((s_.get('median') or 0) * sg, 1)}), выборка {s_['n']}, "
                   f"продолжение перевешивало в {round((s_.get('pos_years') or 0) * 100)}% лет{nw_txt}</i>")
    txt += ["", "<i>Почему это не идея на вход: как торговая стратегия (со стопом и издержками) продолжение после резких движений на истории "
                "не дало устойчивой прибыли на годах, которые не участвовали в подборе. В остальных случаях движение разворачивалось.</i>"]
    # отдельного поста нет: блок войдёт в «Итоги дня» (evening, 19:25 МСК)
    save_json("strong_today.json", {"day": now.date().isoformat(), "lines": txt})
    log("резкие движения: найдено", len(found[:6]), "— войдут в итоги дня")


def trader_mod():
    import trader  # рубрика «Трейдер» (channel/trader.py)
    return trader


# =============================================================================================
# Навигация, календарь недели, опрос
# =============================================================================================
NAV_VERSION = "2026-11-04"
SITE_TITLE = os.environ.get("CHANNEL_TITLE", "На пути к успеху | Куда идут деньги на Мосбирже")
SITE_DESC = os.environ.get("CHANNEL_DESC", "Куда идут деньги на Мосбирже: потоки покупателей и продавцов, ОФЗ, аномалии, план и итоги дня. "
                                           "Закрытые идеи с вероятностью цели — в боте. Не является индивидуальной инвестиционной рекомендацией.")


def how_to_read():
    nav = load_json("nav.json", {})
    if nav.get("link"):
        return f"ℹ️ <a href=\"{nav['link']}\">Как читать посты канала</a> — в закреплённом сообщении"
    return "ℹ️ Как читать посты — в закреплённом сообщении канала"


def nav_text():
    bot = ""
    try:
        me = PUB._tg("getMe") if not PUB.dry else {}
        bot = me.get("username") or ""
    except Exception as e:  # noqa: BLE001
        log("навигация: getMe", e)
    t = [f"📌 <b>{esc(SITE_TITLE)}</b>",
         "Куда идут деньги на Мосбирже — кто покупает, кто продаёт и что из этого следует. Только публичные данные Мосбиржи и открытые источники.", "",
         "🗓 <b>Расписание (МСК)</b>",
         "06:20–10:20 · 📋 #план_дня — подготовка, план торгового дня и открытие",
         "10:00–18:50 · ⚖️ #потоки — покупатели и продавцы (один пост, обновляется каждые 30 минут)",
         "10:45–19:10 · 🏦 #ОФЗ — потоки в госбондах (один пост, обновляется каждый час)",
         "12:15 · 🔍 #разбор — разбор одной бумаги",
         "по событию, до 3 в день · ⚡ #аномалия — резкие движения с причинами и вероятностями",
         "в течение дня · 🗞 #главное — лента важных новостей (один пост) и отдельно самые весомые #новости",
         "19:25 · 🏁 #итоги_дня — итоги, резкие движения, что ждать завтра",
         "понедельник 08:30 · 📅 #календарь_недели · пятница 19:05 · 📊 опрос · пятница 19:35 · 📚 #неделя",
         "",
         "📖 <b>Как читать</b>",
         "<b>Перевес покупок/продаж</b> — доля оборота, где инициатором сделки была покупка или продажа по рынку. ↳ — что может стоять за движением.",
         "<b>Кит</b> — сделка от 50 млн ₽ в голубых фишках или от 5% среднего дневного оборота в остальных бумагах.",
         "<b>Чистый поток в ОФЗ</b> — покупки минус продажи по рынку. Деньги в длинные ОФЗ при падении доходностей — рынок ждёт снижения ставки.",
         "<b>Аномалия</b> — движение к индексу сильнее порога эшелона или оборот в 4–6 раз выше обычного; вероятности — модель на истории 2013–2026.",
         "<b>Вес новости</b> — насколько сильно рынок исторически реагировал на такие новости (2017–2026).",
         "<b>Сделки инсайдеров</b> — уже раскрытые покупки и продажи менеджмента и крупных акционеров (публичная информация, 224-ФЗ).",
         "",
         "🔒 <b>Закрытые идеи</b> — #идея: тикер, вход, стоп, тейки, план фиксации и вероятность цели по истории бумаги. "
         "Раз в месяц — открытая статистика всех идей (#итоги_идей)." + (f" Доступ — в боте @{bot}." if bot else ""),
         "",
         f"<i>{DISCLAIMER}</i>"]
    return "\n".join(t), bot


def nav():
    """Закреплённый пост-навигатор + описание канала. Обновляется, когда меняется NAV_VERSION или пропал пост."""
    st = load_json("nav.json", {})
    text, bot = nav_text()
    if PUB.dry:
        print(text)
        return
    chat = PUB.tg_chat
    kb = {"inline_keyboard": [[{"text": "🔒 Закрытые идеи", "url": f"https://t.me/{bot}?start=pay"}]]} if bot else None
    ok = False
    if st.get("id"):
        try:
            PUB._tg("editMessageText", {"chat_id": chat, "message_id": st["id"], "text": text, "parse_mode": "HTML", "disable_web_page_preview": True,
                                        **({"reply_markup": kb} if kb else {})})
            ok = True
        except Exception as e:  # noqa: BLE001
            ok = "not modified" in str(e)
            if not ok:
                log("навигация: пост не отредактирован, публикую заново —", e)
    if not ok:
        m = PUB._tg("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **({"reply_markup": kb} if kb else {})})
        st = {"id": m["message_id"], "link": PUB.tg_link(m)}
        try:
            PUB._tg("pinChatMessage", {"chat_id": chat, "message_id": m["message_id"], "disable_notification": True})
        except Exception as e:  # noqa: BLE001
            log("навигация: не удалось закрепить (нужно право бота «Закреплять сообщения» / «Изменять сообщения»):", e)
    if st.get("ver") != NAV_VERSION:
        for meth, data in (("setChatTitle", {"title": SITE_TITLE[:128]}), ("setChatDescription", {"description": SITE_DESC[:255]})):
            try:
                PUB._tg(meth, {"chat_id": chat, **data})
            except Exception as e:  # noqa: BLE001
                if "not modified" not in str(e):
                    log(f"навигация: {meth} не прошёл (нужно право бота «Изменять профиль канала»):", e)
    st["ver"] = NAV_VERSION
    save_json("nav.json", st)
    log("навигация: закреплённый пост актуален", st.get("link"))


CBR_MEETINGS = ["2026-02-13", "2026-03-20", "2026-04-24", "2026-06-19", "2026-07-24", "2026-09-11", "2026-10-23", "2026-12-18",
                "2027-02-12", "2027-03-19", "2027-04-30", "2027-06-11", "2027-07-30", "2027-09-10", "2027-10-22", "2027-12-17"]


def calendar_week():
    """Понедельник 08:30 МСК: календарь недели — дивидендные отсечки, заседание ЦБ, регулярные события."""
    import divs
    now = now_msk()
    d0 = now.date() - dt.timedelta(days=now.weekday())
    d1 = d0 + dt.timedelta(days=6)
    days = [d0 + dt.timedelta(days=k) for k in range(5)]
    wd = ["пн", "вт", "ср", "чт", "пт"]
    ev = {d: [] for d in days}
    try:
        for r in divs.upcoming(14):
            lb = dt.date.fromisoformat(r["last_buy"]) if r.get("last_buy") else None
            if lb and d0 <= lb <= d1 and lb in ev:
                y = f", доходность {pc(r['yld'], 1, False)}" if r.get("yld") else ""
                ev[lb].append(f"💰 <b>${r['t']}</b> — последний день с дивидендом {fmt(r['amount'], 2)} ₽{y}" + ("" if r.get("declared") else " <i>(рекомендован)</i>"))
    except Exception as e:  # noqa: BLE001
        log("календарь: дивиденды", e)
    for m in CBR_MEETINGS:
        md_ = dt.date.fromisoformat(m)
        if md_ in ev:
            ev[md_].append("🏦 <b>Заседание Банка России по ключевой ставке</b> — решение в 13:30, пресс-конференция в 15:00")
    nxt = [dt.date.fromisoformat(m) for m in CBR_MEETINGS if dt.date.fromisoformat(m) > d1]
    for d in days:
        if d.weekday() == 2:
            ev[d] += ["🏛 Аукционы Минфина по ОФЗ (если объявлены накануне)", "📈 Недельная инфляция Росстата (вечером)"]
        if d.weekday() == 3 and d.month in (3, 6, 9, 12) and 15 <= d.day <= 21:
            ev[d].append("📆 Экспирация квартальных фьючерсов и опционов")
    tax = dt.date(d0.year, d0.month, 28)
    while tax.weekday() >= 5:
        tax -= dt.timedelta(days=1)
    if tax in ev:
        ev[tax].append("🧾 Уплата налогов (28-е): экспортёры продают валюту — обычно поддержка рубля")
    txt = [f"📅 <b>Календарь недели</b> · {d0.strftime('%d.%m')}–{(d0 + dt.timedelta(days=4)).strftime('%d.%m')}", ""]
    for d, k in zip(days, wd):
        if ev[d]:
            txt.append(f"<b>{k}, {d.strftime('%d.%m')}</b>")
            txt += ev[d]
            txt.append("")
    if len(txt) <= 2:
        txt.append("Крупных запланированных событий нет.")
    if nxt:
        txt.append(f"Следующее заседание ЦБ по ставке — {nxt[0].strftime('%d.%m.%Y')}.")
    txt += ["Отчёты компаний и советы директоров по дивидендам — в ленте #главное по мере выхода."]
    PUB.post("\n".join(txt), ai_lead=False)


def poll():
    """Пятница 19:05 МСК: опрос — где будет индекс через неделю."""
    try:
        im = (index_markets().get("IMOEX") or {})
        cur = im.get("CURRENTVALUE") or im.get("LASTVALUE")
    except Exception as e:  # noqa: BLE001
        log("опрос: нет индекса", e)
        return
    if not cur:
        return
    now = now_msk()
    nx = (now + dt.timedelta(days=7)).strftime("%d.%m")
    hi, lo = round(cur * 1.02 / 10) * 10, round(cur * 0.98 / 10) * 10
    q = f"📊 Где будет индекс МосБиржи в пятницу {nx}? Сейчас {fmt(cur, 0)}"
    opts = [f"Выше {fmt(hi, 0)} (рост больше 2%)", f"Между {fmt(lo, 0)} и {fmt(hi, 0)}", f"Ниже {fmt(lo, 0)} (падение больше 2%)"]
    if PUB.dry:
        print(q, opts)
        return
    PUB._tg("sendPoll", {"chat_id": PUB.tg_chat, "question": q, "options": [{"text": o} for o in opts], "is_anonymous": True})
    log("опрос опубликован")


if __name__ == "__main__":
    main()
