# -*- coding: utf-8 -*-
"""Иллюстрация к новости паблика: карточка с подписью-мемом и графиком бумаги (или индекса) за 3 месяца.

Запуск: python card.py card.json out.png
card.json: {"t": "SBER" | "РЫНОК", "label": "Дивиденды", "caption": "...", "sign": 1|-1|0, "sector": "MOEXFN"}
Свои картинки, без чужих изображений и шаблонов мемов: нет вопросов с авторскими правами и узнаваемыми людьми.
"""
import json
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BG, FG, GRID, UP, DN, BLUE, plt, share_candles, index_candles, fmt, pc, log  # noqa: E402


def main(src, out):
    d = json.load(open(src, encoding="utf-8"))
    t = d.get("t") or "РЫНОК"
    sign = d.get("sign") or 0
    col = UP if sign > 0 else DN if sign < 0 else BLUE
    try:
        bars = share_candles(t, 130) if t != "РЫНОК" else index_candles("IMOEX", 130)
    except Exception as e:  # noqa: BLE001
        log("карточка: нет свечей", t, e)
        bars = []
    bars = bars[-90:]

    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor=BG)
    # верхняя плашка
    fig.patches.append(plt.Rectangle((0, 0.9), 1, 0.1, transform=fig.transFigure, color=col, alpha=0.9, zorder=0))
    who = "Индекс МосБиржи" if t == "РЫНОК" else "$" + t
    fig.text(0.035, 0.945, f"{'▲' if sign > 0 else '▼' if sign < 0 else '●'}  {who}  ·  {d.get('label', '')}", color="#ffffff",
             fontsize=24, fontweight="bold", va="center")
    # подпись-мем
    cap = (d.get("caption") or "").strip()
    size = 34 if len(cap) <= 60 else 29 if len(cap) <= 100 else 25
    lines = textwrap.wrap(cap, {34: 21, 29: 25, 25: 29}[size])[:6]
    fig.text(0.035, 0.82, "\n".join(lines), color="#ffffff", fontsize=size, fontweight="bold", va="top", linespacing=1.22)
    fact = (d.get("fact") or "").strip()
    if fact:
        fl = textwrap.wrap(fact, 52)[:4]
        if len(textwrap.wrap(fact, 52)) > 4:
            fl[-1] = fl[-1].rstrip(".,;: ") + "…"
        fig.text(0.035, 0.12, "\n".join(fl), color=FG, fontsize=14, va="bottom", linespacing=1.35)
    # график
    if len(bars) >= 10:
        ax = fig.add_axes([0.53, 0.14, 0.43, 0.68])
        ax.set_facecolor(BG)
        cl = [b["c"] for b in bars]
        x = range(len(cl))
        ax.plot(x, cl, color=col, linewidth=2.6)
        ax.fill_between(x, cl, min(cl) * 0.995, color=col, alpha=0.18)
        ax.scatter([len(cl) - 1], [cl[-1]], color="#ffffff", s=60, zorder=5, edgecolors=col, linewidths=2)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(colors=FG, labelsize=10, length=0)
        ax.grid(color=GRID, linewidth=0.6)
        step = max(1, len(bars) // 4)
        ax.set_xticks(list(range(0, len(bars), step)))
        ax.set_xticklabels([f"{b['t'][8:10]}.{b['t'][5:7]}" for b in bars[::step]])
        ax.yaxis.tick_right()
        ch3 = cl[-1] / cl[0] - 1
        ch1 = cl[-1] / cl[-2] - 1
        ax.set_title(f"{fmt(cl[-1], 2 if cl[-1] < 1000 else 0)}   {pc(ch1, 1)} за день   {pc(ch3, 1)} за 3 мес.", color=FG, fontsize=13, loc="left", pad=10)
    chan = os.environ.get("TG_PUBLIC_CHAT", "")
    fig.text(0.035, 0.05, (chan + "  ·  " if chan.startswith("@") else "") + "данные Мосбиржи · не инвестиционная рекомендация", color="#787b86", fontsize=12)
    fig.savefig(out, facecolor=BG)
    plt.close(fig)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
