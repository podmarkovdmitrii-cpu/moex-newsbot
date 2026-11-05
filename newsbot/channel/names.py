# -*- coding: utf-8 -*-
"""Название компании к тикеру: «$SBER» → «$SBER (Сбербанк)».

Источник названий: ticker_names.json (короткие привычные названия) и, для остальных бумаг, полное название с Мосбиржи
(ISS, кэш на сутки) без «ПАО/МКПАО/ао». Название добавляется к первому упоминанию тикера в посте и не добавляется,
если рядом уже есть название компании (например «Обзор дня: Сбербанк ($SBER)»).
"""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("CHANNEL_STATE") or os.environ.get("CHANNEL_STATE_DIR") or os.path.join(HERE, "state")
_CUR = None
_ISS = None


def _curated():
    global _CUR
    if _CUR is None:
        try:
            _CUR = json.load(open(os.path.join(HERE, "ticker_names.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _CUR = {}
    return _CUR


def clean_name(nm, pref=False):
    nm = re.sub(r"[\"«»]", "", str(nm or ""))
    nm = re.sub(r"\((ПАО|ОАО|АО)\)", "", nm)
    nm = re.sub(r"(?i)\b(МКПАО|ПАО|ОАО|АО|акции|акц\.?|об\.?|обыкн\.?|ао\.?|ап\.?|пр\.?|ак\.)\b", "", nm)
    nm = re.sub(r"[-–]\s*$|\s+[-–]\s+|\s{2,}", " ", nm).strip(" -–.,")
    return (nm + ", прив.") if pref and nm else nm


def _iss_names():
    global _ISS
    if _ISS is not None:
        return _ISS
    f = os.path.join(STATE, "names_iss.json")
    try:
        c = json.load(open(f, encoding="utf-8"))
        if time.time() - c.get("ts", 0) < 86400 and len(c.get("names", {})) > 100:
            _ISS = c["names"]
            return _ISS
    except Exception:  # noqa: BLE001
        pass
    _ISS = {}
    try:
        import urllib.request
        u = ("https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json?iss.meta=off&iss.only=securities"
             "&securities.columns=SECID,SECNAME")
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
            js = json.loads(r.read().decode())
        for sid, sn in js["securities"]["data"]:
            nm = clean_name(sn, pref=bool(re.search(r"\bап\b|прив|акц\.?\s*пр", sn or "")))
            if nm:
                _ISS[sid] = nm
        if len(_ISS) > 100:
            os.makedirs(STATE, exist_ok=True)
            json.dump({"ts": time.time(), "names": _ISS}, open(f, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
    return _ISS


def name(t):
    t = (t or "").upper()
    return _curated().get(t) or _iss_names().get(t) or ""


def label(t):
    """«SBER» → «Сбербанк (SBER)» — для мест, где тикер пишется без $."""
    n = name(t)
    return f"{n} ({t})" if n else t


def _stem(s):
    w = re.sub(r"[^\wё]", " ", s.lower()).split()
    return [x[:5] for x in w if len(x) >= 3]


TICK = re.compile(r"(?<![\w/#=&\"'])\$([A-Z][A-Z0-9]{0,6})\b")


def annotate(html):
    """К первому упоминанию каждого $ТИКЕРА в посте добавить название компании (если рядом его ещё нет)."""
    if not html or "$" not in html:
        return html
    seen = set()
    out, pos = [], 0
    plain_all = html
    for m in TICK.finditer(html):
        t = m.group(1)
        if t in seen:
            continue
        seen.add(t)
        nm = name(t)
        if not nm or nm.upper() == t:
            continue
        # внутри ссылки (href) — не трогаем
        pre = html[max(0, m.start() - 200):m.start()]
        if pre.rfind("<") > pre.rfind(">"):  # внутри тега (атрибут ссылки)
            continue
        around = re.sub(r"<[^>]+>", " ", plain_all[max(0, m.start() - 70):m.end() + 70]).lower()
        if any(s in around for s in _stem(nm)[:1]):
            continue  # название уже стоит рядом
        nxt = html[m.end():m.end() + 2]
        if nxt.startswith(" (") or nxt.startswith("("):
            continue
        out.append(html[pos:m.end()])
        out.append(f" ({nm})")
        pos = m.end()
    out.append(html[pos:])
    return "".join(out)
