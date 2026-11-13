# -*- coding: utf-8 -*-
"""Проверка, что ещё даёт подписка ALGOPACK (без публикаций): реальное время ISS через apim.moex.com против бесплатного
iss.moex.com (задержка до 15 минут), стакан, суперсвечи срочного и валютного рынка, HI2, FUTOI на сегодня."""
import datetime as dt
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import ru_ctx  # noqa: E402

TOK = (os.environ.get("ALGOPACK_TOKEN") or "").strip()


def get(url, auth=True):
    h = {"User-Agent": "moex-newsbot"}
    if auth:
        h["Authorization"] = f"Bearer {TOK}"
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40, context=ru_ctx()) as r:
            return json.loads(r.read().decode("utf-8")), round(time.time() - t0, 2), None
    except Exception as e:  # noqa: BLE001
        return None, round(time.time() - t0, 2), f"{type(e).__name__}: {str(e)[:150]}"


def show(name, url, auth=True, keys=None):
    js, sec, err = get(url, auth)
    if err:
        print(f"[{name}] ОШИБКА за {sec} с: {err}", flush=True)
        return None
    blocks = {k: v for k, v in js.items() if isinstance(v, dict) and "columns" in v}
    out = []
    for k, v in blocks.items():
        rows = v.get("data") or []
        out.append(f"{k}: строк {len(rows)}, поля {v['columns'][:25]}" + (f", пример {rows[0][:25]}" if rows else ""))
    print(f"[{name}] {sec} с; " + " | ".join(out)[:1500], flush=True)
    return js


def main():
    now = dt.datetime.utcnow()
    print("UTC сейчас", now.isoformat(timespec="seconds"), "МСК", (now + dt.timedelta(hours=3)).isoformat(timespec="seconds"), flush=True)
    q = "iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,LAST,UPDATETIME,SYSTIME,TIME&securities=SBER,GAZP"
    for host, auth in (("https://iss.moex.com/iss", False), ("https://apim.moex.com/iss", True)):
        show(f"marketdata {host[8:20]}", f"{host}/engines/stock/markets/shares/boards/TQBR/securities.json?{q}", auth)
        show(f"свечи 1 мин {host[8:20]}", f"{host}/engines/stock/markets/shares/boards/TQBR/securities/SBER/candles.json?iss.meta=off&interval=1&from={(now + dt.timedelta(hours=3)).date()}&iss.reverse=true&limit=3", auth)
        show(f"сделки {host[8:20]}", f"{host}/engines/stock/markets/shares/boards/TQBR/securities/SBER/trades.json?iss.meta=off&iss.reverse=true&limit=3", auth)
        show(f"стакан {host[8:20]}", f"{host}/engines/stock/markets/shares/boards/TQBR/securities/SBER/orderbook.json?iss.meta=off", auth)
        show(f"фьючерсы {host[8:20]}", f"{host}/engines/futures/markets/forts/securities.json?iss.meta=off&iss.only=marketdata&marketdata.columns=SECID,LAST,UPDATETIME,SYSTIME&securities=SiZ6,BRZ6", auth)
        show(f"история {host[8:20]}", f"{host}/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json?iss.meta=off&from={(now - dt.timedelta(days=5)).date()}", auth)
    d = (now + dt.timedelta(hours=3)).date().isoformat()
    base = "https://apim.moex.com/iss/datashop/algopack"
    for m in ("fo", "fx"):
        for ds in ("tradestats", "orderstats", "obstats"):
            show(f"суперсвечи {m}/{ds}", f"{base}/{m}/{ds}.json?date={d}")
    show("суперсвечи fo/tradestats SiZ6", f"{base}/fo/tradestats/SiZ6.json?from={d}&till={d}")
    show("суперсвечи fo/tradestats Si", f"{base}/fo/tradestats/si.json?from={d}&till={d}")
    show("HI2 сегодня", f"{base}/eq/hi2.json?date={d}")
    show("FUTOI сегодня", f"https://apim.moex.com/iss/analyticalproducts/futoi/securities/sr.json?from={d}&till={d}")
    show("календарь", "https://apim.moex.com/iss/calendars.json?iss.meta=off")


if __name__ == "__main__":
    main()
