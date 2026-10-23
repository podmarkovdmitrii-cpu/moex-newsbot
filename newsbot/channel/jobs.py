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
import os
import re
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (DISCLAIMER, MSK as MSKTZ, BG, BLUE, DEMO, DN, FG, GRID, NEWS_STATE, ORANGE, OUT, UP, VIOLET, Publisher, bln, candle_chart, currency_candles, ema, esc, fmt, hbars,  # noqa: E402
                    http_get, index_candles, iss, load_json, log, now_msk, pc, plt, save_json, share_candles, style, table,
                    FUT, FUT_CODE, fut_continuous, fut_fronts, fut_candles, tf_bars, atr, TF_RU)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(os.path.join(HERE, "data.json"), encoding="utf-8"))
DIVS = json.load(open(os.path.join(HERE, "..", "divhist.json"), encoding="utf-8"))
SEC_OF = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8"))["secOf"]
CBR_DAYS = ["2026-10-23", "2026-12-18", "2027-02-12", "2027-03-20", "2027-04-24", "2027-06-11", "2027-07-23", "2027-09-10", "2027-10-22", "2027-12-17"]
FAM_RU = {"mom": "импульс", "pull": "откат к EMA 20", "sbrk": "шорт пробоя"}
SEC_RU = {"bank": "банки", "oil": "нефтегаз", "metal": "металлурги", "retail": "ритейл", "tech": "IT и связь", "power": "энергетика", "chem": "химия", "devel": "девелоперы", "transport": "транспорт"}
SCHED = {"40 3 * * 1-5": "morning", "30 5 * * 1": "weekly", "*/30 7-15 * * 1-5": "flows", "50 15 * * 1-5": "flows",
         "45 10 * * 1-5": "ofz", "10 16 * * 1-5": "ofz", "25 16 * * 1-5": "evening", "20 6-18/2 * * 1-5": "pulse"}
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
        rows = table(iss(f"engines/stock/markets/{market}/securities/{secid}/trades.json", **p), "trades")
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
    md = liquid_shares(int(os.environ.get("FLOWS_N", "60")))
    avg = turnover_avg([r["SECID"] for r in md])
    try:
        fut = fut_fronts()
    except Exception as e:  # noqa: BLE001
        log("фьючерсы", e)
        fut = {}
    items = [("share", r["SECID"], r) for r in md] + [("fut", f["secid"], f) for f in fut.values()]
    for kind, t, r in items:
        s = stt["tk"].setdefault(t, {"last": None, "vb": 0.0, "vs": 0.0, "big": [], "pb": 0.0, "ps": 0.0})
        s["pb"], s["ps"] = s["vb"], s["vs"]  # на момент прошлого поста — для перевеса за последние 30 минут
        if kind == "share":
            a20 = avg.get(t) or 0
            thr = float(os.environ.get("BIG_TRADE", "50e6")) if a20 > 3e9 else max(5e6, min(50e6, 0.05 * a20 if a20 else 20e6))
        else:
            thr = 30e6
        try:
            if DEMO:
                rows = demo_trades(t, s["last"])
            elif kind == "share":
                rows = fetch_trades(t, "shares", s["last"])
            else:
                rows = fetch_fut_trades(t, s["last"])
        except Exception as e:  # noqa: BLE001
            log("сделки", t, e)
            continue
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
    bigs = sorted([(x["lab"], x["kind"], *b[:4]) for x in res for b in x["big"]], key=lambda z: -z[4])[:10]
    hm = now.strftime("%H:%M")

    def row(x):
        xx = f", оборот {fmt(x['x'], 1)}× к среднему" if x["x"] else ""
        w = f"за 30 мин {pc(x['wimb'], 0)}, " if x["wimb"] is not None and x["wtot"] > 0 else ""
        return f"• <b>${x['lab']}</b> {w}за день {pc(x['imb'], 0)} · {bln(x['tot'])}{xx} · цена {pc(x['chg'], 1)}"
    txt = [f"⚖️ <b>Покупатели vs продавцы</b> — {now.strftime('%d.%m')}, на {hm} МСК",
           f"{len(sh)} акций 1–3 эшелона: покупок по рынку {pc(allb / allt, 0, False)} от оборота {bln(allt)}.", "",
           "🟢 <b>Перевес покупателей</b>:", *(row(x) for x in buyers), "", "🔴 <b>Перевес продавцов</b>:", *(row(x) for x in sellers)]
    if fu:
        txt += ["", "🛢 <b>Сырьё (фьючерсы)</b>:"]
        for x in sorted(fu, key=lambda x: -x["tot"])[:8]:
            w = f"за 30 мин {pc(x['wimb'], 0)}, " if x["wimb"] is not None and x["wtot"] > 0 else ""
            txt.append(f"• {esc(FUT[x['lab']][0])} ({x['t']}): {w}за день {pc(x['imb'], 0)} · {bln(x['tot'])} · цена {pc(x['chg'], 1)}")
    if bigs:
        txt += ["", "🐋 <b>Сделки китов за последние 30 минут</b> (крупнейшие одной сделкой):"]
        txt += [f"• {'$' + t if k_ == 'share' else esc(FUT[t][0])} {tm[:5]} — {'покупка' if side == 'B' else 'продажа'} по рынку на {bln(v)} по {fmt(p, 2)}" for t, k_, tm, p, v, side in bigs]
    txt += ["", "<i>Перевес — доля оборота, где инициатором была покупка (B) или продажа (S) по рынку. «Кит» — сделка от 50 млн ₽ в голубых фишках, "
            "от 5% среднего дневного оборота во 2–3 эшелоне, от 30 млн ₽ во фьючерсах. Данные Мосбиржи с задержкой до 15 минут; перевес — не прогноз.</i>", "#потоки #перевес #киты"]
    rows = [(x["lab"], key(x), None) for x in sorted(buyers + sellers[::-1], key=lambda x: -key(x))]
    img = hbars(rows, os.path.join(OUT, "flows.png"), f"Перевес покупок (+) и продаж (−) за 30 мин, {now.strftime('%d.%m')} {hm} МСК", fmt_v=lambda v: pc(v, 0))
    PUB.post("\n".join(txt), [(img, f"Перевес покупателей и продавцов на {hm} МСК")])
    save_json("flows_last.json", dict(time=now.isoformat(), buyers=[(x["lab"], x["imb"]) for x in buyers], sellers=[(x["lab"], x["imb"]) for x in sellers], share=allb / allt))


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
    elif longnet > thr and (dylong is None or dylong < 0):
        concl = "Деньги идут в длинные ОФЗ, доходности снижаются — рынок закладывает снижение ключевой ставки. Обычно это поддерживает акции роста, девелоперов и банки."
    elif longnet < -thr and (dylong is None or dylong > 0):
        concl = "Длинные ОФЗ продают, доходности растут — рынок закладывает более жёсткую политику ЦБ. Для акций это фон осторожности."
    elif shortnet > 0 and longnet <= 0:
        concl = "Покупают в основном короткие выпуски — деньги паркуются без ставки на снижение ключевой."
    else:
        concl = "Потоки смешанные, явного направления нет."
    mins = (now.hour * 60 + now.minute) - 600
    frac = max(0.1, min(1.0, mins / 530))
    xa = f" ({fmt(tot / (avg * frac), 1)}× к обычному обороту {'на это время' if frac < 1 else 'за день'})" if avg else ""
    txt = [f"🏦 <b>Потоки в ОФЗ</b> — {now.strftime('%d.%m')}, {'середина дня, ' if now.hour < 16 else 'итоги дня, '}{now.strftime('%H:%M')} МСК", f"Оборот гособлигаций: {bln(tot)}{xa}." + (f" Индекс RGBI {pc(rg1, 2)} за день, {pc(rg5, 2)} за 5 дней." if rg1 is not None else ""), ""]
    for x in lines:
        dy = f", доходность {fmt(x['y'] * 100, 2)}%" + (f" ({'+' if x['dy'] >= 0 else '−'}{fmt(abs(x['dy']) * 1e4, 0)} б.п. за неделю)" if x["dy"] is not None else "") if x["y"] else ""
        txt.append(f"• {x['lab']}: {'покупки' if x['net'] >= 0 else 'продажи'} по рынку {'+' if x['net'] >= 0 else '−'}{bln(abs(x['net']))} из {bln(x['val'])}{dy}")
    txt += ["", f"<b>Вывод:</b> {concl}", "", "<i>Чистый поток — покупки минус продажи по рынку (агрессор сделки), данные Мосбиржи. Аукционы Минфина по средам — в ленте официальных новостей.</i>", "#ОФЗ #облигации"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.5, 4.2), dpi=110, facecolor=BG)
    for a in (a1, a2):
        style(a)
    labs = [x["lab"] for x in lines]
    a1.bar(labs, [x["net"] / 1e9 for x in lines], color=[UP if x["net"] >= 0 else DN for x in lines], zorder=3)
    a1.set_title("Чистые покупки по рынку, млрд ₽", color="#ffffff", fontsize=10.5, loc="left")
    a1.axhline(0, color="#787b86", linewidth=0.8)
    a2.plot(labs, [(x["y"] or 0) * 100 for x in lines], color=BLUE, marker="o", label="сегодня")
    if ref:
        a2.plot(labs, [((ref.get(lab) or 0) * 100) or None for lab in labs], color=ORANGE, marker="o", linestyle="--", label="неделю назад")
    a2.set_title("Доходность ОФЗ по сроку, %", color="#ffffff", fontsize=10.5, loc="left")
    leg = a2.legend(fontsize=8, facecolor=BG, edgecolor=GRID)
    for t in leg.get_texts():
        t.set_color(FG)
    fig.tight_layout()
    img = os.path.join(OUT, "ofz.png")
    fig.savefig(img, facecolor=BG)
    plt.close(fig)
    PUB.post("\n".join(txt), [(img, "Потоки в ОФЗ и кривая доходности")])
    save_json("ofz_last.json", dict(date=today, concl=concl, tot=tot, x=(tot / avg if avg else None), rg5=rg5, lines=lines))


# =============================================================================================
# Т-Пульс: посты самых читаемых авторов
# =============================================================================================
def pulse():
    cache = load_json("pulse.json", {"ids": {}, "last": {}})
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)
    items, fails = [], 0
    for a in DATA["pulse"][:25]:
        if fails >= 4:
            log("Пульс: API недоступен, прекращаю опрос")
            break
        nick = a["nick"]
        try:
            pid = cache["ids"].get(nick)
            if not pid:
                js = json.loads(http_get(f"https://www.tbank.ru/api/invest-gw/social/v1/profile/nickname/{nick}", timeout=15, tries=2))
                pid = (js.get("payload") or {}).get("id")
                if not pid:
                    continue
                cache["ids"][nick] = pid
            js = json.loads(http_get(f"https://www.tbank.ru/api/invest-gw/social/v1/profile/{pid}/post?limit=20", timeout=15, tries=2))
        except Exception as e:  # noqa: BLE001
            log("Пульс", nick, e)
            fails += 1
            continue
        fails = 0
        last = cache["last"].get(nick, "")
        newest = last
        for p in (js.get("payload") or {}).get("items", []):
            ins = p.get("inserted") or ""
            newest = max(newest, ins)
            if ins <= last:
                continue
            try:
                when = dt.datetime.fromisoformat(ins.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when < since:
                continue
            cont = p.get("content") or {}
            tx = re.sub(r"\s+", " ", cont.get("text") or p.get("text") or "").strip()
            tks = sorted({i.get("ticker") for i in (cont.get("instruments") or []) + (p.get("instruments") or []) if i and i.get("ticker")})
            if len(tx) < 80 and not tks:
                continue
            items.append(dict(a=a, id=p.get("id"), tx=tx, tks=tks, when=when))
        cache["last"][nick] = newest
        time.sleep(1.0)
    save_json("pulse.json", cache)
    log("Пульс: новых постов за 6 ч", len(items))
    if not items:
        return
    items.sort(key=lambda x: -(x["a"]["f"] or 0))
    buys = sum(1 for x in items if re.search(r"\b(купил|докупил|покупаю|беру|лонг|набираю)\b", x["tx"], re.I))
    sells = sum(1 for x in items if re.search(r"\b(продал|продаю|шорт|скинул|фиксирую|вышел)\b", x["tx"], re.I))
    txt = ["👥 <b>Пульс: что пишут самые читаемые авторы</b>", ""]
    for x in items[:8]:
        a = x["a"]
        acc = f"; заявленные сделки на истории: {a['n']}, в среднем {pc(a['mean'] / 100, 2)} к индексу за 5 дн." if a.get("n") and a.get("mean") is not None else ""
        link = f"https://www.tbank.ru/invest/social/profile/{a['nick']}/{x['id']}/"
        txt.append(f"👤 <b>{esc(a['nick'])}</b> ({fmt((a['f'] or 0) / 1000, 0)} тыс. подписчиков{acc})" + (" — " + " ".join("$" + t for t in x["tks"][:5]) if x["tks"] else ""))
        txt.append(f"«{esc(x['tx'][:260])}{'…' if len(x['tx']) > 260 else ''}»")
        txt.append(f"🔗 <a href=\"{link}\">пост в Пульсе</a>")
        txt.append("")
    if buys + sells >= 3:
        txt.append(f"Настроение авторов: {buys} постов о покупках, {sells} о продажах. По нашей статистике 2019–2026 массовые покупки в Пульсе в среднем отставали от рынка, а продажи чаще оказывались правы — используйте как индикатор настроения толпы, а не как сигнал.")
    txt.append("#пульс")
    PUB.post("\n".join(txt))


# =============================================================================================
# Утренний торговый план (совмещённая стратегия v2)
# =============================================================================================
DIGEST = re.compile(r"Что произошло за|Главное за|Главное к|Итоги дня|Итоги недели|дайджест|#крипто|биткоин|#BTC|Утренний обзор|Вечерний обзор", re.I)


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
        tk = (c.get("key") or "|").split("|")[0]
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
        if DIGEST.search(raw) or c["first"]["link"] in seen or len(raw) < 25:
            continue
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
        upc = divs.upcoming(45)
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
            mk.append("<b>Перевес к закрытию прошлого дня:</b> " + "; ".join(x for x in (("покупатели — " + bs) if bs else "", ("продавцы — " + ss) if ss else "") if x))
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
    mnews = news_pick(cls, since, market=True, k=4)
    mnews = [c for c in mnews if c not in top]
    if mnews:
        B.append(("h4", "Новости для всего рынка"))
        B.append(("ul", [news_line(c) for c in mnews]))
    # дивидендный календарь
    if upc:
        B.append(("h3", "💰 Дивидендный календарь (объявленные выплаты, 45 дней)"))
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
        for c in nn:
            focus.setdefault(c["key"].split("|")[0], []).append("весомая новость" + (" (+)" if (c.get("score") or 0) > 0 else " (−)"))
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
        sect_out.append((score, name, blocks))
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
    if sect_out:
        tz += ["🧭 <b>В фокусе секторов:</b> " + ", ".join(n.split(" ", 1)[1] for _, n, _ in sect_out[:4])]
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
    article.publish(PUB, title, B, "\n".join(tz), imgs, fname=f"plan_{today.isoformat()}")
    try:
        commod_plan()
    except Exception as e:  # noqa: BLE001
        log("сырьё", e)


COMMOD_MAIN = ["BR", "NG", "TTF", "GOLD", "SILV", "PLT", "PLD", "COPPER", "ALUM", "NICKEL", "WHEAT"]


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
    txt = [f"🛢 <b>Сырьё: план на {now.strftime('%d.%m')}</b> (фьючерсы Мосбиржи, ближайший контракт)", ""]
    for x in rows:
        f = x["f"]
        tr = {1: "🟢 восходящий тренд", -1: "🔴 нисходящий тренд", 0: "⚪ боковик"}[x["trend"]]
        line = f"<b>{esc(f['name'])}</b> ({f['secid']}): {fmt(x['last'], 2)} {f['unit']} · {pc(f['chg'], 1)} за день" + (f" · {pc(x['r1m'], 1)} за месяц" if x["r1m"] is not None else "") + f"\n  {tr}" + (f", {x['brk']}" if x["brk"] else "")
        if x["idea"]:
            i = x["idea"]
            line += f"\n  Идея по тренду: {'ЛОНГ' if i['side'] > 0 else 'ШОРТ'} от {fmt(i['entry'], 2)}, стоп {fmt(i['stop'], 2)}, тейк {fmt(i['take'], 2)}"
        rel = FUT[x["a"]][2]
        if rel and x["trend"]:
            line += f"\n  Для акций: {' '.join('$' + r for r in rel[:4])} — {'попутный ветер' if x['trend'] > 0 else 'встречный ветер'}"
        txt.append(line)
    txt += ["", "<i>Тренд — по EMA 20/50 дневных свечей склеенных контрактов; стоп — 1,5 дневного ATR, тейк — 2 риска. Фьючерсы — плечевой инструмент: гарантийное обеспечение и вариационная маржа.</i>", "#сырьё #фьючерсы"]
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
        tmr, focus, notes = watch_tomorrow(md, now)
    except Exception as e:  # noqa: BLE001
        log("на завтра", e)
        tmr, focus, notes = None, {}, []
    if focus or notes:
        txt += ["", f"👀 <b>На что обратить внимание завтра, {tmr.strftime('%d.%m')} ({wd_ru(tmr)}):</b>"]
        for t, why in sorted(focus.items(), key=lambda x: -len(x[1]))[:10]:
            txt.append(f"• <b>${t}</b> — " + "; ".join(dict.fromkeys(why)))
        txt += notes
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
    txt += ["", "Полная статья со всеми активами, таблицами и методикой — в PDF ниже.", "#неделя #долгосрок"]
    pdf = weekly_pdf(now, rows, stocks, imgs)
    PUB.post("\n".join(txt), imgs, (pdf, f"Базовые активы — неделя {now.strftime('%d.%m.%Y')} (PDF)") if pdf else None)


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


def tg_check():
    """Проверка бота паблика: кто он, в каких каналах админ и может ли публиковать. Ничего не публикует."""
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
        txt.append(f"⚙️ <b>Кто {'тянет индекс вверх' if up else 'тянет индекс вниз'}</b> (вклад = вес в индексе × изменение цены):")
        txt += [f"• ${r['t']} {pc(r['chg'], 1)} — вклад {pc(r['con'], 2)} (вес {pc(W[r['t']], 1, False)})" for r in drivers[:5] if (r["con"] > 0) == up and r["con"]]
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
    nn = news_pick(cls, now - 4 * 3600, market=True, min_pct=0.3, k=2) + news_pick(cls, now - 4 * 3600, tks={r["t"] for r in drivers[:3]}, min_pct=0.3, k=2)
    if nn:
        txt += ["", "📰 <b>Возможные причины — свежие новости:</b>"] + [news_line(c, short=True) for c in nn[:3]]
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
    PUB.post("\n".join(txt), [(img, "Индекс МосБиржи: свечи 1 час")] if img and os.path.exists(img) else None)
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
        upc = divs.upcoming(45)
        dst = divs.history_stats()
    except Exception as e:  # noqa: BLE001
        log("дивиденды", e)
        upc, dst = [], {}
    for r in upc:
        if r.get("last_buy") == tmr.isoformat():
            add(r["t"], f"последний день покупки под дивиденд {fmt(r['amount'], 2)} ₽ ({pc(r['yld'], 1, False)})")
        if r["reg"] == tmr.isoformat() or (r.get("last_buy") == now.date().isoformat()):
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
        if tk != "РЫНОК":
            add(tk, f"весомая {'позитивная' if (c.get('score') or 0) > 0 else 'негативная'} новость: «{esc(cut(clean_tx(c['first'].get('tx')), 90))}»")
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
    found.sort(key=lambda x: -x["lvl"])
    posted = 0
    for x in found:
        if posted >= 3 or stt["n"] >= int(os.environ.get("ANOMALY_PER_DAY", "15")):
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
    txt = [f"🚨 <b>Аномалия: {lab}</b> — {'резкий рост' if up else 'резкое падение'}" + (" на большом объёме" if x.get("vx") and x["vx"] >= 4 else ""),
           "Что случилось: " + "; ".join(x["why"]) + "."]
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
            txt.append(f"Кто двигал: в последних {len(tr)} сделках {'покупатели' if vb >= vs else 'продавцы'} ({pc((vb - vs) / (vb + vs), 0)} перевес), "
                       f"крупнейшая — {'покупка' if big[1] == 'B' else 'продажа'} на {bln(big[0])} в {str(big[2].get('TRADETIME', ''))[:5]}.")
    # причины
    if x["kind"] == "share":
        cl = news_for([t])
    else:
        cl = news_for(FUT[x["asset"]][2], types={"BR": ["oil_", "geo_", "sanctions"], "NG": ["oil_", "geo_", "sanctions"], "TTF": ["oil_", "geo_", "sanctions"],
                                                     "GOLD": ["geo_", "cbr_"], "SILV": ["geo_"], "WHEAT": ["geo_", "tax"]}.get(x["asset"]))
    if cl:
        txt.append("Возможная причина — свежие новости:")
        for c in cl:
            head = re.sub(r"\s+", " ", html.unescape(c["first"].get("tx") or ""))[:170]
            txt.append(f"• {esc(head)} — <a href=\"{c['first']['link']}\">{esc(c['first'].get('ch', ''))}</a>")
    elif x["kind"] == "fut":
        exp = (fut_fronts([x["asset"]]).get(x["asset"]) or {}).get("exp")
        txt.append("Возможная причина: в публичных новостях за 6 часов объяснения нет — вероятно, движение мировых цен на это сырьё, крупный участник или приближение экспирации контракта"
                   + (f" ({exp[8:10]}.{exp[5:7]})" if exp else "") + ". Проверьте зарубежные котировки.")
    else:
        txt.append("Возможная причина: в публичных новостях за 6 часов объяснения нет — так бывает при выходе крупного участника, перед корпоративными событиями (отчётность, дивиденды, допэмиссия) или из-за слухов. Осторожно: такие движения часто частично откатываются.")
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
    txt += ["", "<i>Аномалия — движение цены или оборота намного сильнее обычного. Это повод присмотреться, а не готовый сигнал на сделку.</i>", "#аномалия" + (" #" + t if x["kind"] == "share" else "")]
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
    PUB.post("\n".join(txt), [(img, f"{lab}: свечи 1 час")] if img and os.path.exists(img) else None)


_SEC = None


def sec_of_cache():
    global _SEC
    if _SEC is None:
        _SEC = sec_of()
    return _SEC


def main():
    mode = next((a for a in sys.argv[1:] if not a.startswith("--")), None) or os.environ.get("MODE") or SCHED.get(os.environ.get("SCHED", "").strip())
    if not mode:
        h = now_msk().strftime("%H:%M")
        mode = "morning" if "06:00" <= h < "08:30" else "pulse"
    if mode in ("morning_dry", "evening_dry"):  # проверка без публикации: всё собирается, текст — в журнал
        PUB.dry = True
        PUB.sink = open(os.path.join(OUT, "posts.txt"), "a", encoding="utf-8")
        mode = mode[:-4]
    log("режим", mode, "(без отправки)" if PUB.dry else "")
    {"morning": morning, "flows": flows, "ofz": ofz, "pulse": pulse, "evening": evening, "weekly": weekly, "aitest": aitest, "test_post": test_post, "tg_check": tg_check, "anomaly": anomaly, "commod": commod_plan}[mode]()


if __name__ == "__main__":
    main()
