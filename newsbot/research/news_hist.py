# -*- coding: utf-8 -*-
"""История новостей 2008–2026 для исследований: архив раздела «Экономика» Интерфакса (interfax.ru/business/news/ГГГГ/ММ/ДД).

Зачем: журнал новостей бота (Telegram-каналы) есть только с 2018 г., поэтому условие «движение без новостей о компании»
до 2018 г. нельзя было проверить. Архив Интерфакса — публичный, по дням с февраля 2008 г., заголовки с временем публикации.

Что берём: только заголовок, время и номер новости (для разметки «была ли новость о компании и какая»). Тексты статей не
скачиваем и нигде не публикуем. robots.txt Интерфакса не запрещает разделы /business/news; запросы — не чаще ~2 в секунду.
ВАЖНО: с серверов GitHub Actions архив отвечает 404 (доступ из-за рубежа закрыт), поэтому готовые события лежат в репозитории —
research/data/ifx_events.json.gz (собраны в браузере с российского IP той же разметкой nlp.js). Этот скрипт — для запуска с
российского IP. Кэш по месяцам: RESEARCH_HIST/ifx/ГГГГ-ММ.json.gz (прошлые месяцы скачиваются один раз; текущий — заново).
Дальше research/ifx_events.js размечает заголовки словарём бота (nlp.js): тикеры-субъекты, тип события → ifx_events.json.gz.
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import html
import json
import os
import re
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("RESEARCH_DIR") or os.path.join(HERE, "out")
HIST = os.environ.get("RESEARCH_HIST") or os.path.join(os.path.dirname(OUT), "research-hist")
DIR = os.path.join(HIST, "ifx")
FIRST = dt.date.fromisoformat(os.environ.get("IFX_FROM", "2008-02-01"))
UA = "Mozilla/5.0 (research bot for a public Telegram channel; headlines only; contact via GitHub podmarkovdmitrii-cpu/moex-newsbot)"
ITEM = re.compile(r'<div data-id="(\d+)">\s*<span>(\d\d:\d\d)</span>\s*<a href="([^"]+)"[^>]*>\s*<h3>(.*?)</h3>', re.S)


def log(*a):
    print(time.strftime("%H:%M:%S"), "[новости 2008+]", *a, flush=True)


def day_page(d, tries=3):
    url = f"https://www.interfax.ru/business/news/{d:%Y/%m/%d}"
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
            with urllib.request.urlopen(req, timeout=30) as r:
                cs = r.headers.get_content_charset() or "utf-8"
                txt = r.read().decode(cs, "replace")
            return [[t, int(i), html.unescape(re.sub(r"<[^>]+>", "", h)).strip(), a] for i, t, a, h in ITEM.findall(txt)]
        except Exception as e:  # noqa: BLE001
            if k == tries - 1:
                log("не скачано", d, e)
                return None
            time.sleep(3 + 5 * k)


def month_days(y, m):
    d = dt.date(y, m, 1)
    while d.month == m:
        yield d
        d += dt.timedelta(days=1)


def main():
    os.makedirs(DIR, exist_ok=True)
    today = dt.date.today()
    months = []
    y, m = FIRST.year, FIRST.month
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    todo = []
    for y, m in months:
        p = os.path.join(DIR, f"{y}-{m:02d}.json.gz")
        if os.path.exists(p) and (y, m) != (today.year, today.month):
            try:
                with gzip.open(p, "rt") as f:
                    if json.load(f).get("complete"):
                        continue
            except Exception:  # noqa: BLE001
                pass
        todo.append((y, m))
    log("месяцев в архиве", len(months), "| докачать", len(todo))
    t0, n_req = time.time(), 0

    def one(d):
        time.sleep(0.6)
        return d, day_page(d)
    for y, m in todo:
        days = [d for d in month_days(y, m) if FIRST <= d <= today]
        got, ok = {}, True
        with cf.ThreadPoolExecutor(2) as ex:
            for d, items in ex.map(one, days):
                n_req += 1
                if items is None:
                    ok = False
                    continue
                got[d.isoformat()] = items
        complete = ok and (y, m) != (today.year, today.month)
        with gzip.open(os.path.join(DIR, f"{y}-{m:02d}.json.gz"), "wt") as f:
            json.dump({"complete": complete, "days": got}, f, ensure_ascii=False, separators=(",", ":"))
        log(f"{y}-{m:02d}: заголовков {sum(len(v) for v in got.values())}" + ("" if complete else " (месяц неполный — докачается в следующий раз)"))
    # сводка покрытия по годам
    cover = {}
    for y, m in months:
        p = os.path.join(DIR, f"{y}-{m:02d}.json.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                js = json.load(f)
            cover[f"{y}-{m:02d}"] = sum(len(v) for v in js["days"].values())
    json.dump(cover, open(os.path.join(HIST, "ifx_meta.json"), "w"))
    by_y = {}
    for k, v in cover.items():
        by_y[k[:4]] = by_y.get(k[:4], 0) + v
    log("заголовков по годам:", ", ".join(f"{k}: {v}" for k, v in sorted(by_y.items())), f"| запросов {n_req} за {(time.time() - t0) / 60:.1f} мин")


if __name__ == "__main__":
    main()
