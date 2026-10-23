# -*- coding: utf-8 -*-
"""Дивиденды: ближайшие объявленные выплаты (dohod.ru) и историческая реакция цены каждой бумаги на отсечки (свечи Мосбиржи).

Статистика по бумаге (по всем отсечкам с доходностью от 1%, история divhist.json + свечи ISS):
  run20 — как бумага вела себя к индексу за 20 торговых дней до последнего дня покупки (дивидендное ралли);
  gap   — какую долю дивиденда «съел» гэп в день отсечки;
  close — за сколько торговых дней цена возвращалась к уровню до отсечки (закрытие гэпа) и в какой доле случаев за 60 дней.
"""
import datetime as dt
import html as _html
import json
import os
import re
import statistics as st
import time

from common import DEMO, http_get, index_candles, load_json, log, save_json, share_candles

HERE = os.path.dirname(os.path.abspath(__file__))
DIVS = json.load(open(os.path.join(HERE, "..", "divhist.json"), encoding="utf-8"))


def upcoming(days=45):
    """Объявленные (рекомендованные/утверждённые) дивиденды с датой закрытия реестра в ближайшие days дней."""
    cache = load_json("div_upcoming.json", {})
    today = dt.date.today().isoformat()
    if cache.get("date") == today:
        return cache.get("rows", [])
    rows = []
    if DEMO:
        d0 = dt.date.today()
        return [dict(t="LKOH", name="Лукойл", period="9 мес. 2026", amount=535.0, yld=0.091, reg=(d0 + dt.timedelta(days=12)).isoformat(), last_buy=(d0 + dt.timedelta(days=11)).isoformat(), declared=True),
                dict(t="AKRN", name="Акрон", period="спец.", amount=146.0, yld=0.008, reg=(d0 + dt.timedelta(days=4)).isoformat(), last_buy=(d0 + dt.timedelta(days=1)).isoformat(), declared=True)]
    if not DEMO:
        try:
            raw = http_get("https://www.dohod.ru/ik/analytics/dividend", timeout=40, tries=2).decode("utf-8", "ignore")
            body = raw[raw.find('id="table-dividend"'):]
            body = body[:body.find("</table>")]
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)[1:]:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
                if len(cells) < 21:
                    continue
                txt = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in cells]
                declared = "title=" in cells[4] or "title=" in cells[9]
                period, amount, reg, last_buy, slug = txt[2], txt[3], txt[8], txt[19], txt[20]
                if "всего" in period or not re.match(r"\d\d\.\d\d\.\d{4}", reg):
                    continue
                d = dt.datetime.strptime(reg, "%d.%m.%Y").date()
                lb = dt.datetime.strptime(last_buy, "%d.%m.%Y").date().isoformat() if re.match(r"\d\d\.\d\d\.\d{4}", last_buy) else None
                if not (dt.date.today() <= d <= dt.date.today() + dt.timedelta(days=days)):
                    continue
                try:
                    amt = float(amount.replace(" ", "").replace(",", "."))
                except ValueError:
                    continue
                rows.append(dict(t=slug.upper(), name=txt[0], period=period, amount=amt, yld=float(txt[6].rstrip("%").replace(",", ".") or 0) / 100,
                                 reg=d.isoformat(), last_buy=lb, declared=declared))
        except Exception as e:  # noqa: BLE001
            log("дивиденды dohod.ru:", e)
    rows = [r for r in rows if r["declared"]]
    rows.sort(key=lambda r: r["reg"])
    save_json("div_upcoming.json", dict(date=today, rows=rows))
    return rows


def _ex_index(B, d):
    """Индекс бара отсечки (первая сессия без права на дивиденд). В divhist.json дата — первый будний день без дивиденда;
    с появлением торгов выходного дня гэп может прийти раньше, в субботу. Берём из двух кандидатов бар с самым глубоким гэпом."""
    days = [b["t"] for b in B]
    pw = dt.date.fromisoformat(d) - dt.timedelta(days=1)
    while pw.weekday() >= 5:
        pw -= dt.timedelta(days=1)
    c = {next((i for i, x in enumerate(days) if x > pw.isoformat()), None), next((i for i, x in enumerate(days) if x >= d), None)}
    c = [i for i in c if i]
    return min(c, key=lambda i: B[i]["o"] / B[i - 1]["c"] if B[i - 1]["c"] else 9) if c else None


def history_stats(tickers=None, max_age_days=7):
    """Статистика реакции на отсечки по каждой бумаге; пересчёт раз в неделю (кэш div_stats.json)."""
    cache = load_json("div_stats.json", {})
    if cache.get("v") != 2:
        cache = {}  # прежняя версия расчёта отсечки — пересчитать
    if cache.get("date") and (dt.date.today() - dt.date.fromisoformat(cache["date"])).days < max_age_days and not tickers:
        return cache.get("st", {})
    out = dict(cache.get("st", {}))
    try:
        ix = index_candles("IMOEX", 4500)
    except Exception as e:  # noqa: BLE001
        log("дивиденды: индекс", e)
        return out
    ixc = {b["t"]: b["c"] for b in ix}
    for t in (tickers or list(DIVS)):
        try:
            B = share_candles(t, 4500)
        except Exception as e:  # noqa: BLE001
            log("дивиденды: свечи", t, e)
            continue
        days = [b["t"] for b in B]
        ev = []
        for reg, amt in DIVS.get(t, []):
            if reg < "2012-01-01" or reg > dt.date.today().isoformat():
                continue
            i = _ex_index(B, reg)
            if not i or i < 25 or i + 2 >= len(B):
                continue
            prev = B[i - 1]["c"]
            y = amt / prev if prev else 0
            if y < 0.01:
                continue
            gap = B[i]["o"] / prev - 1
            d0, d20 = days[i - 1], days[i - 21]
            run = (prev / B[i - 21]["c"] - 1) - ((ixc.get(d0, 0) / ixc.get(d20, 1) - 1) if ixc.get(d0) and ixc.get(d20) else 0)
            k = next((j - i for j in range(i, min(len(B), i + 250)) if B[j]["c"] >= prev), None)
            ev.append(dict(d=reg, y=y, gap=gap, share=-gap / y if y else None, run=run, close=k))
        if len(ev) >= 3:
            closed = [e["close"] for e in ev if e["close"] is not None]
            out[t] = dict(n=len(ev), y=st.mean(e["y"] for e in ev), run=st.mean(e["run"] for e in ev), run_up=sum(e["run"] > 0 for e in ev) / len(ev),
                          share=st.median(e["share"] for e in ev if e["share"] is not None), close_med=st.median(closed) if closed else None,
                          close60=sum(1 for c in closed if c <= 60) / len(ev), last=ev[-1])
        time.sleep(0.05)
    if not tickers:
        save_json("div_stats.json", dict(date=dt.date.today().isoformat(), st=out, v=2))
    return out


def describe(t, s):
    """Короткое описание исторической реакции на отсечку."""
    if not s:
        return "истории отсечек мало"
    from common import pc, fmt
    parts = [f"за 20 дней до отсечки бумага {'обгоняла' if s['run'] >= 0 else 'отставала от'} индекс{'' if s['run'] >= 0 else 'а'} в среднем на {pc(abs(s['run']), 1).lstrip('+')} "
             f"(рост к индексу в {round(s['run_up'] * 100)}% случаев)",
             f"гэп в день отсечки ≈ {round((s['share'] or 0) * 100)}% дивиденда".replace("-", "−")]
    if s.get("close_med") is not None:
        parts.append(f"гэп закрывался в среднем за {fmt(s['close_med'], 0)} торг. дней, за 60 дней — в {round(s['close60'] * 100)}% случаев")
    n = s["n"]
    w = "отсечка" if n % 10 == 1 and n % 100 != 11 else "отсечки" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "отсечек"
    return "; ".join(parts) + f" ({n} {w} с 2012 г.)"
