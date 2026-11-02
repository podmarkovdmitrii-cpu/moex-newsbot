# -*- coding: utf-8 -*-
"""Агент-ревизор для постов паблика из jobs.py (план дня, потоки, итоги, обзоры, выходные): проверяет готовый текст перед отправкой.

Ищет ошибки вывода, а не стиля:
  • $ТИКЕР, которого нет среди акций Мосбиржи (TQBR) или фьючерсов из словаря — строка удаляется;
  • None / nan / inf / undefined в тексте — строка удаляется;
  • сигнал против собственной цели: «ЛОНГ … цель (−3%)» или «ШОРТ … цель (+3%)» — сигнал удаляется;
  • «по импульсу» против движения в той же строке (ЛОНГ по импульсу после падения и наоборот) — сигнал удаляется;
  • незакрытые HTML-теги — пост не уходит (Telegram его не примет).
Всё, что исправлено или остановлено, пишется в CHANNEL_STATE/audit_log.jsonl.
"""
import json
import os
import re
import time

STATE = os.environ.get("CHANNEL_STATE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
_TQ = {"ts": 0, "set": None}
EXTRA = {"IMOEX", "RTSI", "RGBI", "MOEXBC", "Si", "CNY", "Eu", "ED", "UCNY", "BR", "GOLD", "NG", "MIX", "RTS"}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[ревизор]", *a, flush=True)


def tqbr():
    if _TQ["set"] is not None and time.time() - _TQ["ts"] < 6 * 3600:
        return _TQ["set"]
    p = os.path.join(STATE, "tqbr_list.json")
    try:
        js = json.load(open(p, encoding="utf-8"))
        if time.time() - js["ts"] < 20 * 3600 and len(js["list"]) > 100:
            _TQ.update(ts=time.time(), set=set(js["list"]))
            return _TQ["set"]
    except Exception:  # noqa: BLE001
        pass
    try:
        from common import iss, table
        rows = table(iss("engines/stock/markets/shares/boards/TQBR/securities.json", **{"iss.only": "securities", "securities.columns": "SECID"}), "securities")
        lst = sorted({r["SECID"] for r in rows})
        if len(lst) > 100:
            os.makedirs(STATE, exist_ok=True)
            json.dump({"ts": time.time(), "list": lst}, open(p, "w", encoding="utf-8"))
            _TQ.update(ts=time.time(), set=set(lst))
            return _TQ["set"]
    except Exception as e:  # noqa: BLE001
        log("список акций недоступен:", e)
    _TQ.update(ts=time.time(), set=set())
    return _TQ["set"]


def _journal(rec):
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "audit_log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(ts=int(time.time()), agent="ревизор-паблик", **rec), ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


SIG = re.compile(r"(ЛОНГ|ШОРТ)[^\n]*?цель\s*(?:<code>)?[\d\s ,.]+(?:</code>)?\s*\(([+−-])")
MOVE = re.compile(r"\$[A-Z0-9]+</b>\s*([+−-])\d")


def audit(text):
    """Вернуть (исправленный текст, список исправлений, fatal). fatal — пост не отправлять."""
    fixes, fatal = [], None
    known = tqbr()
    lines = text.split("\n")
    out = []
    prev = ""
    tags = re.compile(r"</?(?:b|i|u|s|code|pre|a|blockquote)\b[^>]*>")

    def drop(ln):  # удаляем текст строки, но сохраняем теги, которые открывают/закрывают блоки на других строках
        kept = re.sub(r"<(b|i|u|s|code|a)\b[^>]*></\1>", "", "".join(tags.findall(ln)))
        if kept:
            out.append(kept)
    for ln in lines:
        plain = re.sub(r"<[^>]+>", "", ln)
        if re.search(r"\b(None|nan|NaN|inf|undefined)\b", plain):
            fixes.append("удалена строка с пустыми данными: " + plain[:60])
            drop(ln)
            continue
        bad = [t for t in re.findall(r"\$([A-Za-z][A-Za-z0-9]{0,6})\b", plain) if known and t not in known and t not in EXTRA]
        if bad:
            fixes.append(f"удалена строка с несуществующим тикером ${bad[0]}")
            drop(ln)
            continue
        m = SIG.search(ln)
        if m and (m.group(1) == "ЛОНГ") != (m.group(2) == "+"):
            fixes.append("удалён сигнал: цель против направления сделки — " + plain[:60])
            drop(ln)
            continue
        if "по импульсу" in ln:
            mv = MOVE.search(prev)
            if mv and (("ЛОНГ" in ln) != (mv.group(1) == "+")):
                fixes.append("удалён сигнал: «по импульсу» против движения бумаги — " + plain[:60])
                drop(ln)
                continue
        out.append(ln)
        prev = ln
    res = "\n".join(out)
    stack = []
    for m in re.finditer(r"<(/?)(b|i|u|s|code|pre|a|blockquote)\b[^>]*>", res):
        if not m.group(1):
            stack.append(m.group(2))
        elif not stack or stack.pop() != m.group(2):
            fatal = "перепутанные HTML-теги"
            break
    if stack and not fatal:
        fatal = "незакрытые HTML-теги: " + ",".join(stack)
    if len(re.sub(r"<[^>]+>", "", res).strip()) < 40:
        fatal = fatal or "после исправлений в посте почти не осталось текста"
    if fixes or fatal:
        log("; ".join(fixes + ([fatal] if fatal else [])))
        _journal(dict(fixes=fixes, fatal=fatal, head=re.sub(r"<[^>]+>", "", text.split("\n")[0])[:80]))
    return res, fixes, fatal
