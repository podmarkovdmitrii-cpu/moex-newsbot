# -*- coding: utf-8 -*-
"""Статья для паблика: страница Telegraph (если задан секрет TELEGRAPH_TOKEN) или PDF-файл.

Статья описывается списком блоков с простой HTML-разметкой внутри (<b>, <i>, <a href>):
  ("h3", "Заголовок раздела"), ("h4", "Подзаголовок"), ("p", "Абзац"), ("ul", ["пункт", ...]), ("hr",), ("img", путь, подпись)
Картинки попадают только в PDF (загрузка картинок на telegra.ph закрыта) — в Telegram они уходят альбомом к анонсу.
Токен Telegraph создаёт владелец паблика сам и кладёт в секреты GitHub; бот аккаунтов не создаёт.
"""
import html as _html
import json
import os
import re
import urllib.request
from html.parser import HTMLParser

from common import DEMO, DRY, OUT, log

TOKEN = os.environ.get("TELEGRAPH_TOKEN", "")
ALLOWED = {"a", "b", "i", "strong", "em", "br", "p", "h3", "h4", "ul", "ol", "li", "blockquote", "hr", "code", "aside"}


class _Nodes(HTMLParser):
    """HTML → узлы Telegraph (Node: строка или {tag, attrs, children})."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED:
            return
        n = {"tag": tag}
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            n["attrs"] = {"href": a["href"]}
        if tag in ("br", "hr"):
            self.stack[-1]["children"].append(n)
            return
        n["children"] = []
        self.stack[-1]["children"].append(n)
        self.stack.append(n)

    def handle_endtag(self, tag):
        if tag not in ALLOWED or tag in ("br", "hr"):
            return
        for k in range(len(self.stack) - 1, 0, -1):
            if self.stack[k].get("tag") == tag:
                del self.stack[k:]
                break

    def handle_data(self, data):
        if data:
            self.stack[-1]["children"].append(data)


def nodes(fragment):
    p = _Nodes()
    p.feed(fragment)
    p.close()
    return p.root["children"]


def to_html(blocks):
    out = []
    for b in blocks:
        k = b[0]
        if k in ("h3", "h4", "p"):
            out.append(f"<{k}>{b[1]}</{k}>")
        elif k == "ul":
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in b[1]) + "</ul>")
        elif k == "hr":
            out.append("<hr/>")
        elif k == "aside":
            out.append(f"<aside>{b[1]}</aside>")
    return "".join(out)


def telegraph(title, blocks, author="", author_url=""):
    content = nodes(to_html(blocks))
    while len(json.dumps(content, ensure_ascii=False).encode()) > 60000 and len(content) > 5:
        content = content[:-2]  # лимит Telegraph 64 КБ — обрезаем хвост
    body = dict(access_token=TOKEN, title=title[:256], author_name=author[:128], content=content, return_content=False)
    if author_url:
        body["author_url"] = author_url
    req = urllib.request.Request("https://api.telegra.ph/createPage", data=json.dumps(body, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        js = json.loads(r.read().decode())
    if not js.get("ok"):
        raise RuntimeError("Telegraph: " + str(js.get("error")))
    return js["result"]["url"]


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def pdf(path, title, blocks):
    try:
        import matplotlib
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer
    except ImportError as e:
        log("PDF не собран:", e)
        return None
    fdir = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    for nm, f in (("DV", "DejaVuSans.ttf"), ("DVB", "DejaVuSans-Bold.ttf"), ("DVI", "DejaVuSans-Oblique.ttf"), ("DVBI", "DejaVuSans-BoldOblique.ttf")):
        pdfmetrics.registerFont(TTFont(nm, os.path.join(fdir, f)))
    pdfmetrics.registerFontFamily("DV", normal="DV", bold="DVB", italic="DVI", boldItalic="DVBI")
    H1 = ParagraphStyle("h1", fontName="DVB", fontSize=16, leading=20, spaceAfter=6)
    H3 = ParagraphStyle("h3", fontName="DVB", fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1a3d7c"))
    H4 = ParagraphStyle("h4", fontName="DVB", fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=2)
    TX = ParagraphStyle("tx", fontName="DV", fontSize=9.2, leading=12.8, spaceAfter=3)
    LI = ParagraphStyle("li", parent=TX, leftIndent=10, bulletIndent=0)
    W = A4[0] - 3 * cm

    def clean(s):
        s = EMOJI.sub("", s)
        s = re.sub(r'<a href="([^"]+)">', r'<a href="\1" color="#1a5fb4">', s)
        return re.sub(r"<(?!/?(b|i|a|br)\b)[^>]+>", "", s).strip()
    E = [Paragraph(clean(_html.escape(title, quote=False)), H1)]
    for b in blocks:
        k = b[0]
        try:
            if k == "h3":
                E.append(Paragraph(clean(b[1]), H3))
            elif k == "h4":
                E.append(Paragraph(clean(b[1]), H4))
            elif k in ("p", "aside"):
                E.append(Paragraph(clean(b[1]), TX))
            elif k == "ul":
                for x in b[1]:
                    E.append(Paragraph(clean(x), LI, bulletText="•"))
            elif k == "hr":
                E.append(Spacer(1, 6))
            elif k == "img" and b[1] and os.path.exists(b[1]):
                iw, ih = ImageReader(b[1]).getSize()
                E.append(Image(b[1], width=W, height=W * ih / iw))
                if len(b) > 2 and b[2]:
                    E.append(Paragraph(f"<i>{clean(b[2])}</i>", TX))
        except Exception as e:  # noqa: BLE001
            log("PDF: блок пропущен", k, e)
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.3 * cm, bottomMargin=1.3 * cm, title=title)
    doc.build(E)
    return path


def account_info():
    """Проверка токена Telegraph: имя автора и число страниц (ничего не публикует)."""
    req = urllib.request.Request("https://api.telegra.ph/getAccountInfo", data=json.dumps(dict(access_token=TOKEN, fields=["short_name", "author_name", "page_count"])).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        js = json.loads(r.read().decode())
    if not js.get("ok"):
        raise RuntimeError("Telegraph: " + str(js.get("error")))
    return js["result"]


def tg_chunks(title, blocks, limit=3800):
    """Статья → сообщения Telegram (HTML: b, i, a), если Telegraph недоступен. Без PDF."""
    parts = [f"<b>{_html.escape(title, quote=False)}</b>"]
    for b in blocks:
        k = b[0]
        if k == "h3":
            parts.append(f"\n<b>{b[1]}</b>")
        elif k == "h4":
            parts.append(f"<b>{b[1]}</b>")
        elif k in ("p", "aside"):
            parts.append(b[1])
        elif k == "ul":
            parts.extend("• " + x for x in b[1])
    clean = [re.sub(r"<(?!/?(b|i|a)\b)[^>]+>", "", x) for x in parts]
    out, cur = [], ""
    for x in clean:
        if len(cur) + len(x) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + x[:limit]
    if cur:
        out.append(cur)
    return out


def publish(pub, title, blocks, teaser, photos=None, fname="article"):
    """Статья + анонс в паблике. Основной путь — страница Telegraph (секрет TELEGRAPH_TOKEN), ссылка в анонсе.
    Если Telegraph недоступен — статья уходит обычными сообщениями Telegram под анонсом (PDF больше не используется).
    teaser — HTML анонса; строка «{LINK}» в нём заменяется ссылкой на статью."""
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, fname + ".html"), "w", encoding="utf-8") as f:  # копия статьи для проверки
        f.write(f"<meta charset='utf-8'><h1>{_html.escape(title)}</h1>" + to_html(blocks))
    if getattr(pub, "dry", False):
        print("----- СТАТЬЯ (без публикации) -----\n" + title + "\n" + _html.unescape(re.sub(r"<[^>]+>", "", to_html(blocks).replace("</p>", "\n").replace("</li>", "\n").replace("</h3>", "\n").replace("</h4>", "\n"))), flush=True)
        log("статья пойдёт в Telegraph" if TOKEN else "TELEGRAPH_TOKEN не задан — статья пойдёт сообщениями Telegram")
    chan = os.environ.get("TG_PUBLIC_CHAT", "")
    url = None
    if TOKEN and not DRY and not DEMO:
        for k in range(3):
            try:
                url = telegraph(title, blocks, author=(chan if chan.startswith("@") else "MOEX паблик"), author_url=(f"https://t.me/{chan[1:]}" if chan.startswith("@") else ""))
                log("статья Telegraph:", url)
                break
            except Exception as e:  # noqa: BLE001
                log("Telegraph недоступен (попытка", k + 1, "):", e)
                import time
                time.sleep(5 * (k + 1))
    if url:
        return pub.post(teaser.replace("{LINK}", f"📖 <b><a href=\"{url}\">Читать статью целиком</a></b>"), photos)
    res = pub.post(teaser.replace("{LINK}", "📖 <b>Статья целиком — в сообщениях ниже</b>"), photos)
    for m in tg_chunks(title, blocks):
        pub.post(m, ai_lead=False)
    return res
