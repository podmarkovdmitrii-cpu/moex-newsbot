# -*- coding: utf-8 -*-
"""Меню бота закрытых идей: кнопки внизу чата и пошаговые сценарии.

Подписчик: 💳 Оплатить · 🗂 Моя подписка · 🔨 Техническая поддержка · ❓ FAQ · ⬇️ Мой бонусный счёт.
Администратор дополнительно: 🟢 Новый лонг · 🔴 Новый шорт · 📋 Мои идеи: стопы и тейки.

Сценарии хранятся в S["wiz"][uid]; функции paywall (say, tg, S, …) передаются через init(pw).
Бонусный счёт: реферальная ссылка; за каждого приглашённого, кто впервые оплатил, — BONUS_DAYS дней доступа.
Техподдержка: вопрос пересылается администраторам, ответ — кнопкой «Ответить» под вопросом.
"""
import html
import os
import re
import time

PW = None  # модуль paywall

BTN_PAY, BTN_SUB, BTN_SUP, BTN_FAQ, BTN_BONUS = "💳 Оплатить", "🗂 Моя подписка", "🔨 Техническая поддержка", "❓ FAQ", "⬇️ Мой бонусный счёт"
BTN_LONG, BTN_SHORT, BTN_IDEAS, BTN_CANCEL = "🟢 Новый лонг", "🔴 Новый шорт", "📋 Мои идеи: стопы и тейки", "✖ Отмена"
BONUS_DAYS = int(os.environ.get("BONUS_DAYS") or 7)
SUPPORT = os.environ.get("SUPPORT_CONTACT", "").strip()
FAQ_TEXT = os.environ.get("FAQ_TEXT", "").strip()
NUM = r"\d+(?:[.,]\d+)?"


def init(pw):
    global PW
    PW = pw
    for k in ("wiz", "refs", "bonus", "refpaid", "ozpend"):
        PW.S.setdefault(k, {})


def kb(admin):
    rows = [[BTN_PAY, BTN_SUB], [BTN_SUP, BTN_FAQ], [BTN_BONUS]]
    if admin:
        rows += [[BTN_LONG, BTN_SHORT], [BTN_IDEAS]]
    return {"keyboard": [[{"text": t} for t in r] for r in rows], "resize_keyboard": True, "is_persistent": True}


def cancel_kb():
    return {"keyboard": [[{"text": BTN_CANCEL}]], "resize_keyboard": True}


def menu(uid, text="Меню:"):
    PW.say(uid, text, reply_markup=kb(uid in PW.ADMINS))


def wiz_set(uid, **w):
    PW.S["wiz"][str(uid)] = {"t": PW.now(), **w}


def wiz_get(uid):
    w = PW.S["wiz"].get(str(uid))
    if w and PW.now() - w.get("t", 0) > 3 * 3600:  # забытый сценарий
        PW.S["wiz"].pop(str(uid), None)
        return None
    return w


def wiz_end(uid):
    PW.S["wiz"].pop(str(uid), None)


def num(s):
    return float(s.replace(",", "."))


def side_word(side):
    return "лонг" if side > 0 else "шорт"


# ---------------- подписчик ----------------
def show_sub(uid):
    u = PW.sub_until(uid)
    if u > PW.now():
        left = (u - PW.now()) // PW.DAY
        txt = f"🗂 <b>Моя подписка</b>\nДоступ к закрытым идеям открыт до <b>{PW.dstr(u)}</b> (осталось {left} дн.)."
        btn = "Продлить доступ"
    else:
        txt = "🗂 <b>Моя подписка</b>\nДоступа пока нет."
        btn = "Оплатить доступ"
    b = PW.S["bonus"].get(str(uid)) or {}
    if b.get("days"):
        txt += f"\nИз них бонусных дней за приглашения: {b['days']}."
    PW.say(uid, txt, reply_markup={"inline_keyboard": [[{"text": btn, "callback_data": "pay"}]]})


def show_faq(uid):
    price = f"{PW.PRICE} ⭐" if PW.CURRENCY == "XTR" else f"{PW.PRICE} ₽"
    txt = FAQ_TEXT or (
        "❓ <b>FAQ</b>\n\n"
        "<b>Что внутри?</b> Закрытые торговые идеи по акциям Мосбиржи: тикер, вход, стоп, тейки, план фиксации прибыли "
        "и уровень подтверждения. Каждую идею бот проверяет по истории бумаги и показывает вероятность дойти до цели.\n\n"
        f"<b>Сколько стоит?</b> {price} за {PW.DAYS} дней. " + ("Оплата переводом через Ozon Банк (картой любого банка или по СБП); доступ открывается после проверки перевода администратором.\n\n" if PW.use_ozon() else "Доступ открывается сразу после оплаты.\n\n") +
        "<b>Как смотреть идеи?</b> Под постом в канале нажмите «Смотреть идею» или «План сделки».\n\n"
        "<b>Как продлить?</b> Кнопка «💳 Оплатить» — дни добавятся к текущему сроку. За 3 дня до конца бот напомнит.\n\n"
        f"<b>Бонусы.</b> Пригласите друга по своей ссылке из «⬇️ Мой бонусный счёт»: когда он оплатит, вы получите {BONUS_DAYS} дней доступа.\n\n"
        "<b>Это гарантия прибыли?</b> Нет. Вероятности — статистика прошлого, торговля связана с риском потерь.\n\n"
        f"<i>{PW.DISCLAIMER}</i>")
    PW.say(uid, txt)


def show_bonus(uid):
    me = PW.bot_username()
    link = f"https://t.me/{me}?start=ref_{uid}"
    invited = [k for k, v in PW.S["refs"].items() if str(v) == str(uid)]
    paid = [k for k in invited if PW.S["refpaid"].get(k)]
    b = PW.S["bonus"].get(str(uid)) or {}
    PW.say(uid, f"⬇️ <b>Мой бонусный счёт</b>\n\nНачислено бонусных дней: <b>{b.get('days', 0)}</b>\n"
                f"Приглашено: {len(invited)}, из них оплатили: {len(paid)}\n\n"
                f"За каждого приглашённого, кто впервые оплатит доступ, — <b>+{BONUS_DAYS} дней</b> доступа (начисляются автоматически).\n\n"
                f"Ваша ссылка для приглашения:\n{link}")


def start_support(uid):
    wiz_set(uid, k="support")
    extra = f"\nМожно написать и напрямую: {html.escape(SUPPORT)}" if SUPPORT else ""
    PW.say(uid, "🔨 Опишите вопрос одним сообщением — передам администратору, ответ придёт сюда." + extra, reply_markup=cancel_kb())


def on_ref(uid, ref):
    """Первый /start по реферальной ссылке."""
    if str(uid) == str(ref) or str(uid) in PW.S["refs"] or PW.S["subs"].get(str(uid)):
        return
    if not str(ref).lstrip("-").isdigit():
        return
    PW.S["refs"][str(uid)] = str(ref)


def on_first_payment(uid):
    """Вызывается из on_paid: бонус пригласившему за первую оплату."""
    ref = PW.S["refs"].get(str(uid))
    if not ref or PW.S["refpaid"].get(str(uid)):
        return
    PW.S["refpaid"][str(uid)] = PW.now()
    until = PW.extend(int(ref), BONUS_DAYS)
    b = PW.S["bonus"].setdefault(str(ref), {"days": 0})
    b["days"] = b.get("days", 0) + BONUS_DAYS
    try:
        PW.say(int(ref), f"🎁 Приглашённый вами пользователь оплатил доступ — начислено +{BONUS_DAYS} дней. Доступ открыт до {PW.dstr(until)}.")
    except Exception:  # noqa: BLE001
        pass


# ---------------- оплата через «Чаевые» Ozon Банка ----------------
def ozon_amount(uid):
    """Сумма с «копейками-меткой» из id — по ней администратор быстро находит перевод в выписке Ozon Банка."""
    return f"{PW.PRICE},{int(uid) % 89 + 10:02d}"


def ozon_offer(uid):
    amt = ozon_amount(uid)
    PW.say(uid, f"💳 <b>Оплата доступа — {PW.PRICE} ₽ за {PW.DAYS} дней</b>\n\n"
                f"1. Нажмите «Оплатить в Ozon Банке» и переведите ровно <b>{amt} ₽</b> — копейки нужны, чтобы мы узнали ваш платёж.\n"
                "   Платить можно с карты любого банка или по СБП.\n"
                "2. Вернитесь сюда и нажмите «✅ Я оплатил» — пришлите скриншот или чек перевода.\n"
                "3. Администратор сверит перевод и откроет доступ (обычно в течение нескольких часов, в рабочее время быстрее).\n\n"
                f"Если уже есть доступ — дни добавятся к текущему сроку.\n<i>{PW.DISCLAIMER}</i>",
           reply_markup={"inline_keyboard": [[{"text": "Оплатить в Ozon Банке", "url": PW.OZON_URL}],
                                             [{"text": "✅ Я оплатил", "callback_data": "ozpaid"}]]})


def on_proof(uid, msg):
    """Скриншот/чек после «Я оплатил» — администраторам с кнопками подтверждения. True — сообщение обработано."""
    w = wiz_get(uid)
    if not w or w.get("k") != "ozproof":
        return False
    text = (msg.get("text") or "").strip()
    if text == BTN_CANCEL:
        return False
    if text.startswith("/"):
        return False
    if not (msg.get("photo") or msg.get("document") or text):
        return False
    wiz_end(uid)
    u = msg.get("from") or {}
    who = html.escape((u.get("first_name") or "") + (" @" + u["username"] if u.get("username") else ""))
    PW.S.setdefault("ozpend", {})[str(uid)] = {"t": PW.now(), "who": who, "amt": ozon_amount(uid)}
    for a in PW.ADMINS:
        try:
            PW.tg("copyMessage", {"chat_id": a, "from_chat_id": uid, "message_id": msg["message_id"]})
            PW.say(a, f"💳 <b>Оплата через Ozon «Чаевые»</b> от {who} (id {uid}).\nОжидаемая сумма: <b>{ozon_amount(uid)} ₽</b>. "
                      "Проверьте поступление в приложении Ozon Банка.",
                   reply_markup={"inline_keyboard": [[{"text": f"✅ Подтвердить ({PW.DAYS} дн.)", "callback_data": f"ozok:{uid}"},
                                                      {"text": "❌ Не нашёл", "callback_data": f"ozno:{uid}"}]]})
        except Exception as e:  # noqa: BLE001
            PW.log("заявка на оплату не отправлена администратору:", e)
    menu(uid, "Спасибо! Заявка на проверку оплаты отправлена. Как только администратор подтвердит перевод, доступ откроется и придёт сообщение.")
    return True


def ozon_cb(uid, kind, arg, cq):
    if kind == "ozpaid":
        wiz_set(uid, k="ozproof")
        PW.say(uid, f"Пришлите скриншот или чек перевода на {ozon_amount(uid)} ₽ одним сообщением (можно фото).", reply_markup=cancel_kb())
        return ""
    if uid not in PW.ADMINS:
        return None
    pend = PW.S.setdefault("ozpend", {})
    rec = pend.pop(arg, None)
    msg = cq.get("message") or {}
    if kind == "ozok":
        until = PW.extend(int(arg), PW.DAYS, (rec or {}).get("who"))
        PW.S["payments"].append({"uid": int(arg), "t": PW.now(), "amount": PW.PRICE * 100, "cur": "RUB", "charge": "ozon-tips", "by": uid})
        PW.S["payments"] = PW.S["payments"][-2000:]
        try:
            on_first_payment(int(arg))
        except Exception as e:  # noqa: BLE001
            PW.log("бонус пригласившему не начислен:", e)
        try:
            PW.say(int(arg), f"✅ Оплата подтверждена, спасибо! Доступ к закрытым идеям открыт до {PW.dstr(until)}.\n"
                             "Нажимайте «Смотреть идею» и «План сделки» под постами в канале.")
        except Exception:  # noqa: BLE001
            pass
        note = f"✅ Подтверждено: доступ id {arg} до {PW.dstr(until)}"
    else:
        try:
            PW.say(int(arg), "Перевод пока не найден. Проверьте сумму и получателя или напишите в «🔨 Техническая поддержка» — приложите чек.")
        except Exception:  # noqa: BLE001
            pass
        note = f"❌ Отклонено: id {arg}"
    try:
        PW.tg("editMessageReplyMarkup", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"], "reply_markup": {"inline_keyboard": []}})
    except Exception:  # noqa: BLE001
        pass
    for a in PW.ADMINS:
        if a != uid:
            try:
                PW.say(a, note + " (решение другого администратора).")
            except Exception:  # noqa: BLE001
                pass
    PW.DIRTY_SUBS = True
    return note


# ---------------- администратор: новая идея ----------------
def start_new(uid, side):
    wiz_set(uid, k="new", side=side, step="ticker", d={})
    PW.say(uid, f"{'🟢 Новый лонг' if side > 0 else '🔴 Новый шорт'}. Шаг 1/5 — тикер (например SBER):", reply_markup=cancel_kb())


def new_step(uid, w, text):
    d, side, st = w["d"], w["side"], w["step"]
    if st == "ticker":
        t = text.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{1,12}", t):
            return PW.say(uid, "Тикер латиницей, например SBER.")
        q = PW.moex_quote(t)
        if q and not q.get("found"):
            return PW.say(uid, f"{t} не найден на Мосбирже (TQBR). Введите другой тикер.")
        d["ticker"], d["last"] = t, (q or {}).get("last")
        w["step"] = "entry"
        cur = (f"{html.escape(PW.tlabel(t))}: сейчас {PW.fmt(d['last'])} ₽. " if d.get("last") else f"{html.escape(PW.tlabel(t))}. ")
        mk = {"inline_keyboard": [[{"text": f"По рынку {PW.fmt(d['last'])}", "callback_data": "wmkt"}]]} if d.get("last") else None
        return PW.say(uid, f"{cur}Шаг 2/5 — цена входа:", **({"reply_markup": mk} if mk else {}))
    if st in ("entry", "stop", "target"):
        nums = re.findall(NUM, text)
        if not nums:
            return PW.say(uid, "Нужно число, например 291,5.")
        if st == "entry":
            d["entry"] = num(nums[0])
            w["step"] = "stop"
            return PW.say(uid, f"Шаг 3/5 — стоп ({'ниже' if side > 0 else 'выше'} входа {PW.fmt(d['entry'])}):")
        if st == "stop":
            s = num(nums[0])
            if (s - d["entry"]) * side >= 0:
                return PW.say(uid, f"Для {side_word(side)}а стоп должен быть {'ниже' if side > 0 else 'выше'} входа {PW.fmt(d['entry'])}.")
            d["stop"] = s
            w["step"] = "target"
            return PW.say(uid, "Шаг 4/5 — цель (общая; можно несколько тейков через «/», например 280/270). "
                               "Если укажете одну цель — бот сам предложит, как разбить её на тейки:")
        tk = [num(x) for x in nums]
        if any((x - d["entry"]) * side <= 0 for x in tk):
            return PW.say(uid, f"Для {side_word(side)}а тейки должны быть {'выше' if side > 0 else 'ниже'} входа {PW.fmt(d['entry'])}.")
        d["takes"] = sorted(tk, reverse=side < 0)
        w["step"] = "comment"
        return PW.say(uid, "Шаг 5/5 — комментарий к идее (коротко) или нажмите «Без комментария»:",
                      reply_markup={"inline_keyboard": [[{"text": "Без комментария", "callback_data": "wnoc"}]]})
    if st == "comment":
        d["comment"] = text.strip()[:300]
        return finish_new(uid, w)


def finish_new(uid, w):
    d = w["d"]
    wiz_end(uid)
    line = f"/idea {d['ticker']} {side_word(w['side'])} {d['entry']} {d['stop']} {'/'.join(str(x) for x in d['takes'])} {d.get('comment', '')}"
    idea = PW.parse_idea(line)
    if isinstance(idea, str):
        return menu(uid, html.escape(idea))
    if d.get("last"):
        idea["name"] = d["ticker"]
    menu(uid, "⏳ Разбираю идею: история бумаги и модель аномалий (обычно до минуты, первый разбор за день — до 3 минут)…")
    PW.judge_async(uid, idea, publish=True)


# ---------------- администратор: управление идеями ----------------
def open_ideas():
    items = [(k, v) for k, v in PW.S["ideas"].items() if not v.get("closed")]
    return sorted(items, key=lambda kv: -int(kv[0]))[:12]


def show_ideas(uid):
    items = open_ideas()
    if not items:
        return PW.say(uid, "Открытых идей нет. Новая — кнопками «🟢 Новый лонг» / «🔴 Новый шорт».")
    rows = [[{"text": f"#{k} {PW.tlabel(v['ticker'])} {side_word(v['side'])} · стоп {PW.fmt(v['stop'])}", "callback_data": f"mi:{k}"}] for k, v in items]
    PW.say(uid, "📋 Открытые идеи — выберите, чтобы перенести стоп, изменить тейки или закрыть:", reply_markup={"inline_keyboard": rows})


def idea_card(iid):
    v = PW.S["ideas"][iid]
    hit = set(v.get("hit") or [])
    tk = " / ".join(("✅" if i in hit else "") + PW.fmt(x) for i, x in enumerate(v["takes"]))
    st = "\n".join(v.get("status") or [])
    txt = (f"<b>#{iid} {PW.tlabel(v['ticker'])} {side_word(v['side'])}</b>\nВход {PW.fmt(v['entry'])} · стоп {PW.fmt(v['stop'])}"
           f"{' (безубыток)' if v.get('be') else ''}\nТейки: {tk}" + (f"\n{html.escape(st)}" if st else ""))
    rows = [[{"text": "🛡 Стоп в безубыток", "callback_data": f"mbe:{iid}"}, {"text": "✏️ Изменить стоп", "callback_data": f"mst:{iid}"}],
            [{"text": "🎯 Изменить тейки", "callback_data": f"mtk:{iid}"}]]
    hits = [{"text": f"✅ Т{i + 1} взят", "callback_data": f"mh:{iid}:{i}"} for i in range(len(v["takes"])) if i not in hit]
    for j in range(0, len(hits), 3):
        rows.append(hits[j:j + 3])
    if not v.get("open"):
        rows.append([{"text": "🔓 Открыть всем (витрина)", "callback_data": f"mop:{iid}"}])
    rows.append([{"text": "🏁 Закрыть идею", "callback_data": f"mcl:{iid}"}])
    return txt, {"inline_keyboard": rows}


def refresh(iid, note):
    """Обновить пост в канале и разослать подписчикам короткое уведомление (уровни — только подписчикам)."""
    v = PW.S["ideas"][iid]
    v.setdefault("status", []).append(note)
    v["status"] = v["status"][-6:]
    try:
        PW.tg("editMessageText", {"chat_id": v["chat"], "message_id": v["msg"], "text": PW.channel_text(v), "parse_mode": "HTML",
                                  "reply_markup": PW.keyboard(iid)})
    except Exception as e:  # noqa: BLE001
        PW.log("пост не обновлён:", e)
    sent = 0
    for u in PW.active_subs():
        if int(u) in PW.ADMINS:
            continue
        try:
            PW.say(int(u), f"🔔 Обновление по идее <b>{PW.tlabel(v['ticker'])} {side_word(v['side'])}</b>: {html.escape(note)}",
                   reply_markup={"inline_keyboard": [[{"text": "План сделки", "url": f"https://t.me/{PW.bot_username()}?start=plan_{iid}"}]]})
            sent += 1
            time.sleep(0.05)
        except Exception:  # noqa: BLE001
            pass
    PW.save()
    PW.DIRTY_SUBS = True
    return sent


def on_manage_cb(uid, kind, arg, cq):
    """Кнопки управления идеей. Возвращает текст для answerCallbackQuery."""
    iid, _, extra = arg.partition(":")
    v = PW.S["ideas"].get(iid)
    if not v:
        return "Идея не найдена."
    msg = cq.get("message") or {}
    if kind == "mi":
        txt, mk = idea_card(iid)
        PW.say(uid, txt, reply_markup=mk)
        return ""
    if kind == "mbe":
        v["stop"], v["be"] = v["entry"], True
        n = refresh(iid, f"стоп перенесён в безубыток ({PW.fmt(v['entry'])})")
        _recard(uid, iid, msg)
        return f"Стоп в безубытке, уведомлено подписчиков: {n}"
    if kind == "mst":
        wiz_set(uid, k="stop", iid=iid)
        PW.say(uid, f"Новый стоп для #{iid} {PW.tlabel(v['ticker'])} (сейчас {PW.fmt(v['stop'])}):", reply_markup=cancel_kb())
        return ""
    if kind == "mtk":
        wiz_set(uid, k="takes", iid=iid)
        PW.say(uid, f"Новые тейки для #{iid} через «/» (сейчас {' / '.join(PW.fmt(x) for x in v['takes'])}):", reply_markup=cancel_kb())
        return ""
    if kind == "mh":
        i = int(extra or 0)
        hit = set(v.get("hit") or [])
        hit.add(i)
        v["hit"] = sorted(hit)
        pl = (v.get("plan") or {}).get("takes") or []
        share = f" — по плану закрыть {pl[i]['share']:.0%}" if i < len(pl) else ""
        note = f"Т{i + 1} {PW.fmt(v['takes'][i])} взят{share}"
        if len(hit) == len(v["takes"]):
            v["closed"] = True
            v["closed_t"] = PW.now()
            v["result"] = v.get("result") or "✅ Все тейки взяты"
            note += ". Идея закрыта: все тейки взяты"
        elif i == 0 and not v.get("be"):
            v["stop"], v["be"] = v["entry"], True
            note += f", стоп переносим в безубыток ({PW.fmt(v['entry'])})"
        elif i >= 1:
            prev = v["takes"][i - 1]
            if (prev - v["stop"]) * v["side"] > 0:
                v["stop"] = prev
                note += f", стоп переносим на Т{i} ({PW.fmt(prev)})"
        n = refresh(iid, note)
        _recard(uid, iid, msg)
        return f"Отмечено, уведомлено подписчиков: {n}"
    if kind == "mop":
        v["open"] = True
        n = refresh(iid, "идея открыта для всех читателей канала")
        _recard(uid, iid, msg)
        return "Идея открыта: уровни показаны в посте."
    if kind == "mcl":
        wiz_set(uid, k="close", iid=iid)
        PW.say(uid, f"Итог по #{iid} {PW.tlabel(v['ticker'])} одной строкой (например «✅ Тейк взят +6,7%» или «❌ Стоп −3,1%»):", reply_markup=cancel_kb())
        return ""
    return ""


def _recard(uid, iid, msg):
    txt, mk = idea_card(iid)
    try:
        PW.tg("editMessageText", {"chat_id": msg["chat"]["id"], "message_id": msg["message_id"], "text": txt, "parse_mode": "HTML", "reply_markup": mk})
    except Exception:  # noqa: BLE001
        PW.say(uid, txt, reply_markup=mk)


def manage_step(uid, w, text):
    iid = w.get("iid")
    v = PW.S["ideas"].get(iid)
    if not v:
        wiz_end(uid)
        return menu(uid, "Идея не найдена.")
    if w["k"] == "stop":
        n = re.findall(NUM, text)
        if not n:
            return PW.say(uid, "Нужно число.")
        s = num(n[0])
        if (min(v["takes"], key=lambda x: abs(x - v["entry"])) - s) * v["side"] <= 0:
            return PW.say(uid, "Стоп оказался за тейком — проверьте число.")
        old = v["stop"]
        v["stop"], v["be"] = s, abs(s - v["entry"]) < 1e-9
        wiz_end(uid)
        cnt = refresh(iid, f"стоп изменён: {PW.fmt(old)} → {PW.fmt(s)}")
        menu(uid, f"Стоп обновлён, уведомлено подписчиков: {cnt}.")
    elif w["k"] == "takes":
        n = [num(x) for x in re.findall(NUM, text)]
        if not n or any((x - v["entry"]) * v["side"] <= 0 for x in n):
            return PW.say(uid, f"Тейки должны быть {'выше' if v['side'] > 0 else 'ниже'} входа {PW.fmt(v['entry'])}, через «/».")
        v["takes"] = sorted(n, reverse=v["side"] < 0)
        v["hit"] = []
        if v.get("plan"):
            v["plan"]["takes"] = [{"px": x, "share": 1 / len(n), "p": None} for x in v["takes"]]
        v["rr"] = round(abs(v["takes"][-1] - v["entry"]) / abs(v["entry"] - v["stop"]), 2) if v["stop"] != v["entry"] else v.get("rr")
        wiz_end(uid)
        cnt = refresh(iid, "тейки изменены: " + " / ".join(PW.fmt(x) for x in v["takes"]))
        menu(uid, f"Тейки обновлены, уведомлено подписчиков: {cnt}.")
    elif w["k"] == "close":
        v["result"] = text.strip()[:120]
        v["closed"] = True
        v["closed_t"] = PW.now()
        wiz_end(uid)
        cnt = refresh(iid, "идея закрыта: " + v["result"])
        menu(uid, f"Идея #{iid} закрыта, уведомлено подписчиков: {cnt}.")


# ---------------- общий вход ----------------
def on_text(uid, text, msg):
    """Кнопки меню и шаги сценариев. True — сообщение обработано."""
    admin = uid in PW.ADMINS
    if text == BTN_CANCEL:
        wiz_end(uid)
        menu(uid, "Отменено.")
        return True
    if text in (BTN_PAY, BTN_SUB, BTN_SUP, BTN_FAQ, BTN_BONUS, BTN_LONG, BTN_SHORT, BTN_IDEAS):
        wiz_end(uid)
        if text == BTN_PAY:
            PW.send_invoice(uid)
        elif text == BTN_SUB:
            show_sub(uid)
        elif text == BTN_SUP:
            start_support(uid)
        elif text == BTN_FAQ:
            show_faq(uid)
        elif text == BTN_BONUS:
            show_bonus(uid)
        elif admin and text == BTN_LONG:
            start_new(uid, 1)
        elif admin and text == BTN_SHORT:
            start_new(uid, -1)
        elif admin and text == BTN_IDEAS:
            show_ideas(uid)
        return True
    w = wiz_get(uid)
    if not w:
        return False
    if w["k"] == "support":
        wiz_end(uid)
        u = msg.get("from") or {}
        who = html.escape((u.get("first_name") or "") + (" @" + u["username"] if u.get("username") else ""))
        st = f"доступ до {PW.dstr(PW.sub_until(uid))}" if PW.sub_until(uid) > PW.now() else "без доступа"
        for a in PW.ADMINS:
            try:
                PW.say(a, f"🔨 <b>Вопрос в поддержку</b> от {who} (id {uid}, {st}):\n\n{html.escape(text[:3000])}",
                       reply_markup={"inline_keyboard": [[{"text": "Ответить", "callback_data": f"rep:{uid}"}]]})
            except Exception:  # noqa: BLE001
                pass
        menu(uid, "Спасибо! Вопрос передан, ответ придёт сюда.")
        return True
    if w["k"] == "reply" and admin:
        wiz_end(uid)
        try:
            PW.say(int(w["to"]), f"🔨 <b>Ответ поддержки:</b>\n\n{html.escape(text[:3500])}")
            menu(uid, "Ответ отправлен.")
        except Exception as e:  # noqa: BLE001
            menu(uid, f"Не удалось отправить: {html.escape(str(e))}")
        return True
    if not admin:
        wiz_end(uid)
        return False
    if w["k"] == "new":
        new_step(uid, w, text)
        return True
    if w["k"] in ("stop", "takes", "close"):
        manage_step(uid, w, text)
        return True
    return False


def on_callback(uid, data, cq):
    """Кнопки меню под сообщениями. None — не наше; иначе текст ответа на нажатие."""
    kind, _, arg = data.partition(":")
    if kind in ("ozpaid", "ozok", "ozno"):
        r = ozon_cb(uid, kind, arg, cq)
        if r is not None:
            return r
    if kind == "rep" and uid in PW.ADMINS:
        wiz_set(uid, k="reply", to=arg)
        PW.say(uid, f"Напишите ответ пользователю {arg}:", reply_markup=cancel_kb())
        return ""
    if kind in ("wmkt", "wnoc") and uid in PW.ADMINS:
        w = wiz_get(uid)
        if not w or w.get("k") != "new":
            return "Сценарий устарел — начните заново."
        if kind == "wmkt" and w["step"] == "entry" and w["d"].get("last"):
            new_step(uid, w, str(w["d"]["last"]))
        elif kind == "wnoc" and w["step"] == "comment":
            w["d"]["comment"] = ""
            finish_new(uid, w)
        return ""
    if kind in ("mi", "mbe", "mst", "mtk", "mh", "mcl", "mop") and uid in PW.ADMINS:
        return on_manage_cb(uid, kind, arg, cq)
    return None
