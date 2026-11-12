# -*- coding: utf-8 -*-
"""FUTOI (ALGOPACK): открытые позиции физлиц и юрлиц на фьючерсах на акции — «толпа» против «крупных».

Запрос (как в библиотеке moexalgo): https://apim.moex.com/iss/analyticalproducts/futoi/securities/<код>.json?from=&till=&offset=&limit=
с заголовком Authorization: Bearer <ALGOPACK_TOKEN>. Код — короткий код фьючерса (SR — Сбербанк, GZ — Газпром…), берётся из
списка фьючерсов ISS (SECID без двух последних символов) и сопоставляется с акцией.
Сохраняем последний срез дня: ANOM_DATA/futoi/<код>.csv.gz (дата, группа FIZ/YUR, позиции длинные/короткие, число лиц).
Запуск: python futoi.py probe | python futoi.py download
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import algopack as AP  # noqa: E402

DATA = AP.DATA
ROOT = os.path.join(DATA, "futoi")
BASE = "https://apim.moex.com/iss/analyticalproducts/futoi/securities"
log = AP.log


def _get(url, tok):
    return AP._get(url, tok)


def fetch(code, d0, d1, tok):
    """Ответ — блок «futoi» (а не «data»), страницы — параметром start (как в moexalgo.features.futoi)."""
    rows, cols, start = [], None, 0
    while True:
        q = urllib.parse.urlencode({"from": d0, "till": d1, "start": start})
        js = _get(f"{BASE}/{code}.json?{q}", tok)
        blk = js.get("futoi") or js.get("data") or {}
        c, data = (blk.get("columns") or [], blk.get("data") or []) if isinstance(blk, dict) else ([], [])
        if not data:
            break
        cols = cols or c
        rows += data
        start += len(data)
        if len(data) % 100:
            break
    return pd.DataFrame(rows, columns=cols) if cols else pd.DataFrame()


def codes():
    """{короткий код фьючерса: тикер акции} по списку фьючерсов ISS."""
    import experiment as EX
    from common import iss, table
    js = iss("engines/futures/markets/forts/securities.json", **{"iss.only": "securities", "securities.columns": "SECID,ASSETCODE"})
    mp = {}
    for r in table(js, "securities"):
        a, s = r.get("ASSETCODE") or "", r["SECID"]
        if len(s) >= 4 and a:
            mp[s[:-2]] = EX.ASSET2TK.get(a, a)
    return mp


def daily_last(df):
    if df.empty:
        return df
    df = df.copy()
    df["tradedate"] = df["tradedate"].astype(str)
    df = df.sort_values(["tradedate", "tradetime"]) if "tradetime" in df else df.sort_values("tradedate")
    keep = [c for c in ("tradedate", "clgroup", "pos", "pos_long", "pos_short", "pos_long_num", "pos_short_num") if c in df.columns]
    return df.groupby(["tradedate", "clgroup"]).last().reset_index()[keep]


def download(start="2020-01-01"):
    tok = os.environ.get("ALGOPACK_TOKEN", "").strip()
    os.makedirs(ROOT, exist_ok=True)
    mp = codes()
    stocks = set(json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8")).get("secOf", {}))
    todo = {c: t for c, t in mp.items() if not stocks or t in stocks}
    log("FUTOI: кодов фьючерсов на акции", len(todo))
    deadline = time.time() + 60 * float(os.environ.get("ALGOPACK_BUDGET_MIN", "300"))
    today = dt.date.today()
    for code, tk in sorted(todo.items()):
        p = os.path.join(ROOT, f"{code}.csv.gz")
        old = pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()
        d0 = dt.date.fromisoformat(start)
        if len(old):
            d0 = dt.date.fromisoformat(str(old.tradedate.max())[:10]) + dt.timedelta(days=1)
        parts = [old] if len(old) else []
        while d0 < today and time.time() < deadline:
            d1 = min(today, d0 + dt.timedelta(days=90))
            try:
                parts.append(daily_last(fetch(code.lower(), d0.isoformat(), d1.isoformat(), tok)))
            except Exception as e:  # noqa: BLE001
                log(f"FUTOI {code}: {d0}: {str(e)[:150]}")
                break
            d0 = d1 + dt.timedelta(days=1)
        D = pd.concat([x for x in parts if len(x)], ignore_index=True) if any(len(x) for x in parts) else pd.DataFrame()
        if len(D):
            D = D.drop_duplicates(["tradedate", "clgroup"], keep="last")
            D["stock"] = tk
            D.to_csv(p, index=False, compression="gzip")
        log(f"FUTOI {code} → {tk}: дней {D.tradedate.nunique() if len(D) else 0}")


def load():
    fr = []
    if os.path.isdir(ROOT):
        for f in os.listdir(ROOT):
            if f.endswith(".csv.gz"):
                fr.append(pd.read_csv(os.path.join(ROOT, f)).assign(code=f[:-7]))
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        tok = os.environ.get("ALGOPACK_TOKEN", "").strip()
        d1 = dt.date.today() - dt.timedelta(days=3)
        for code in ("sr", "gz", "si"):
            try:
                df = fetch(code, (d1 - dt.timedelta(days=2)).isoformat(), d1.isoformat(), tok)
                log(f"FUTOI {code}: строк {len(df)}; поля {list(df.columns)}; пример {df.iloc[-1].tolist() if len(df) else '-'}")
            except Exception as e:  # noqa: BLE001
                log(f"FUTOI {code}: ошибка {e}")
    elif len(sys.argv) > 1 and sys.argv[1] == "download":
        download()
