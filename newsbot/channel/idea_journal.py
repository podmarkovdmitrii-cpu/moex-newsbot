# -*- coding: utf-8 -*-
"""Журнал всех сделок (идей) в Excel и формализованный дневной отчёт в PDF — бот закрытых идей, каждый день в 19:15 МСК.

Excel (накопительный, пересобирается каждый вечер; последний файл закрепляется в чате):
  «Журнал» — все идеи: открытые и закрытые, уровни, итог, результат в % и R, срок, геометрия (стоп в ATR, цель в R), разбор бота,
             факторы в сделке и подтверждённые (все 6 уровней проверки, см. idea_errors.py);
  «Открытые» — текущая цена, нереализованный результат в R, расстояние до стопа и цели;
  «Факторы» — по каждой закрытой идее каждый фактор с уровнями проверки У1–У6;
  «По месяцам» — сделки, доля прибыльных, сумма и средний R, прибыль/убыток (profit factor);
  «Повторяемость», «Уровни проверки».
PDF (за день): итоги дня и с начала ведения, кривая накопленного R и просадка, сделки дня, открытые позиции, разбор убыточных
(только подтверждённые факторы + внешние причины), повторяющиеся ошибки, правила на следующий день из подтверждённых факторов.
Результаты факторного разбора кэшируются в состоянии бота (S["fa"]); идеи, закрытые не более 60 дней назад, пересчитываются каждый
вечер (проверка «ранний выход» смотрит, куда цена пошла после закрытия). Не является индивидуальной инвестиционной рекомендацией.
"""
import io
import os
import time

import numpy as np

import idea_errors as IE

RULES = {
    "против тренда": "Не открывать идеи против тренда (цена и наклон 50-дневной средней в другую сторону) — или вдвое меньшим риском.",
    "вход после сильного хода (догоняние)": "Не догонять: после хода больше 2 ATR за 5 дней ждать отката или пропускать.",
    "вход у края 60-дневного диапазона": "Не покупать у верхнего края диапазона (не продавать у нижнего) без пробоя и закрепления.",
    "перекупленность / перепроданность (RSI)": "При RSI > 70 (для шорта < 30) — вход только на откате.",
    "высокая волатильность бумаги": "В периоды высокой волатильности уменьшать размер позиции, чтобы стоп стоил те же 1% счёта.",
    "стоп в зоне шума (< 1,5 ATR)": "Стоп не ближе 2 ATR от входа или за сильным уровнем (кнопка «🤖 Стоп и цель автоматически»).",
    "цель слишком близко (< 1,5R)": "Цель не ближе 3R; по истории лучше один далёкий тейк (4–5R).",
    "вход после убытка (≤ 48 ч, риск тильта)": "После убытка — пауза 48 часов перед новой идеей; после двух убытков подряд — до конца недели.",
    "стоп перенесён дальше": "Стоп дальше не переносить никогда: только к безубытку или в сторону прибыли.",
    "ранний выход в минус до стопа": "Не закрывать в минус до стопа без новой причины, записанной в комментарии.",
    "убыток больше плана": "Проверять, что стоп выставлен в терминале сразу после входа.",
}


def _f(x, nd=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    return f"{x:.{nd}f}".replace(".", ",")


def _sg(x, nd=2):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    return (f"{x:+.{nd}f}").replace(".", ",").replace("-", "−")


def _pdf_txt(x):
    """Текст для PDF: шрифт DejaVu не содержит эмодзи — убираем их (✓ и ✗ оставляем), экранируем HTML."""
    import html
    t = "".join(ch for ch in str(x) if ord(ch) < 0x2600 or ch in "✓✗")
    return html.escape(t.strip(), quote=False)


def _ser(r):
    return {k: r.get(k) for k in ("name", "group", "present", "value", "verdict", "external", "error")} | \
        {"levels": [list(x) for x in (r.get("levels") or [])]}


def factor_cache(PW, today):
    """Факторный разбор всех закрытых идей (с кэшем в S["fa"]). → {iid: [факторы]}, {iid: контекст или None}."""
    import json
    ideas = PW.S.get("ideas") or {}
    fa = PW.S.setdefault("fa", {})
    need = []
    for k, v in ideas.items():
        if not v.get("closed"):
            continue
        c = fa.get(k)
        age = (time.time() - int(v.get("closed_t") or 0)) / 86400
        if not c or (age <= 60 and c.get("day") != today):
            need.append(k)
    ctxs = {}
    if need:
        divh = json.load(open(os.path.join(IE.HERE, "..", "divhist.json"), encoding="utf-8"))
        sec_of = json.load(open(os.path.join(IE.HERE, "..", "impact.json"), encoding="utf-8")).get("secOf", {})
        idx = IE.index_data()
        for k in need:
            v = ideas[k]
            try:
                ctx = IE.context(k, v, ideas, idx, divh, sec_of)
            except Exception as e:  # noqa: BLE001
                IE.log("идея", k, "не разобрана:", e)
                fa[k] = {"day": today, "f": [], "error": str(e)[:200]}
                continue
            ctxs[k] = ctx
            fs = []
            for grp, name, fn in IE.FACTORS:
                try:
                    r = fn(ctx)
                except Exception as e:  # noqa: BLE001
                    r = dict(present=None, error=str(e)[:150])
                r.update(group=grp, name=name)
                if r.get("present"):
                    r["verdict"] = IE.verdict(r["levels"])
                fs.append(_ser(r))
            fa[k] = {"day": today, "f": fs, "A": ctx["A"], "mae": _mae(ctx), "stopA": (ctx["risk"] / ctx["A"]) if ctx["A"] else None}
        PW.save()
    return {k: fa.get(k, {}) for k, v in ideas.items() if v.get("closed")}


def _mae(ctx):
    ph = ctx["path_h"] if ctx["path_h"] is not None and len(ctx["path_h"]["c"]) else ctx["path_d"]
    if not len(ph["c"]) or not ctx["risk"]:
        return None
    s, E, r = ctx["side"], ctx["E"], ctx["risk"]
    mae = (E - ph["l"].min()) / r if s > 0 else (ph["h"].max() - E) / r
    mfe = (ph["h"].max() - E) / r if s > 0 else (E - ph["l"].min()) / r
    return [round(-float(mae), 3), round(float(mfe), 3)]


def rows_all(ideas):
    import ideas_calc as IC
    out = []
    for k, v in sorted(ideas.items(), key=lambda kv: int(kv[0])):
        E = float(v["entry"])
        S0 = float(v.get("stop0") or v["stop"])
        risk = abs(E - S0)
        r = IC.result(v) if (v.get("closed") or v.get("hit")) else None
        out.append(dict(iid=k, v=v, E=E, S0=S0, risk=risk, rr=abs(float(v["takes"][-1]) - E) / risk if risk else None,
                        closed=bool(v.get("closed")), res=r, t0=int(v.get("t") or 0), t1=int(v.get("closed_t") or 0) if v.get("closed") else None))
    return out


def equity(rows):
    cl = sorted([r for r in rows if r["closed"] and r["res"] and r["res"]["R"] is not None], key=lambda r: r["t1"])
    R = np.array([r["res"]["R"] for r in cl])
    if not len(R):
        return cl, R, np.array([]), 0.0
    eq = np.cumsum(R)
    dd = float((eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min())
    return cl, R, eq, min(0.0, dd)


def open_marks(rows):
    """Текущая цена (закрытие последней дневной свечи) и нереализованный R по открытым идеям."""
    J = IE._J()
    out = {}
    for r in rows:
        if r["closed"]:
            continue
        try:
            d, _ = J.load(r["v"]["ticker"])
            px = float(d["c"][-1])
            out[r["iid"]] = (px, (px - r["E"]) * int(r["v"]["side"]) / r["risk"] if r["risk"] else None, d["t"][-1][:10])
        except Exception as e:  # noqa: BLE001
            IE.log("нет цены", r["v"]["ticker"], e)
    return out


def stats(R):
    if not len(R):
        return dict(n=0)
    pos, neg = R[R > 0].sum(), -R[R < 0].sum()
    return dict(n=int(len(R)), win=float((R > 0).mean()), sum=float(R.sum()), avg=float(R.mean()), pf=float(pos / neg) if neg > 0 else None,
                best=float(R.max()), worst=float(R.min()))


# ---------------- Excel ----------------
def journal_xlsx(PW, day, rows, fa, marks):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    ideas = PW.S.get("ideas") or {}
    wb = Workbook()
    bold = Font(bold=True)
    green, red, yellow, grey = (PatternFill("solid", fgColor=c) for c in ("C6EFCE", "FFC7CE", "FFEB9C", "EDEDED"))
    wrap = Alignment(wrap_text=True, vertical="top")
    ws = wb.active
    ws.title = "Журнал"
    hdr = ["Идея", "Бумага", "Сторона", "Статус", "Открыта (МСК)", "Закрыта (МСК)", "Дней", "Вход", "Стоп при входе", "Стоп сейчас/при закрытии",
           "Тейки", "Взято тейков", "Итог автора", "Результат, %", "R", "Тип итога", "Источник", "Риск, % от входа", "Стоп, ATR", "Цель, R",
           "Вероятность цели (разбор)", "Макс. против, R", "Макс. в плюс, R", "Факторы в сделке", "Подтверждённые факторы", "Комментарий"]
    ws.append(hdr)
    for c in ws[1]:
        c.font = bold
    for r in rows:
        v = r["v"]
        f = (fa.get(r["iid"]) or {}).get("f") or []
        pres = [x["name"] for x in f if x.get("present")]
        conf = [x["name"] for x in f if x.get("present") and (x.get("verdict") or "").startswith("подтверждён")]
        res = r["res"] or {}
        mm = (fa.get(r["iid"]) or {}).get("mae") or [None, None]
        days = ((r["t1"] or time.time()) - r["t0"]) / 86400 if r["t0"] else None
        ws.append([int(r["iid"]), v["ticker"], "лонг" if int(v["side"]) > 0 else "шорт", "закрыта" if r["closed"] else "открыта",
                   IE.msk_dt(r["t0"]) if r["t0"] else "", IE.msk_dt(r["t1"]) if r["t1"] else "", round(days, 1) if days is not None else None,
                   r["E"], r["S0"], float(v["stop"]), " / ".join(f"{float(x):g}" for x in v["takes"]), len(v.get("hit") or []),
                   v.get("result") or "", round(res["pct"] * 100, 2) if res.get("pct") is not None else None,
                   round(res["R"], 2) if res.get("R") is not None else None, res.get("kind") or "", res.get("src") or "",
                   round(r["risk"] / r["E"] * 100, 2) if r["risk"] else None, round((fa.get(r["iid"]) or {}).get("stopA") or 0, 2) or None,
                   round(r["rr"], 2) if r["rr"] else None, (v.get("plan") or {}).get("p"), mm[0], mm[1],
                   "; ".join(pres) or ("—" if r["closed"] else "разбор после закрытия"), "; ".join(conf) or "—", v.get("comment") or ""])
        row = ws.max_row
        if r["closed"] and res.get("R") is not None:
            ws.cell(row, 15).fill = green if res["R"] > 0 else red if res["R"] < 0 else grey
        elif not r["closed"]:
            ws.cell(row, 4).fill = yellow
    for i, w in enumerate((6, 8, 7, 9, 16, 16, 6, 9, 9, 10, 14, 7, 22, 9, 7, 10, 8, 8, 7, 7, 9, 8, 8, 40, 34, 30)):
        ws.cell(1, i + 1).alignment = wrap
        ws.column_dimensions[ws.cell(1, i + 1).column_letter].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    # Открытые
    wo = wb.create_sheet("Открытые")
    wo.append(["Идея", "Бумага", "Сторона", "Открыта", "Вход", "Стоп", "Цель", "Цена (закрытие)", "На дату", "Нереализовано, R",
               "До стопа, %", "До цели, %"])
    for c in wo[1]:
        c.font = bold
    for r in rows:
        if r["closed"]:
            continue
        v, m = r["v"], marks.get(r["iid"])
        px = m[0] if m else None
        s = int(v["side"])
        wo.append([int(r["iid"]), v["ticker"], "лонг" if s > 0 else "шорт", IE.msk_dt(r["t0"]) if r["t0"] else "", r["E"], float(v["stop"]),
                   float(v["takes"][-1]), px, m[2] if m else "", round(m[1], 2) if m and m[1] is not None else None,
                   round((px - float(v["stop"])) / px * 100 * s, 2) if px else None, round((float(v["takes"][-1]) - px) / px * 100 * s, 2) if px else None])
    for col in "ABCDEFGHIJKL":
        wo.column_dimensions[col].width = 13
    # Факторы
    wf = wb.create_sheet("Факторы")
    wf.append(["Идея", "Бумага", "Результат, R", "Группа", "Фактор", "Значение", "Вердикт", "У1 Данные", "У2 Сверка", "У3 Время", "У4 Рынок",
               "У5 Бумага", "У6 Ваши сделки"])
    for c in wf[1]:
        c.font = bold
    for r in rows:
        if not r["closed"]:
            continue
        for x in (fa.get(r["iid"]) or {}).get("f") or []:
            if not x.get("present"):
                continue
            lv = {a: f"{b} {c}" for a, b, c in x["levels"]}
            wf.append([int(r["iid"]), r["v"]["ticker"], round(r["res"]["R"], 2) if r["res"] and r["res"]["R"] is not None else None,
                       x["group"], x["name"], x["value"], x["verdict"]] + [lv.get(k, "") for k in ("У1", "У2", "У3", "У4", "У5", "У6")])
            row = wf.max_row
            vd = x["verdict"] or ""
            wf.cell(row, 7).fill = green if vd == "подтверждён" else yellow if vd.startswith("подтверждён") else red
            for j, k in enumerate(("У1", "У2", "У3", "У4", "У5", "У6")):
                s_ = lv.get(k, "")[:1]
                wf.cell(row, 8 + j).fill = green if s_ == IE.OK else red if s_ == IE.BAD else yellow
    for i, w in enumerate((6, 8, 9, 11, 32, 32, 26, 28, 34, 28, 60, 48, 34)):
        wf.column_dimensions[wf.cell(1, i + 1).column_letter].width = w
    for row in wf.iter_rows(min_row=2):
        for c in row:
            c.alignment = wrap
    wf.freeze_panes = "F2"
    wf.auto_filter.ref = wf.dimensions
    # По месяцам
    wm = wb.create_sheet("По месяцам")
    wm.append(["Месяц", "Закрыто", "В плюсе", "Доля прибыльных", "Сумма R", "Средний R", "Прибыль/убыток (PF)", "Лучшая, R", "Худшая, R"])
    for c in wm[1]:
        c.font = bold
    cl, R, eq, dd = equity(rows)
    months = {}
    for r in cl:
        months.setdefault(IE.msk_date(r["t1"])[:7], []).append(r["res"]["R"])
    for mth, vals in sorted(months.items()):
        s = stats(np.array(vals))
        wm.append([mth, s["n"], int(round(s["win"] * s["n"])), round(s["win"], 3), round(s["sum"], 2), round(s["avg"], 3),
                   round(s["pf"], 2) if s["pf"] else None, round(s["best"], 2), round(s["worst"], 2)])
    s = stats(R)
    if s["n"]:
        wm.append(["Всего", s["n"], int(round(s["win"] * s["n"])), round(s["win"], 3), round(s["sum"], 2), round(s["avg"], 3),
                   round(s["pf"], 2) if s["pf"] else None, round(s["best"], 2), round(s["worst"], 2)])
        wm.cell(wm.max_row, 1).font = bold
        wm.append([f"Максимальная просадка накопленного результата: {dd:.2f}R"])
    for col in "ABCDEFGHI":
        wm.column_dimensions[col].width = 14
    # Повторяемость и уровни
    rec_rows, n = IE.recurrence(ideas)
    wr = wb.create_sheet("Повторяемость")
    wr.append([f"Все закрытые идеи с историей свечей: {n}. Как часто фактор встречался у убыточных и у остальных."])
    wr.append(["Фактор", "Убыточных", "из них с фактором", "Доля", "Остальных", "из них с фактором", "Доля", "Чаще у убыточных?"])
    for c in wr[2]:
        c.font = bold
    for name, nl, kl, nw, kw in rec_rows:
        a, b = (kl / nl if nl else None), (kw / nw if nw else None)
        wr.append([name, nl, kl, round(a, 3) if a is not None else None, nw, kw, round(b, 3) if b is not None else None,
                   ("да" if a > b else "нет") if a is not None and b is not None and nl >= 5 and nw >= 5 else "мало данных"])
    wr.column_dimensions["A"].width = 40
    wl = wb.create_sheet("Уровни проверки")
    for line in ((IE.__doc__ or "").strip() + "\n\n" + (__doc__ or "").strip()).split("\n"):
        wl.append([line])
    wl.column_dimensions["A"].width = 160
    ws.sheet_view.tabSelected = True
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue(), (rec_rows, n)


# ---------------- PDF ----------------
def _fonts():
    import matplotlib
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    fdir = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    for nm, f in (("DV", "DejaVuSans.ttf"), ("DVB", "DejaVuSans-Bold.ttf")):
        try:
            pdfmetrics.getFont(nm)
        except KeyError:
            pdfmetrics.registerFont(TTFont(nm, os.path.join(fdir, f)))


def _chart(eq, cl):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7.2, 2.6), dpi=150)
    x = np.arange(1, len(eq) + 1)
    ax.plot(np.r_[0, x], np.r_[0, eq], color="#1a3d7c", lw=1.8)
    peak = np.maximum.accumulate(np.r_[0, eq])
    ax.fill_between(np.r_[0, x], np.r_[0, eq], peak, color="#c0392b", alpha=0.15, lw=0)
    ax.axhline(0, color="#888", lw=0.8)
    ax.set_xlabel("закрытые сделки по порядку", fontsize=8)
    ax.set_ylabel("накопленный результат, R", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    bio = io.BytesIO()
    fig.savefig(bio, format="png")
    plt.close(fig)
    bio.seek(0)
    return bio


def day_pdf(PW, day, rows, fa, marks, rec):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    _fonts()
    H1 = ParagraphStyle("h1", fontName="DVB", fontSize=15, leading=19, spaceAfter=2)
    SUB = ParagraphStyle("sub", fontName="DV", fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"), spaceAfter=8)
    H2 = ParagraphStyle("h2", fontName="DVB", fontSize=11.5, leading=15, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1a3d7c"))
    TX = ParagraphStyle("tx", fontName="DV", fontSize=8.8, leading=12, spaceAfter=2)
    SM = ParagraphStyle("sm", fontName="DV", fontSize=7.4, leading=9.6)
    SMB = ParagraphStyle("smb", fontName="DVB", fontSize=7.4, leading=9.6)

    def table(data, widths, hl=None):
        t = Table([[Paragraph(_pdf_txt(c), SMB if i == 0 else SM) for c in row] for i, row in enumerate(data)], colWidths=widths, repeatRows=1)
        st = [("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf6")),
              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
        for (r_, c_, col) in (hl or []):
            st.append(("BACKGROUND", (c_, r_), (c_, r_), colors.HexColor(col)))
        t.setStyle(TableStyle(st))
        return t
    dd_ = day.split("-")
    dstr = f"{dd_[2]}.{dd_[1]}.{dd_[0]}"
    story = [Paragraph(f"Отчёт по сделкам за {dstr}", H1),
             Paragraph(f"Бот закрытых идей · сформирован {time.strftime('%d.%m.%Y %H:%M', time.gmtime(time.time() + IE.MSK))} МСК · "
                       "R — риск сделки (расстояние от входа до первоначального стопа)", SUB)]
    opened = [r for r in rows if r["t0"] and IE.msk_date(r["t0"]) == day]
    closed = [r for r in rows if r["closed"] and r["t1"] and IE.msk_date(r["t1"]) == day]
    Rd = np.array([r["res"]["R"] for r in closed if r["res"] and r["res"]["R"] is not None])
    cl, R, eq, dd = equity(rows)
    s_all, s_day = stats(R), stats(Rd)
    # 1. Итоги дня
    story.append(Paragraph("1. Итоги дня", H2))
    data = [["Показатель", "Значение"],
            ["Открыто идей", str(len(opened))],
            ["Закрыто идей", f"{len(closed)} (в плюсе {int((Rd > 0).sum())}, в минусе {int((Rd < 0).sum())}, в ноль {int((Rd == 0).sum())})"],
            ["Результат закрытых за день", f"{_sg(Rd.sum() if len(Rd) else 0)}R; "
             + f"сумма {_sg(sum((r['res']['pct'] for r in closed if r['res']), 0.0) * 100, 1)}% (без учёта размера позиций)"],
            ["Лучшая / худшая сделка дня", f"{_sg(s_day.get('best'))}R / {_sg(s_day.get('worst'))}R" if s_day["n"] else "—"],
            ["Открытых позиций на конец дня", str(sum(1 for r in rows if not r["closed"]))]]
    story.append(table(data, [6 * cm, 11 * cm]))
    # 2. С начала ведения
    story.append(Paragraph("2. Итоги с начала ведения журнала", H2))
    if s_all["n"]:
        first = IE.msk_date(cl[0]["t1"])
        data = [["Показатель", "Значение"], ["Период", f"{first} — {day}"], ["Закрыто сделок", str(s_all["n"])],
                ["Доля прибыльных", f"{s_all['win']:.0%}"], ["Сумма / средний результат", f"{_sg(s_all['sum'])}R / {_sg(s_all['avg'], 3)}R на сделку"],
                ["Прибыль / убыток (profit factor)", _f(s_all["pf"]) if s_all["pf"] else "—"],
                ["Максимальная просадка", f"{_sg(dd)}R"], ["Лучшая / худшая", f"{_sg(s_all['best'])}R / {_sg(s_all['worst'])}R"]]
        story.append(table(data, [6 * cm, 11 * cm]))
        if len(eq) >= 2:
            story.append(Spacer(1, 4))
            story.append(Image(_chart(eq, cl), width=17 * cm, height=6.1 * cm))
    else:
        story.append(Paragraph("Закрытых сделок пока нет.", TX))
    # 3. Закрытые за день
    story.append(Paragraph("3. Сделки, закрытые за день", H2))
    if closed:
        data = [["№", "Бумага", "Сторона", "Вход", "Стоп", "Итог", "%", "R", "Дней"]]
        hl = []
        for i, r in enumerate(closed, 1):
            v, res = r["v"], r["res"] or {}
            data.append([r["iid"], v["ticker"], "лонг" if int(v["side"]) > 0 else "шорт", f"{r['E']:g}", f"{r['S0']:g}", v.get("result") or res.get("kind", ""),
                         _sg((res.get("pct") or 0) * 100, 1), _sg(res.get("R")), _f((r["t1"] - r["t0"]) / 86400, 1) if r["t0"] else "—"])
            if res.get("R") is not None:
                hl.append((i, 7, "#d5f5e3" if res["R"] > 0 else "#fadbd8"))
        story.append(table(data, [1 * cm, 1.7 * cm, 1.7 * cm, 1.7 * cm, 1.7 * cm, 4.3 * cm, 1.4 * cm, 1.4 * cm, 1.2 * cm], hl))
    else:
        story.append(Paragraph("Закрытых сделок за день нет.", TX))
    # 4. Открытые
    story.append(Paragraph("4. Открытые позиции", H2))
    op = [r for r in rows if not r["closed"]]
    if op:
        data = [["№", "Бумага", "Сторона", "Вход", "Стоп", "Цель", "Цена", "Нереализ., R"]]
        hl = []
        for i, r in enumerate(op, 1):
            v, m = r["v"], marks.get(r["iid"])
            data.append([r["iid"], v["ticker"], "лонг" if int(v["side"]) > 0 else "шорт", f"{r['E']:g}", f"{float(v['stop']):g}", f"{float(v['takes'][-1]):g}",
                         f"{m[0]:g} ({m[2]})" if m else "—", _sg(m[1]) if m and m[1] is not None else "—"])
            if m and m[1] is not None:
                hl.append((i, 7, "#d5f5e3" if m[1] > 0 else "#fadbd8"))
        story.append(table(data, [1 * cm, 1.8 * cm, 1.7 * cm, 2 * cm, 2 * cm, 2 * cm, 3.4 * cm, 2.2 * cm], hl))
    else:
        story.append(Paragraph("Открытых позиций нет.", TX))
    # 5. Разбор убыточных
    story.append(Paragraph("5. Разбор убыточных сделок дня", H2))
    losers = [r for r in closed if r["res"] and r["res"]["pct"] < 0]
    conf_day = {}
    if losers:
        for r in losers:
            f = (fa.get(r["iid"]) or {}).get("f") or []
            conf = [x for x in f if x.get("present") and (x.get("verdict") or "").startswith("подтверждён")]
            unc = [x for x in f if x.get("present") and not (x.get("verdict") or "").startswith("подтверждён")]
            lines = [f"<b>№{r['iid']} {_pdf_txt(r['v']['ticker'])}</b> ({_sg(r['res']['R'])}R)"]
            if conf:
                for x in conf:
                    conf_day[x["name"]] = conf_day.get(x["name"], 0) + 1
                    lines.append(f"• <b>{_pdf_txt(x['name'])}</b> — {_pdf_txt(x['value'])} ({x['verdict']}{'; внешняя причина' if x.get('external') else ''})")
            else:
                lines.append("• Ни один фактор не прошёл все шесть уровней проверки.")
            if unc:
                lines.append("Не подтверждены: " + "; ".join(f"{_pdf_txt(x['name'])} — не пройдены " + x['verdict'].replace('не подтверждён ', '').strip('()')
                                                        for x in unc))
            story.append(KeepTogether([Paragraph(lines[0], TX)] + [Paragraph(x, SM) for x in lines[1:]] + [Spacer(1, 4)]))
    else:
        story.append(Paragraph("Убыточных сделок за день нет.", TX))
    # 6. Повторяемость
    story.append(Paragraph("6. Повторяющиеся факторы (все ваши закрытые идеи)", H2))
    rec_rows, n = rec
    data = [["Фактор", "У убыточных", "У остальных", "Вывод"]]
    any_ = False
    for name, nl, kl, nw, kw in rec_rows:
        if nl + nw == 0:
            continue
        any_ = True
        a, b = (kl / nl if nl else None), (kw / nw if nw else None)
        concl = ("чаще у убыточных" if a > b else "не чаще") if a is not None and b is not None and nl >= 5 and nw >= 5 else "мало данных"
        data.append([name, f"{kl} из {nl}" + (f" ({a:.0%})" if a is not None else ""), f"{kw} из {nw}" + (f" ({b:.0%})" if b is not None else ""), concl])
    story.append(table(data, [7 * cm, 3.2 * cm, 3.2 * cm, 3.6 * cm]) if any_ else Paragraph("Данных пока нет.", TX))
    # 7. Правила на следующий день
    story.append(Paragraph("7. Правила на следующий торговый день", H2))
    rules = [RULES[k] for k in sorted(conf_day, key=lambda k: -conf_day[k]) if k in RULES]
    if not rules:
        rules = ["Подтверждённых ошибок за день нет — торговать по прежнему плану: стоп и цель до входа, риск не больше 1% счёта на сделку."]
    rules.append("Перед входом: стоп и цель записаны, размер позиции посчитан так, чтобы стоп стоил ≤ 1% счёта; дневной лимит убытка — 2% счёта.")
    for i, x in enumerate(rules, 1):
        story.append(Paragraph(f"{i}. {x}", TX))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Подробности по каждому фактору и уровням проверки — в Excel-журнале (лист «Факторы»). Это статистика прошлого, а не гарантия. "
                           "Не является индивидуальной инвестиционной рекомендацией.", SUB))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("DV", 7)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(1.5 * cm, 1 * cm, f"Отчёт по сделкам за {dstr}")
        canvas.drawRightString(A4[0] - 1.5 * cm, 1 * cm, f"стр. {doc.page}")
        canvas.restoreState()
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.4 * cm, bottomMargin=1.6 * cm,
                            title=f"Отчёт по сделкам за {dstr}", author="Бот закрытых идей")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return bio.getvalue()


# ---------------- вечерняя отправка ----------------
def evening(PW, day=None, force=False):
    """19:15 МСК: Excel-журнал всех сделок (закрепляется) и PDF-отчёт за день — первому администратору.
    В выходные — только если в этот день идеи открывались или закрывались."""
    t = time.gmtime(time.time() + IE.MSK)
    day = day or time.strftime("%Y-%m-%d", t)
    if not force and (time.strftime("%H:%M", t) < os.environ.get("ERRORS_AT", "19:15") or PW.S.get("err_day") == day):
        return
    if not PW.ADMINS:
        return
    if not force:
        PW.S["err_day"] = day
        PW.save()
    admin = sorted(PW.ADMINS)[0]
    ideas = PW.S.get("ideas") or {}
    rows = rows_all(ideas)
    active = any((r["t0"] and IE.msk_date(r["t0"]) == day) or (r["t1"] and IE.msk_date(r["t1"]) == day) for r in rows)
    wd = time.strptime(day, "%Y-%m-%d").tm_wday
    if not force and wd >= 5 and not active:
        return
    try:
        fa = factor_cache(PW, day)
        marks = open_marks(rows)
        xls, rec = journal_xlsx(PW, day, rows, fa, marks)
        pdf = day_pdf(PW, day, rows, fa, marks, rec)
        cl, R, eq, dd = equity(rows)
        s = stats(R)
        cap = (f"📒 Журнал сделок на {day}: всего идей {len(rows)}, закрыто {s['n']}"
               + (f", итог {_sg(s['sum'])}R, прибыльных {s['win']:.0%}" if s["n"] else "") + ". Не ИИР.")
        m = PW.tg_upload_doc(admin, f"journal_{day}.xlsx", xls, cap, ctype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        try:
            old = PW.S.get("journal_msg")
            if old:
                PW.tg("unpinChatMessage", {"chat_id": admin, "message_id": old})
            PW.tg("pinChatMessage", {"chat_id": admin, "message_id": m["message_id"], "disable_notification": True})
            PW.S["journal_msg"] = m["message_id"]
            PW.save()
        except Exception as e:  # noqa: BLE001
            IE.log("журнал не закреплён:", e)
        PW.tg_upload_doc(admin, f"report_{day}.pdf", pdf, f"📄 Отчёт по сделкам за {day}. Не является индивидуальной инвестиционной рекомендацией.",
                         ctype="application/pdf")
        IE.log("журнал и отчёт отправлены за", day)
    except Exception as e:  # noqa: BLE001
        IE.log("журнал/отчёт не сформированы:", e)
        try:
            PW.say(admin, f"⚠️ Журнал и отчёт за {day} не сформированы: {str(e)[:200]}")
        except Exception:  # noqa: BLE001
            pass
