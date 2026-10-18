# -*- coding: utf-8 -*-
"""Общие функции паблика: данные Мосбиржи (ISS), публикация в Telegram-канал и сообщество ВК, графики, состояние."""
import datetime as dt
import html
import io
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

MSK = dt.timezone(dt.timedelta(hours=3))
ISS = "https://iss.moex.com/iss/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
ARGS = set(sys.argv[1:])
DRY = "--dry-run" in ARGS
DEMO = "--demo" in ARGS
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
STATE = os.environ.get("CHANNEL_STATE") or os.path.join(HERE, "state")
NEWS_STATE = os.environ.get("NEWS_STATE") or os.path.join(HERE, "..", "state")
DISCLAIMER = "Не является индивидуальной инвестиционной рекомендацией."
BG, FG, GRID = "#131722", "#d1d4dc", "#2a2e39"
UP, DN, BLUE, ORANGE, VIOLET = "#26a69a", "#ef5350", "#2962ff", "#ff9800", "#b388ff"
os.makedirs(OUT, exist_ok=True)


def now_msk():
    return dt.datetime.now(MSK)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def esc(s):
    return html.escape(str(s), quote=False)


def fmt(x, d=2):
    if x is None:
        return "—"
    s = f"{x:,.{d}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def pc(x, d=1, sign=True):
    if x is None:
        return "—"
    return (f"{x * 100:+.{d}f}" if sign else f"{x * 100:.{d}f}").replace(".", ",").replace("-", "−") + "%"


def bln(x):
    return fmt(x / 1e9, 1) + " млрд ₽" if abs(x) >= 1e9 else fmt(x / 1e6, 0) + " млн ₽"


# ---------------------------------------------------------------------------------------------
# Сеть и данные ISS
# ---------------------------------------------------------------------------------------------
def http_get(url, timeout=30, tries=3, headers=None):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8", **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (k + 1))
    raise last


def iss(path, **params):
    params.setdefault("iss.meta", "off")
    url = ISS + path + ("?" + urllib.parse.urlencode(params) if params else "")
    return json.loads(http_get(url).decode("utf-8"))


def table(js, name):
    t = js.get(name) or {}
    cols = t.get("columns", [])
    return [dict(zip(cols, row)) for row in t.get("data", [])]


def candles(path, days=420, interval=24):
    frm = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows, start = [], 0
    while True:
        part = table(iss(path, interval=interval, **{"from": frm, "start": start}), "candles")
        rows += part
        if len(part) < 500:
            break
        start += len(part)
    B = [dict(t=r["begin"][:10], o=r["open"], h=r["high"], l=r["low"], c=r["close"], v=r.get("value") or r.get("volume") or 0) for r in rows if r.get("close")]
    return adjust_splits(B)


def adjust_splits(B):
    for i in range(len(B) - 1, 0, -1):
        p, b = B[i - 1], B[i]
        if not (p["c"] > 0 and b["o"] > 0):
            continue
        r = b["o"] / p["c"]
        if not (r > 3.5 or r < 1 / 3.5):
            continue
        for q in B[:i]:
            for f in ("o", "h", "l", "c"):
                q[f] *= r
    return B


def share_candles(t, days=420):
    if DEMO:
        return demo_bars(t, days)
    return candles(f"engines/stock/markets/shares/boards/TQBR/securities/{t}/candles.json", days)


def index_candles(t, days=420):
    if DEMO:
        return demo_bars(t, days)
    return candles(f"engines/stock/markets/index/securities/{t}/candles.json", days)


def currency_candles(t, days=420):
    if DEMO:
        return demo_bars(t, days)
    return candles(f"engines/currency/markets/selt/boards/CETS/securities/{t}/candles.json", days)


_HIST = {}


def demo_bars(t, days):
    """Для проверки без сети: свечи из файла исследования (до 16.10.2026) или случайное блуждание."""
    p = os.environ.get("DEMO_HIST", "/home/claude/research/data/hist.json")
    if not _HIST and os.path.exists(p):
        _HIST.update(json.load(open(p)))
    rows = (_HIST.get("sh") or {}).get(t) or (_HIST.get("ix") or {}).get(t)
    if rows:
        rows = rows[-int(days * 0.69):]
        return [dict(t=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5] if len(r) > 5 else 0) for r in rows]
    rnd = random.Random(hash(t) % 1000)
    p, out = 100.0, []
    d0 = dt.date.today() - dt.timedelta(days=days)
    for k in range(days):
        d = d0 + dt.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        o = p
        p *= 1 + rnd.gauss(0.0003, 0.012)
        out.append(dict(t=d.isoformat(), o=o, h=max(o, p) * 1.004, l=min(o, p) * 0.996, c=p, v=1e9))
    return out


def ema(a, n):
    out, e, k = [], None, 2 / (n + 1)
    for x in a:
        e = x if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def sma(a, n):
    out, s = [], 0.0
    for i, x in enumerate(a):
        s += x
        if i >= n:
            s -= a[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


# ---------------------------------------------------------------------------------------------
# Состояние (кэш GitHub Actions)
# ---------------------------------------------------------------------------------------------
def load_json(name, default, base=None):
    try:
        with open(os.path.join(base or STATE, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(name, obj):
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------------------------
# Публикация: Telegram-канал + сообщество ВК
# ---------------------------------------------------------------------------------------------
def split_text(s, n):
    parts, cur = [], ""
    for line in s.split("\n"):
        while len(line) > n:
            parts.append(cur + line[:n - len(cur)])
            line, cur = line[n - len(cur):], ""
        if len(cur) + len(line) + 1 > n:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def vk_text(s):
    s = re.sub(r'<a href="([^"]+)">([^<]*)</a>', r"\2: \1", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


class Publisher:
    """Telegram (TG_PUBLIC_CHAT) и ВК (VK_GROUP_TOKEN + VK_GROUP_ID; фото — с VK_USER_TOKEN). Без настроек — пишет в out/."""

    def __init__(self):
        self.tg_token = os.environ.get("TG_PUBLIC_TOKEN") or os.environ.get("TG_TOKEN", "")
        self.tg_chat = os.environ.get("TG_PUBLIC_CHAT", "")
        self.vk_token = os.environ.get("VK_GROUP_TOKEN", "")
        self.vk_user = os.environ.get("VK_USER_TOKEN", "")
        self.vk_group = os.environ.get("VK_GROUP_ID", "").lstrip("-")
        self.dry = DRY or not (self.tg_chat or self.vk_token)
        self.sink = open(os.path.join(OUT, "posts.txt"), "a", encoding="utf-8") if self.dry else None
        self.tg_username = self.tg_chat.lstrip("@") if self.tg_chat.startswith("@") else None

    # ---- Telegram ----
    def _tg(self, method, data=None, files=None):
        url = f"https://api.telegram.org/bot{self.tg_token}/{method}"
        if files:
            bnd = "----moex" + str(random.randint(10 ** 8, 10 ** 9))
            body = io.BytesIO()
            for k, v in (data or {}).items():
                body.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
            for k, (fn, content) in files.items():
                ct = "application/pdf" if fn.endswith(".pdf") else "image/png"
                body.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\nContent-Type: {ct}\r\n\r\n".encode())
                body.write(content)
                body.write(b"\r\n")
            body.write(f"--{bnd}--\r\n".encode())
            req = urllib.request.Request(url, data=body.getvalue(), headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
        else:
            req = urllib.request.Request(url, data=json.dumps(data or {}).encode(), headers={"Content-Type": "application/json"})
        for _ in range(4):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    res = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                res = json.loads(e.read().decode() or "{}")
            if res.get("ok"):
                time.sleep(1.2)
                return res["result"]
            ra = (res.get("parameters") or {}).get("retry_after")
            if ra:
                time.sleep(ra + 1)
                continue
            raise RuntimeError(f"Telegram {method}: {res.get('description')}")
        raise RuntimeError(f"Telegram {method}: слишком много повторов")

    def tg_link(self, msg):
        if not msg or not isinstance(msg, dict):
            return None
        mid = msg.get("message_id")
        if self.tg_username:
            return f"https://t.me/{self.tg_username}/{mid}"
        cid = str(msg.get("chat", {}).get("id", ""))
        return f"https://t.me/c/{cid[4:]}/{mid}" if cid.startswith("-100") else None

    def post(self, text, photos=None, document=None, vk_text_override=None):
        """text — HTML; photos — [(path, подпись)]; document — (path, подпись). Возвращает ссылку на пост в Telegram."""
        text = text.rstrip() + f"\n\n<i>{DISCLAIMER}</i>"
        photos = [p for p in (photos or []) if p and p[0] and os.path.exists(p[0])]
        if self.dry:
            self.sink.write("=" * 30 + " TELEGRAM " + "=" * 30 + "\n" + re.sub(r"<[^>]+>", "", text) + "\n")
            for p, cap in photos:
                self.sink.write(f"[фото {os.path.basename(p)}] {re.sub(r'<[^>]+>', '', cap)}\n")
            if document:
                self.sink.write(f"[документ {os.path.basename(document[0])}]\n")
            self.sink.write("\n")
            self.sink.flush()
            print("----- ПОСТ (без отправки) -----\n" + re.sub(r"<[^>]+>", "", text) + "\n" + "\n".join(f"[фото] {os.path.basename(p)}" for p, _ in photos), flush=True)
            return None
        link = None
        if self.tg_chat:
            try:
                first = None
                for part in split_text(text, 4000):
                    m = self._tg("sendMessage", {"chat_id": self.tg_chat, "text": part, "parse_mode": "HTML", "disable_web_page_preview": True})
                    first = first or m
                link = self.tg_link(first)
                for k in range(0, len(photos), 10):
                    chunk = photos[k:k + 10]
                    if len(chunk) == 1:
                        with open(chunk[0][0], "rb") as f:
                            self._tg("sendPhoto", {"chat_id": self.tg_chat, "caption": chunk[0][1][:1020], "parse_mode": "HTML"}, {"photo": (os.path.basename(chunk[0][0]), f.read())})
                        continue
                    media, files = [], {}
                    for j, (p, cap) in enumerate(chunk):
                        with open(p, "rb") as f:
                            files[f"p{j}"] = (os.path.basename(p), f.read())
                        media.append({"type": "photo", "media": f"attach://p{j}", "caption": cap[:1020], "parse_mode": "HTML"})
                    self._tg("sendMediaGroup", {"chat_id": self.tg_chat, "media": json.dumps(media, ensure_ascii=False)}, files)
                if document:
                    with open(document[0], "rb") as f:
                        self._tg("sendDocument", {"chat_id": self.tg_chat, "caption": document[1][:1020], "parse_mode": "HTML"}, {"document": (os.path.basename(document[0]), f.read())})
            except Exception as e:  # noqa: BLE001
                log("Telegram: ошибка", e)
        if self.vk_token and self.vk_group:
            try:
                self.vk_post(vk_text_override or text, photos, link)
            except Exception as e:  # noqa: BLE001
                log("ВК: ошибка", e)
        return link

    # ---- ВКонтакте ----
    def _vk(self, method, token, **params):
        params.update(access_token=token, v="5.199")
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request("https://api.vk.com/method/" + method, data=data)
        with urllib.request.urlopen(req, timeout=60) as r:
            js = json.loads(r.read().decode())
        if "error" in js:
            raise RuntimeError(f"VK {method}: {js['error'].get('error_code')} {js['error'].get('error_msg')}")
        time.sleep(0.4)
        return js["response"]

    def vk_photo(self, path):
        srv = self._vk("photos.getWallUploadServer", self.vk_user, group_id=self.vk_group)
        bnd = "----vk" + str(random.randint(10 ** 8, 10 ** 9))
        with open(path, "rb") as f:
            content = f.read()
        body = f"--{bnd}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{os.path.basename(path)}\"\r\nContent-Type: image/png\r\n\r\n".encode() + content + f"\r\n--{bnd}--\r\n".encode()
        req = urllib.request.Request(srv["upload_url"], data=body, headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
        with urllib.request.urlopen(req, timeout=90) as r:
            up = json.loads(r.read().decode())
        saved = self._vk("photos.saveWallPhoto", self.vk_user, group_id=self.vk_group, server=up["server"], photo=up["photo"], hash=up["hash"])[0]
        return f"photo{saved['owner_id']}_{saved['id']}"

    def vk_post(self, text, photos, tg_link):
        msg = vk_text(text)
        att = []
        if photos and self.vk_user:
            try:
                att = [self.vk_photo(p) for p, _ in photos[:10]]
            except Exception as e:  # noqa: BLE001
                log("ВК: фото не загрузились, публикую текст со ссылкой:", e)
                att = []
        if photos and not att and tg_link:
            msg += f"\n\nГрафики: {tg_link}"
        params = dict(owner_id="-" + self.vk_group, from_group=1, message=msg[:15500])
        if att:
            params["attachments"] = ",".join(att)
        self._vk("wall.post", self.vk_user if att else self.vk_token, **params)


# ---------------------------------------------------------------------------------------------
# Графики (тёмная тема как у торговых терминалов)
# ---------------------------------------------------------------------------------------------
def style(ax):
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, labelsize=8)
    ax.grid(color=GRID, linewidth=0.6)
    for s in ax.spines.values():
        s.set_color(GRID)


def candle_chart(bars, path, title, lines=None, emas=((20, BLUE, "EMA 20"), (100, ORANGE, "EMA 100")), note=None, n=110, zone=None, mark=None, dec=2):
    """lines: [(цена, цвет, подпись, стиль)]; zone: (низ, верх, цвет); mark: 'СИГНАЛ'/'ЖДЁМ'."""
    if not bars:
        return None
    closes = [b["c"] for b in bars]
    ind = {lab: ema(closes, k) for k, _, lab in emas}
    B = bars[-n:]
    off = len(bars) - len(B)
    m = len(B)
    fig = plt.figure(figsize=(10, 6.2), dpi=110, facecolor=BG)
    ax = fig.add_axes([0.07, 0.27, 0.78, 0.64])
    axv = fig.add_axes([0.07, 0.17, 0.78, 0.09], sharex=ax)
    style(ax)
    style(axv)
    for k, b in enumerate(B):
        col = UP if b["c"] >= b["o"] else DN
        ax.vlines(k, b["l"], b["h"], color=col, linewidth=0.9)
        ax.add_patch(Rectangle((k - 0.35, min(b["o"], b["c"])), 0.7, max(abs(b["c"] - b["o"]), 1e-9), color=col))
        axv.bar(k, b["v"], color=col, width=0.7, alpha=0.6)
    for k_, col, lab in emas:
        ys = ind[lab][off:]
        ax.plot(range(m), ys, color=col, linewidth=1.3, label=lab)
    lo, hi = min(b["l"] for b in B), max(b["h"] for b in B)
    right = m + 12
    for y, col, lab, ls in lines or []:
        if y is None:
            continue
        ax.hlines(y, m - 0.5, right, color=col, linewidth=1.4, linestyles=ls)
        ax.text(right + 0.3, y, f"{lab} {fmt(y, dec)}", color=col, va="center", fontsize=8.5, fontweight="bold")
        lo, hi = min(lo, y), max(hi, y)
    if zone:
        ax.add_patch(Rectangle((m - 0.5, zone[0]), right - m + 0.5, zone[1] - zone[0], color=zone[2], alpha=0.15))
    if mark:
        ax.annotate(mark, xy=(m - 1, B[-1]["h"]), xytext=(m - 14, hi), color="#ffeb3b", fontsize=9, fontweight="bold", arrowprops=dict(arrowstyle="->", color="#ffeb3b", lw=1.5))
    pad = (hi - lo) * 0.06 or 1
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, right + 9)
    step = max(1, m // 6)
    axv.set_xticks(list(range(0, m, step)))
    axv.set_xticklabels([B[k]["t"][5:] for k in range(0, m, step)], color=FG, fontsize=8)
    plt.setp(ax.get_xticklabels(), visible=False)
    axv.set_yticks([])
    leg = ax.legend(loc="upper left", fontsize=8, facecolor=BG, edgecolor=GRID)
    for t in leg.get_texts():
        t.set_color(FG)
    fig.text(0.06, 0.95, title, color="#ffffff", fontsize=11.5, fontweight="bold")
    if note:
        import textwrap
        fig.text(0.06, 0.02, "\n".join(textwrap.wrap(note, 125)[:4]), color=FG, fontsize=8.4, va="bottom")
    fig.text(0.86, 0.02, "данные: Мосбиржа", color="#787b86", fontsize=7)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path


def hbars(rows, path, title, xlabel="", fmt_v=lambda v: pc(v, 0), height=None):
    """rows: [(подпись, значение, цвет или None)]."""
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(10, height or max(3.2, 0.36 * len(rows) + 1.2)), dpi=110, facecolor=BG)
    style(ax)
    ys = list(range(len(rows)))[::-1]
    vals = [r[1] for r in rows]
    ax.barh(ys, vals, color=[r[2] or (UP if r[1] >= 0 else DN) for r in rows], height=0.62, zorder=3)
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], color=FG, fontsize=9)
    ax.axvline(0, color="#787b86", linewidth=0.8)
    span = max(abs(v) for v in vals) or 1
    for y, v in zip(ys, vals):
        ax.text(v + (span * 0.02 if v >= 0 else -span * 0.02), y, fmt_v(v), va="center", ha="left" if v >= 0 else "right", color=FG, fontsize=8.5)
    ax.set_xlim(-span * 1.25 if min(vals) < 0 else 0, span * 1.25 if max(vals) > 0 else 0)
    ax.set_xlabel(xlabel, color=FG, fontsize=8.5)
    ax.set_title(title, color="#ffffff", fontsize=11.5, loc="left")
    fig.text(0.86, 0.01, "данные: Мосбиржа", color="#787b86", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path
