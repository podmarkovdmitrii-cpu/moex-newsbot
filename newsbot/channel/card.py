# -*- coding: utf-8 -*-
"""Иллюстрация к посту паблика в стиле торговых терминалов: один крупный дневной график на всю ширину,
шапка «тикер · цена · изменение за день · событие», зоны сделки как инструмент «позиция» (цель — зелёная, стоп — красная).

Запуск: python card.py card.json out.png [--demo]
card.json: {"t": "SBER" | "РЫНОК" | "BRZ6", "kind": "share|index|fut", "tf": "1d|4h|1h", "label": "Дивиденды",
            "sign": 1|-1|0, "side": 1|-1|0, "noimg": false, "sector": "банки"}
Печатает одну строку JSON: {"png": путь или null, "last", "atr", "chg", "entry", "stop", "take", "risk",
                            "lv": {"1": {...}, "-1": {...}}}.
noimg=true — только цифры (уровни для обеих сторон и изменение за день), без картинки; свечи кешируются на 5 минут,
чтобы следующий вызов с выбранной стороной сделки не качал их заново.
Нет свечей — картинка не делается (пост выйдет текстом), а не рисуется пустая карточка.
Свои картинки (без чужих изображений и шаблонов мемов).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BG, FG, GRID, UP, DN, BLUE, ORANGE, plt, Rectangle, tf_bars, ema, atr, fmt, pc, tlab, log  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

MUTED = "#787b86"


def levels(last, a, side):
    """Вход по текущей цене, стоп за 1,5 дневного ATR (не ближе 1,5% и не дальше 8%), цель — 3 риска."""
    if not side or not last or not a:
        return None
    dist = min(max(1.5 * a, 0.015 * last), 0.08 * last)
    return dict(entry=last, stop=last - side * dist, take=last + side * 3 * dist, risk=dist / last)


def _bars(sec, kind, cache_dir):
    """Дневные свечи (≈150 дней) с кешем на 5 минут."""
    p = os.path.join(cache_dir, f"bars_{sec}.json")
    try:
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < 300:
            return json.load(open(p))
    except Exception:  # noqa: BLE001
        pass
    try:
        B = tf_bars(sec, "1d", kind)
    except Exception as e:  # noqa: BLE001
        log("карточка: нет свечей", sec, e)
        return []
    try:
        json.dump(B, open(p, "w"))
    except Exception:  # noqa: BLE001
        pass
    return B


def dec_of(x):
    return 0 if x >= 1000 else 1 if x >= 100 else 2 if x >= 1 else 4


def pill(fig, x, y, text, fc, fs=15, color="#ffffff", ha="left"):
    return fig.text(x, y, text, color=color, fontsize=fs, fontweight="bold", va="center", ha=ha,
                    bbox=dict(boxstyle="round,pad=0.38,rounding_size=0.5", fc=fc, ec="none"))


def render(d, B, lv, chg, out):
    t = d.get("t") or "РЫНОК"
    side = d.get("side") or 0
    sign = d.get("sign") or 0
    last = B[-1]["c"]
    dc = dec_of(last)
    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor=BG)

    # ---- шапка: тикер, сектор, цена и изменение за день, событие ----
    who = "IMOEX" if t == "РЫНОК" else "$" + t
    wt = fig.text(0.04, 0.915, who, color="#ffffff", fontsize=34, fontweight="bold", va="center")
    rnd = fig.canvas.get_renderer()
    xr = wt.get_window_extent(rnd).transformed(fig.transFigure.inverted()).x1 + 0.02
    sub = d.get("sector") or ""
    fig.text(0.04, 0.855, (sub + " · " if sub else "") + "дневные свечи · Мосбиржа", color=MUTED, fontsize=13, va="center")
    fig.text(0.955, 0.915, fmt(last, dc), color="#ffffff", fontsize=34, fontweight="bold", va="center", ha="right")
    if chg is not None:
        pill(fig, 0.955, 0.855, f"{pc(chg, 2)} сегодня", UP if chg >= 0 else DN, fs=13, ha="right")
    lab = (d.get("label") or "").strip()
    if lab:
        if len(lab) > 46:
            lab = lab[:45].rstrip() + "…"
        ev_col = UP if sign > 0 else DN if sign < 0 else "#3a3f4b"
        pt = pill(fig, xr, 0.915, ("▲ " if sign > 0 else "▼ " if sign < 0 else "") + lab, ev_col, fs=15)
        fig.canvas.draw()
        xr = pt.get_bbox_patch().get_window_extent(rnd).transformed(fig.transFigure.inverted()).x1 + 0.012
    if lv:
        pill(fig, xr, 0.915, ("ЛОНГ" if side > 0 else "ШОРТ") + " · сигнал", UP if side > 0 else DN, fs=15)

    # ---- график на всю ширину + объём ----
    m = len(B)
    right = m + (16 if lv else 3)
    ax = fig.add_axes([0.04, 0.2, 0.86, 0.6])
    axv = fig.add_axes([0.04, 0.09, 0.86, 0.1], sharex=ax)
    for a_ in (ax, axv):
        a_.set_facecolor(BG)
        for s_ in a_.spines.values():
            s_.set_visible(False)
        a_.tick_params(colors=FG, labelsize=12, length=0)
    ax.grid(color=GRID, linewidth=0.7)
    axv.grid(False)
    vmax = max((b.get("v") or 0) for b in B) or 1
    for k, b in enumerate(B):
        c2 = UP if b["c"] >= b["o"] else DN
        ax.vlines(k, b["l"], b["h"], color=c2, linewidth=1.3)
        ax.add_patch(Rectangle((k - 0.38, min(b["o"], b["c"])), 0.76, max(abs(b["c"] - b["o"]), last * 1e-4), color=c2))
        axv.bar(k, (b.get("v") or 0) / vmax, color=c2, width=0.76, alpha=0.45)
    full = d.get("_closes") or [b["c"] for b in B]
    for n_, col, w_ in ((20, ORANGE, 1.6), (50, BLUE, 1.6)):
        e = ema(full, n_)[-m:]
        ax.plot(range(m), e, color=col, linewidth=w_, label=f"EMA {n_}")
    lo, hi = min(b["l"] for b in B), max(b["h"] for b in B)

    if lv:
        x0, x1 = m - 0.5, m + 11
        e_, s_, t_ = lv["entry"], lv["stop"], lv["take"]
        ax.add_patch(Rectangle((x0, min(e_, t_)), x1 - x0, abs(t_ - e_), color=UP, alpha=0.22, lw=0))
        ax.add_patch(Rectangle((x0, min(e_, s_)), x1 - x0, abs(s_ - e_), color=DN, alpha=0.22, lw=0))
        ax.hlines(e_, x0, x1, color="#ffffff", linewidth=1.4)
        ax.hlines(t_, x0, x1, color=UP, linewidth=1.4)
        ax.hlines(s_, x0, x1, color=DN, linewidth=1.4)
        for y, txt, col in ((t_, f"Цель {fmt(t_, dc)}  {pc(t_ / e_ - 1, 1)}", UP), (s_, f"Стоп {fmt(s_, dc)}  {pc(s_ / e_ - 1, 1)}", DN)):
            ax.text(x0 + 0.4, y, txt, color="#ffffff", fontsize=13, fontweight="bold", va="bottom" if (y - e_) * side > 0 else "top",
                    bbox=dict(boxstyle="round,pad=0.25", fc=col, ec="none", alpha=0.95))
        lo, hi = min(lo, s_, t_), max(hi, s_, t_)
    else:
        ax.axhline(last, color=MUTED, linewidth=1, linestyle=(0, (3, 3)))

    pad = (hi - lo) * 0.06 or 1
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, right)
    ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: fmt(v, dc if dc < 4 else 3)))
    # метка последней цены на правой оси, как в терминале
    ax.annotate(fmt(last, dc), xy=(1, last), xycoords=("axes fraction", "data"), xytext=(4, 0), textcoords="offset points",
                color="#ffffff", fontsize=12, fontweight="bold", va="center",
                bbox=dict(boxstyle="square,pad=0.25", fc=UP if B[-1]["c"] >= B[-1]["o"] else DN, ec="none"))
    plt.setp(ax.get_xticklabels(), visible=False)
    step = max(1, m // 6)
    axv.set_xticks(list(range(0, m, step)))
    axv.set_xticklabels([tlab(B[k]["t"])[:5] for k in range(0, m, step)], color=FG, fontsize=12)
    axv.set_yticks([])
    leg = ax.legend(loc="upper left", fontsize=12, facecolor=BG, edgecolor=BG, framealpha=0.6)
    for tx in leg.get_texts():
        tx.set_color(FG)
    chan = os.environ.get("TG_PUBLIC_CHAT", "")
    fig.text(0.04, 0.025, (chan + "  ·  " if chan.startswith("@") else "") + "не является инвестиционной рекомендацией", color=MUTED, fontsize=11)
    fig.savefig(out, facecolor=BG)
    plt.close(fig)


def main(src, out):
    d = json.load(open(src, encoding="utf-8"))
    t = d.get("t") or "РЫНОК"
    kind = d.get("kind") or ("index" if t == "РЫНОК" else "share")
    sec = "IMOEX" if t == "РЫНОК" else t
    daily = _bars(sec, kind, os.path.dirname(os.path.abspath(out)))
    last = daily[-1]["c"] if daily else None
    a = atr(daily[-15:]) if len(daily) > 2 else None
    chg = daily[-1]["c"] / daily[-2]["c"] - 1 if len(daily) > 1 else None
    side = d.get("side") or 0
    lv = levels(last, a, side)
    res = dict(png=None, last=last, atr=a, chg=chg, ld=(daily[-1].get("t") or "")[:10] if daily else None, lv={"1": levels(last, a, 1), "-1": levels(last, a, -1)})
    if lv:
        res.update(lv)
    if not d.get("noimg") and len(daily) >= 20:
        d["_closes"] = [b["c"] for b in daily]
        render(d, daily[-75:], lv, chg, out)
        res["png"] = out
    elif not d.get("noimg"):
        log("карточка: свечей нет или мало —", sec, len(daily), "— пост выйдет без картинки")
    print(json.dumps(res))
    return res


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
