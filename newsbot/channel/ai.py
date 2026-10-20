# -*- coding: utf-8 -*-
"""ИИ-вступление к постам паблика через шлюз Claude (формат Anthropic Messages API).

Ключ берётся ТОЛЬКО из переменной окружения LLM_API_KEY (секрет GitHub), адрес — LLM_BASE_URL.
Модель пишет 2–3 предложения «Коротко» по готовому шаблонному посту. Защита от выдумок:
каждое число и каждый $ТИКЕР в ответе должны встречаться в исходном посте, иначе вступление отбрасывается
и пост уходит как раньше — шаблоном из цифр. Без ключа модуль ничего не делает.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = (os.environ.get("LLM_BASE_URL") or "").rstrip("/")
KEY = os.environ.get("LLM_API_KEY") or ""
AUTH = (os.environ.get("LLM_AUTH") or "bearer").lower()  # bearer | x-api-key
MODELS = [m for m in [os.environ.get("LLM_MODEL"), "claude-sonnet-4-5", "claude-sonnet-4-20250514", "claude-3-5-haiku-latest"] if m]
MAX_PER_DAY = int(os.environ.get("LLM_MAX_PER_DAY") or 40)
ON = bool(BASE and KEY and os.environ.get("LLM_OFF") != "1")

SYSTEM = (
    "Ты редактор русскоязычного паблика о рынке акций Мосбиржи. Тебе дают готовый пост с цифрами. "
    "Напиши вступление «Коротко»: 2–3 простых предложения (не пересказывай пост целиком: одна-две главные мысли, не больше трёх чисел), главное для частного инвестора. "
    "Строго: используй только факты и числа из поста, ничего не добавляй и не округляй по-новому; "
    "тикеры пиши как в посте ($SBER); без призывов купить или продать, без прогнозов цены, без эмодзи и хештегов, "
    "без HTML и Markdown. Не длиннее 350 знаков. Ответь только текстом вступления."
)

NUM = re.compile(r"\d+(?:[.,]\d+)?")
TK = re.compile(r"\$[A-Z0-9]{2,8}")


def _log(*a):
    print(time.strftime("%H:%M:%S"), "[ИИ]", *a, flush=True)


def _norm_nums(s):
    return {x.replace(",", ".").lstrip("0") or "0" for x in NUM.findall(s.replace(" ", "").replace(" ", ""))}


def _plain(html):
    return re.sub(r"<[^>]+>", "", html).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def check(lead, source):
    """True, если во вступлении нет чисел и тикеров, которых нет в исходном посте."""
    src = _plain(source)
    bad_n = _norm_nums(lead) - _norm_nums(src)
    bad_t = set(TK.findall(lead)) - set(TK.findall(src))
    if bad_n or bad_t:
        _log("вступление отброшено: лишние числа", sorted(bad_n)[:5], "тикеры", sorted(bad_t)[:5])
        return False
    if re.search(r"(?i)\b(покупайте|продавайте|рекомендуем|советуем|стоит купить|стоит продать)\b", lead):
        _log("вступление отброшено: похоже на рекомендацию")
        return False
    return 40 <= len(lead) <= 500


def _call(model, text, timeout=60):
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if AUTH == "x-api-key":
        headers["x-api-key"] = KEY
    else:
        headers["authorization"] = "Bearer " + KEY
    body = json.dumps({"model": model, "max_tokens": 400, "temperature": 0.3, "system": SYSTEM,
                       "messages": [{"role": "user", "content": text[:6000]}]}).encode()
    req = urllib.request.Request(BASE + "/v1/messages", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        js = json.loads(r.read())
    return "".join(b.get("text", "") for b in js.get("content", []) if b.get("type") == "text").strip()


def list_models():
    """Список моделей шлюза (GET /v1/models); пустой список, если шлюз его не отдаёт."""
    headers = {"anthropic-version": "2023-06-01"}
    if AUTH == "x-api-key":
        headers["x-api-key"] = KEY
    else:
        headers["authorization"] = "Bearer " + KEY
    try:
        req = urllib.request.Request(BASE + "/v1/models?limit=100", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            js = json.loads(r.read())
        return [m.get("id") for m in (js.get("data") or js.get("models") or []) if isinstance(m, dict) and m.get("id")]
    except Exception as e:  # noqa: BLE001
        _log("список моделей недоступен:", e)
        return []


def pick(models):
    """Предпочтение: Sonnet (качество/цена), затем Haiku, затем Opus; внутри — самая новая по имени."""
    for pat in ("sonnet", "haiku", "opus"):
        c = sorted([m for m in models if pat in m.lower()], reverse=True)
        if c:
            return c
    return models


def lead(post_html, state):
    """Вернуть проверенное вступление или None. state — словарь состояния паблика (счётчик и рабочая модель)."""
    if not ON:
        return None
    day = time.strftime("%Y-%m-%d")
    st = state.setdefault("ai", {})
    if st.get("day") != day:
        st.update(day=day, n=0)
    if st["n"] >= MAX_PER_DAY:
        _log("дневной лимит запросов исчерпан", MAX_PER_DAY)
        return None
    if not st.get("model") and not os.environ.get("LLM_MODEL") and not st.get("listed"):
        st["listed"] = 1
        found = pick(list_models())
        _log("модели шлюза:", ", ".join(found[:8]) or "—")
        if found:
            st["model"] = found[0]
            st["cands"] = found[:4]
    models = ([st["model"]] if st.get("model") else []) + [m for m in (st.get("cands") or []) + MODELS if m != st.get("model")]
    src = _plain(post_html)
    for m in models:
        try:
            st["n"] += 1
            out = _call(m, src)
        except urllib.error.HTTPError as e:
            msg = e.read()[:200].decode("utf-8", "ignore")
            _log("модель", m, "ошибка", e.code, msg)
            if e.code in (400, 404) and "model" in msg.lower():
                continue  # такой модели у шлюза нет — пробуем следующую
            return None
        except Exception as e:  # noqa: BLE001
            _log("шлюз недоступен:", e)
            return None
        st["model"] = m
        _log("модель", m, "ответила:", out[:300])
        out = re.sub(r"\s+", " ", out.replace("*", "").replace("#", "")).strip().strip("«»\"")
        out = re.sub(r"^(🧠\s*)?(Коротко|Кратко|Вступление)\s*[:—-]\s*", "", out, flags=re.I)
        return out if check(out, src) else None
    return None


def with_lead(post_html, state, esc):
    """Вставить «🧠 Коротко: …» после заголовка поста (первой строки)."""
    ld = lead(post_html, state)
    if not ld:
        return post_html
    head, _, rest = post_html.partition("\n")
    return f"{head}\n\n🧠 <i>Коротко: {esc(ld)}</i>\n\n{rest.lstrip(chr(10))}"
