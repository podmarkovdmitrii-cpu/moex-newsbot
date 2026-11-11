# -*- coding: utf-8 -*-
"""Закрытые идеи в паблике: пост с кнопками, просмотр по подписке, оплата через Telegram Payments (ЮKassa / Robokassa / Stars).

Как работает
  1. Админ пишет боту паблика в личку:  /idea SBER шорт 300 310 280  [комментарий]
     (тикер, сторона, вход, стоп, тейк; тейков можно несколько: 280/270). Бот проверяет уровни и тикер,
     присылает превью с кнопками «Опубликовать» / «Отмена».
  2. В канал уходит закрытый пост «#Идея ШОРТ — риск» с кнопками:
       «Смотреть идею»          — подписчику всплывает окно с тикером, входом, стопом и тейком; остальным — «оплатите доступ»;
       «Купить в Т-Инвестиции»   — подписчику бот присылает ссылку на бумагу в Т-Инвестициях; остальным — «оплатите доступ»;
       «Оплатить доступ»         — открывает бота, тот выставляет счёт (ЮKassa/Robokassa в рублях или Telegram Stars).
  3. После оплаты доступ продлевается на PAY_DAYS дней; за 3 дня до конца бот напоминает о продлении.
  4. Перед публикацией бот строго разбирает идею (idea_judge.py): вероятность цели по истории бумаги, план частичных
     тейков с переносом стопа в безубыток, уровни подтверждения. /check ТИКЕР … — только разбор, без публикации.

Работает внутри непрерывной цепочки news-bot.yml (фоновым процессом рядом с bot.js), опрашивает getUpdates.
Состояние: CHANNEL_STATE_DIR/paywall.json (кэш GitHub) + резервная копия подписчиков документом, закреплённым в личке
первого администратора (если кэш пропадёт — восстановится оттуда).

Переменные (Settings → Secrets and variables → Actions):
  секреты:     TG_PUBLIC_TOKEN (бот паблика), PAY_PROVIDER_TOKEN (из @BotFather → Payments; для Stars не нужен)
  переменные:  TG_PUBLIC_CHAT (канал), IDEA_ADMINS (ваш Telegram id, можно через запятую),
               PAY_PRICE (500), PAY_DAYS (30), PAY_CURRENCY (RUB или XTR — Stars), PAY_RECEIPT (1 — чек 54-ФЗ для ЮKassa),
               PAY_VAT_CODE (1 — без НДС), IDEA_SITE_URL (необязательно — кнопка «Смотреть идею на сайте»)
"""
import html
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("TG_PUBLIC_TOKEN") or os.environ.get("TG_TOKEN", "")
CHAT = os.environ.get("TG_PUBLIC_CHAT", "")
ADMINS = {int(x) for x in re.findall(r"-?\d+", os.environ.get("IDEA_ADMINS", ""))}
PROVIDER = os.environ.get("PAY_PROVIDER_TOKEN", "")
CURRENCY = (os.environ.get("PAY_CURRENCY") or "RUB").upper()
PRICE = int(float(os.environ.get("PAY_PRICE") or 500))
DAYS = int(os.environ.get("PAY_DAYS") or 30)
RECEIPT = (os.environ.get("PAY_RECEIPT") or "1") != "0"
VAT = int(os.environ.get("PAY_VAT_CODE") or 1)
SITE = os.environ.get("IDEA_SITE_URL", "")
STATE_DIR = os.environ.get("CHANNEL_STATE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
STATE = os.path.join(STATE_DIR, "paywall.json")
RUN_MIN = float(os.environ.get("PAYWALL_MIN") or (float(os.environ.get("NEWS_LOOP_MIN") or 110) + 8))
DISCLAIMER = "Не является индивидуальной инвестиционной рекомендацией."
DAY = 86400
LOCK = threading.RLock()  # разбор идеи идёт в отдельном потоке
# Разбор идеи (idea_judge → jobs/common) берёт прогноз модели аномалий из состояния новостного бота. bot.js передаёт
# своим задачам NEWS_STATE/CHANNEL_STATE, а этот процесс запускается отдельно — без них разбор не находил готовый
# прогноз и пересчитывал его по 250 бумагам (минуты на каждую идею).
if os.environ.get("STATE_DIR"):
    os.environ.setdefault("NEWS_STATE", os.environ["STATE_DIR"])
    os.environ.setdefault("CHANNEL_STATE", os.path.join(os.environ["STATE_DIR"], "channel"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu as MENU  # noqa: E402  (кнопки меню и пошаговые сценарии)

LONG = {"лонг", "long", "покупка", "купить", "buy", "l", "л", "вверх"}
SHORT = {"шорт", "short", "продажа", "продать", "sell", "s", "ш", "вниз"}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[закрытые идеи]", *a, flush=True)


# ---------------- Telegram ----------------
class TgError(RuntimeError):
    pass


def tg(method, data=None, timeout=40):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    body = json.dumps(data or {}, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                res = json.loads(e.read().decode() or "{}")
            except Exception:  # noqa: BLE001
                res = {"description": f"HTTP {e.code}"}
            res.setdefault("error_code", e.code)
        if res.get("ok"):
            return res["result"]
        ra = (res.get("parameters") or {}).get("retry_after")
        if ra:
            time.sleep(min(ra, 30) + 1)
            continue
        raise TgError(f"{method}: {res.get('error_code')} {res.get('description')}")
    raise TgError(f"{method}: слишком много повторов")


def tg_upload_doc(chat_id, name, content, caption=""):
    bnd = "----pw" + str(int(time.time() * 1000))
    parts = []
    for k, v in (("chat_id", str(chat_id)), ("caption", caption), ("disable_notification", "true")):
        parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{name}\"\r\nContent-Type: application/json\r\n\r\n".encode())
    parts.append(content + b"\r\n" + f"--{bnd}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendDocument", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode())
    if not res.get("ok"):
        raise TgError("sendDocument: " + str(res.get("description")))
    return res["result"]


# ---------------- состояние ----------------
S = {"subs": {}, "ideas": {}, "drafts": {}, "offset": 0, "payments": [], "seq": 0, "me": None, "wiz": {}, "refs": {}, "bonus": {}, "refpaid": {}}
DIRTY_SUBS = False


def save():
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(S, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def backup():
    """Копия подписчиков и идей — документом в личку первого администратора, закрепляется (на случай потери кэша)."""
    global DIRTY_SUBS
    DIRTY_SUBS = False
    if not ADMINS:
        return
    admin = sorted(ADMINS)[0]
    data = {k: S[k] for k in ("subs", "ideas", "payments", "seq", "refs", "bonus", "refpaid") if k in S}
    try:
        m = tg_upload_doc(admin, "paywall_backup.json", json.dumps(data, ensure_ascii=False).encode(),
                          f"Резервная копия подписчиков ({len(active_subs())} активных). Не удаляйте закреплённое сообщение.")
        tg("pinChatMessage", {"chat_id": admin, "message_id": m["message_id"], "disable_notification": True})
    except Exception as e:  # noqa: BLE001
        log("резервная копия не отправлена:", e)


def restore():
    if os.path.exists(STATE):
        try:
            S.update(json.load(open(STATE, encoding="utf-8")))
            log("состояние из кэша:", len(S["subs"]), "подписчиков,", len(S["ideas"]), "идей")
            return
        except Exception as e:  # noqa: BLE001
            log("paywall.json повреждён:", e)
    if not ADMINS:
        return
    try:
        ch = tg("getChat", {"chat_id": sorted(ADMINS)[0]})
        doc = ((ch.get("pinned_message") or {}).get("document") or {})
        if doc.get("file_name") == "paywall_backup.json":
            f = tg("getFile", {"file_id": doc["file_id"]})
            with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{f['file_path']}", timeout=60) as r:
                S.update(json.loads(r.read().decode()))
            log("состояние восстановлено из резервной копии в личке администратора:", len(S["subs"]), "подписчиков")
    except Exception as e:  # noqa: BLE001
        log("резервную копию прочитать не удалось:", e)


def now():
    return int(time.time())


def sub_until(uid):
    return int((S["subs"].get(str(uid)) or {}).get("until", 0))


def has_access(uid):
    return uid in ADMINS or sub_until(uid) > now()


def active_subs():
    t = now()
    return [u for u, v in S["subs"].items() if int(v.get("until", 0)) > t]


def extend(uid, days, who=None, until=None):
    global DIRTY_SUBS
    rec = S["subs"].setdefault(str(uid), {})
    base = max(now(), int(rec.get("until", 0)))
    rec["until"] = int(until) if until else base + days * DAY
    rec["warned"] = False
    if who:
        rec["name"] = who
    DIRTY_SUBS = True
    return rec["until"]


def dstr(ts):
    return time.strftime("%d.%m.%Y", time.gmtime(int(ts) + 3 * 3600))  # по Москве


# ---------------- разбор идеи ----------------
NUM = r"\d+(?:[.,]\d+)?"


def num(x):
    return float(x.replace(",", "."))


def fmt(p):
    s = f"{p:.6f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def moex_quote(ticker):
    """Последняя цена и название с Мосбиржи; None — если ISS недоступен."""
    url = (f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{urllib.parse.quote(ticker)}.json"
           "?iss.meta=off&iss.only=securities,marketdata&securities.columns=SECID,SHORTNAME,PREVPRICE&marketdata.columns=LAST")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "moex-newsbot"}), timeout=15) as r:
            j = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None
    sec = j.get("securities", {}).get("data") or []
    if not sec:
        return {"found": False}
    md = j.get("marketdata", {}).get("data") or [[None]]
    last = md[0][0] or sec[0][2]
    return {"found": True, "name": sec[0][1], "last": last}


def parse_idea(text):
    """«/idea SBER шорт 300 310 280/270 комментарий» → dict или строка с ошибкой."""
    body = re.sub(r"^/(idea|check|разбор)(@\w+)?", "", text.strip(), flags=re.I).strip()
    m = re.match(rf"^([A-Za-zА-Яа-я0-9._-]+)\s+(\S+)\s+({NUM})\s+({NUM})\s+({NUM}(?:\s*[/;]\s*{NUM})*)\s*(.*)$", body, re.S)
    if not m:
        return ("Формат: /idea ТИКЕР лонг|шорт ВХОД СТОП ТЕЙК [комментарий]\n"
                "Пример: /idea SBER шорт 300 310 280\nНесколько тейков — через «/»: /idea GAZP лонг 120 115 128/135")
    ticker, sd, e, s, tk, comment = m.groups()
    sd = sd.lower()
    side = 1 if sd in LONG else (-1 if sd in SHORT else 0)
    if not side:
        return f"Не понял сторону «{sd}» — напишите лонг или шорт."
    entry, stop = num(e), num(s)
    takes = [num(x) for x in re.split(r"\s*[/;]\s*", tk)]
    if side > 0 and not (stop < entry < min(takes)):
        return "Для лонга должно быть: стоп < вход < тейк."
    if side < 0 and not (stop > entry > max(takes)):
        return "Для шорта должно быть: стоп > вход > тейк."
    risk = abs(entry - stop) / entry
    if risk > 0.25:
        return f"Стоп дальше 25% от входа ({risk:.0%}) — похоже на опечатку."
    return {"ticker": ticker.upper(), "side": side, "entry": entry, "stop": stop, "stop0": stop, "takes": takes, "comment": comment.strip()[:300],
            "risk_pct": round(risk * 100, 2), "rr": round(abs(takes[0] - entry) / abs(entry - stop), 2)}


def risk_label(risk_pct):
    if risk_pct > 5:
        return "Повышенный риск ❗"
    if risk_pct > 2:
        return "Средний риск"
    return "Умеренный риск"


def channel_text(idea):
    side = "ЛОНГ 🟢" if idea["side"] > 0 else "ШОРТ 🔴"
    t = f"🔒 <b>#Идея {side}</b> 🇷🇺\n\n{risk_label(idea['risk_pct'])}"
    pl = idea.get("plan") or {}
    pot = abs(idea["takes"][-1] - idea["entry"]) / idea["entry"]
    facts = []
    if pl.get("term"):
        facts.append(f"срок {pl['term'][0]}–{pl['term'][1]} дн.")
    facts.append(f"потенциал ~{pot * 100:.1f}%".replace(".", ","))
    facts.append(f"прибыль/риск {fmt(idea.get('rr'))}")
    if pl.get("p") is not None:
        facts.append(f"вероятность цели {pl['p']:.0%}")
    t += "\n" + " · ".join(facts)
    if idea.get("open"):
        t += f"\n\n🔓 <b>Открытая идея:</b> {html.escape(tlabel(idea['ticker']))} · вход {fmt(idea['entry'])} · стоп {fmt(idea['stop'])} · тейки {' / '.join(fmt(x) for x in idea['takes'])}"
    marks = []  # статус без цен — уровни видят только подписчики
    if idea.get("hit"):
        marks.append(f"✅ Взято тейков: {len(idea['hit'])} из {len(idea['takes'])}")
    if idea.get("be") and not idea.get("closed"):
        marks.append("🛡 Стоп в безубытке")
    if marks:
        t += "\n" + "\n".join(marks)
    if idea.get("result"):
        t += f"\n\n{html.escape(idea['result'])}"
    return t + f"\n\n<i>{DISCLAIMER}</i>"


def tlabel(t):
    try:
        import names
        return names.label(t)
    except Exception:  # noqa: BLE001
        return t


def alert_text(idea):
    side = "ЛОНГ" if idea["side"] > 0 else "ШОРТ"
    tk = " / ".join(fmt(x) for x in idea["takes"])
    t = f"{tlabel(idea['ticker'])} — {side}\nВход: {fmt(idea['entry'])}\nСтоп: {fmt(idea['stop'])}\nТейк: {tk}\nРиск {fmt(idea['risk_pct'])}%, прибыль/риск {fmt(idea['rr'])}"
    pl = idea.get("plan") or {}
    if pl.get("p") is not None:
        t += f"\nВероятность цели ~{pl['p']:.0%}"
    if idea.get("result"):
        t += "\n" + idea["result"]
    if idea.get("comment") and len(t) + len(idea["comment"]) < 199:
        t += "\n" + idea["comment"]
    return t[:200]


def plan_text(idea):
    """Полный план для подписчика (в личку): тейки с долями, перенос стопа, уровень подтверждения, вероятность."""
    side = "ЛОНГ" if idea["side"] > 0 else "ШОРТ"
    L = [f"<b>{html.escape(tlabel(idea['ticker']))} — {side}</b>", f"Вход {fmt(idea['entry'])} · стоп {fmt(idea['stop'])}"]
    pl = idea.get("plan") or {}
    tk = pl.get("takes") or [{"px": x, "share": 1 / len(idea["takes"])} for x in idea["takes"]]
    for i, t in enumerate(tk, 1):
        pr = f" — вероятность дойти ~{t['p']:.0%}" if t.get("p") is not None else ""
        L.append(f"Т{i} {fmt(t['px'])}: закрыть {t['share']:.0%}{pr}")
    for r in pl.get("rules") or []:
        L.append("• " + r)
    if pl.get("confirm"):
        c = pl["confirm"]
        word = "ниже" if idea["side"] < 0 else "выше"
        L.append(f"Подтверждение: закрепление {word} {fmt(c['px'])} — вероятность цели растёт до ~{c['p']:.0%}.")
    if pl.get("p") is not None:
        L.append(f"Вероятность дойти до цели раньше стопа (по истории бумаги): ~{pl['p']:.0%}.")
    if idea.get("comment"):
        L.append(html.escape(idea["comment"]))
    if idea.get("result"):
        L.append(html.escape(idea["result"]))
    L.append(f"\nВ Т-Инвестициях: {tinvest_url(idea['ticker'])}")
    L.append(f"<i>{DISCLAIMER}</i>")
    return "\n".join(L)


def bot_username():
    if not S.get("me"):
        S["me"] = tg("getMe").get("username")
    return S["me"]


def keyboard(iid):
    rows = [[{"text": "Смотреть идею", "callback_data": f"see:{iid}"}]]
    if SITE:
        rows.append([{"text": "Смотреть идею на сайте", "url": SITE}])
    rows.append([{"text": "План сделки: тейки и уровни", "callback_data": f"plan:{iid}"}])
    rows.append([{"text": "Купить в Т-Инвестиции", "callback_data": f"buy:{iid}"}])
    rows.append([{"text": "Оплатить доступ", "url": f"https://t.me/{bot_username()}?start=pay"}])
    return {"inline_keyboard": rows}


def tinvest_url(ticker):
    return f"https://www.tbank.ru/invest/stocks/{urllib.parse.quote(ticker)}/"


# ---------------- оплата ----------------
OZON_URL = os.environ.get("OZON_TIPS_URL", "").strip()
PAY_METHOD = (os.environ.get("PAY_METHOD") or "").strip().lower()


def use_ozon():
    """Оплата переводом через «Чаевые» Ozon Банка с ручным подтверждением администратором (у Ozon нет API уведомлений)."""
    return bool(OZON_URL) and (PAY_METHOD == "ozon" or not (CURRENCY == "XTR" or PROVIDER))


def pay_ready():
    return CURRENCY == "XTR" or bool(PROVIDER) or bool(OZON_URL)


def send_invoice(chat_id):
    if use_ozon():
        MENU.ozon_offer(chat_id)
        return
    if not pay_ready():
        tg("sendMessage", {"chat_id": chat_id, "text": "Оплата ещё не подключена. Напишите администратору канала."})
        log("счёт не выставлен: нет PAY_PROVIDER_TOKEN")
        return
    title = f"Закрытые идеи — {DAYS} дней"
    desc = f"Доступ к закрытым торговым идеям канала на {DAYS} дней: тикер, вход, стоп и тейк. {DISCLAIMER}"
    if CURRENCY == "XTR":
        link = tg("createInvoiceLink", {"title": title, "description": desc, "payload": f"sub:{chat_id}", "currency": "XTR",
                                         "prices": [{"label": title, "amount": PRICE}], "subscription_period": 30 * DAY})
        tg("sendMessage", {"chat_id": chat_id, "text": f"Подписка {PRICE} ⭐ в месяц, продлевается автоматически.",
                           "reply_markup": {"inline_keyboard": [[{"text": f"Оплатить {PRICE} ⭐", "url": link}]]}})
        return
    data = {"chat_id": chat_id, "title": title, "description": desc, "payload": f"sub:{chat_id}", "provider_token": PROVIDER,
            "currency": CURRENCY, "prices": [{"label": title, "amount": PRICE * 100}], "start_parameter": "pay"}
    if RECEIPT:  # чек 54-ФЗ (формат ЮKassa)
        data["need_email"] = True
        data["send_email_to_provider"] = True
        data["provider_data"] = json.dumps({"receipt": {"items": [{
            "description": title, "quantity": "1.00", "amount": {"value": f"{PRICE:.2f}", "currency": CURRENCY},
            "vat_code": VAT, "payment_mode": "full_payment", "payment_subject": "service"}]}}, ensure_ascii=False)
    tg("sendInvoice", data)


def on_paid(msg):
    sp = msg["successful_payment"]
    u = msg["from"]
    who = (u.get("first_name") or "") + (" @" + u["username"] if u.get("username") else "")
    until = extend(u["id"], DAYS, who, until=sp.get("subscription_expiration_date"))
    S["payments"].append({"uid": u["id"], "t": now(), "amount": sp.get("total_amount"), "cur": sp.get("currency"),
                          "charge": sp.get("provider_payment_charge_id") or sp.get("telegram_payment_charge_id")})
    S["payments"] = S["payments"][-2000:]
    try:
        MENU.on_first_payment(u["id"])
    except Exception as e:  # noqa: BLE001
        log("бонус пригласившему не начислен:", e)
    tg("sendMessage", {"chat_id": u["id"], "text": f"Оплата получена, спасибо! Доступ к закрытым идеям открыт до {dstr(until)}.\n"
                                                   "Нажимайте «Смотреть идею» под постами в канале."})
    for a in ADMINS:
        try:
            amt = sp.get("total_amount", 0) / (1 if sp.get("currency") == "XTR" else 100)
            tg("sendMessage", {"chat_id": a, "text": f"💰 Новая оплата: {who} (id {u['id']}) — {fmt(amt)} {sp.get('currency')}, доступ до {dstr(until)}."})
        except Exception:  # noqa: BLE001
            pass
    log("оплата:", u["id"], sp.get("total_amount"), sp.get("currency"))


# ---------------- обработчики ----------------
def say(chat_id, text, **kw):
    return tg("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **kw})


HELP_ADMIN = ("Команды администратора:\n"
              "/idea ТИКЕР лонг|шорт ВХОД СТОП ЦЕЛЬ [комментарий] — разбор и публикация закрытой идеи\n"
              "/check ТИКЕР лонг|шорт ВХОД СТОП ЦЕЛЬ — только разбор: вероятность цели, тейки, уровни (можно без /check)\n"
              "/ideas — последние идеи\n"
              "/result НОМЕР текст — дописать итог в пост (например: /result 3 ✅ Тейк взят +6,7%)\n"
              "/subs — подписчики\n"
              "/stats [ГГГГ-ММ] — итоги закрытых идей за месяц в канал (по умолчанию — прошлый месяц; 1-го числа публикуются сами)\n"
              "/grant ID ДНЕЙ — выдать доступ вручную, /revoke ID — забрать\n"
              "/myid — ваш Telegram id")


def on_message(msg):
    global DIRTY_SUBS
    chat = msg.get("chat", {})
    if chat.get("type") != "private":
        return
    if msg.get("successful_payment"):
        on_paid(msg)
        return
    uid = msg["from"]["id"]
    text = (msg.get("text") or "").strip()
    cmd = text.split()[0].lower().split("@")[0] if text else ""
    admin = uid in ADMINS

    if MENU.on_proof(uid, msg):
        return
    if text and not text.startswith("/") and MENU.on_text(uid, text, msg):
        return
    if cmd == "/myid":
        say(uid, f"Ваш Telegram id: <code>{uid}</code>\nДобавьте его в переменную IDEA_ADMINS, чтобы публиковать идеи.")
    elif cmd == "/start":
        arg = text.split()[1] if len(text.split()) > 1 else ""
        MENU.wiz_end(uid)
        if arg.startswith("ref_"):
            MENU.on_ref(uid, arg[4:])
            arg = ""
        if arg == "pay":
            send_invoice(uid)
        elif arg.startswith("plan_"):
            idea = S["ideas"].get(arg[5:])
            if idea and (has_access(uid) or idea.get("open")):
                say(uid, plan_text(idea))
            else:
                say(uid, "План доступен подписчикам закрытых идей.", reply_markup={"inline_keyboard": [[{"text": "Оплатить доступ", "callback_data": "pay"}]]})
        elif arg.startswith("buy_"):
            idea = S["ideas"].get(arg[4:])
            if idea and has_access(uid):
                say(uid, f"<b>{html.escape(tlabel(idea['ticker']))}</b> в Т-Инвестициях: {tinvest_url(idea['ticker'])}\n\n{html.escape(alert_text(idea))}\n\n<i>{DISCLAIMER}</i>")
            else:
                say(uid, "Ссылка доступна подписчикам закрытых идей.", reply_markup={"inline_keyboard": [[{"text": "Оплатить доступ", "callback_data": "pay"}]]})
        else:
            u = sub_until(uid)
            st = f"Ваш доступ открыт до {dstr(u)}." if u > now() else "Доступа пока нет."
            price = f"{PRICE} ⭐" if CURRENCY == "XTR" else f"{PRICE} ₽"
            say(uid, f"Здесь оплачивается доступ к закрытым идеям канала: тикер, вход, стоп и тейк.\n{st}\n\nСтоимость: {price} за {DAYS} дней.",
                reply_markup={"inline_keyboard": [[{"text": "Оплатить доступ", "callback_data": "pay"}]]})
            MENU.menu(uid, "Кнопки меню — внизу 👇" + ("\nДля вас есть кнопки «🟢 Новый лонг», «🔴 Новый шорт» и «📋 Мои идеи: стопы и тейки»." if admin else ""))
    elif cmd in ("/status", "/статус"):
        u = sub_until(uid)
        say(uid, f"Доступ открыт до {dstr(u)}." if u > now() else "Доступа нет. /start — оплатить.")
    elif cmd in ("/idea", "/check", "/разбор") or (admin and re.match(r"^[A-Za-z0-9]{1,6}\s+(лонг|шорт|long|short|buy|sell)\s+\d", text, re.I)):
        if not admin:
            say(uid, "Публиковать идеи может только администратор." + ("" if ADMINS else " Переменная IDEA_ADMINS не задана — узнайте свой id командой /myid."))
            return
        idea = parse_idea(text)
        if isinstance(idea, str):
            say(uid, html.escape(idea))
            return
        q = moex_quote(idea["ticker"])
        warn = ""
        if q is None:
            warn = "⚠️ Мосбиржа не ответила — тикер и цену не проверил.\n"
        elif not q["found"]:
            warn = f"⚠️ {idea['ticker']} не найден в режиме TQBR — проверьте тикер.\n"
        else:
            idea["name"] = q["name"]
            if q["last"]:
                d = (idea["entry"] / q["last"] - 1) * 100
                warn = f"Сейчас {fmt(q['last'])} ₽ ({q['name']}); вход {'выше' if d > 0 else 'ниже'} текущей цены на {fmt(round(abs(d), 2))}%.\n"
                if abs(d) > 7:
                    warn = "⚠️ " + warn
        if warn:
            say(uid, html.escape(warn))
        say(uid, "⏳ Разбираю идею: история бумаги и модель аномалий (обычно 10–30 секунд)…")
        judge_async(uid, idea, publish=(cmd == "/idea"))
    elif cmd == "/ideas" and admin:
        last = sorted(S["ideas"].items(), key=lambda kv: -int(kv[0]))[:15]
        say(uid, "\n".join(f"#{k} {html.escape(tlabel(v['ticker']))} {'лонг' if v['side'] > 0 else 'шорт'} {fmt(v['entry'])}/{fmt(v['stop'])}/{'/'.join(fmt(x) for x in v['takes'])}"
                           f" — {dstr(v['t'])}{' — ' + html.escape(v['result']) if v.get('result') else ''}" for k, v in last) or "Идей пока нет.")
    elif cmd == "/result" and admin:
        p = text.split(maxsplit=2)
        idea = S["ideas"].get(p[1].lstrip("#")) if len(p) > 1 else None
        if not idea or len(p) < 3:
            say(uid, "Формат: /result НОМЕР текст — номер из /ideas")
            return
        idea["result"] = p[2][:120]
        try:
            tg("editMessageText", {"chat_id": idea["chat"], "message_id": idea["msg"], "text": channel_text(idea), "parse_mode": "HTML",
                                   "reply_markup": keyboard(p[1].lstrip("#"))})
            say(uid, "Пост обновлён.")
        except Exception as e:  # noqa: BLE001
            say(uid, f"Не удалось обновить пост: {html.escape(str(e))}")
        save()
    elif cmd == "/subs" and admin:
        act = active_subs()
        lines = [f"{S['subs'][u].get('name') or u} (id {u}) — до {dstr(S['subs'][u]['until'])}" for u in sorted(act, key=lambda u: S['subs'][u]['until'])]
        say(uid, f"Активных подписчиков: {len(act)}\n" + "\n".join(lines[:60]))
    elif cmd == "/grant" and admin:
        p = text.split()
        if len(p) < 3 or not p[1].lstrip("-").isdigit():
            say(uid, "Формат: /grant ID ДНЕЙ")
            return
        until = extend(int(p[1]), int(p[2]))
        say(uid, f"Доступ для {p[1]} до {dstr(until)}.")
    elif cmd == "/revoke" and admin:
        p = text.split()
        if len(p) > 1 and p[1] in S["subs"]:
            S["subs"][p[1]]["until"] = now() - 1
            DIRTY_SUBS = True
            say(uid, "Доступ закрыт.")
    elif admin and cmd == "/stats":
        p = text.split()
        mm = None
        if len(p) > 1 and re.match(r"^\d{4}-\d{2}$", p[1]):
            mm = (int(p[1][:4]), int(p[1][5:]))
        say(uid, "Опубликовано." if stats_post(mm) else "За этот месяц закрытых идей нет.")
    elif admin and cmd == "/help":
        say(uid, HELP_ADMIN)


def judge_async(uid, idea, publish):
    """Строгий разбор в отдельном потоке (ISS бывает медленным) — кнопки других пользователей не ждут."""
    def run():
        r, rep = None, ""
        try:
            import idea_judge as J
            r = J.analyze(idea["ticker"], idea["side"], idea["entry"], idea["stop"], idea["takes"][-1])
            rep = J.report(r)
        except Exception as e:  # noqa: BLE001
            log("разбор не удался:", e)
            rep = f"⚠️ Разбор не удался ({html.escape(str(e))[:200]}). Идею можно опубликовать и без него."
        try:
            say(uid, rep)
        except Exception as e:  # noqa: BLE001
            log("отчёт не отправлен:", e)
        if not publish:
            return
        if r and not r.get("error"):
            cf = r.get("confirm")
            idea["plan"] = {"takes": [{"px": float(t["px"]), "share": float(t["share"]), "p": float(t["p"])} for t in r["takes"]],
                            "rules": list(r["rules"]), "p": float(r.get("p_final", r["p"])), "verdict": r["verdict"],
                            "term": [int(x) for x in r["term"]] if r.get("term") else None, "potential": float(r.get("potential") or 0),
                            "confirm": {"px": float(cf["px"]), "p": float(cf["p"]), "strong": bool(cf.get("strong"))} if cf else None}
            an = r.get("anom") or {}
            if an:  # короткая сводка проверки моделью аномалий — для постов (бот для Пульса)
                idea["plan"]["anom"] = {"lvl": an.get("lvl"), "against": bool(an.get("against")), "with": bool(an.get("with_"))}
        with LOCK:
            S["seq"] += 1
            did = str(S["seq"])
            S["drafts"][did] = idea
            save()
        btns = [[{"text": "✅ Опубликовать с планом тейков", "callback_data": f"pubp:{did}"}]] if idea.get("plan") else []
        btns.append([{"text": "Опубликовать как есть" if idea.get("plan") else "✅ Опубликовать", "callback_data": f"pub:{did}"},
                     {"text": "✖ Отмена", "callback_data": f"del:{did}"}])
        try:
            say(uid, f"Превью поста в канале:\n\n{channel_text(idea)}\n\n— — —\nЧто увидит подписчик по кнопке «Смотреть идею»:\n"
                     f"<code>{html.escape(alert_text(idea))}</code>\n\nКнопка «План сделки» пришлёт подписчику в личку тейки с долями, "
                     "перенос стопа и уровень подтверждения.", reply_markup={"inline_keyboard": btns})
        except Exception as e:  # noqa: BLE001
            log("превью не отправлено:", e)
    threading.Thread(target=run, daemon=True).start()


def on_callback(cq):
    global DIRTY_SUBS
    uid = cq["from"]["id"]
    data = cq.get("data") or ""
    kind, _, iid = data.partition(":")
    ans = {"callback_query_id": cq["id"]}
    r = MENU.on_callback(uid, data, cq)
    if r is not None:
        tg("answerCallbackQuery", {**ans, **({"text": r[:190]} if r else {})})
        save()
        return
    if kind == "plan":
        idea = S["ideas"].get(iid)
        if idea and (has_access(uid) or idea.get("open")):
            ans["url"] = f"https://t.me/{bot_username()}?start=plan_{iid}"
        else:
            ans.update(text="Здесь закрытые идеи. Чтобы их видеть, необходимо оплатить доступ через бота по кнопке «Оплатить доступ» ниже.", show_alert=True)
        tg("answerCallbackQuery", ans)
        return
    if kind in ("see", "buy"):
        idea = S["ideas"].get(iid)
        if not idea:
            ans.update(text="Идея не найдена.", show_alert=True)
        elif not (has_access(uid) or idea.get("open")):
            ans.update(text="Здесь закрытые идеи. Чтобы их видеть, необходимо оплатить доступ через бота по кнопке «Оплатить доступ» ниже.", show_alert=True)
        elif kind == "see":
            ans.update(text=alert_text(idea), show_alert=True)
            idea["views"] = idea.get("views", 0) + 1
        else:
            ans["url"] = f"https://t.me/{bot_username()}?start=buy_{iid}"
        tg("answerCallbackQuery", ans)
        return
    if kind == "pay":
        tg("answerCallbackQuery", ans)
        send_invoice(uid)
        return
    if kind in ("pub", "pubp", "del") and uid in ADMINS:
        with LOCK:
            idea = S["drafts"].pop(iid, None)
        msg = cq.get("message") or {}
        if not idea:
            tg("answerCallbackQuery", {**ans, "text": "Черновик уже обработан."})
            return
        if kind == "del":
            tg("answerCallbackQuery", {**ans, "text": "Отменено."})
            tg("editMessageReplyMarkup", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"], "reply_markup": {"inline_keyboard": []}})
            save()
            return
        if not CHAT:
            tg("answerCallbackQuery", {**ans, "text": "Не задан TG_PUBLIC_CHAT — некуда публиковать.", "show_alert": True})
            return
        if kind == "pubp" and idea.get("plan"):
            idea["takes"] = [t["px"] for t in idea["plan"]["takes"]]
            idea["rr"] = round(abs(idea["takes"][-1] - idea["entry"]) / abs(idea["entry"] - idea["stop"]), 2)
        idea["t"] = now()
        m = tg("sendMessage", {"chat_id": CHAT, "text": channel_text(idea), "parse_mode": "HTML", "reply_markup": keyboard(iid)})
        idea["chat"], idea["msg"] = m["chat"]["id"], m["message_id"]
        S["ideas"][iid] = idea
        save()
        DIRTY_SUBS = True  # копия идей тоже нужна для кнопок после потери кэша
        tg("answerCallbackQuery", {**ans, "text": f"Опубликовано (№{iid})."})
        tg("editMessageReplyMarkup", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"], "reply_markup": {"inline_keyboard": []}})
        log("опубликована идея", iid, idea["ticker"])
        return
    tg("answerCallbackQuery", ans)


def on_precheckout(q):
    ok = (q.get("invoice_payload") or "").startswith("sub:")
    tg("answerPreCheckoutQuery", {"pre_checkout_query_id": q["id"], "ok": ok, **({} if ok else {"error_message": "Счёт устарел, запросите новый: /start"})})


def idea_outcome(v):
    """Итог закрытой идеи в R: процент из итога автора при закрытии, иначе по уровням (ideas_calc.py — та же формула в боте для Пульса)."""
    import ideas_calc as IC
    r = IC.result(v)
    return r["R"], r["kind"]


def stats_post(month=None):
    """Итоги закрытых идей за месяц — открытым постом в канал (прозрачность для подписчиков)."""
    t = time.gmtime(now() + 3 * 3600)
    if month is None:  # прошлый месяц
        y, m = (t.tm_year, t.tm_mon - 1) if t.tm_mon > 1 else (t.tm_year - 1, 12)
    else:
        y, m = month
    key = f"{y}-{m:02d}"
    ideas = [v for v in S["ideas"].values() if v.get("closed") and time.strftime("%Y-%m", time.gmtime(int(v.get("closed_t") or v.get("t") or 0) + 3 * 3600)) == key]
    if not ideas:
        log("итоги идей:", key, "— закрытых идей нет")
        return None
    rs = [(idea_outcome(v), v) for v in ideas]
    vals = [r for (r, k), v in rs if r is not None]
    cnt = {}
    for (r, k), v in rs:
        cnt[k] = cnt.get(k, 0) + 1
    tot = sum(vals)
    win = sum(1 for x in vals if x > 0)
    mn = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"][m - 1]
    lines = [f"📊 <b>Итоги закрытых идей · {mn} {y}</b>", "",
             f"Закрыто идей: <b>{len(ideas)}</b> · в плюсе {win} · " + " · ".join(f"{k} {n}" for k, n in cnt.items()),
             f"Суммарный результат: <b>{'+' if tot >= 0 else '−'}{abs(tot):.1f}R</b>, в среднем {'+' if tot >= 0 else '−'}{abs(tot) / max(1, len(vals)):.2f}R на идею".replace(".", ","),
             "<i>R — риск идеи (расстояние от входа до стопа). Результат — по итогу, указанному при закрытии; если процента в нём нет — по уровням: доли тейков из плана, остаток по стопу.</i>", ""]
    for (r, k), v in sorted(rs, key=lambda z: -(z[0][0] or -99)):
        sd = "лонг" if v["side"] > 0 else "шорт"
        lines.append(f"{'🟢' if (r or 0) > 0 else '🔴' if (r or 0) < 0 else '⚪️'} {html.escape(tlabel(v['ticker']))} {sd} — {k}, {'+' if (r or 0) >= 0 else '−'}{abs(r or 0):.2f}R".replace(".", ",")
                     + (f" · {html.escape(v['result'])}" if v.get("result") else ""))
    lines += ["", "🔒 Новые идеи — по кнопке «Смотреть идею» под постами #идея после оплаты доступа в боте.", f"<i>{DISCLAIMER}</i>"]
    txt = "\n".join(lines)
    m_ = tg("sendMessage", {"chat_id": CHAT, "text": txt[:4000], "parse_mode": "HTML", "disable_web_page_preview": True,
                            "reply_markup": {"inline_keyboard": [[{"text": "🔒 Доступ к идеям", "url": f"https://t.me/{bot_username()}?start=pay"}]]}})
    log("итоги идей опубликованы:", key)
    return m_


def monthly():
    t = time.gmtime(now() + 3 * 3600)
    key = time.strftime("%Y-%m", t)
    if t.tm_mday == 1 and t.tm_hour >= 12 and S.get("stats_m") != key and CHAT:
        S["stats_m"] = key
        try:
            stats_post()
        except Exception as e:  # noqa: BLE001
            log("итоги идей:", e)


def remind():
    """За 3 дня до конца доступа — одно напоминание с кнопкой продления (ЮKassa не продлевает сама)."""
    if CURRENCY == "XTR":
        return
    t = now()
    for u, rec in S["subs"].items():
        left = int(rec.get("until", 0)) - t
        if 0 < left < 3 * DAY and not rec.get("warned"):
            try:
                say(int(u), f"Доступ к закрытым идеям заканчивается {dstr(rec['until'])}. Продлить на {DAYS} дней:",
                    reply_markup={"inline_keyboard": [[{"text": "Продлить доступ", "callback_data": "pay"}]]})
            except Exception as e:  # noqa: BLE001
                log("напоминание не отправлено", u, e)
            rec["warned"] = True


STOP = False


def _stop(*_):
    global STOP
    STOP = True


def main():
    if not TOKEN:
        log("нет TG_PUBLIC_TOKEN — закрытые идеи выключены")
        return
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    restore()
    MENU.init(sys.modules[__name__])
    log("старт: канал", CHAT or "—", "| админы", sorted(ADMINS) or "НЕ ЗАДАНЫ (IDEA_ADMINS)", "| оплата",
        ("Ozon «Чаевые», подтверждение вручную" if use_ozon() else f"{PRICE} {CURRENCY}/{DAYS} дн.") if pay_ready() else "НЕ ПОДКЛЮЧЕНА (PAY_PROVIDER_TOKEN или OZON_TIPS_URL)")
    try:
        tg("setMyCommands", {"commands": [{"command": "start", "description": "Меню и доступ к закрытым идеям"},
                                          {"command": "status", "description": "До какого числа открыт доступ"}]})
    except Exception as e:  # noqa: BLE001
        log("setMyCommands:", e)
    end = time.time() + RUN_MIN * 60
    last_save = last_remind = 0
    import hotreload
    hot = hotreload.Watcher()
    while not STOP and time.time() < end:
        if hot.changed():  # bot.js подтянул новый код/модели с GitHub — продолжаем на новой версии
            save()
            if DIRTY_SUBS:
                backup()
            hotreload.restart(log, {"PAYWALL_MIN": round(max(1.0, (end - time.time()) / 60), 1)})
        try:
            ups = tg("getUpdates", {"offset": S["offset"], "timeout": 20,
                                    "allowed_updates": ["message", "callback_query", "pre_checkout_query"]}, timeout=40)
        except TgError as e:
            if " 409 " in str(e):  # другой процесс читает обновления (перекрытие запусков) — ждём
                time.sleep(5)
            else:
                log("getUpdates:", e)
                time.sleep(5)
            continue
        except Exception as e:  # noqa: BLE001
            log("сеть:", e)
            time.sleep(5)
            continue
        for u in ups:
            S["offset"] = u["update_id"] + 1
            try:
                if "pre_checkout_query" in u:
                    on_precheckout(u["pre_checkout_query"])
                elif "callback_query" in u:
                    on_callback(u["callback_query"])
                elif "message" in u:
                    on_message(u["message"])
            except Exception as e:  # noqa: BLE001
                log("ошибка обработки:", e)
        if ups or time.time() - last_save > 60:
            save()
            last_save = time.time()
        if DIRTY_SUBS:
            backup()
        if time.time() - last_remind > 3600:
            monthly()
            remind()
            last_remind = time.time()
    save()
    if DIRTY_SUBS:
        backup()
    log("остановлен; подписчиков активно:", len(active_subs()))


if __name__ == "__main__":
    main()
