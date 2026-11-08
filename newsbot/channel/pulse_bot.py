# -*- coding: utf-8 -*-
"""Бот-помощник для Пульса (Т-Инвестиции): готовит посты с картинками — вы копируете их в Пульс сами.

У Пульса нет открытого API для публикации, а автопостинг ботами правилами не предусмотрен, поэтому бот присылает
администратору черновик: картинку (сохранить и прикрепить) и текст (нажать — скопируется) с тикерами, хэштегами,
дисклеймером и ссылкой на Telegram-канал.

Что бот присылает сам:
  • новая закрытая идея в канале → тизер без уровней (тикер, сторона, потенциал, вероятность, срок; детали — в канале);
  • идею открыли всем (кнопка «Открыть всем») → полный пост с уровнями;
  • взят тейк → пост «тейк взят» с графиком и результатом;
  • идея закрыта → итог сделки: результат в % и R, срок, график входа/выхода;
  • 1-го числа после 12:00 → итоги сделок за прошлый месяц (таблица + картинка).
Кнопки: итоги месяца, последние сделки (итог/тизер по любой), пост о рынке (потоки денег и аномалии дня),
свой текст → пост для Пульса, где ставить ссылку (в тексте / «в профиле» / без ссылки).

Результаты сделок берутся из закрытых идей (paywall.py, файл paywall.json): доли тейков из плана, остаток — по стопу
на момент закрытия; комиссии не учитываются. Переменные: TG_PULSE_TOKEN (токен отдельного бота от @BotFather),
IDEA_ADMINS, TG_PUBLIC_CHAT (адрес канала), CHANNEL_LINK (если адрес другой), PULSE_LINK_MODE (text|profile|none).
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from common import BG, FG, UP, DN, BLUE, DISCLAIMER, fmt, pc, share_candles, candle_chart, hbars  # noqa: E402

TOKEN = os.environ.get("TG_PULSE_TOKEN", "")
ADMINS = {int(x) for x in re.findall(r"-?\d+", os.environ.get("IDEA_ADMINS", ""))}
CHAN = (os.environ.get("TG_PUBLIC_CHAT") or "").lstrip("@")
LINK = os.environ.get("CHANNEL_LINK") or (f"t.me/{CHAN}" if CHAN and not CHAN.lstrip("-").isdigit() else "")
TITLE = os.environ.get("CHANNEL_TITLE") or "На пути к успеху"
STATE_DIR = os.environ.get("CHANNEL_STATE_DIR") or os.path.join(HERE, "state")
PW_STATE = os.path.join(STATE_DIR, "paywall.json")
ST_FILE = os.path.join(STATE_DIR, "pulse_bot.json")
OUT = os.path.join(STATE_DIR, "pulse_img")
MSK = 3 * 3600
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
ST = {"offset": 0, "seen": {}, "init": False, "mode": os.environ.get("PULSE_LINK_MODE") or "text", "month_sent": "", "wiz": {}}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[пульс-бот]", *a, flush=True)


def save():
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = ST_FILE + ".tmp"
    json.dump(ST, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, ST_FILE)


def load():
    try:
        ST.update(json.load(open(ST_FILE, encoding="utf-8")))
    except Exception:  # noqa: BLE001
        pass


def ideas():
    try:
        return json.load(open(PW_STATE, encoding="utf-8")).get("ideas") or {}
    except Exception:  # noqa: BLE001
        return {}


# ---------------- Telegram ----------------
def tg(method, data=None, timeout=40):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=json.dumps(data or {}, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("result")
    except urllib.error.HTTPError as e:
        log(method, e.code, e.read().decode("utf-8", "replace")[:200])
    except Exception as e:  # noqa: BLE001
        log(method, e)
    return None


def tg_photo(chat, path, caption=""):
    bnd = "----pulse" + str(int(time.time() * 1000))
    parts = []
    for k, v in (("chat_id", str(chat)), ("caption", caption), ("parse_mode", "HTML")):
        parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"pulse.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
                 + open(path, "rb").read() + f"\r\n--{bnd}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode()).get("result")
    except Exception as e:  # noqa: BLE001
        log("sendPhoto", e)


KB = {"keyboard": [[{"text": "📊 Итоги месяца"}, {"text": "🧾 Последние сделки"}],
                   [{"text": "🗞 Пост о рынке"}, {"text": "✍️ Свой текст → пост"}],
                   [{"text": "🔗 Где ссылка на канал"}]], "resize_keyboard": True}


def say(chat, text, kb=None):
    return tg("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **({"reply_markup": kb} if kb else {})})


def deliver(title, text, imgs=()):
    """Черновик всем администраторам: картинки, затем текст поста одним блоком (нажать — скопируется)."""
    for a in ADMINS:
        say(a, f"📝 <b>Черновик для Пульса: {html.escape(title)}</b>\nСохраните картинку → в Пульсе «Новый пост» → прикрепите её → вставьте текст. "
               "Тикеры после вставки проверьте: Пульс превращает их в ссылки, если выбрать тикер из подсказки после «$».")
        for p in imgs:
            if p and os.path.exists(p):
                tg_photo(a, p)
        say(a, "<pre>" + html.escape(text) + "</pre>")
    log("черновик:", title)


# ---------------- расчёты по сделкам ----------------
def shares_of(v):
    sh = [t.get("share") for t in ((v.get("plan") or {}).get("takes") or [])]
    if len(sh) != len(v["takes"]) or any(x is None for x in sh):
        sh = [1 / len(v["takes"])] * len(v["takes"])
    return sh


def outcome(v):
    """(% к цене входа, R, тип) по тейкам из плана и стопу на момент закрытия — как в статистике канала."""
    E, side = v["entry"], v["side"]
    rf = (v.get("risk_pct") or 0) / 100 or abs(E - (v.get("stop0") or v["stop"])) / E or 0.01  # риск в долях цены входа
    sh, hit = shares_of(v), set(v.get("hit") or [])
    pct = sum(sh[i] * (v["takes"][i] - E) * side / E for i in hit if i < len(sh))
    rest = 1 - sum(sh[i] for i in hit if i < len(sh))
    if rest > 1e-6 and v.get("closed"):
        pct += rest * (v["stop"] - E) * side / E
    R = pct / rf
    kind = "тейк" if rest <= 1e-6 else ("безубыток" if abs(v["stop"] - E) < 1e-9 and not hit else "частично" if hit else "стоп")
    return pct, R, kind


def days(v):
    a, b = v.get("t") or 0, v.get("closed_t") or time.time()
    return max(1, round((b - a) / 86400)) if a else None


def side_w(v, short=False):
    return ("Л" if v["side"] > 0 else "Ш") if short else ("ЛОНГ" if v["side"] > 0 else "ШОРТ")


def footer(tags=""):
    m = ST.get("mode", "text")
    link = (f"📲 Все сделки с входами, стопами и итогами — в моём Telegram-канале «{TITLE}»: {LINK}" if m == "text" and LINK
            else "📲 Все сделки и статистика — в моём Telegram-канале (ссылка в профиле)" if m in ("text", "profile") else "")
    return "\n\n" + "\n".join(x for x in (link, tags, DISCLAIMER) if x)


# ---------------- картинки ----------------
def stamp(path, top, color, bottom=None):
    """Крупная плашка с результатом сверху и подпись канала снизу (PIL поверх графика)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        from matplotlib import font_manager
        fb = font_manager.findfont(font_manager.FontProperties(family="DejaVu Sans", weight="bold"))
        im = Image.open(path).convert("RGB")
        W, H = im.size
        band = int(H * 0.13)
        out = Image.new("RGB", (W, H + band + (int(H * 0.06) if bottom else 0)), BG)
        out.paste(im, (0, band))
        d = ImageDraw.Draw(out)
        d.rectangle([0, 0, W, band], fill=color)
        f1 = ImageFont.truetype(fb, int(band * 0.5))
        d.text((int(W * 0.04), band // 2), top, font=f1, fill="#ffffff", anchor="lm")
        if bottom:
            f2 = ImageFont.truetype(fb, int(H * 0.032))
            d.text((W // 2, H + band + int(H * 0.03)), bottom, font=f2, fill=FG, anchor="mm")
        out.save(path)
    except Exception as e:  # noqa: BLE001
        log("плашка на картинке не нарисована:", e)
    return path


def chart(v, iid, title, show_levels=True):
    try:
        bars = share_candles(v["ticker"], 260)
    except Exception as e:  # noqa: BLE001
        log("свечи", v["ticker"], e)
        return None
    lines = []
    if show_levels:
        s0 = v.get("stop0") or (v["entry"] * (1 - v["side"] * v["risk_pct"] / 100) if v.get("risk_pct") else v["stop"])
        lines = [(v["entry"], BLUE, "Вход", "--"), (s0, DN, "Стоп", "-")]
        lines += [(x, UP, f"Т{i + 1}", "-") for i, x in enumerate(v["takes"])]
    os.makedirs(OUT, exist_ok=True)
    return candle_chart(bars, os.path.join(OUT, f"{iid}_{int(time.time())}.png"), title, lines=lines, n=90)


def bottom_line():
    return f"Все сделки и статистика — Telegram: {LINK}" if ST.get("mode") == "text" and LINK else None


# ---------------- черновики ----------------
def d_teaser(iid, v):
    pl = v.get("plan") or {}
    pot = abs(v["takes"][-1] - v["entry"]) / v["entry"]
    L = [f"🔒 Новая идея: ${v['ticker']} — {side_w(v)}", "",
         f"Потенциал ~{pc(pot, 1, False)}" + (f", срок обычно {pl['term'][0]}–{pl['term'][1]} торг. дн." if pl.get("term") else "")
         + (f", вероятность цели по истории бумаги ~{pl['p']:.0%}" if pl.get("p") is not None else "") + ".",
         f"Прибыль/риск {fmt(v.get('rr'))}. Точка входа, стоп и план фиксации — у подписчиков канала, итог опубликую здесь же."]
    text = "\n".join(L) + footer("#идея #" + v["ticker"])
    p = chart(v, iid, f"{v['ticker']} · новая идея · {side_w(v)}", show_levels=False)
    if p:
        stamp(p, f"{v['ticker']} · {side_w(v)} · потенциал {pc(pot, 1, False)}", UP if v["side"] > 0 else DN, bottom_line())
    deliver(f"тизер идеи #{iid} ${v['ticker']}", text, [p])


def d_open(iid, v):
    L = [f"💡 Идея: ${v['ticker']} — {side_w(v)}", "",
         f"Вход {fmt(v['entry'])} · стоп {fmt(v['stop'])} · тейки {' / '.join(fmt(x) for x in v['takes'])}",
         f"Риск {fmt(v.get('risk_pct'))}%, прибыль/риск {fmt(v.get('rr'))}."]
    pl = v.get("plan") or {}
    if pl.get("rules"):
        L.append("План: " + "; ".join(pl["rules"][:2]))
    if v.get("comment"):
        L += ["", v["comment"]]
    p = chart(v, iid, f"{v['ticker']} · {side_w(v)} · вход {fmt(v['entry'])}")
    if p:
        stamp(p, f"{v['ticker']} · {side_w(v)} · вход {fmt(v['entry'])}", UP if v["side"] > 0 else DN, bottom_line())
    deliver(f"открытая идея #{iid} ${v['ticker']}", "\n".join(L) + footer("#идея #" + v["ticker"]), [p])


def d_take(iid, v, i):
    E = v["entry"]
    mv = (v["takes"][i] - E) * v["side"] / E
    L = [f"✅ ${v['ticker']}: тейк {i + 1} из {len(v['takes'])} взят — {pc(mv, 1)} от входа", "",
         f"{side_w(v)} от {fmt(E)}, тейк {fmt(v['takes'][i])}" + (f", в сделке {days(v)} дн" if days(v) else "") + "."]
    if i + 1 < len(v["takes"]):
        L.append("Остаток позиции держу" + (", стоп — в безубытке." if v.get("be") else "."))
    p = chart(v, iid, f"{v['ticker']} · тейк {i + 1} взят")
    if p:
        stamp(p, f"{v['ticker']} · тейк {i + 1} · {pc(mv, 1)}", UP, bottom_line())
    deliver(f"тейк {i + 1} по ${v['ticker']}", "\n".join(L) + footer("#сделка #" + v["ticker"]), [p])


def d_close(iid, v):
    pct, R, kind = outcome(v)
    good = pct >= 0
    L = [f"{'🏁' if good else '⛔️'} Итог сделки ${v['ticker']} ({side_w(v)}): {pc(pct, 1)}, {'+' if R >= 0 else '−'}{fmt(abs(R), 1)}R", "",
         f"Вход {fmt(v['entry'])}, тейки {' / '.join(('✅' if j in set(v.get('hit') or []) else '') + fmt(x) for j, x in enumerate(v['takes']))}"
         + (f", закрыта за {days(v)} дн." if days(v) else "") + f" ({kind}).",
         "Каждую сделку публикую до входа и закрываю публично — итоги по всем сделкам ниже в ленте и в канале."]
    if v.get("result"):
        L.insert(2, v["result"])
    p = chart(v, iid, f"{v['ticker']} · итог сделки")
    if p:
        stamp(p, f"{v['ticker']} · {side_w(v)} · {pc(pct, 1)} · {'+' if R >= 0 else '−'}{fmt(abs(R), 1)}R", UP if good else DN, bottom_line())
    deliver(f"итог ${v['ticker']}", "\n".join(L) + footer("#итоги #сделка #" + v["ticker"]), [p])


def d_month(key=None):
    t = time.gmtime(time.time() + MSK)
    if key is None:
        y, m = (t.tm_year, t.tm_mon - 1) if t.tm_mon > 1 else (t.tm_year - 1, 12)
        key = f"{y}-{m:02d}"
    y, m = int(key[:4]), int(key[5:])
    rows = []
    for iid, v in sorted(ideas().items(), key=lambda kv: int(kv[0])):
        if v.get("closed") and time.strftime("%Y-%m", time.gmtime(int(v.get("closed_t") or v.get("t") or 0) + MSK)) == key:
            rows.append((iid, v, *outcome(v)))
    if not rows:
        return None
    wins = sum(1 for r in rows if r[2] > 0)
    tot = sum(r[2] for r in rows)
    sR = sum(r[3] for r in rows)
    L = [f"📊 Мои сделки за {MONTHS[m - 1]} {y}: {wins} из {len(rows)} в плюс, сумма {pc(tot, 1)} ({'+' if sR >= 0 else '−'}{fmt(abs(sR), 1)}R)", ""]
    for iid, v, pct, R, kind in rows:
        L.append(f"{'✅' if pct > 0 else '➖' if abs(pct) < 1e-9 else '❌'} ${v['ticker']} {side_w(v)} {pc(pct, 1)} ({kind}{', ' + str(days(v)) + ' дн.' if days(v) else ''})")
    L.append("\nСумма — простая сумма результатов сделок в % к цене входа, без плеча и комиссий; убыточные сделки тоже в списке.")
    os.makedirs(OUT, exist_ok=True)
    p = hbars([(f"{v['ticker']} {side_w(v, True)}", pct * 100, None) for _, v, pct, _, _ in rows], os.path.join(OUT, f"month_{key}.png"),
              f"Сделки за {MONTHS[m - 1]} {y}: {wins} из {len(rows)} в плюс", xlabel="результат, % к цене входа", fmt_v=lambda x: f"{x:+.1f}%".replace(".", ","))
    if p:
        stamp(p, f"{MONTHS[m - 1].capitalize()} {y}: {pc(tot, 1)} · {wins}/{len(rows)} в плюс", UP if tot >= 0 else DN, bottom_line())
    deliver(f"итоги за {MONTHS[m - 1]} {y}", "\n".join(L) + footer("#итоги"), [p])
    return True


def d_market():
    def j(name):
        for base in (STATE_DIR, os.path.join(os.environ.get("STATE_DIR", ""), "channel")):
            try:
                return json.load(open(os.path.join(base, name), encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
        return {}
    fl, an = j("flows_last.json"), j("anomaly.json")
    L, rows = ["🧭 Куда шли деньги на Мосбирже сегодня", ""], []
    if fl.get("buyers") or fl.get("sellers"):
        L.append("Перевес покупок: " + ", ".join(f"${t} {pc(v, 0)}" for t, v in (fl.get("buyers") or [])[:4]))
        L.append("Перевес продаж: " + ", ".join(f"${t} {pc(v, 0)}" for t, v in (fl.get("sellers") or [])[:4]))
        rows = [(t, v * 100, None) for t, v in (fl.get("buyers") or [])[:5] + (fl.get("sellers") or [])[:5]]
    sent = (an.get("sent") or {}) if an.get("date") == time.strftime("%Y-%m-%d", time.gmtime(time.time() + MSK)) else {}
    if sent:
        L += ["", "⚡️ Резкие движения дня: " + ", ".join(f"${t} {pc(x[1], 1)}" for t, x in list(sent.items())[:5])]
    if len(L) <= 2:
        return None
    L.append("\nПеревес — доля оборота, где инициатором была покупка или продажа по рынку (данные Мосбиржи).")
    p = None
    if rows:
        os.makedirs(OUT, exist_ok=True)
        p = hbars(rows, os.path.join(OUT, f"market_{int(time.time())}.png"), "Перевес покупок и продаж по рынку", fmt_v=lambda x: f"{x:+.0f}%")
        if p:
            stamp(p, "Куда шли деньги сегодня", BLUE, bottom_line())
    deliver("пост о рынке", "\n".join(L) + footer("#рынок #потоки"), [p])
    return True


def d_custom(text):
    tks = re.findall(r"\$([A-Z][A-Z0-9]{1,6})\b", text)
    body = text.strip()
    p = None
    if tks:
        try:
            os.makedirs(OUT, exist_ok=True)
            p = candle_chart(share_candles(tks[0], 260), os.path.join(OUT, f"c_{tks[0]}_{int(time.time())}.png"), f"{tks[0]} · дневной график", n=90)
            if p:
                stamp(p, f"{tks[0]}", BLUE, bottom_line())
        except Exception as e:  # noqa: BLE001
            log("график", e)
    deliver("ваш текст", body + footer(" ".join("#" + t for t in dict.fromkeys(tks))), [p])


# ---------------- события из закрытых идей ----------------
def check_events():
    cur = ideas()
    seen = ST["seen"]
    first = not ST.get("init")
    for iid, v in cur.items():
        s = seen.setdefault(iid, {"pub": False, "open": False, "hit": [], "closed": False})
        if first:
            s.update(pub=True, open=bool(v.get("open")), hit=list(v.get("hit") or []), closed=bool(v.get("closed")))
            continue
        try:
            if not s["pub"]:
                s["pub"] = True
                if not v.get("closed"):
                    (d_open if v.get("open") else d_teaser)(iid, v)
                    s["open"] = bool(v.get("open"))
            if v.get("open") and not s["open"]:
                s["open"] = True
                d_open(iid, v)
            for i in sorted(set(v.get("hit") or []) - set(s["hit"])):
                if not (v.get("closed") and len(v.get("hit") or []) == len(v["takes"])):
                    d_take(iid, v, i)
            s["hit"] = list(v.get("hit") or [])
            if v.get("closed") and not s["closed"]:
                s["closed"] = True
                d_close(iid, v)
        except Exception as e:  # noqa: BLE001
            log("черновик по идее", iid, e)
    if first:
        ST["init"] = True
        log("начальное состояние: идей", len(cur), "— черновики пойдут по новым событиям")
    t = time.gmtime(time.time() + MSK)
    key = time.strftime("%Y-%m", t)
    if t.tm_mday == 1 and t.tm_hour >= 12 and ST.get("month_sent") != key:
        ST["month_sent"] = key
        try:
            d_month()
        except Exception as e:  # noqa: BLE001
            log("итоги месяца", e)
    save()


# ---------------- команды ----------------
def on_message(m):
    uid = (m.get("from") or {}).get("id")
    text = (m.get("text") or "").strip()
    if uid not in ADMINS:
        return say(m["chat"]["id"], "Это служебный бот автора канала: он готовит посты для Пульса." + (f" Канал: {LINK}" if LINK else ""))
    if ST["wiz"].get(str(uid)) == "custom" and text and not text.startswith(("/", "📊", "🧾", "🗞", "✍️", "🔗")):
        ST["wiz"].pop(str(uid), None)
        save()
        return d_custom(text)
    if text in ("/start", "/menu", "меню"):
        return say(uid, "Готовлю посты для Пульса с картинками, результатами ваших сделок и ссылкой на канал. "
                        "Черновики по идеям (тизер, тейк, итог, итоги месяца) приходят сами.", KB)
    if text.startswith("📊"):
        if not d_month():
            say(uid, "За прошлый месяц закрытых идей нет. Итоги текущего: /month " + time.strftime("%Y-%m", time.gmtime(time.time() + MSK)))
    elif text.startswith("/month"):
        k = text.split()[1] if len(text.split()) > 1 else None
        if not d_month(k):
            say(uid, "Закрытых идей за этот месяц нет.")
    elif text.startswith("🧾"):
        cur = sorted(ideas().items(), key=lambda kv: -int(kv[0]))[:10]
        if not cur:
            return say(uid, "Идей пока нет.")
        kb = {"inline_keyboard": [[{"text": f"#{iid} {v['ticker']} {side_w(v, True)}" + (" · итог" if v.get("closed") else ""), "callback_data": f"pc:{iid}"},
                                   {"text": "тизер", "callback_data": f"pt:{iid}"}] for iid, v in cur]}
        say(uid, "Последние идеи — пост с итогом (для закрытых) или тизер:", kb)
    elif text.startswith("🗞"):
        if not d_market():
            say(uid, "Данных о потоках за сегодня ещё нет (пост «Покупатели vs продавцы» выходит с 10:00).")
    elif text.startswith("✍️"):
        ST["wiz"][str(uid)] = "custom"
        save()
        say(uid, "Пришлите текст поста (тикеры — как $SBER). Добавлю график первого тикера, хэштеги, дисклеймер и ссылку на канал.")
    elif text.startswith("🔗"):
        kb = {"inline_keyboard": [[{"text": ("✅ " if ST.get("mode") == k else "") + lab, "callback_data": "pm:" + k}]
                                  for k, lab in (("text", "Ссылка в тексте поста и на картинке"), ("profile", "«Ссылка в профиле» (без ссылки в посте)"), ("none", "Без упоминания канала"))]}
        say(uid, "Где указывать канал. Ссылки в постах Пульс допускает, если пост не выглядит рекламой; самый безопасный вариант — "
                 "ссылка в описании профиля и фраза «ссылка в профиле» в посте.", kb)
    else:
        say(uid, "Выберите действие в меню.", KB)


def on_callback(q):
    uid = (q.get("from") or {}).get("id")
    tg("answerCallbackQuery", {"callback_query_id": q["id"]})
    if uid not in ADMINS:
        return
    d = q.get("data") or ""
    if d.startswith("pm:"):
        ST["mode"] = d[3:]
        save()
        return say(uid, "Готово: " + {"text": "ссылка в тексте и на картинке", "profile": "«ссылка в профиле»", "none": "без упоминания канала"}[ST["mode"]] + ".")
    iid = d[3:]
    v = ideas().get(iid)
    if not v:
        return say(uid, "Идея не найдена.")
    if d.startswith("pc:"):
        (d_close if v.get("closed") else d_open)(iid, v)
    elif d.startswith("pt:"):
        d_teaser(iid, v)


def main():
    if not TOKEN or not ADMINS:
        log("TG_PULSE_TOKEN или IDEA_ADMINS не заданы — бот для Пульса не запущен")
        return
    load()
    me = tg("getMe") or {}
    log("запущен:", "@" + str(me.get("username")), "| админов", len(ADMINS), "| ссылка:", LINK or "нет", "| режим:", ST.get("mode"))
    tg("setMyCommands", {"commands": [{"command": "start", "description": "Меню"}, {"command": "month", "description": "Итоги месяца: /month ГГГГ-ММ"}]})
    run_min = float(os.environ.get("PULSE_RUN_MIN", "0") or 0)
    stop_at = time.time() + run_min * 60 if run_min else 0
    last = 0
    while True:
        if time.time() - last > 60:
            last = time.time()
            check_events()
        ups = tg("getUpdates", {"offset": ST["offset"], "timeout": 25, "allowed_updates": ["message", "callback_query"]}, timeout=40) or []
        for u in ups:
            ST["offset"] = u["update_id"] + 1
            try:
                if u.get("message"):
                    on_message(u["message"])
                elif u.get("callback_query"):
                    on_callback(u["callback_query"])
            except Exception as e:  # noqa: BLE001
                log("обработка", e)
        if ups:
            save()
        if stop_at and time.time() >= stop_at:
            break


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, lambda *a: (save(), sys.exit(0)))
    main()
