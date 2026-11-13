# -*- coding: utf-8 -*-
"""ALGOPACK Мосбиржи в работе бота (платная подписка администратора, секрет ALGOPACK_TOKEN).

  day_flows(tickers)  — покупки и продажи за день по бумагам (tradestats: val_b / val_s — объём сделок по инициатору),
                        точнее своей оценки по ленте сделок и без постраничной загрузки ленты;
  book(ticker)        — стакан на последний 5-минутный бар (obstats): спред, спред на 1 млн ₽, глубина, дисбаланс заявок;
  alerts(day)         — сигналы биржи об аномальных событиях (MEGA ALERTS: редкие изменения цены, объёма, числа сделок).

Без токена или при ошибке функции возвращают пусто — бот работает как раньше (лента сделок ISS).
Сервер apim.moex.com подписан сертификатом НУЦ Минцифры: контекст TLS — common.ru_ctx() (файл RU_CA из workflow).
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://apim.moex.com/iss/datashop/algopack/eq"
ROOT = "https://apim.moex.com/iss/datashop/algopack"


def token():
    return (os.environ.get("ALGOPACK_TOKEN") or "").strip()


def enabled():
    return bool(token())


def _ctx():
    try:
        from common import ru_ctx
        return ru_ctx()
    except Exception:  # noqa: BLE001
        import ssl
        c = ssl.create_default_context()
        p = os.environ.get("RU_CA", "")
        if p and os.path.exists(p):
            c.load_verify_locations(cafile=p)
        return c


def _get(path, tries=4, market="eq", **params):
    url = f"{ROOT}/{market}/{path}.json" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token()}", "User-Agent": "moex-newsbot"})
    for k in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=40, context=_ctx()) as r:
                js = json.loads(r.read().decode("utf-8"))
            d = js.get("data", js)
            if isinstance(d, dict) and "columns" in d:
                return [dict(zip(d["columns"], row)) for row in (d.get("data") or [])]
            return []
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) or k == tries - 1:
                raise
        except Exception:  # noqa: BLE001
            if k == tries - 1:
                raise
        time.sleep(1.5 * (k + 1))
    return []


def _rows(path, market="eq", **params):
    out, start = [], 0
    while True:
        part = _get(path, start=start, market=market, **params)
        out += part
        if not part or len(part) % 100:
            break
        start += len(part)
        if start > 200000:
            break
    return out


def _today():
    return (dt.datetime.utcnow() + dt.timedelta(hours=3)).date().isoformat()


def day_flows(tickers, day=None, workers=8, market="eq"):
    """{тикер: {"vb": покупки ₽, "vs": продажи ₽, "tb": сделок покупателей, "ts": продавцов, "last": время последнего бара}}.
    market: eq — акции, fo — фьючерсы (код контракта, например BRZ6), fx — валюта."""
    if not enabled():
        return {}
    day = day or _today()

    def one(t):
        try:
            R = _rows(f"tradestats/{t}", market=market, **{"from": day, "till": day})
        except Exception:  # noqa: BLE001
            return t, None
        if not R:
            return t, None
        f = lambda k: sum(float(r.get(k) or 0) for r in R)
        return t, dict(vb=f("val_b"), vs=f("val_s"), tb=f("trades_b"), ts=f("trades_s"), last=max(str(r.get("tradetime") or "") for r in R))
    out = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        for t, v in ex.map(one, list(tickers)):
            if v:
                out[t] = v
    return out


def book(ticker, day=None):
    """Стакан на последний бар дня: спред (б.п. и %), спред на 1 млн ₽, дисбаланс объёма заявок, глубина покупок/продаж."""
    if not enabled():
        return None
    day = day or _today()
    try:
        R = _rows(f"obstats/{ticker}", **{"from": day, "till": day})
        if not R:  # до открытия — последний бар прошлого торгового дня
            d0 = dt.date.fromisoformat(day)
            R = _rows(f"obstats/{ticker}", **{"from": (d0 - dt.timedelta(days=6)).isoformat(), "till": (d0 - dt.timedelta(days=1)).isoformat()})
    except Exception:  # noqa: BLE001
        return None
    if not R:
        return None
    R.sort(key=lambda r: (str(r.get("tradedate")), str(r.get("tradetime"))))
    r = R[-1]
    last = R[-12:]  # последний час
    num = lambda x: float(x) if x is not None else None
    imb_h = [num(x.get("imbalance_vol")) for x in last if x.get("imbalance_vol") is not None]
    return dict(date=r.get("tradedate"), time=str(r.get("tradetime"))[:5], spread_bp=num(r.get("spread_bbo")), spread_1m_bp=num(r.get("spread_1mio")),
                imb=num(r.get("imbalance_vol")), imb_hour=(sum(imb_h) / len(imb_h)) if imb_h else None,
                vol_b=num(r.get("val_b")), vol_s=num(r.get("val_s")), levels_b=r.get("levels_b"), levels_s=r.get("levels_s"))


def alerts(day=None):
    """Сигналы биржи за день: {тикер: [{"time", "type", "text", "up"}]} (type — например pr_change_99_9_pctl+)."""
    if not enabled():
        return {}
    day = day or _today()
    try:
        R = _rows("alerts", date=day)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for r in R:
        ty = str(r.get("alert_type") or "")
        out.setdefault(r.get("secid"), []).append(dict(time=str(r.get("tradetime"))[:5], type=ty, text=alert_text(ty), up=ty.endswith("+")))
    return out


def alert_text(ty):
    """pr_change_99_9_pctl+ → «изменение цены вверх реже, чем в 0,1% обычных 5-минуток»."""
    base = ty.rstrip("+-")
    what = ("изменение цены" if base.startswith("pr_") else "оборот" if base.startswith(("val", "vol")) else
            "число сделок" if base.startswith("trades") else "спред" if "spread" in base else "дисбаланс заявок" if "imbal" in base else base.split("_")[0])
    pct = ""
    if "pctl" in base:
        m = re.search(r"(\d+(?:_\d+)?)_pctl", base)
        if m:
            val = float(m.group(1).replace("_", "."))
            pct = f" — реже, чем в {('%g' % round(100 - val, 2)).replace('.', ',')}% обычных 5-минутных интервалов"
    d = " вверх" if ty.endswith("+") else " вниз" if ty.endswith("-") else ""
    return f"{what}{d}{pct}"


def orderbook(ticker):
    """Стакан сейчас (ISS реального времени через ALGOPACK): лучшие цены, спред, объём заявок в пределах ±1% от середины."""
    if not enabled():
        return None
    try:
        from common import iss, table
        R = table(iss(f"engines/stock/markets/shares/boards/TQBR/securities/{ticker}/orderbook.json"), "orderbook")
    except Exception:  # noqa: BLE001
        return None
    bids = [(float(r["PRICE"]), float(r["QUANTITY"])) for r in R if r.get("BUYSELL") == "B" and r.get("PRICE")]
    asks = [(float(r["PRICE"]), float(r["QUANTITY"])) for r in R if r.get("BUYSELL") == "S" and r.get("PRICE")]
    if not bids or not asks:
        return None
    bb, ba = max(p for p, _ in bids), min(p for p, _ in asks)
    mid = (bb + ba) / 2
    vb = sum(p * q for p, q in bids if p >= mid * 0.99)
    va = sum(p * q for p, q in asks if p <= mid * 1.01)
    return dict(bid=bb, ask=ba, spread=(ba - bb) / mid, depth_b=vb, depth_s=va, imb=(vb - va) / (vb + va) if vb + va > 0 else None,
                time=str(max((r.get("UPDATETIME") or "") for r in R))[:8])

