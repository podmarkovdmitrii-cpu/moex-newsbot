# -*- coding: utf-8 -*-
"""Отчёт после обучения модели аномалий — администратору в личку (TG_TOKEN, TG_CHAT_ID):
период обучения, качество на будущих годах, что снял ревизор связей в истории и за неделю работы бота."""
import collections
import html
import json
import os
import time
import urllib.parse
import urllib.request

MODELS = os.environ.get("ANOM_MODELS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
STATE = os.environ.get("NEWS_STATE") or ""


def load(p, d=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return d


def text():
    meta = load(os.path.join(MODELS, "meta.json"), {}) or {}
    rep = load(os.path.join(MODELS, "linkcheck_report.json"), {}) or {}
    L = ["🧠 <b>Модель аномалий переобучена</b>"]
    if meta:
        L.append(f"Данные: с {meta.get('data_from', '?')}, строк {meta.get('rows', '?')}, бумаг {meta.get('tickers', '?')}. Обучена {meta.get('trained', '')[:16]} UTC.")
        r = (meta.get("report") or {}).get("daily_y") or []
        if r:
            L.append("Проверка на будущих годах (AUC / доля аномалий в топ-5% прогнозов): " +
                     ", ".join(f"{x['year']}: {x['auc']:.2f} / {x.get('top5', 0):.0%}" for x in r if x.get("auc") is not None))
    if rep:
        d = rep.get("dropped") or {}
        n = sum(d.values())
        L += ["", f"🔎 <b>Ревизор связей «новость → бумага»</b>: из {rep.get('total', 0)} исторических событий снято {n}"
              + (" (" + ", ".join(f"{k} — {v}" for k, v in d.items()) + ")" if d else "") + "."]
        for tk, p in (rep.get("polluted") or {}).items():
            L.append(f"• ${tk} ({html.escape(p['meaning'])}): сняты типы {', '.join(p['types'])} — {p['events']} событий")
        if rep.get("review"):
            L.append("На проверку (тип события у бумаги в 4+ раза чаще, чем у сектора): " +
                     ", ".join(f"${r['tk']} {r['ty']} ×{r['x']}" for r in rep["review"][:6]))
    lp = os.path.join(STATE, "linkcheck_log.jsonl")
    if os.path.exists(lp):
        week, ex = collections.Counter(), []
        for line in open(lp, encoding="utf-8"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("ts", 0) < time.time() - 7 * 86400:
                continue
            for d in e.get("drop", []):
                week[d["tk"]] += 1
                if len(ex) < 3:
                    ex.append(html.escape(f"{d['why']} — «{e.get('tx', '')[:90]}…»"))
        if week:
            L += ["", f"За неделю работы бота снято связей: {sum(week.values())} — " + ", ".join(f"${t} {n}" for t, n in week.most_common(6))]
            L += [f"• {x}" for x in ex]
    return "\n".join(L)


def main():
    t = text()
    print(t)
    tok, chat = os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not tok or not chat:
        print("TG_TOKEN/TG_CHAT_ID не заданы — отчёт только в журнал")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": t[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=30).read()
    except Exception as e:  # noqa: BLE001
        print("отчёт не отправлен:", e)


if __name__ == "__main__":
    main()
