# -*- coding: utf-8 -*-
"""Иллюстрация к посту паблика: свечной график (1д / 4ч / 1ч) + подпись-мем + суть + уровни сделки.

Запуск: python card.py card.json out.png
card.json: {"t": "SBER" | "РЫНОК" | "BRZ6", "kind": "share|index|fut", "tf": "1h|4h|1d", "label": "Дивиденды",
            "caption": "...", "fact": "...", "sign": 1|-1|0, "side": 1|-1|0}
Печатает одну строку JSON: {"png": путь, "last": цена, "atr": ATR дневной, "chg": изменение за день, "entry", "stop", "take"}.
Свои картинки (без чужих изображений и шаблонов мемов): нет вопросов с авторскими правами и узнаваемыми людьми.
"""
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BG, FG, GRID, UP, DN, BLUE, ORANGE, plt, Rectangle, tf_bars, ema, atr, fmt, pc, tlab, log, TF_RU  # noqa: E402


def levels(last, a, side):
    """Вход по текущей цене, стоп за 1,5 дневного ATR (не ближе 1,5% и не дальше 8%), цель — 2 риска."""
    if not side or not last or not a:
        return None
    dist = min(max(1.5 * a, 0.015 * last), 0.08 * last)
    return dict(entry=last, stop=last - side * dist, take=last + side * 3 * dist, risk=dist / last)


def main(src, out):
    d = json.load(open(src, encoding="utf-8"))
    t = d.get("t") or "РЫНОК"
    tf = d.get("tf") or "1h"
    kind = d.get("kind") or ("index" if t == "РЫНОК" else "share")
    sec = "IMOEX" if t == "РЫНОК" else t
    sign = d.get("sign") or 0
    col = UP if sign > 0 else DN if sign < 0 else BLUE
    try:
        bars = tf_bars(sec, tf, kind)
        daily = bars if tf == "1d" else tf_bars(sec, "1d", kind)
    except Exception as e:  # noqa: BLE001
        log("карточка: нет свечей", sec, e)
        bars, daily = [], []
    last = bars[-1]["c"] if bars else None
    a = atr(daily[-15:]) if len(daily) > 2 else None
    chg = daily[-1]["c"] / daily[-2]["c"] - 1 if len(daily) > 1 else None
    lv = levels(last, a, d.get("side") or 0)

    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor=BG)
    fig.patches.append(plt.Rectangle((0, 0.9), 1, 0.1, transform=fig.transFigure, color=col, alpha=0.9, zorder=0))
    who = "Индекс МосБиржи" if t == "РЫНОК" else (d.get("name") or ("$" + t))
    fig.text(0.035, 0.945, f"{'▲' if sign > 0 else '▼' if sign < 0 else '●'}  {who}  ·  {d.get('label', '')}", color="#ffffff",
             fontsize=23, fontweight="bold", va="center")
    cap = (d.get("caption") or "").strip()
    size = 30 if len(cap) <= 60 else 26 if len(cap) <= 100 else 22
    lines = textwrap.wrap(cap, {30: 20, 26: 23, 22: 27}[size])[:6]
    fig.text(0.035, 0.84, "\n".join(lines), color="#ffffff", fontsize=size, fontweight="bold", va="top", linespacing=1.2)
    fact = (d.get("fact") or "").strip()
    yb = 0.11
    if lv:
        s = d["side"]
        fig.text(0.035, yb + 0.165, f"{'ЛОНГ' if s > 0 else 'ШОРТ'}  вход {fmt(lv['entry'], 2)}  ·  стоп {fmt(lv['stop'], 2)}  ·  тейк {fmt(lv['take'], 2)}",
                 color=UP if s > 0 else DN, fontsize=14.5, fontweight="bold")
    if fact:
        wl = textwrap.wrap(fact, 50)
        fl = wl[:4]
        if len(wl) > 4:
            fl[-1] = fl[-1].rstrip(".,;: ") + "…"
        fig.text(0.035, yb, "\n".join(fl), color=FG, fontsize=13, va="bottom", linespacing=1.3)

    if len(bars) >= 10:
        B = bars[-70:]
        ax = fig.add_axes([0.5, 0.14, 0.42, 0.68])
        ax.set_facecolor(BG)
        for s_ in ax.spines.values():
            s_.set_visible(False)
        ax.tick_params(colors=FG, labelsize=9.5, length=0)
        ax.grid(color=GRID, linewidth=0.6)
        for k, b in enumerate(B):
            c2 = UP if b["c"] >= b["o"] else DN
            ax.vlines(k, b["l"], b["h"], color=c2, linewidth=1)
            ax.add_patch(Rectangle((k - 0.36, min(b["o"], b["c"])), 0.72, max(abs(b["c"] - b["o"]), 1e-9), color=c2))
        cl = [b["c"] for b in bars]
        e20 = ema(cl, 20)[-len(B):]
        ax.plot(range(len(B)), e20, color=ORANGE, linewidth=1.3, label="EMA 20")
        lo, hi = min(b["l"] for b in B), max(b["h"] for b in B)
        m = len(B)
        if lv:
            for y, c3, lab, ls in ((lv["entry"], BLUE, "Вход", "--"), (lv["stop"], DN, "Стоп", "-"), (lv["take"], UP, "Тейк", "-")):
                ax.hlines(y, m - 8, m + 6, color=c3, linewidth=1.5, linestyles=ls)
                ax.text(m + 6.3, y, f"{lab} {fmt(y, 2)}", color=c3, va="center", fontsize=9, fontweight="bold")
                lo, hi = min(lo, y), max(hi, y)
        pad = (hi - lo) * 0.06 or 1
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_xlim(-1, m + (16 if lv else 2))
        step = max(1, m // 4)
        ax.set_xticks(list(range(0, m, step)))
        ax.set_xticklabels([tlab(B[k]["t"]) for k in range(0, m, step)])
        ax.yaxis.tick_right()
        leg = ax.legend(loc="upper left", fontsize=8.5, facecolor=BG, edgecolor=GRID)
        for tx in leg.get_texts():
            tx.set_color(FG)
        ax.set_title(f"Свечи {TF_RU.get(tf, tf)}   {fmt(last, 2 if last < 1000 else 0)}" + (f"   {pc(chg, 1)} за день" if chg is not None else ""),
                     color=FG, fontsize=12.5, loc="left", pad=10)
    chan = os.environ.get("TG_PUBLIC_CHAT", "")
    fig.text(0.035, 0.035, (chan + "  ·  " if chan.startswith("@") else "") + "данные Мосбиржи · не инвестиционная рекомендация", color="#787b86", fontsize=11)
    fig.savefig(out, facecolor=BG)
    plt.close(fig)
    res = dict(png=out, last=last, atr=a, chg=chg)
    if lv:
        res.update(lv)
    print(json.dumps(res))
    return res


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
