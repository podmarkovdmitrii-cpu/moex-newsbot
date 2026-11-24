# -*- coding: utf-8 -*-
"""Инструменты для теханализа в боте идей кроме акций: индексы Мосбиржи (IMOEX и др.) и фьючерсы срочного рынка (FORTS).

• /ta IMOEX — индекс Мосбиржи (также RTSI, MOEXBC, MOEXOG, MOEXFN … — любой индекс ISS с историей).
• /ta Si, /ta BR, /ta GOLD, /ta SBRF … — фьючерс по коду базового актива (ASSETCODE) или по короткому коду контракта (Si, BR, GD, SR …):
  берётся непрерывный ряд ближайших контрактов, склеенный на экспирациях с обратной корректировкой цен (как у графиков
  «непрерывный фьючерс»); часовые — по текущему ближайшему контракту.
• /ta SiZ6 — конкретный контракт; /ta USDRUBF, IMOEXF … — вечные фьючерсы (своя история с запуска).
• /futures — список всех фьючерсов: код, ближайший контракт, экспирация, оборот, открытый интерес.
Какие фьючерсы есть в Т-Инвестициях: брокер даёт доступ к срочному рынку Мосбиржи (FORTS). Если задан секрет TINVEST_TOKEN
(токен T-Invest API «только чтение» — добавляет сам владелец в GitHub), список сверяется с инструментами T-Invest API, доступными
для торговли; без токена показываются все фьючерсы FORTS с пометкой, что доступность конкретного контракта надо проверить в приложении.
Не является индивидуальной инвестиционной рекомендацией.
"""
import datetime as dt
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import common as C

MCODE = "FGHJKMNQUVXZ"
_C = {}
INDEX_NAMES = {"IMOEX": "Индекс Мосбиржи", "RTSI": "Индекс РТС", "MOEXBC": "Индекс голубых фишек", "RGBI": "Индекс гособлигаций RGBI",
               "MOEXOG": "Индекс нефти и газа", "MOEXFN": "Индекс финансов", "MOEXMM": "Индекс металлов и добычи", "MOEXEU": "Индекс электроэнергетики",
               "MOEXTL": "Индекс телекоммуникаций", "MOEXCN": "Индекс потребительского сектора", "MOEXCH": "Индекс химии и нефтехимии",
               "MOEXTN": "Индекс транспорта", "MOEXIT": "Индекс IT", "MOEXRE": "Индекс строительных компаний", "MCXSM": "Индекс малой и средней капитализации"}


def _cached(key, ttl, fn):
    hit = _C.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _C[key] = (time.time(), v)
    return v


# ---------------- список фьючерсов ----------------
def _tinvest_tickers():
    """Тикеры фьючерсов, доступных в Т-Инвестициях (T-Invest API, нужен токен только для чтения в TINVEST_TOKEN). None — без токена/ошибка."""
    import os
    tok = (os.environ.get("TINVEST_TOKEN") or "").strip()
    if not tok:
        return None

    def get():
        for host in ("invest-public-api.tbank.ru", "invest-public-api.tinkoff.ru"):
            try:
                req = urllib.request.Request(f"https://{host}/rest/tinkoff.public.invest.api.contract.v1.InstrumentsService/Futures",
                                             data=json.dumps({"instrumentStatus": "INSTRUMENT_STATUS_BASE"}).encode(),
                                             headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30, context=C.ru_ctx()) as r:
                    js = json.loads(r.read().decode())
                return {str(x.get("ticker") or "").upper() for x in js.get("instruments") or [] if x.get("apiTradeAvailableFlag", True)}
            except Exception as e:  # noqa: BLE001
                C.log("T-Invest API:", host, str(e)[:120])
        return None
    return _cached("tinv", 6 * 3600, get)


def fut_all():
    """Все торгуемые контракты FORTS → (контракты по SECID, активы по коду). Кэш 1 ч."""
    def get():
        js = C.iss("engines/futures/markets/forts/securities.json", **{"iss.only": "securities,marketdata",
                   "securities.columns": "SECID,SHORTNAME,SECNAME,ASSETCODE,SECTYPE,LASTTRADEDATE,PREVSETTLEPRICE",
                   "marketdata.columns": "SECID,LAST,VALTODAY,OPENPOSITION,NUMTRADES"})
        md = {r["SECID"]: r for r in C.table(js, "marketdata")}
        tinv = _tinvest_tickers()
        today = dt.date.today().isoformat()
        cons, assets = {}, {}
        for r in C.table(js, "securities"):
            sid = r["SECID"]
            if (r.get("LASTTRADEDATE") or "9") < today:
                continue
            m = md.get(sid) or {}
            perp = not re.fullmatch(r"[A-Za-z0-9]{2}[FGHJKMNQUVXZ]\d", sid)
            x = dict(secid=sid, short=r.get("SHORTNAME") or sid, name=r.get("SECNAME") or "", asset=r.get("ASSETCODE") or sid,
                     code=(r.get("SECTYPE") or sid[:2]) if not perp else sid, exp=r.get("LASTTRADEDATE"), perp=perp,
                     last=m.get("LAST") or r.get("PREVSETTLEPRICE"), val=m.get("VALTODAY") or 0, oi=m.get("OPENPOSITION") or 0,
                     tinv=(sid.upper() in tinv) if tinv is not None else None)
            cons[sid] = x
            key = sid if perp else x["code"]
            assets.setdefault(key, []).append(x)
        soon = (dt.date.today() + dt.timedelta(days=3)).isoformat()
        for k, lst in assets.items():
            lst.sort(key=lambda z: z["exp"] or "9")
            live = [z for z in lst if (z["exp"] or "9") > soon] or lst
            front = max(live[:3], key=lambda z: (z["oi"] or 0, z["val"] or 0))   # ближайший ликвидный (по открытому интересу)
            for z in lst:
                z["front"] = z is front
        return cons, assets
    return _cached("fut", 3600, get)


def group_of(x):
    a, code = (x["asset"] or "").upper(), x["code"]
    act = None
    try:
        act = C.active_shares()
    except Exception:  # noqa: BLE001
        pass
    fx = {"SI", "EU", "CR", "ED", "UC", "CNY", "USDRUBF", "EURRUBF", "CNYRUBF", "UCNY", "EURUSD", "AE", "TY", "HK", "KZ", "GU", "AU", "UJ"}
    if code.upper() in fx or a in ("SI", "EU", "CNY", "ED", "UCNY", "EUR", "USD") or "RUBF" in x["secid"].upper() and "GLD" not in x["secid"].upper():
        return "Валюты"
    if a in ("RTS", "MIX", "MXI", "RTSM", "IMOEX", "RGBI", "MOEX", "RVI", "IMOEXF", "SPYF", "NASD", "HANG", "DAX", "SPX", "NIKK", "EM", "OGI", "MMI", "FNI", "CNI") \
            or code.upper() in ("RI", "MX", "MM", "RM", "RB", "IMOEXF", "SF", "NA", "HS", "DX"):
        return "Индексы"
    if act and (a in act or a + "P" in act or a.rstrip("P") in act):
        return "Акции"
    if code.upper() in ("BR", "NG", "GD", "SV", "PT", "PD", "CE", "AN", "NC", "ZN", "W4", "SA", "CC", "KC", "FF", "GL", "GLDRUBF", "SL", "CU", "WH", "SU", "BW") \
            or a in ("BR", "NG", "GOLD", "SILV", "PLT", "PLD", "COPPER", "ALUM", "NICKEL", "ZINC", "WHEAT", "SUGAR", "COCOA", "COFFEE", "TTF", "GLDRUBF"):
        return "Товары"
    return "Акции" if len(a) >= 4 else "Прочее"


def fut_list_texts():
    """Сообщения со списком всех фьючерсов (по группам, ≤ 3800 символов каждое)."""
    cons, assets = fut_all()
    tinv = next(iter(cons.values()))["tinv"] is not None if cons else False
    rows = {}
    for k, lst in assets.items():
        f = next(z for z in lst if z["front"])
        if tinv and not any(z["tinv"] for z in lst):
            continue
        rows.setdefault(group_of(f), []).append((k, f, len(lst)))
    head = (f"📜 <b>Фьючерсы срочного рынка Мосбиржи</b> — {sum(len(v) for v in rows.values())} базовых активов, {len(cons)} контрактов. "
            + ("Список сверен с инструментами Т-Инвестиций (T-Invest API)." if tinv else
               "Т-Инвестиции дают доступ к срочному рынку Мосбиржи; доступность конкретного контракта (и требования к квалификации) проверьте "
               "в приложении. Точная сверка — если добавить секрет TINVEST_TOKEN (токен T-Invest API только для чтения).")
            + "\nТеханализ: нажмите команду (/ta_КОД — непрерывный ряд ближайших контрактов) или /ta КОНТРАКТ, например /ta SiZ6.")
    out, cur = [], head
    for g in ("Индексы", "Валюты", "Товары", "Акции", "Прочее"):
        if g not in rows:
            continue
        block = [f"\n\n<b>{g}</b>"]
        for k, f, n in sorted(rows[g], key=lambda z: -(z[1]["val"] or 0)):
            val = f["val"] or 0
            nm = re.sub(r"\s*-\s*\d+\.\d+$", "", f["short"])
            block.append(f"/ta_{k} — {nm}" + ("" if f["perp"] else f" · {f['secid']} до {f['exp'][8:10]}.{f['exp'][5:7]}")
                         + (f" · оборот {val / 1e9:.1f} млрд ₽" if val >= 1e8 else f" · оборот {val / 1e6:.0f} млн ₽" if val >= 1e6 else " · оборот мал")
                         + (f" · {n} контр." if n > 1 else "") + (" · вечный" if f["perp"] else ""))
        for line in block:
            if len(cur) + len(line) > 3800:
                out.append(cur)
                cur = ""
            cur += ("\n" if cur and not line.startswith("\n") else "") + line
    out.append(cur + "\n\n<i>Не является индивидуальной инвестиционной рекомендацией.</i>")
    return out


# ---------------- что за инструмент ----------------
def resolve(arg):
    """→ dict(kind=share|index|fut, code, name, unit, secid?) или None."""
    a = (arg or "").strip()
    au = a.upper()
    if not re.fullmatch(r"[A-Za-z0-9]{1,16}", a):
        return None
    if au in INDEX_NAMES or au.startswith("MOEX") and len(au) <= 8:
        return dict(kind="index", code=au, name=INDEX_NAMES.get(au, au), unit="п.")
    try:
        act = C.active_shares()
    except Exception:  # noqa: BLE001
        act = None
    if act and au in act:
        return dict(kind="share", code=au, unit="₽")
    try:
        cons, assets = fut_all()
    except Exception as e:  # noqa: BLE001
        C.log("список фьючерсов:", e)
        return dict(kind="share", code=au, unit="₽") if not act else None
    by_up = {k.upper(): k for k in cons}
    if au in by_up:
        x = cons[by_up[au]]
        if x["perp"]:
            return dict(kind="fut", code=x["secid"], secid=x["secid"], perp=True, name=_fname(x), unit="")
        return dict(kind="fut", code=x["code"], secid=x["secid"], single=True, name=_fname(x) + f" ({x['secid']})", unit="")
    akeys = {k.upper(): k for k in assets}
    k = akeys.get(au)
    if k is None:   # по коду базового актива: GOLD → GD, SBRF → SR
        hit = [kk for kk, lst in assets.items() if (lst[0]["asset"] or "").upper() == au]
        k = max(hit, key=lambda kk: sum(z["oi"] or 0 for z in assets[kk])) if hit else None
    if k is None:
        return dict(kind="share", code=au, unit="₽") if not act else None
    lst = assets[k]
    f = next(z for z in lst if z["front"])
    if f["perp"]:
        return dict(kind="fut", code=f["secid"], secid=f["secid"], perp=True, name=_fname(f), unit="")
    return dict(kind="fut", code=k, secid=f["secid"], name=_fname(f) + f" — непрерывный ряд, сейчас {f['secid']}", unit="")


def _fname(x):
    nm = re.sub(r"\s*-\s*\d+\.\d+$", "", x["short"] or x["secid"])
    return f"фьючерс {nm}"


# ---------------- свечи ----------------
def _fut_bars(sid, days, interval):
    return C.candles(f"engines/futures/markets/forts/securities/{sid}/candles.json", days, interval)


def fut_continuous(code, days=760):
    """Склейка ближайших контрактов кода (все месяцы, назад ~26 мес.): на последнем общем дне цены прошлого контракта
    умножаются на отношение закрытий нового и старого (обратная корректировка); переход — за 3 торговых дня до экспирации старого."""
    today = dt.date.today()
    sids = []
    for k in range(-27, 5):
        y, m = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
        sids.append(f"{code}{MCODE[m - 1]}{y % 10}")

    def one(sid):
        try:
            B = _fut_bars(sid, days + 120, 24)
        except Exception:  # noqa: BLE001
            B = []
        return B if len(B) >= 5 else None
    with ThreadPoolExecutor(6) as ex:
        series = [B for B in ex.map(one, sids) if B]
    if not series:
        return []
    series.sort(key=lambda B: B[-1]["t"])
    out = []
    for i, B in enumerate(series):
        if not out:
            out = [dict(b) for b in B[:-3]] if i < len(series) - 1 else [dict(b) for b in B]
            continue
        same = {b["t"]: b for b in B}
        common_t = [b["t"] for b in out if b["t"] in same]
        if common_t:
            ct = common_t[-1]
            old = next(b for b in out if b["t"] == ct)
            r = same[ct]["c"] / old["c"] if old["c"] and same[ct]["c"] and same[ct]["c"] / old["c"] > 0 else 1.0
            out = [dict(b, o=b["o"] * r, h=b["h"] * r, l=b["l"] * r, c=b["c"] * r) for b in out if b["t"] <= ct]
        else:
            ct = out[-1]["t"]
        nxt = [dict(b) for b in B if b["t"] > ct]
        if i < len(series) - 1 and len(nxt) > 3:
            nxt = nxt[:-3]
        out += nxt
    frm = (today - dt.timedelta(days=days)).isoformat()
    return [b for b in out if b["t"] >= frm]


def load(inst):
    """→ (дневные, часовые) в формате idea_judge._arr; кэш 30 минут."""
    import idea_judge as J
    if inst["kind"] == "share":
        return J.load(inst["code"])
    key = ("bars", inst["kind"], inst["code"], inst.get("single"))

    def get():
        if inst["kind"] == "index":
            path = f"engines/stock/markets/index/securities/{inst['code']}/candles.json"
            with ThreadPoolExecutor(2) as ex:
                fd, fh = ex.submit(C.candles, path, 365 * 10, 24), ex.submit(C.candles, path, 150, 60)
                d, h = fd.result(), fh.result()
        else:
            sid = inst["secid"]
            cont = False
            if inst.get("perp") or inst.get("single"):
                d = _fut_bars(sid, 365 * 10, 24)
                if inst.get("single") and len(d) < 260:   # у контракта меньше года истории — разбор по непрерывному ряду кода
                    d, cont = fut_continuous(inst["code"]), True
            else:
                d, cont = fut_continuous(inst["code"]), True
            try:
                h = _fut_bars(sid, 150, 60)
            except Exception:  # noqa: BLE001
                h = []
            return J._arr(d) if d else None, (J._arr(h) if h else None), cont
        return J._arr(d) if d else None, (J._arr(h) if h else None), False
    a_d, a_h, cont = _cached(key, 1800, get)
    if cont and inst.get("single") and "непрерывн" not in inst.get("name", ""):
        inst["name"] += " — у контракта меньше года истории, разбор по непрерывному ряду ближайших контрактов"
    inst["cont"] = cont
    return a_d, a_h
