# -*- coding: utf-8 -*-
"""Бот-помощник для Пульса (Т-Инвестиции): готовит посты с картинками — вы копируете их в Пульс сами.

У Пульса нет открытого API для публикации, а автопостинг ботами правилами не предусмотрен, поэтому бот присылает
администратору черновик: картинку (сохранить и прикрепить) и текст (нажать — скопируется) с тикерами, хэштегами,
дисклеймером. Черновики соблюдают правила Пульса (см. PULSE_RULES ниже): без рекламы и упоминаний Telegram-канала,
не больше 10 бумаг ($тикеров) в посте.

Что бот присылает сам — ровно 2 черновика в торговый день (будни):
  • ~13:05 МСК — рынок в целом: индекс, секторы, кто растёт и падает, куда идут деньги, аномалии дня и прогноз модели;
  • ~19:45 МСК — результаты открытых идей канала (% от входа, тейки, безубыток), закрытые сегодня, общая статистика,
    взгляд на завтра: положение индекса относительно средних, перевес к закрытию, вероятные аномалии по модели.
Черновики по каждому событию идеи (тизер, тейк, итог) и итоги месяца — кнопками; автоматически — только с PULSE_EVENTS=1.
Кнопки: рынок днём, вечер (идеи и завтра), итоги месяца, последние сделки (итог/тизер по любой), вся статистика,
свой текст → пост для Пульса, где ставить ссылку (в тексте / «в профиле» / без ссылки).

Сделки — только идеи, опубликованные в Telegram-канале (paywall.py, файл paywall.json). Результат — итог, который автор
написал при закрытии идеи («❌ Стоп −3,1%»); если процента нет — по уровням: доли тейков из плана, остаток по стопу
(ideas_calc.py — та же формула, что в #итоги_идей канала). Комиссии не учитываются.
Посты подчёркивают особенности канала: идея публикуется до входа (дата и ссылка на пост), проверка идеи моделью
(вероятность цели, риск аномалии), план с частичными тейками, публикация убыточных сделок, общая статистика с первой идеи,
модель аномалий и проверенные новости. Кнопка «🏆 Вся статистика» — пост со всеми закрытыми идеями. Переменные: TG_PULSE_TOKEN (токен отдельного бота от @BotFather),
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
from common import BG, FG, UP, DN, BLUE, DISCLAIMER, fmt, pc, share_candles, index_candles, candle_chart, hbars, iss, table  # noqa: E402
import ideas_calc as IC  # noqa: E402

# ---------------- правила Пульса ----------------
# «Правила модерации сообщества» Пульса: п. 3.8 «Реклама» — нельзя ссылки на личные ресурсы и призывы перейти в другие
# соцсети (Telegram, ВКонтакте, Дзен…); п. 3.16 — нельзя ссылки на внешние ресурсы (гиперссылки, QR-коды); п. 3.3 — нельзя
# формулировки, подталкивающие к сделке с бумагой (призывы), и ИИР; п. 3.5 — нельзя неподтверждённую информацию без
# источника; п. 3.10 — без политики и религии. Плюс ограничение редактора: не больше 10 бумаг в посте.
# Поэтому в черновиках нет ссылок и упоминаний канала (режим «где ссылка» больше не действует), ссылки вычищаются,
# лишние бумаги срезаются (первые 10 по порядку в тексте — порядок блоков задан по важности), призывы — предупреждение.
MAX_TICKERS = 10
TICK = re.compile(r"\$([A-Z][A-Z0-9]{0,6})\b")  # в т.ч. однобуквенные ($T)
PROMO = re.compile(r"t\.me/|telegram|телеграм|tg-канал|тг-канал|вконтакте|\bвк\b|vk\.com|дзен|dzen|youtube|ютуб|rutube|в (?:моём |мой |моем )?канале|"
                   r"из (?:моего )?канала|подписчик|подпис(?:ывайтесь|аться)|ссылк[аи] в (?:профиле|описании)|переходите|пишите в (?:личку|лс)|донат", re.I)
URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:ru|com|me|io|org|net|рф|su|info|pro|app)(?:/\S*)?", re.I)
CALL = re.compile(r"\b(?:покупайте|продавайте|докупайте|шортите|входите|заходите|успейте|не упустите|пора (?:покупать|продавать|входить)|"
                  r"рекомендую (?:купить|продать|покупать|продавать)|советую (?:купить|продать)|гарантир\w*|без риска для вас)\b", re.I)
POLIT = re.compile(r"\b(?:выбор\w*|президент\w*|санкц\w* против|войн\w*|религ\w*)\b", re.I)
PULSE_RULES = ("Правила Пульса, которые соблюдают черновики (по «Правилам модерации сообщества»):\n"
               "• п. 3.8 и 3.16: никаких ссылок на внешние ресурсы и рекламы своих площадок — ни t.me, ни названия канала, "
               "ни «ссылка в профиле», ни QR-кодов; ссылки из текста бот вычищает сам;\n"
               "• п. 3.3: без призывов купить/продать («покупайте», «успейте», «пора входить») и обещаний доходности; "
               "в каждом посте — «Не является индивидуальной инвестиционной рекомендацией»;\n"
               "• п. 3.5: цифры только из данных Мосбиржи и моих сделок, новости — с источником, слухи помечены;\n"
               "• п. 3.10: без политики и религии;\n"
               "• не больше 10 бумаг ($тикеров) в одном посте — лишние строки бот убирает сам.\n"
               "Нарушение — удаление поста и блокировка профиля от 1 дня до 1 года.")


def _items(s, sep):
    """Разбить перечисление по sep; куски без $тикера («вверх», «неясно») приклеить к предыдущему пункту."""
    out = []
    for k, part in enumerate(s.split(sep)):
        if out and not TICK.search(part):
            out[-1] += sep + part
        else:
            out.append(part)
    return out


def limit_tickers(text, n=MAX_TICKERS):
    """Оставить в посте не больше n разных $тикеров. Строка, где все бумаги помещаются, — целиком; в перечислениях
    («$A 1%, вверх; $B 2%…» или «$A, $B, $C») лишние пункты убираются; если не помещается первый пункт (в нём начало
    фразы) — строка удаляется. Возвращает (текст, убранные бумаги)."""
    allowed, dropped, out = [], [], []

    def fits(seg):
        nw = [t for t in dict.fromkeys(TICK.findall(seg)) if t not in allowed]
        return len(allowed) + len(nw) <= n, nw

    for line in text.split("\n"):
        ok, nw = fits(line)
        if ok:
            allowed += nw
            out.append(line)
            continue
        end = "." if line.rstrip().endswith(".") else ""
        body = line.rstrip()[:-1] if end else line.rstrip()
        kept, first_lost = [], False
        for i1, big in enumerate(_items(body, "; ")):
            ok, nw = fits(big)
            if ok:
                allowed += nw
                kept.append((big, "; "))
                continue
            subs = _items(big, ", ")
            sk = []
            for i2, sm in enumerate(subs):
                ok, nw = fits(sm)
                if ok:
                    allowed += nw
                    sk.append(sm)
                else:
                    dropped += [t for t in nw if t not in dropped]
                    if i1 == 0 and i2 == 0:
                        first_lost = True
            if sk and not first_lost:
                kept.append((", ".join(sk), "; "))
            if first_lost:
                break
        if first_lost or not kept:
            for t in TICK.findall(line):
                if t not in allowed and t not in dropped:
                    dropped.append(t)
            continue
        out.append("; ".join(k for k, _ in kept) + end)
    txt = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return txt, dropped


def pulse_check(text):
    """Последняя проверка черновика по правилам Пульса: ссылки (вычищаются), лимит бумаг (срезается), реклама площадок,
    призывы и политика (предупреждение автору). Возвращает (текст, замечания для автора)."""
    notes = []
    urls = URL.findall(text)
    if urls:
        text = "\n".join(re.sub(r"\s*[:—-]?\s*$", "", URL.sub("", ln)) if URL.search(ln) else ln for ln in text.split("\n"))
        notes.append("убрал ссылки (п. 3.16 — ссылки на внешние ресурсы запрещены): " + ", ".join(urls[:3]))
    text, dropped = limit_tickers(text)
    if dropped:
        notes.append(f"убрал бумаги сверх лимита Пульса ({MAX_TICKERS}): " + ", ".join(dropped))
    lines = [ln for ln in text.split("\n") if DISCLAIMER not in ln]
    for rx, what in ((PROMO, "похоже на рекламу своей площадки (п. 3.8)"), (CALL, "похоже на призыв к сделке или обещание (п. 3.3)"),
                     (POLIT, "политика/религия (п. 3.10)")):
        bad = [ln for ln in lines if rx.search(ln)]
        if bad:
            notes.append(f"⚠️ {what} — поправьте перед публикацией: «{bad[0][:80]}»")
    if DISCLAIMER not in text:
        text = text.rstrip() + "\n\n" + DISCLAIMER
    return text, notes


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
    """Только идеи, опубликованные в Telegram-канале (черновики и отменённые сюда не попадают)."""
    try:
        cur = json.load(open(PW_STATE, encoding="utf-8")).get("ideas") or {}
    except Exception:  # noqa: BLE001
        return {}
    return {k: v for k, v in cur.items() if v.get("msg") or v.get("t")}


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


KB = {"keyboard": [[{"text": "🧭 Рынок днём"}, {"text": "🌙 Вечер: идеи и завтра"}],
                   [{"text": "📊 Итоги месяца"}, {"text": "🧾 Последние сделки"}],
                   [{"text": "✍️ Свой текст → пост"}],
                   [{"text": "🏆 Вся статистика"}, {"text": "📏 Правила Пульса"}]], "resize_keyboard": True}


def say(chat, text, kb=None):
    return tg("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **({"reply_markup": kb} if kb else {})})


def deliver(title, text, imgs=()):
    """Черновик всем администраторам: картинки, затем текст поста одним блоком (нажать — скопируется).
    Перед отправкой — проверка по правилам Пульса (не больше 10 бумаг, без рекламы канала)."""
    text, notes = pulse_check(text)
    nt = len(set(TICK.findall(text)))
    for a in ADMINS:
        say(a, f"📝 <b>Черновик для Пульса: {html.escape(title)}</b>\nСохраните картинку → в Пульсе «Новый пост» → прикрепите её → вставьте текст. "
               "Тикеры после вставки проверьте: Пульс превращает их в ссылки, если выбрать тикер из подсказки после «$». "
               f"Бумаг в посте: {nt} из {MAX_TICKERS}." + "".join("\n" + html.escape(x) for x in notes))
        for p in imgs:
            if p and os.path.exists(p):
                tg_photo(a, p)
        say(a, "<pre>" + html.escape(text) + "</pre>")
    log("черновик:", title)


# ---------------- расчёты по сделкам ----------------
def outcome(v):
    """(% к цене входа, R, тип) — как в #итоги_идей канала: итог автора при закрытии, иначе по уровням (ideas_calc.py)."""
    r = IC.result(v)
    return r["pct"], (r["R"] if r["R"] is not None else 0.0), r["kind"]


def days(v):
    a, b = v.get("t") or 0, v.get("closed_t") or time.time()
    return max(1, round((b - a) / 86400)) if a else None


def side_w(v, short=False):
    return ("Л" if v["side"] > 0 else "Ш") if short else ("ЛОНГ" if v["side"] > 0 else "ШОРТ")


def dm(ts, year=False):
    return time.strftime("%d.%m.%Y" if year else "%d.%m", time.gmtime(int(ts) + MSK)) if ts else ""


def plural(n, one, few, many):
    n10, n100 = n % 10, n % 100
    return one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many


def Rs(x, d=1):
    return f"{'+' if x >= 0 else '−'}{fmt(abs(x), d)}R"


def post_url(v):
    """Ссылка на пост с идеей в канале — доказательство, что идея вышла до входа (только для публичного канала и режима «ссылка в тексте»)."""
    return ""  # ссылки на Telegram в Пульсе запрещены (реклама канала)


def published(v):
    return f"Идея опубликована {dm(v['t'])} в {time.strftime('%H:%M', time.gmtime(int(v['t']) + MSK))} МСК" if v.get("t") else ""


def model_line(v):
    """Что сказала модель при проверке идеи перед публикацией."""
    pl = v.get("plan") or {}
    if pl.get("p") is None:
        return ""
    t = f"🤖 Проверка моделью перед публикацией: вероятность цели раньше стопа ~{pl['p']:.0%} (по истории бумаги и модели аномалий)"
    an = pl.get("anom") or {}
    if an.get("against"):
        t += "; модель предупредила: риск резкого движения против сделки повышен"
    elif an.get("with"):
        t += "; модель ждёт резкое движение в сторону идеи"
    elif an.get("lvl"):
        t += f"; вероятность аномалии по бумаге — {an['lvl']}"
    return t + "."


def record_line():
    rec = IC.record(ideas())
    if not rec:
        return ""
    since = f" с {dm(rec['first_t'], True)}" if rec.get("first_t") else ""
    return (f"📒 Все мои закрытые идеи{since}: {rec['n']}, в плюс {rec['wins']}, в минус {rec['losses']}, сумма {Rs(rec['sum_R'])}"
            + (f" (в среднем {Rs(rec['avg_R'], 2)} на сделку)" if rec.get("avg_R") is not None else "") + ".")


FEAT = {
    "idea": "Каждую идею перед публикацией проверяет моя модель: вероятность цели по многолетней истории бумаги и риск резкого движения против сделки.",
    "plan": "К каждой идее — план: частичные тейки, перенос стопа в безубыток после первого тейка, уровень подтверждения.",
    "honest": "Публикую все сделки, включая убыточные: идея выходит до входа, итог — после закрытия, статистика открыта.",
    "anom": "Моя модель аномалий (данные Мосбиржи с 2008 года) замечает резкие движения и всплески оборота за минуты.",
    "news": "Новости — только официальные источники и агентства; слухи помечены как слухи, с историей их подтверждения.",
}
SHORT_FEAT = ""


def footer(tags=""):
    """Хэштеги и дисклеймер. Ссылок и упоминаний Telegram-канала нет: в Пульсе это реклама, такие посты удаляют."""
    return "\n\n" + "\n".join(x for x in (tags, DISCLAIMER) if x)


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


def bars_for(t):
    """Дневные свечи: акция TQBR, иначе — сырьё/валюта (непрерывный фьючерс FORTS)."""
    try:
        B = share_candles(t, 260)
        if B:
            return B
    except Exception as e:  # noqa: BLE001
        log("свечи", t, e)
    try:
        from common import FUT, fut_continuous
        a = next((k for k in FUT if k.lower() == t.lower()), None)
        return fut_continuous(a, 260) if a else []
    except Exception as e:  # noqa: BLE001
        log("свечи фьючерса", t, e)
        return []


def chart(v, iid, title, show_levels=True):
    bars = bars_for(v["ticker"])
    if not bars:
        return None
    lines = []
    if show_levels:
        s0 = v.get("stop0") or (v["entry"] * (1 - v["side"] * v["risk_pct"] / 100) if v.get("risk_pct") else v["stop"])
        lines = [(v["entry"], BLUE, "Вход", "--"), (s0, DN, "Стоп", "-")]
        lines += [(x, UP, f"Т{i + 1}", "-") for i, x in enumerate(v["takes"])]
    os.makedirs(OUT, exist_ok=True)
    return candle_chart(bars, os.path.join(OUT, f"{iid}_{int(time.time())}.png"), title, lines=lines, n=90)


def bottom_line():
    return None  # подпись канала на картинке — тоже реклама канала по правилам Пульса


# ---------------- черновики ----------------
def d_teaser(iid, v):
    pl = v.get("plan") or {}
    pot = abs(v["takes"][-1] - v["entry"]) / v["entry"]
    L = [f"🆕 Новая идея: ${v['ticker']} — {side_w(v)}", "",
         f"Потенциал ~{pc(pot, 1, False)}" + (f", срок обычно {pl['term'][0]}–{pl['term'][1]} торг. дн." if pl.get("term") else "")
         + f", прибыль/риск {fmt(v.get('rr'))}.", *([model_line(v)] if model_line(v) else []),
         "Итог сделки опубликую здесь, каким бы он ни был.",
         "", FEAT["honest"] if model_line(v) else FEAT["idea"]]
    text = "\n".join(L) + footer("#идея #" + v["ticker"])
    p = chart(v, iid, f"{v['ticker']} · новая идея · {side_w(v)}", show_levels=False)
    if p:
        stamp(p, f"{v['ticker']} · {side_w(v)} · потенциал {pc(pot, 1, False)}", UP if v["side"] > 0 else DN, bottom_line())
    deliver(f"тизер идеи #{iid} ${v['ticker']}", text, [p])


def d_open(iid, v):
    L = [f"💡 Моя идея: ${v['ticker']} — {side_w(v)}", "",
         f"Вход {fmt(v['entry'])} · стоп {fmt(v['stop'])} · тейки {' / '.join(fmt(x) for x in v['takes'])}",
         f"Риск {fmt(v.get('risk_pct'))}%, прибыль/риск {fmt(v.get('rr'))}."]
    pl = v.get("plan") or {}
    if pl.get("rules"):
        L.append("План: " + "; ".join(pl["rules"][:2]))
    if model_line(v):
        L.append(model_line(v))
    if v.get("comment"):
        L += ["", v["comment"]]
    pub = published(v)
    if pub:
        L += ["", pub + (f": {post_url(v)}" if post_url(v) else ".")]
    L += ["", FEAT["plan"]]
    p = chart(v, iid, f"{v['ticker']} · {side_w(v)} · вход {fmt(v['entry'])}")
    if p:
        stamp(p, f"{v['ticker']} · {side_w(v)} · вход {fmt(v['entry'])}", UP if v["side"] > 0 else DN, bottom_line())
    deliver(f"открытая идея #{iid} ${v['ticker']}", "\n".join(L) + footer("#идея #" + v["ticker"]), [p])


def d_take(iid, v, i):
    E = v["entry"]
    mv = (v["takes"][i] - E) * v["side"] / E
    L = [f"✅ ${v['ticker']}: тейк {i + 1} из {len(v['takes'])} взят — {pc(mv, 1)} от входа", "",
         f"{side_w(v)} от {fmt(E)}, тейк {fmt(v['takes'][i])}" + (f", в сделке {days(v)} дн." if days(v) else "")
         + (f" Идея опубликована {dm(v['t'])} — до входа." if v.get("t") else "")]
    if i + 1 < len(v["takes"]):
        L.append("Остаток позиции держу" + (", стоп — в безубытке, сделка уже без риска." if v.get("be") else "."))
    L += ["", FEAT["plan"]]
    p = chart(v, iid, f"{v['ticker']} · тейк {i + 1} взят")
    if p:
        stamp(p, f"{v['ticker']} · тейк {i + 1} · {pc(mv, 1)}", UP, bottom_line())
    deliver(f"тейк {i + 1} по ${v['ticker']}", "\n".join(L) + footer("#сделка #" + v["ticker"]), [p])


def d_close(iid, v):
    pct, R, kind = outcome(v)
    good = pct >= 0
    hit = set(v.get("hit") or [])
    L = [f"{'🏁' if good else '⛔️'} Итог сделки ${v['ticker']} ({side_w(v)}): {pc(pct, 1)}, {Rs(R)}", "",
         f"Вход {fmt(v['entry'])}, тейки {' / '.join(('✅' if j in hit else '') + fmt(x) for j, x in enumerate(v['takes']))} ({kind})."]
    if v.get("result"):
        L.append(f"Мой итог при закрытии: {v['result']}")
    if v.get("t"):
        L.append(f"Идея опубликована {dm(v['t'])}, закрыта {dm(v.get('closed_t'))}" + (f" — {days(v)} дн. в сделке" if days(v) else "")
                 + (f": {post_url(v)}" if post_url(v) else "."))
    if not good:
        L.append("Убыточные сделки публикую так же, как прибыльные: без этого статистика ничего не стоит.")
    rl = record_line()
    if rl:
        L += ["", rl]
    L += ["", FEAT["honest"]]
    p = chart(v, iid, f"{v['ticker']} · итог сделки")
    if p:
        stamp(p, f"{v['ticker']} · {side_w(v)} · {pc(pct, 1)} · {Rs(R)}", UP if good else DN, bottom_line())
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
    L = [f"📊 Мои сделки за {MONTHS[m - 1]} {y}: {wins} из {len(rows)} в плюс, сумма {pc(tot, 1)} ({Rs(sR)})", ""]
    for iid, v, pct, R, kind in rows:
        L.append(f"{'✅' if pct > 0 else '➖' if abs(pct) < 1e-9 else '❌'} ${v['ticker']} {side_w(v)} {pc(pct, 1)} ({kind}{', ' + str(days(v)) + ' дн.' if days(v) else ''})")
    L.append("\nСумма — простая сумма результатов сделок в % к цене входа, без плеча и комиссий; убыточные сделки тоже в списке. "
             "Результат — мой итог при закрытии идеи, если процента нет — по уровням.")
    rl = record_line()
    if rl:
        L += ["", rl]
    L += ["", FEAT["honest"]]
    os.makedirs(OUT, exist_ok=True)
    p = hbars([(f"{v['ticker']} {side_w(v, True)}", pct * 100, None) for _, v, pct, _, _ in rows], os.path.join(OUT, f"month_{key}.png"),
              f"Сделки за {MONTHS[m - 1]} {y}: {wins} из {len(rows)} в плюс", xlabel="результат, % к цене входа", fmt_v=lambda x: f"{x:+.1f}%".replace(".", ","))
    if p:
        stamp(p, f"{MONTHS[m - 1].capitalize()} {y}: {pc(tot, 1)} · {wins}/{len(rows)} в плюс", UP if tot >= 0 else DN, bottom_line())
    deliver(f"итоги за {MONTHS[m - 1]} {y}", "\n".join(L) + footer("#итоги"), [p])
    return True


def d_record():
    """Вся статистика закрытых идей канала с первой сделки — главный аргумент доверия."""
    rec = IC.record(ideas())
    if not rec:
        return None
    rows = sorted(rec["rows"], key=lambda z: int(z[1].get("closed_t") or z[1].get("t") or 0))
    since = f" с {dm(rec['first_t'], True)}" if rec.get("first_t") else ""
    L = [f"📒 Все мои сделки{since}", "",
         f"Закрыто идей: {rec['n']} · в плюс {rec['wins']} · в минус {rec['losses']} · сумма {pc(rec['sum_pct'], 1)} ({Rs(rec['sum_R'])})"
         + (f", в среднем {Rs(rec['avg_R'], 2)} на сделку" if rec.get("avg_R") is not None else "") + ".", ""]
    for iid, v, r in rows[-12:]:  # бумаги сверх 10 срежет pulse_check (правило Пульса)
        L.append(f"{'✅' if r['pct'] > 0 else '➖' if abs(r['pct']) < 1e-9 else '❌'} {dm(v.get('closed_t'))} ${v['ticker']} {side_w(v)} {pc(r['pct'], 1)}")
    if len(rows) > 12:
        L.append(f"…и ещё {len(rows) - 12} — на картинке накопленный результат по всем.")
    L += ["", "R — результат в рисках сделки (расстояние от входа до стопа). Без плеча и комиссий.", "", FEAT["honest"], FEAT["idea"]]
    os.makedirs(OUT, exist_ok=True)
    acc, pts = 0.0, []
    for iid, v, r in rows:
        acc += r["R"] or 0
        pts.append((f"{dm(v.get('closed_t'))} {v['ticker']}", acc, None))
    p = hbars(pts[-15:], os.path.join(OUT, f"record_{int(time.time())}.png"), "Накопленный результат сделок, R", xlabel="сумма R с первой идеи",
              fmt_v=lambda x: f"{x:+.1f}R".replace(".", ","))
    if p:
        stamp(p, f"{rec['n']} {plural(rec['n'], 'сделка', 'сделки', 'сделок')} · {rec['wins']} в плюс · {Rs(rec['sum_R'])}", UP if rec["sum_R"] >= 0 else DN, bottom_line())
    deliver("вся статистика сделок", "\n".join(L) + footer("#итоги #статистика"), [p])
    return True


# ---------------- данные рынка ----------------
SECT_IX = [("MOEXOG", "нефть и газ"), ("MOEXFN", "финансы"), ("MOEXMM", "металлы и добыча"), ("MOEXCN", "потребительский"),
           ("MOEXEU", "электроэнергетика"), ("MOEXTL", "телеком"), ("MOEXCH", "химия"), ("MOEXTN", "транспорт"), ("MOEXRE", "недвижимость"),
           ("MOEXIT", "IT")]


def j(name):
    """Файл состояния канала: посты по расписанию (CHANNEL_STATE_DIR) или новостной бот (STATE_DIR/channel).
    Если файл есть в обеих папках — берём более свежий (раньше брался первый найденный, даже вчерашний)."""
    best, key = {}, ""
    for base in (STATE_DIR, os.path.join(os.environ.get("STATE_DIR", ""), "channel")):
        try:
            d = json.load(open(os.path.join(base, name), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        k = str(d.get("made") or d.get("time") or d.get("date") or "") if isinstance(d, dict) else ""
        if not best or k > key:
            best, key = d, k
    return best


def today():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + MSK))


def market():
    """Индекс, секторы, лидеры по 60 самым ликвидным акциям — с Мосбиржи (ISS)."""
    out = {"ix": {}, "sect": [], "md": []}
    try:
        ixm = {r["SECID"]: r for r in table(iss("engines/stock/markets/index/securities.json", **{"iss.only": "marketdata",
                                                 "marketdata.columns": "SECID,CURRENTVALUE,LASTCHANGEPRC,UPDATETIME,TRADEDATE"}), "marketdata")}
        out["ix"] = ixm.get("IMOEX") or {}
        out["sect"] = [(n, ixm[s]["LASTCHANGEPRC"] / 100) for s, n in SECT_IX if (ixm.get(s) or {}).get("LASTCHANGEPRC") is not None]
    except Exception as e:  # noqa: BLE001
        log("индексы", e)
    try:
        md = table(iss("engines/stock/markets/shares/boards/TQBR/securities.json", **{"iss.only": "marketdata",
                       "marketdata.columns": "SECID,LAST,LASTTOPREVPRICE,VALTODAY"}), "marketdata")
        out["md"] = sorted([r for r in md if r.get("VALTODAY") and r.get("LAST")], key=lambda r: -r["VALTODAY"])[:60]
    except Exception as e:  # noqa: BLE001
        log("акции", e)
    return out


def last_prices(tks):
    try:
        md = table(iss("engines/stock/markets/shares/boards/TQBR/securities.json", securities=",".join(tks),
                       **{"iss.only": "marketdata", "marketdata.columns": "SECID,LAST,LCURRENTPRICE"}), "marketdata")
        return {r["SECID"]: r.get("LAST") or r.get("LCURRENTPRICE") for r in md if r.get("LAST") or r.get("LCURRENTPRICE")}
    except Exception as e:  # noqa: BLE001
        log("цены идей", e)
        return {}


def trend_line():
    """Индекс относительно средних за 20 и 50 дней — без прогнозов, только положение."""
    try:
        B = index_candles("IMOEX", 120)
        c = [b["c"] for b in B]
        if len(c) < 50:
            return ""
        m20, m50 = sum(c[-20:]) / 20, sum(c[-50:]) / 50
        w = (f"выше средних за 20 и 50 дней — краткосрочный тренд вверх" if c[-1] > m20 > m50 else
             f"ниже средних за 20 и 50 дней — краткосрочный тренд вниз" if c[-1] < m20 < m50 else
             f"между средними за 20 ({fmt(m20, 0)}) и 50 дней ({fmt(m50, 0)}) — без явного тренда")
        return f"Индекс {w}; за 5 дней {pc(c[-1] / c[-6] - 1, 1)}."
    except Exception as e:  # noqa: BLE001
        log("тренд индекса", e)
        return ""


def dir_word(r):
    pu = r.get("p_dir_up")
    if pu is None:
        up, dn = r.get("p_y_up") or 0, r.get("p_y_dn") or 0
        pu = up / (up + dn) if up + dn > 0.01 else 0.5
    return "направление неясно" if abs(pu - 0.5) < 0.1 else (f"скорее вверх ({pu:.0%})" if pu > 0.5 else f"скорее вниз ({1 - pu:.0%})")


# ---------------- пост 1: рынок днём ----------------
def d_market():
    mk = market()
    ix, md = mk["ix"], mk["md"]
    if not ix and not md:
        return None
    L = [f"🧭 Рынок к середине дня · {time.strftime('%d.%m', time.gmtime(time.time() + MSK))}", ""]
    if ix.get("CURRENTVALUE"):
        L.append(f"Индекс МосБиржи {fmt(ix['CURRENTVALUE'], 0)} ({pc((ix.get('LASTCHANGEPRC') or 0) / 100, 2)})"
                 + (f", оборот 60 крупнейших акций {fmt(sum(r['VALTODAY'] for r in md) / 1e9, 1)} млрд ₽." if md else "."))
    if mk["sect"]:
        sc = sorted(mk["sect"], key=lambda x: -x[1])
        L.append(f"Сильнее рынка — {sc[0][0]} ({pc(sc[0][1], 1)}), слабее — {sc[-1][0]} ({pc(sc[-1][1], 1)}).")
    if md:
        up = [r for r in sorted(md, key=lambda r: -(r.get("LASTTOPREVPRICE") or 0))[:2] if (r.get("LASTTOPREVPRICE") or 0) > 0]
        dn = [r for r in sorted(md, key=lambda r: (r.get("LASTTOPREVPRICE") or 0))[:2] if (r.get("LASTTOPREVPRICE") or 0) < 0]
        parts = ([("Растут: " + ", ".join(f"${r['SECID']} {pc(r['LASTTOPREVPRICE'] / 100, 1)}" for r in up))] if up else []) + \
                ([("падают: " if up else "Падают: ") + ", ".join(f"${r['SECID']} {pc(r['LASTTOPREVPRICE'] / 100, 1)}" for r in dn)] if dn else [])
        if parts:
            L.append("; ".join(parts) + ".")
    fl = j("flows_last.json")
    if (fl.get("time") or "")[:10] == today() and (fl.get("buyers") or fl.get("sellers")):
        L += ["", "⚖️ Куда идут деньги (перевес покупок/продаж по рынку): покупают " + ", ".join(f"${t}" for t, _ in (fl.get("buyers") or [])[:2])
              + "; продают " + ", ".join(f"${t}" for t, _ in (fl.get("sellers") or [])[:2]) + "."]
    an = j("anomaly.json")
    sent = (an.get("sent") or {}) if an.get("date") == today() else {}
    if sent:
        L.append("⚡️ Аномалии сегодня (резкое движение или всплеск оборота): "
                 + ", ".join(f"${t} {pc(x[1], 1)}" if isinstance(x, list) and len(x) > 1 and x[1] is not None else f"${t}" for t, x in list(sent.items())[:3]) + ".")
    pre = j("anom_today.json")
    if pre.get("date") == today() and pre.get("rows"):
        rows = pre["rows"]
        top = [t for t in sorted(rows, key=lambda t: -(rows[t].get("p_y") or 0))[:3] if t not in sent]
        if top:
            L.append("🔮 Моя модель утром выделила (ещё не сработали): " + ", ".join(f"${t} {rows[t]['p_y']:.0%}, {dir_word(rows[t])}" for t in top) + ".")
    L += ["", FEAT["anom"], FEAT["news"]]
    p = None
    if mk["sect"]:
        os.makedirs(OUT, exist_ok=True)
        p = hbars([(n.capitalize(), v * 100, None) for n, v in sorted(mk["sect"], key=lambda x: -x[1])], os.path.join(OUT, f"noon_{int(time.time())}.png"),
                  "Секторы Мосбиржи сегодня, %", fmt_v=lambda x: f"{x:+.1f}%".replace(".", ","))
        if p:
            stamp(p, f"IMOEX {pc((ix.get('LASTCHANGEPRC') or 0) / 100, 2)} · середина дня", UP if (ix.get("LASTCHANGEPRC") or 0) >= 0 else DN, bottom_line())
    deliver("рынок днём", "\n".join(L) + footer("#рынок #IMOEX"), [p])
    return True


# ---------------- пост 2: вечер — открытые идеи и взгляд на завтра ----------------
def d_evening():
    I = ideas()
    op = {k: v for k, v in I.items() if not v.get("closed")}
    closed_today = {k: v for k, v in I.items() if v.get("closed") and dm(v.get("closed_t"), True) == dm(time.time(), True)}
    px = last_prices(sorted({v["ticker"] for v in op.values()})) if op else {}
    L = [f"🌙 Мои идеи и рынок на завтра · {time.strftime('%d.%m', time.gmtime(time.time() + MSK))}", ""]
    rows = []
    if op:
        L.append(f"📂 Мои открытые идеи ({len(op)}):")
        shown = 0
        for iid, v in sorted(op.items(), key=lambda kv: int(kv[0])):
            p_ = px.get(v["ticker"])
            if not p_:
                continue
            shown += 1
            if shown > 5:  # правило Пульса — до 10 бумаг в посте: остальные идеи только на картинке
                rows.append((f"{v['ticker']} {side_w(v, True)}", (p_ - v["entry"]) * v["side"] / v["entry"] * 100, None))
                continue
            mv = (p_ - v["entry"]) * v["side"] / v["entry"]
            hit = len(v.get("hit") or [])
            st = []
            if hit:
                st.append(f"тейков {hit} из {len(v['takes'])}")
            if v.get("be"):
                st.append("стоп в безубытке")
            L.append(f"{'🟢' if mv > 0 else '🔴' if mv < 0 else '⚪️'} ${v['ticker']} {side_w(v)}: {pc(mv, 1)} от входа"
                     + (f", {days(v)} дн." if days(v) else "") + (f" ({', '.join(st)})" if st else ""))
            rows.append((f"{v['ticker']} {side_w(v, True)}", mv * 100, None))
        if shown > 5:
            L.append(f"…и ещё {shown - 5} — на картинке.")
        if rows:
            L.append(f"Средний результат открытых идей: {pc(sum(r[1] for r in rows) / len(rows) / 100, 1)} (цена на {time.strftime('%H:%M', time.gmtime(time.time() + MSK))} МСК, без комиссий).")
    else:
        L.append("📂 Открытых идей сейчас нет — новые публикую до входа.")
    if closed_today:
        L += ["", "🏁 Закрыты сегодня:"]
        for iid, v in closed_today.items():
            pct, R, kind = outcome(v)
            L.append(f"{'✅' if pct > 0 else '❌' if pct < 0 else '➖'} ${v['ticker']} {side_w(v)}: {pc(pct, 1)}, {Rs(R)} ({kind})")
    rl = record_line()
    if rl:
        L += ["", rl]
    # взгляд на завтра — только факты и модель, без рекомендаций
    mk = market()
    ix = mk["ix"]
    L += ["", "🔭 На завтра:"]
    if ix.get("CURRENTVALUE"):
        L.append(f"Индекс МосБиржи {fmt(ix['CURRENTVALUE'], 0)} ({pc((ix.get('LASTCHANGEPRC') or 0) / 100, 2)} за день). " + trend_line())
    fl = j("flows_last.json")
    if (fl.get("time") or "")[:10] == today() and fl.get("buyers"):
        L.append("К закрытию деньги шли в " + ", ".join(f"${t}" for t, _ in fl["buyers"][:1]) + "; из " + ", ".join(f"${t}" for t, _ in (fl.get("sellers") or [])[:1]) + ".")
    nx = j("anom_next.json")
    if nx.get("rows") and (nx.get("made") or "")[:10] == today():
        r_ = nx["rows"]
        top = sorted(r_, key=lambda t: -(r_[t].get("p_y") or 0))[:3]
        L.append("Модель аномалий ждёт резкое движение или всплеск оборота" + ("" if nx.get("final") else " (предварительно — торги дня ещё идут, утром уточнится)")
                 + ": " + "; ".join(f"${t} {r_[t]['p_y']:.0%}, {dir_word(r_[t])}" for t in top) + ".")
    L += ["", FEAT["honest"]]
    p = None
    os.makedirs(OUT, exist_ok=True)
    if rows:
        p = hbars(rows, os.path.join(OUT, f"eve_{int(time.time())}.png"), "Открытые идеи: результат от входа, %", fmt_v=lambda x: f"{x:+.1f}%".replace(".", ","))
        if p:
            avg = sum(r[1] for r in rows) / len(rows) / 100
            stamp(p, f"Открытые идеи · {len(rows)} · в среднем {pc(avg, 1)}", UP if avg >= 0 else DN, bottom_line())
    else:
        try:
            p = candle_chart(index_candles("IMOEX", 260), os.path.join(OUT, f"eve_ix_{int(time.time())}.png"), "Индекс МосБиржи · дневной график", n=90, dec=0)
            if p:
                stamp(p, f"IMOEX {pc((ix.get('LASTCHANGEPRC') or 0) / 100, 2)} · итоги дня", UP if (ix.get("LASTCHANGEPRC") or 0) >= 0 else DN, bottom_line())
        except Exception as e:  # noqa: BLE001
            log("график индекса", e)
    deliver("вечер: идеи и взгляд на завтра", "\n".join(L) + footer("#идеи #рынок"), [p])
    return True


def d_custom(text):
    tks = TICK.findall(text)
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
NOON, EVE = (13, 5), (19, 45)  # МСК: пост о рынке и вечерний пост (будни)


def check_schedule():
    """Ровно два черновика в торговый день: ~13:05 — рынок, ~19:45 — открытые идеи и взгляд на завтра."""
    t = time.gmtime(time.time() + MSK)
    if t.tm_wday >= 5:
        return
    d, hm = today(), (t.tm_hour, t.tm_min)
    day = ST.setdefault("daily", {})
    if day.get("d") != d:
        day.clear()
        day["d"] = d
    try:
        if NOON <= hm < (16, 0) and not day.get("noon"):
            day["noon"] = True
            save()
            d_market()
        if EVE <= hm < (23, 30) and not day.get("eve"):
            day["eve"] = True
            save()
            d_evening()
    except Exception as e:  # noqa: BLE001
        log("плановый пост", e)
    save()


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
        return say(m["chat"]["id"], "Это служебный бот автора: он готовит посты для Пульса.")
    if ST["wiz"].get(str(uid)) == "custom" and text and not text.startswith(("/", "📊", "🧾", "🗞", "🧭", "🌙", "✍️", "🔗", "📏", "🏆")):
        ST["wiz"].pop(str(uid), None)
        save()
        return d_custom(text)
    if text in ("/start", "/menu", "меню"):
        return say(uid, "Готовлю посты для Пульса с картинками и результатами ваших сделок — по правилам Пульса (до 10 бумаг, без рекламы канала). "
                        "Сделки беру из ваших идей в Telegram-канале. Сам присылаю 2 черновика в торговый день: ~13:05 — рынок в целом, "
                        "~19:45 — результаты открытых идей и взгляд на завтра. Остальное — кнопками.", KB)
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
    elif text.startswith("🏆"):
        if not d_record():
            say(uid, "Закрытых идей пока нет — статистика появится после первой закрытой идеи в канале.")
    elif text.startswith(("🧭", "🗞")):
        if not d_market():
            say(uid, "Нет данных Мосбиржи — попробуйте позже.")
    elif text.startswith("🌙"):
        d_evening()
    elif text.startswith("✍️"):
        ST["wiz"][str(uid)] = "custom"
        save()
        say(uid, "Пришлите текст поста (тикеры — как $SBER). Добавлю график первого тикера, хэштеги и дисклеймер; проверю правила Пульса (до 10 бумаг, без рекламы канала).")
    elif text.startswith(("📏", "🔗")):
        say(uid, html.escape(PULSE_RULES), KB)
    else:
        say(uid, "Выберите действие в меню.", KB)


def on_callback(q):
    uid = (q.get("from") or {}).get("id")
    tg("answerCallbackQuery", {"callback_query_id": q["id"]})
    if uid not in ADMINS:
        return
    d = q.get("data") or ""
    if d.startswith("pm:"):
        return say(uid, html.escape(PULSE_RULES))
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
    import hotreload
    hot = hotreload.Watcher()
    while True:
        if hot.changed():  # bot.js подтянул новый код/модели с GitHub — продолжаем на новой версии
            save()
            hotreload.restart(log, {"PULSE_RUN_MIN": round(max(1.0, (stop_at - time.time()) / 60), 1)} if stop_at else None)
        if time.time() - last > 60:
            last = time.time()
            check_schedule()
            if os.environ.get("PULSE_EVENTS") == "1":  # черновики по каждому событию идеи (по умолчанию выключено: 2 поста в день)
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
