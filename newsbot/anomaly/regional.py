# -*- coding: utf-8 -*-
"""Региональные события: инцидент + регион в тексте новости → события по регионам → признаки для компаний с активами там."""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
R = json.load(open(os.path.join(HERE, "regions.json"), encoding="utf-8"))
INC = re.compile(R["incident"], re.I)
IND = re.compile(R["industrial"], re.I)
REG = {k: re.compile("(?<![а-яёa-z])(" + v + ")", re.I) for k, v in R["regions"].items()}  # граница слова: «утвердил» ≠ Тверь
DIG = re.compile(R["digest"], re.I)
COMP = R["companies"]


def detect(text):
    """→ (список регионов, промышленный объект 0/1) или None, если это не инцидент."""
    t = (text or "")[:600]
    if DIG.search(t[:120]) or not INC.search(t):
        return None
    regs = [k for k, rx in REG.items() if rx.search(t)]
    if not regs:
        return None
    return regs, 1 if IND.search(t) else 0


def events_from_posts(posts):
    """posts: (ts, text) → события [ts, регион, промышленный]; одно событие на регион в пределах 3 часов."""
    out, last = [], {}
    for ts, tx in sorted(posts):
        d = detect(tx)
        if not d:
            continue
        regs, ind = d
        for g in regs:
            if ts - last.get(g, -1e12) < 3 * 3600:
                continue
            last[g] = ts
            out.append([int(ts), g, ind])
    return out


def company_events(events):
    """События регионов → события компаний: тикер → список (ts, промышленный)."""
    by = {}
    reg2c = {}
    for t, regs in COMP.items():
        for g in regs:
            reg2c.setdefault(g, []).append(t)
    for ts, g, ind in events:
        for t in reg2c.get(g, []):
            by.setdefault(t, []).append((ts, ind))
    return by


_A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _decode(js):
    """Компактный формат: R — список регионов, e — «Δts(hex) буква-региона флаг» через пробел."""
    if isinstance(js, list):
        return js
    out, t = [], 0
    for tok in js["e"].split():
        t += int(tok[:-2], 16)
        out.append([t, js["R"][_A.index(tok[-2])], int(tok[-1])])
    return out


def load_events(news_state=None):
    """История региональных событий (2017–2026, из 850 тыс. постов) + журнал бота (записи @REG)."""
    import gzip
    ev = _decode(json.load(gzip.open(os.path.join(HERE, "regional_events.json.gz"), "rt")))
    last = max(e[0] for e in ev) if ev else 0
    lp = os.path.join(news_state or os.environ.get("NEWS_STATE", ""), "news_log.jsonl")
    if os.path.exists(lp):
        for line in open(lp, encoding="utf-8"):
            if '"@REG"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("ts", 0) > last:
                for g in e.get("regs") or []:
                    ev.append([int(e["ts"]), g, int(e.get("ind") or 0)])
    ev.sort()
    return ev


def recent(t, since_ts, events):
    """Региональные события по компании после since_ts: [(ts, регион, промышленный)]."""
    regs = set(COMP.get(t, []))
    return [e for e in events if e[0] >= since_ts and e[1] in regs]


RU = {"perm": "Пермский край", "nizhny": "Нижегородская обл.", "volgograd": "Волгоград", "ryazan": "Рязань", "tuapse": "Туапсе", "novoross": "Новороссийск",
      "krasnodar": "Краснодарский край", "yaroslavl": "Ярославль", "saratov": "Саратов", "samara": "Самарская обл.", "omsk": "Омск", "norilsk": "Норильск",
      "murmansk": "Мурманская обл.", "leningrad": "Ленинградская обл.", "spb": "Санкт-Петербург", "moscow": "Москва и область", "tatarstan": "Татарстан",
      "bashkiria": "Башкирия", "belgorod": "Белгородская обл.", "kursk": "Курская обл.", "bryansk": "Брянская обл.", "orel": "Орловская обл.",
      "rostov": "Ростовская обл.", "lipetsk": "Липецк", "vologda": "Вологодская обл.", "chelyabinsk": "Челябинская обл.", "sverdlovsk": "Свердловская обл.",
      "yakutia": "Якутия", "krasnoyarsk": "Красноярский край", "irkutsk": "Иркутская обл.", "yamal": "Ямал", "khmao": "ХМАО", "tyumen": "Тюменская обл.",
      "kuzbass": "Кузбасс", "primorye": "Приморье", "sakhalin": "Сахалин", "novgorod": "Новгородская обл.", "kaluga": "Калуга", "tula": "Тульская обл.",
      "voronezh": "Воронеж", "smolensk": "Смоленск", "tver": "Тверь", "udmurtia": "Удмуртия", "orenburg": "Оренбург", "astrakhan": "Астрахань",
      "dagestan": "Дагестан", "crimea": "Крым", "kaliningrad": "Калининград", "karelia": "Карелия", "tambov": "Тамбов"}
