# -*- coding: utf-8 -*-
"""Ревизор связей «новость → бумага» для истории новостей (перед обучением модели аномалий).

Ошибочная связь — когда название компании совпало с обычным словом: «самолёт» (SMLT) в новостях о крушениях,
сбитых БПЛА и авиасанкциях, «Иркутск» (IRKT), КамАЗ в сводках ДТП и боевых действий, «магнитная буря» (MGNT)…
Такие «новости о компании» учат модель неправильному: будто у бумаги каждый день новости, а цена не реагирует.

Проверки (правила — newsbot/link_rules.json, те же, что у ревизора в работе бота linkcheck.js):
  1. Текст (если сохранён — журнал бота с ноября 2026 пишет первые 300 знаков): признак обычного значения слова (veto)
     без явного признака компании (strong) → связь снимается.
  2. Новость раньше начала торгов бумагой (за 3+ дня до первой свечи) → снимается: бумаги ещё не было.
  3. «Загрязнённый» профиль (история без текста): у бумаги с неоднозначным названием доля макро-событий
     (война, санкции, суды, налоги, нефть…) и событий «без типа» в 2+ раза выше медианы её сектора → события
     этих типов снимаются, остаются только корпоративные (отчётность, дивиденды, сделки, выкуп, руководство…).
  4. На проверку (без снятия): у любой бумаги пара «бумага — тип события» встречается в 4+ раза чаще,
     чем у сектора (30+ событий) — список в отчёте, чтобы дополнить правила.
Тикеры приводятся к актуальным (YNDX → YDEX, TCSG → T, FIVE → X5, MAIL → VKCO…).
Запуск отдельно: python newsbot/anomaly/linkcheck.py — отчёт по истории в репозитории.
"""
import collections
import datetime as dt
import gzip
import json
import os
import re
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = json.load(open(os.path.join(HERE, "..", "link_rules.json"), encoding="utf-8"))
RENAME = RULES.get("rename", {})
GENERIC = re.compile(RULES["generic_strong"])
AMB = {tk: dict(veto=re.compile(r["veto"]), strong=re.compile(r["strong"]), meaning=r["meaning"], polluted=set(r.get("polluted_types") or []))
       for tk, r in RULES["ambiguous"].items()}
COMPANY = set(RULES["company_types"])
MACRO = set(RULES["macro_types"])
MSK = dt.timezone(dt.timedelta(hours=3))
LISTED_AFTER = dt.date(2014, 1, 1)


def low(t):
    return " " + re.sub(r"\s+", " ", str(t or "").lower().replace("ё", "е")) + " "


def canon(tk):
    return RENAME.get(tk, tk)


def check_text(tk, text):
    """(True, '') — связь в порядке; (False, причина) — обычное слово, а не компания."""
    r = AMB.get(tk)
    if not r or not text:
        return True, ""
    l = low(text)
    m = r["veto"].search(l)
    if not m:
        return True, ""
    if r["strong"].search(l) or GENERIC.search(l) or re.search(r"\$" + tk + r"\b", str(text)):
        return True, ""
    return False, f"«{m.group(0).strip()}» — {r['meaning']}"


def _day(ts):
    return dt.datetime.fromtimestamp(ts, MSK).date()


def audit(ev, first_day=None, sec_of=None, min_events=100):
    """ev — список словарей {tk, ts, ty[, tx]}. Возвращает (очищенный список, отчёт)."""
    sec_of = sec_of or RULES.get("sec_of", {})
    rep = dict(total=len(ev), dropped=collections.Counter(), by_ticker=collections.defaultdict(collections.Counter), polluted={}, review=[], examples=[])
    out = []
    for e in ev:
        e = dict(e)
        e["tk"] = canon(e.get("tk"))
        out.append(e)
    # 1. текст
    keep = []
    for e in out:
        ok, why = check_text(e["tk"], e.get("tx"))
        if not ok:
            rep["dropped"]["текст: обычное слово"] += 1
            rep["by_ticker"][e["tk"]]["текст"] += 1
            if len(rep["examples"]) < 15:
                rep["examples"].append(f"${e['tk']}: {why} — «{str(e.get('tx'))[:120]}»")
            continue
        keep.append(e)
    # 2. раньше начала торгов
    if first_day:
        k2 = []
        for e in keep:
            fd = first_day.get(e["tk"])
            # только для бумаг, появившихся на бирже после 2014 г. (раньше история свечей неполная — ранняя первая свеча ничего не доказывает)
            if fd and fd >= LISTED_AFTER and e["tk"] != "IMOEX" and _day(e["ts"]) < fd - dt.timedelta(days=3):
                rep["dropped"]["до начала торгов"] += 1
                rep["by_ticker"][e["tk"]]["до торгов"] += 1
                continue
            k2.append(e)
        keep = k2
    # 3. профиль типов событий (история без текста)
    cnt = collections.defaultdict(collections.Counter)
    for e in keep:
        if e["tk"] not in ("IMOEX", "@REG", "@INS", "MOEX") and not e.get("tx"):
            cnt[e["tk"]][e.get("ty") or "tone"] += 1
    noncomp = {tk: 1 - sum(c[t] for t in COMPANY) / sum(c.values()) for tk, c in cnt.items() if sum(c.values()) >= min_events}
    by_sec = collections.defaultdict(list)
    for tk, v in noncomp.items():
        by_sec[sec_of.get(tk, "other")].append(v)
    all_med = st.median(noncomp.values()) if noncomp else 0.5
    drop_pairs = set()
    for tk in AMB:
        if tk not in noncomp:
            continue
        peers = [v for t2, v in noncomp.items() if sec_of.get(t2, "other") == sec_of.get(tk, "other") and t2 not in AMB]
        med = st.median(peers) if len(peers) >= 3 else all_med
        # доля «не корпоративных» событий: сколько из них лишних против сектора
        excess = (noncomp[tk] - med) / max(1 - med, 0.05)
        ratio = noncomp[tk] / max(med, 0.05)
        if ratio >= 1.25 and excess >= 0.3 or ratio >= 2:
            types = AMB[tk]["polluted"]
            n = sum(cnt[tk][t] for t in types)
            rep["polluted"][tk] = dict(share=round(noncomp[tk], 3), sector=round(med, 3), types=sorted(types), events=n, meaning=AMB[tk]["meaning"])
            drop_pairs |= {(tk, t) for t in types}
    # отдельные пары «неоднозначная бумага — тип»: тип из списка обычного значения слова в 3+ раза чаще, чем у сектора
    tshare = {tk: {ty: n / sum(c.values()) for ty, n in c.items()} for tk, c in cnt.items() if sum(c.values()) >= min_events}
    for tk in AMB:
        if tk not in tshare or tk in rep["polluted"]:
            continue
        for ty in AMB[tk]["polluted"] - {"tone"}:
            sv = [sh.get(ty, 0) for t2, sh in tshare.items() if sec_of.get(t2, "other") == sec_of.get(tk, "other") and t2 not in AMB]
            m = max(st.median(sv) if len(sv) >= 3 else st.median([sh.get(ty, 0) for sh in tshare.values()]), 0.005)
            if cnt[tk][ty] >= 20 and tshare[tk].get(ty, 0) / m >= 3:
                drop_pairs.add((tk, ty))
                rep["polluted"].setdefault(tk, dict(share=None, sector=None, types=[], events=0, meaning=AMB[tk]["meaning"]))
                rep["polluted"][tk]["types"].append(ty)
                rep["polluted"][tk]["events"] += cnt[tk][ty]
    k3 = []
    for e in keep:
        if (e["tk"], e.get("ty") or "tone") in drop_pairs and not e.get("tx"):
            rep["dropped"]["загрязнённый профиль"] += 1
            rep["by_ticker"][e["tk"]][e.get("ty") or "tone"] += 1
            continue
        k3.append(e)
    # 4. на проверку
    share = {tk: {ty: n / sum(c.values()) for ty, n in c.items()} for tk, c in cnt.items() if sum(c.values()) >= min_events}
    for ty in MACRO | {"tone"}:
        vals = collections.defaultdict(list)
        for tk, sh in share.items():
            vals[sec_of.get(tk, "other")].append(sh.get(ty, 0))
        allv = st.median([sh.get(ty, 0) for sh in share.values()]) if share else 0
        for tk, sh in share.items():
            sv = vals[sec_of.get(tk, "other")]
            m = max(st.median(sv) if len(sv) >= 3 else allv, allv, 0.005)
            if cnt[tk][ty] >= 30 and sh.get(ty, 0) / m >= 4 and (tk, ty) not in drop_pairs:
                rep["review"].append(dict(tk=tk, ty=ty, n=cnt[tk][ty], x=round(sh[ty] / m, 1)))
    rep["review"].sort(key=lambda r: -r["x"])
    rep["kept"] = len(k3)
    rep["dropped"] = dict(rep["dropped"])
    rep["by_ticker"] = {tk: dict(c) for tk, c in sorted(rep["by_ticker"].items(), key=lambda kv: -sum(kv[1].values()))}
    rep["drop_pairs"] = sorted(f"{a}|{b}" for a, b in drop_pairs)
    return k3, rep


def summary(rep, top=8):
    d = rep["dropped"]
    n = sum(d.values())
    L = [f"Ревизор связей: из {rep['total']} событий снято {n} ({n / max(rep['total'], 1):.1%}): " + ", ".join(f"{k} — {v}" for k, v in d.items())]
    for tk, p in rep["polluted"].items():
        why = f"не корпоративных событий {p['share']:.0%} против {p['sector']:.0%} у сектора" if p.get("share") is not None else "тип события в 3+ раза чаще, чем у сектора"
        L.append(f"  ${tk} ({p['meaning']}): {why} → сняты типы {', '.join(p['types'])} ({p['events']})")
    if rep["by_ticker"]:
        L.append("  больше всего снято: " + ", ".join(f"${tk} {sum(c.values())}" for tk, c in list(rep["by_ticker"].items())[:top]))
    if rep["review"]:
        L.append("  на проверку (тип события в 4+ раза чаще, чем у сектора): " + ", ".join(f"${r['tk']} {r['ty']} ×{r['x']} ({r['n']})" for r in rep["review"][:top]))
    for x in rep["examples"][:5]:
        L.append("  пример: " + x)
    return "\n".join(L)


if __name__ == "__main__":
    ev = []
    for path, key in ((os.path.join(HERE, "news_events.json.gz"), None), (os.path.join(HERE, "..", "research", "data", "ifx_events.json.gz"), "events")):
        js = json.load(gzip.open(path, "rt"))
        for tk, ts, ty, s, rum, off in (js[key] if key else js):
            ev.append(dict(tk=tk, ts=ts, ty=ty, s=s, rum=rum, off=off))
    sec = RULES.get("sec_of", {})
    fd = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        fd = {k: dt.date.fromisoformat(v) for k, v in json.load(open(sys.argv[1])).items()}
    clean, rep = audit(ev, fd, sec)
    print(summary(rep, 12))
