# -*- coding: utf-8 -*-
"""Загрузка истории ALGOPACK Мосбиржи (5-минутные «суперсвечи», с 2020 г.) и сжатие до дневных показателей по бумагам.

Нужен токен ALGOPACK (бесплатная подписка на passport.moex.com → «Аналитические продукты» → ALGOPACK): секрет репозитория
ALGOPACK_TOKEN. Запросы: https://apim.moex.com/iss/datashop/algopack/eq/<tradestats|orderstats|obstats>.json?date=Д&start=N
с заголовком Authorization: Bearer <токен> (так же, как в официальной библиотеке moexalgo).

  tradestats — сделки: объём покупок/продаж (val_b/val_s), число сделок покупателей/продавцов, VWAP покупок и продаж;
  orderstats — заявки: выставлено/снято на покупку и продажу;
  obstats    — стакан: спред, глубина и дисбаланс заявок на покупку/продажу.

Дневной файл: ANOM_DATA/algopack/<год>/<дата>.csv.gz — одна строка на бумагу. Запуск:
  python algopack.py download 2023        — скачать все торговые дни года (уже скачанные пропускаются)
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import io
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("ANOM_DATA") or os.path.join(HERE, "data")
ROOT = os.path.join(DATA, "algopack")
BASE = "https://apim.moex.com/iss/datashop/algopack/eq"
DATASETS = ("tradestats", "orderstats", "obstats", "hi2", "alerts")


def log(*a):
    print(dt.datetime.utcnow().strftime("%H:%M:%S"), *a, flush=True)


_CTX = None


def _ctx():
    """apim.moex.com подписан сертификатом НУЦ Минцифры (Russian Trusted Root CA) — его нет в стандартном наборе.
    Сертификаты скачиваются в workflow с gu-st.ru в файл RU_CA (как у бота для API Т-Пульса); проверка остаётся включённой."""
    global _CTX
    if _CTX is None:
        _CTX = ssl.create_default_context()
        path = os.environ.get("RU_CA", "")
        if path and os.path.exists(path):
            _CTX.load_verify_locations(cafile=path)
        else:
            log("RU_CA: нет файла сертификатов НУЦ Минцифры —", path or "(не задан)")
    return _CTX


def _get(url, token, tries=6):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "moex-newsbot-research"})
    for k in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=90, context=_ctx()) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(f"ALGOPACK отказал в доступе ({e.code}): проверьте токен и подписку") from e
            if k == tries - 1:
                raise
        except Exception:  # noqa: BLE001  (сеть, тайм-аут; изредка узел отдаёт другую цепочку сертификатов — повтор)
            if k == tries - 1:
                raise
        time.sleep(2 * (k + 1))


def _table(js):
    d = js.get("data", js)
    if isinstance(d, dict) and "columns" in d:
        return d["columns"], d.get("data") or []
    return [], []


def fetch(ds, day, token):
    """Все 5-минутные строки набора ds за день по всем бумагам (с постраничной загрузкой)."""
    rows, cols, start = [], None, 0
    while True:
        q = urllib.parse.urlencode({"date": day, "start": start})
        c, data = _table(_get(f"{BASE}/{ds}.json?{q}", token))
        if not data:
            break
        cols = cols or c
        rows += data
        start += len(data)
        if len(data) % 100:      # размер страницы сервера — круглое число; неполная страница — последняя
            break
    return pd.DataFrame(rows, columns=cols) if cols else pd.DataFrame()


def _n(df, c):
    return pd.to_numeric(df[c], errors="coerce") if c in df.columns else pd.Series(np.nan, index=df.index)


def daily(tr, od, ob, hi=None, al=None):
    """Сжатие 5-минутных строк до одной строки на бумагу за день."""
    out = []
    if hi is not None and len(hi) and {"metric", "value"} <= set(hi.columns):
        hi = hi.copy()
        hi["value"] = pd.to_numeric(hi["value"], errors="coerce")
        hi = hi.sort_values(["secid", "tradetime"]) if "tradetime" in hi else hi
        out.append(hi.groupby(["secid", "metric"])["value"].last().unstack().add_prefix("hi2_"))
    if al is not None and len(al) and "alert_type" in al.columns:
        at = al["alert_type"].astype(str)
        a = pd.DataFrame({"secid": al["secid"], "al_n": 1, "al_up": at.str.endswith("+").astype(int),
                          "al_dn": at.str.endswith("-").astype(int),
                          "al_pr": at.str.startswith("pr_").astype(int), "al_vol": at.str.contains("vol|val|trades").astype(int)})
        out.append(a.groupby("secid").sum())
    if len(tr):
        tr = tr.copy()
        tm = tr["tradetime"].astype(str).str[:5] if "tradetime" in tr else pd.Series("", index=tr.index)
        late = (tm >= "17:40") & (tm < "18:40")      # последний час основной сессии
        early = (tm >= "10:00") & (tm < "11:00")
        for c in ("val_b", "val_s", "trades_b", "trades_s", "val", "trades"):
            tr[c] = _n(tr, c)
        tr["lb"], tr["ls"] = tr.val_b.where(late), tr.val_s.where(late)
        tr["eb"], tr["es"] = tr.val_b.where(early), tr.val_s.where(early)
        tr["disb"] = _n(tr, "disb")
        g = tr.groupby("secid")
        out.append(g[["val_b", "val_s", "trades_b", "trades_s", "val", "trades", "lb", "ls", "eb", "es"]].sum(min_count=1)
                   .join(g["disb"].mean().rename("disb_m")))
    if len(od):
        od = od.copy()
        cs = ["put_val_b", "put_val_s", "cancel_val_b", "cancel_val_s", "put_orders_b", "put_orders_s",
              "cancel_orders_b", "cancel_orders_s"]
        for c in cs:
            od[c] = _n(od, c)
        out.append(od.groupby("secid")[cs].sum(min_count=1))
    if len(ob):
        ob = ob.copy()
        cs = ["spread_bbo", "spread_lv10", "spread_1mio", "levels_b", "levels_s", "vol_b", "vol_s", "val_b", "val_s",
              "imbalance_vol_bbo", "imbalance_val_bbo", "imbalance_vol", "imbalance_val"]
        for c in cs:
            ob[c] = _n(ob, c)
        ob = ob.sort_values(["secid", "tradetime"]) if "tradetime" in ob else ob
        g = ob.groupby("secid")
        m = g[cs].mean().add_prefix("ob_")
        last = g[["imbalance_vol", "imbalance_val_bbo", "spread_bbo"]].last().add_prefix("obl_")
        out.append(m.join(last))
    if not out:
        return pd.DataFrame()
    D = pd.concat(out, axis=1)
    D.index.name = "t"
    return D.reset_index()


def trading_days(year):
    d0, d1 = dt.date(year, 1, 1), min(dt.date(year, 12, 31), dt.date.today() - dt.timedelta(days=1))
    d0 = max(d0, dt.date(2020, 1, 3))
    return [d0 + dt.timedelta(days=i) for i in range((d1 - d0).days + 1) if (d0 + dt.timedelta(days=i)).weekday() < 5]


def download(year, workers=None):
    workers = workers or int(os.environ.get("ALGOPACK_WORKERS", "12"))
    deadline = time.time() + 60 * float(os.environ.get("ALGOPACK_BUDGET_MIN", "300"))
    token = os.environ.get("ALGOPACK_TOKEN", "").strip()
    if not token:
        raise SystemExit("нет ALGOPACK_TOKEN — добавьте секрет репозитория (Settings → Secrets and variables → Actions)")
    folder = os.path.join(ROOT, str(year))
    os.makedirs(folder, exist_ok=True)
    days = [d for d in trading_days(year) if not os.path.exists(os.path.join(folder, f"{d}.csv.gz"))]
    log(f"ALGOPACK {year}: к загрузке {len(days)} дней")
    t0 = time.time()

    def one(d):
        if time.time() > deadline:
            return d, -1, "время вышло — продолжу в следующем запуске"
        try:
            parts = [fetch(ds, d.isoformat(), token) for ds in DATASETS]
        except Exception as e:  # noqa: BLE001  день пропускаем, следующий запуск докачает
            return d, -1, f"ошибка: {str(e)[:200]}"
        D = daily(*parts)
        p = os.path.join(folder, f"{d}.csv.gz")
        if len(D):
            D.to_csv(p, index=False, compression="gzip")
        else:
            open(p, "wb").write(gzip.compress(b""))      # выходной/праздник — пустой файл, чтобы не качать снова
        return d, len(D), [len(x) for x in parts]
    done = bad = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for d, n, sz in ex.map(one, days):
            done += 1
            bad += n < 0
            if n < 0 and bad <= 10:
                log(f"  {d}: {sz}")
            if done <= 3 or done % 10 == 0 or done == len(days):
                log(f"  {d}: бумаг {n}, строк {sz}; {done}/{len(days)}, пропущено {bad}, {round(time.time() - t0)} с")
    if bad == len(days) and days:
        raise SystemExit("ни один день не загрузился")


def load():
    """Все дневные файлы → DataFrame (t, d, показатели)."""
    fr = []
    for root, _dirs, files in os.walk(ROOT):
        for f in files:
            if f.endswith(".csv.gz"):
                p = os.path.join(root, f)
                if os.path.getsize(p) < 30:
                    continue
                df = pd.read_csv(p)
                df["d"] = pd.to_datetime(f[:10])
                fr.append(df)
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "download":
        download(int(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "probe":
        # проверка токена и названий полей на одном недавнем дне (только первая страница)
        tok = os.environ.get("ALGOPACK_TOKEN", "").strip()
        day = trading_days(dt.date.today().year)[-3].isoformat()
        for ds in DATASETS:
            try:
                c, data = _table(_get(f"{BASE}/{ds}.json?" + urllib.parse.urlencode({"date": day, "start": 0}), tok))
                log(f"{ds} {day}: строк на странице {len(data)}; поля: {', '.join(c)}; пример: {data[0] if data else '-'}")
            except Exception as e:  # noqa: BLE001
                log(f"{ds}: ошибка {e}")
                if ds in DATASETS[:3]:
                    raise
    else:
        print(__doc__)
