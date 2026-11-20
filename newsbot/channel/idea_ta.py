# -*- coding: utf-8 -*-
"""Технический разбор бумаги «как AI Технический анализ / WarrenAI в Investing.com» — для любой акции Мосбиржи (TQBR).

Что считается (дневные свечи ISS за 10 лет, часовые за ~5 месяцев, недельные — из дневных):
  1. Цена: изменение за день / месяц / год, диапазон 52 недель.
  2. Тренд: цена к EMA20, SMA50, SMA200; 12 скользящих средних (SMA/EMA 5–200) на дневном и недельном графике —
     сколько «покупать» и «продавать» (правила Investing.com, модуль anomaly/ta_investing.py, проверка №19).
  3. Индикаторы: сводка 11 индикаторов (RSI, STOCH, STOCHRSI, MACD, ADX, Williams %R, CCI, Highs/Lows, UO, ROC, Bull/Bear Power),
     итог «активно продавать … активно покупать» на дне и неделе; сила тренда ADX и ±DI; перепроданность RSI / CCI / Williams %R;
     волатильность ATR.
  4. Свечные модели последних 5 баров на дневном и недельном графике.
  5. Уровни: сопротивления и поддержки (развороты дневных и часовых свечей, узлы объёма, средние, минимум/максимум 52 недель),
     трендовая линия (по двум последним разворотам), всё — на картинке со свечами, средними, объёмом и RSI.
  6. Сценарии — не «мнение», а статистика этой же бумаги: в похожих состояниях в прошлом (тот же тренд к SMA50 и та же зона RSI)
     куда цена уходила за 5 / 10 / 22 торговых дня (10–90% случаев, в ATR → в рублях от текущей цены) и что раньше достигалось —
     ближайшее сопротивление или ближайшая поддержка.
Новости, фундамент и дивиденды разбор не учитывает. Проверка №19: сами индикаторы Investing.com аномалии не предсказывают;
сводка «активно покупать/продавать» даёт лишь небольшой моментум-эффект на 5 дней (+0,48% против −0,19% к индексу, 13 из 13 лет).
Не является индивидуальной инвестиционной рекомендацией.
"""
import html
import io
import json
import os
import sys
import threading
import time
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "anomaly"))

LABEL = {2: "активно покупать", 1: "покупать", 0: "нейтрально", -1: "продавать", -2: "активно продавать"}
CDL = {"three": ("три белых солдата", "три чёрные вороны"), "engulf": ("бычье поглощение", "медвежье поглощение"),
       "harami": ("бычий харами", "медвежий харами"), "pierce": ("просвет в облаках", "завеса из тёмных облаков"),
       "mstar": ("утренняя звезда", "вечерняя звезда"), "hammer": ("молот", None), "star": (None, "падающая звезда")}
MAS = [5, 10, 20, 50, 100, 200]


# ---------------- данные ----------------
def frame(a):
    df = pd.DataFrame({k: a[k] for k in ("o", "h", "l", "c", "v")})
    df["t"] = pd.to_datetime([str(x)[:10] for x in a["t"]])
    return df.reset_index(drop=True)


def weekly(df):
    g = df.groupby(df.t.dt.to_period("W-FRI"))
    w = pd.DataFrame({"o": g.o.first(), "h": g.h.max(), "l": g.l.min(), "c": g.c.last(), "v": g.v.sum(), "t": g.t.last()})
    return w.reset_index(drop=True)


def fnum(x, nd=None):
    if x is None or x != x:
        return "—"
    if nd is None:
        nd = 2 if abs(x) < 1000 else 1 if abs(x) < 10000 else 0
        if abs(x) < 1:
            nd = 4
    s = f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def pct(x, nd=1, sign=True):
    return (f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%").replace(".", ",").replace("-", "−")


# ---------------- индикаторы ----------------
def ta_block(df):
    import ta_investing as TI
    F = TI.indicators(df[["o", "h", "l", "c"]])
    r = F.iloc[-1]
    c = float(df.c.iloc[-1])
    mas = {}
    for n in MAS:
        mas[f"SMA {n}"] = c / (1 + r[f"sma{n}"]) if r[f"sma{n}"] == r[f"sma{n}"] else np.nan
        mas[f"EMA {n}"] = c / (1 + r[f"ema{n}"]) if r[f"ema{n}"] == r[f"ema{n}"] else np.nan
    ma_buy = sum(1 for v in mas.values() if v == v and c > v)
    ma_sell = sum(1 for v in mas.values() if v == v and c < v)
    rsi, st, sr, wr, uo = r.rsi, r.stoch, r.stochrsi, r.willr, r.uo
    sig = [0 if rsi > 70 or rsi < 30 else (1 if rsi > 50 else -1), 0 if st > 80 or st < 20 else (1 if st > 50 else -1),
           0 if sr > 80 or sr < 20 else (1 if sr > 50 else -1), np.sign(r.macd), np.sign(r.di) if r.adx > 25 else 0,
           0 if wr > -20 or wr < -80 else (1 if wr > -50 else -1), 1 if r.cci > 100 else (-1 if r.cci < -100 else 0),
           np.sign(r.hl), 0 if uo > 70 or uo < 30 else (1 if uo > 50 else -1), np.sign(r.roc), np.sign(r.bbp)]
    sig = [0 if s != s else int(s) for s in sig]
    cd = []
    for j in range(1, min(6, len(F)) + 1):
        row = F.iloc[-j]
        for k, (bull, bear) in CDL.items():
            v = row.get("cdl_" + k, 0)
            nm = bull if v > 0 else bear if v < 0 else None
            if nm:
                cd.append((df.t.iloc[-j], nm, int(np.sign(v))))
        if row.get("cdl_doji", 0) > 0 and j == 1:
            cd.append((df.t.iloc[-j], "доджи (нерешительность)", 0))
    return dict(rsi=float(rsi), stoch=float(st), willr=float(wr), cci=float(r.cci), adx=float(r.adx), di=float(r.di),
                atr=float(r.atr) * c, ind_buy=sig.count(1), ind_sell=sig.count(-1), ind_n=len(sig), ma_buy=ma_buy, ma_sell=ma_sell,
                label=int(r.label), mas=mas, candles=cd, F=F)


# ---------------- уровни и трендовая линия ----------------
def pivots(h, l, k):
    hi = [i for i in range(k, len(h) - k) if h[i] == h[i - k:i + k + 1].max()]
    lo = [i for i in range(k, len(l) - k) if l[i] == l[i - k:i + k + 1].min()]
    return hi, lo


def trendline(df, look=160, k=5):
    """Нисходящая линия по двум последним снижающимся максимумам / восходящая по двум растущим минимумам.
    Линия засчитывается, если между опорами ни одно закрытие её не пересекло."""
    d = df.iloc[-look:].reset_index(drop=True)
    h, l, c = d.h.values, d.l.values, d.c.values
    hi, lo = pivots(h, l, k)
    out = []
    for kind, P, arr in (("down", hi, h), ("up", lo, l)):
        best = None
        for b in range(len(P) - 1, 0, -1):
            for a in range(b - 1, -1, -1):
                i, j = P[a], P[b]
                if j - i < 10:
                    continue
                if kind == "down" and not arr[i] > arr[j]:
                    continue
                if kind == "up" and not arr[i] < arr[j]:
                    continue
                slope = (arr[j] - arr[i]) / (j - i)
                line = arr[i] + slope * (np.arange(i, j + 1) - i)
                seg = c[i:j + 1]
                if (kind == "down" and (seg > line * 1.003).any()) or (kind == "up" and (seg < line * 0.997).any()):
                    continue
                best = (i, j, slope)
                break
            if best:
                break
        if best:
            i, j, slope = best
            now = arr[i] + slope * (len(d) - 1 - i)
            if now <= 0:
                continue
            after = c[j + 1:]
            lin_after = arr[i] + slope * (np.arange(j + 1, len(d)) - i)
            broken = bool(len(after) and ((kind == "down" and after[-1] > lin_after[-1]) or (kind == "up" and after[-1] < lin_after[-1])))
            off = len(df) - len(d)
            out.append(dict(kind=kind, i=off + i, j=off + j, p_i=float(arr[i]), slope=float(slope), now=float(now), broken=broken))
    return out


def level_list(a_d, a_h, df, atr, mas):
    import idea_judge as J
    c = float(df.c.iloc[-1])
    L = J.levels(a_d, a_h, atr)
    cand = [dict(px=x["px"], why="уровень разворотов" + (" и объёма" if x["touches"] >= 3 else ""), s=x["strength"], strong=x.get("strong", False))
            for x in L]
    for nm in ("EMA 20", "SMA 50", "SMA 200"):
        v = mas.get(nm)
        if v == v and v:
            cand.append(dict(px=v, why=nm, s=3.0, strong=nm != "EMA 20"))
    y = df.iloc[-250:]
    cand.append(dict(px=float(y.l.min()), why=f"минимум 52 нед. ({y.t.iloc[int(y.l.values.argmin())]:%d.%m.%Y})", s=5.0, strong=True))
    cand.append(dict(px=float(y.h.max()), why=f"максимум 52 нед. ({y.t.iloc[int(y.h.values.argmax())]:%d.%m.%Y})", s=5.0, strong=True))
    # склейка близких (0,3 ATR), подпись — по самому сильному
    cand.sort(key=lambda x: x["px"])
    merged = []
    for x in cand:
        if merged and x["px"] - merged[-1]["hi"] <= 0.3 * atr:
            g = merged[-1]
            g["hi"] = x["px"]
            g["s"] += x["s"]
            g["strong"] = g["strong"] or x["strong"]
            g["whys"].append(x["why"])
            g["pxs"].append(x["px"])
        else:
            merged.append(dict(lo=x["px"], hi=x["px"], s=x["s"], strong=x["strong"], whys=[x["why"]], pxs=[x["px"]]))
    for g in merged:
        g["px"] = float(np.mean(g["pxs"]))
        w = []
        for t in g["whys"]:
            if t not in w:
                w.append(t)
        g["why"] = ", ".join(w[:3])
    min_s = np.median([g["s"] for g in merged]) if merged else 0
    res = [g for g in merged if g["lo"] > c + 0.15 * atr and (g["s"] >= min_s or g["strong"])][:3]
    sup = [g for g in reversed(merged) if g["hi"] < c - 0.15 * atr and (g["s"] >= min_s or g["strong"])][:3]
    # цена у края годового диапазона — уровни из более старой истории (развороты недельных свечей)
    if len(res) < 2 or len(sup) < 2:
        wk = weekly(df)
        hi, lo = pivots(wk.h.values, wk.l.values, 3)
        old = [(float(wk.h.iloc[i]), wk.t.iloc[i]) for i in hi] + [(float(wk.l.iloc[i]), wk.t.iloc[i]) for i in lo]
        for side, lst in ((1, res), (-1, sup)):
            have = [g["px"] for g in lst]
            cands = sorted([(p, t) for p, t in old if (p - c) * side > 0.5 * atr], key=lambda x: abs(x[0] - c))
            for p, t in cands:
                if len(lst) >= 3:
                    break
                if all(abs(p - q) > 0.8 * atr for q in have):
                    lst.append(dict(px=p, lo=p, hi=p, s=3.0, strong=False, why=f"разворот недельных свечей ({t:%m.%Y})"))
                    have.append(p)
            lst.sort(key=lambda g: abs(g["px"] - c))
    return res, sup


# ---------------- сценарии по истории бумаги ----------------
def scenarios(df, atr_now, res, sup):
    c = df.c.values.astype(float)
    h, l = df.h.values.astype(float), df.l.values.astype(float)
    import ta_investing as TI
    F = TI.indicators(df[["o", "h", "l", "c"]])
    atr = (F.atr.values * c)
    rsi = F.rsi.values
    m50 = pd.Series(c).rolling(50).mean().values
    tr = np.sign(c - m50)
    zone = np.digitize(rsi, [30, 50, 70])
    n = len(c)
    i_now = n - 1
    same = (tr == tr[i_now]) & (zone == zone[i_now])
    out = dict(rows=[], n_sim=0)
    for H in (5, 10, 22):
        idx = np.arange(260, n - H)
        ok = np.isfinite(atr[idx]) & (atr[idx] > 0)
        idx = idx[ok]
        sim = idx[same[idx]]
        use = sim if len(sim) >= 80 else idx
        x = (c[use + H] - c[use]) / atr[use]
        q = np.quantile(x, [0.1, 0.5, 0.9])
        up = float((x > 0).mean())
        out["rows"].append(dict(H=H, lo=c[-1] + q[0] * atr_now, mid=c[-1] + q[1] * atr_now, hi=c[-1] + q[2] * atr_now, up=up, n=len(use),
                                similar=len(sim) >= 80))
    out["n_sim"] = int(same[260:n - 22].sum())
    # что раньше: ближайшее сопротивление или поддержка (за 22 дня), в ATR от цены
    if res and sup:
        du = (res[0]["px"] - c[-1]) / atr_now
        dd = (c[-1] - sup[0]["px"]) / atr_now
        idx = np.arange(260, n - 22)
        idx = idx[np.isfinite(atr[idx]) & (atr[idx] > 0)]
        sim = idx[same[idx]]
        use = sim if len(sim) >= 80 else idx
        first_up = first_dn = none = 0
        for i in use:
            U, D = c[i] + du * atr[i], c[i] - dd * atr[i]
            hu = np.nonzero(h[i + 1:i + 23] >= U)[0]
            hd = np.nonzero(l[i + 1:i + 23] <= D)[0]
            a_, b_ = (hu[0] if len(hu) else 99), (hd[0] if len(hd) else 99)
            if a_ == 99 and b_ == 99:
                none += 1
            elif a_ < b_:
                first_up += 1
            else:
                first_dn += 1  # в одном баре оба — считаем поддержку (осторожно)
        tot = max(1, len(use))
        out["race"] = dict(up=first_up / tot, dn=first_dn / tot, none=none / tot, res=res[0]["px"], sup=sup[0]["px"])
    return out


# ---------------- картинка ----------------
def chart(name, df, ta, res, sup, tl, bars=220):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = df.iloc[-bars:].reset_index(drop=True)
    off = len(df) - len(d)
    x = np.arange(len(d))
    c = df.c.astype(float)
    ema20 = c.ewm(span=20, adjust=False, min_periods=20).mean().values[off:]
    sma50 = c.rolling(50).mean().values[off:]
    sma200 = c.rolling(200).mean().values[off:]
    bg, fg, grid = "#131722", "#d1d4dc", "#2a2e39"
    green, red = "#26a69a", "#ef5350"
    fig = plt.figure(figsize=(12, 8.2), facecolor=bg)
    gs = fig.add_gridspec(3, 1, height_ratios=[5, 1, 1.3], hspace=0.04)
    ax = fig.add_subplot(gs[0])
    av = fig.add_subplot(gs[1], sharex=ax)
    ar = fig.add_subplot(gs[2], sharex=ax)
    for a_ in (ax, av, ar):
        a_.set_facecolor(bg)
        a_.tick_params(colors=fg, labelsize=9)
        for s in a_.spines.values():
            s.set_color(grid)
        a_.grid(color=grid, lw=0.6)
        a_.yaxis.tick_right()
    up = d.c >= d.o
    col = np.where(up, green, red)
    ax.vlines(x, d.l, d.h, color=col, lw=0.8)
    body_lo = np.minimum(d.o, d.c)
    body_h = np.maximum(np.abs(d.c - d.o), (d.h.max() - d.l.min()) * 0.0015)
    ax.bar(x, body_h, bottom=body_lo, color=col, width=0.7, linewidth=0)
    ax.plot(x, ema20, color="#2a7fff", lw=1.3, label=f"EMA 20  {fnum(ema20[-1])}")
    ax.plot(x, sma50, color="#f5a623", lw=1.3, label=f"SMA 50  {fnum(sma50[-1])}")
    ax.plot(x, sma200, color="#e8c547", lw=1.3, ls=(0, (4, 2)), label=f"SMA 200  {fnum(sma200[-1])}")
    atr = ta["atr"]
    ylo, yhi = d.l.min(), d.h.max()
    for g, colr, nm in [(g, red, f"Сопротивление {i + 1}") for i, g in enumerate(res)] + [(g, green, f"Поддержка {i + 1}") for i, g in enumerate(sup)]:
        lo_, hi_ = min(g["lo"], g["px"] - 0.15 * atr), max(g["hi"], g["px"] + 0.15 * atr)
        ax.axhspan(lo_, hi_, color=colr, alpha=0.10 if g["strong"] else 0.05, lw=0)
        ax.axhline(g["px"], color=colr, lw=0.9, ls=(0, (5, 3)), alpha=0.9)
        ax.text(len(d) + 1, g["px"], f"{nm}: {fnum(g['px'])}", color=colr, fontsize=8, va="bottom", ha="left")
        ylo, yhi = min(ylo, g["px"]), max(yhi, g["px"])
    for t in tl:
        i0 = t["i"] - off
        xs = np.array([max(0, i0), len(d) + 8])
        ys = t["p_i"] + t["slope"] * (xs - i0)
        colr = red if t["kind"] == "down" else green
        ax.plot(xs, ys, color=colr, lw=1.2, ls=(0, (6, 3)))
        lab = ("Нисходящая линия" if t["kind"] == "down" else "Восходящая линия") + (" (пробита)" if t["broken"] else "")
        ax.text(xs[1], ys[1], f" {lab}: {fnum(t['now'])}", color=colr, fontsize=8, va="top")
    last = float(d.c.iloc[-1])
    ax.axhline(last, color=fg, lw=0.6, ls=":")
    ax.annotate(f"{fnum(last)}", (len(d) - 1, last), xytext=(len(d) + 2, last), color=bg, fontsize=9, va="center",
                bbox=dict(boxstyle="round,pad=0.25", fc=green if up.iloc[-1] else red, ec="none"))
    pad = (yhi - ylo) * 0.05
    ax.set_ylim(ylo - pad, yhi + pad)
    ax.set_xlim(-1, len(d) + 42)
    leg = ax.legend(loc="upper left", fontsize=9, frameon=False)
    for t_ in leg.get_texts():
        t_.set_color(fg)
    ax.set_title(f"{name} — технический анализ, дневные свечи", color=fg, loc="left", fontsize=12)
    av.bar(x, d.v / 1e6, color=col, width=0.7, alpha=0.7)
    av.set_ylabel("оборот,\nмлн ₽", color=fg, fontsize=8)
    rsi = ta["F"].rsi.values[off:]
    ar.plot(x, rsi, color="#b388ff", lw=1.1)
    for lv in (30, 70):
        ar.axhline(lv, color=fg, lw=0.6, ls=":")
    ar.set_ylim(0, 100)
    ar.set_ylabel("RSI 14", color=fg, fontsize=8)
    ticks = [i for i in range(len(d)) if i == 0 or d.t.iloc[i].month != d.t.iloc[i - 1].month]
    ar.set_xticks(ticks)
    ar.set_xticklabels([d.t.iloc[i].strftime("%m.%y") for i in ticks])
    plt.setp(ax.get_xticklabels(), visible=False)
    plt.setp(av.get_xticklabels(), visible=False)
    fig.text(0.01, 0.005, "Статистика прошлого, не прогноз. Не является индивидуальной инвестиционной рекомендацией.", color="#787b86", fontsize=8)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=bg, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ---------------- разбор ----------------
def analyze(ticker, data=None):
    import idea_judge as J
    a_d, a_h = data or J.load(ticker)
    if a_d is None or len(a_d["c"]) < 260:
        return {"error": "мало истории (нужно ≥ 1 года дневных свечей)"}
    df = frame(a_d)
    wk = weekly(df)
    td, tw = ta_block(df), ta_block(wk)
    c = float(df.c.iloc[-1])
    atr = td["atr"]
    res, sup = level_list(a_d, a_h, df, atr, td["mas"])
    tl = trendline(df)
    tl = [t for t in tl if not t["broken"] or abs(t["now"] - c) <= 3 * atr]
    sc = scenarios(df, atr, res, sup)
    y = df.iloc[-250:]
    chg = dict(d=c / float(df.c.iloc[-2]) - 1, m=c / float(df.c.iloc[-22]) - 1, y=c / float(df.c.iloc[-250]) - 1,
               lo52=float(y.l.min()), hi52=float(y.h.max()))
    return dict(ticker=ticker, price=c, date=df.t.iloc[-1], chg=chg, d=td, w=tw, res=res, sup=sup, tl=tl, sc=sc, df=df)


def text(r, name):
    if r.get("error"):
        return f"⚠️ Теханализ {html.escape(name)}: {html.escape(r['error'])}"
    d, w, c, ch = r["d"], r["w"], r["price"], r["chg"]
    m = d["mas"]
    L = [f"📊 <b>Технический анализ {html.escape(name)}</b> — {fnum(c)} ₽ (закрытие/последняя цена {r['date']:%d.%m.%Y})",
         f"За день {pct(ch['d'])} · за месяц {pct(ch['m'])} · за год {pct(ch['y'])} · диапазон 52 нед.: {fnum(ch['lo52'])}–{fnum(ch['hi52'])}"]
    above = [nm for nm in ("EMA 20", "SMA 50", "SMA 200") if m.get(nm) == m.get(nm) and c > m[nm]]
    below = [nm for nm in ("EMA 20", "SMA 50", "SMA 200") if m.get(nm) == m.get(nm) and c < m[nm]]
    trend = ("цена выше всех ключевых средних — восходящий тренд" if not below else
             "цена ниже всех ключевых средних — нисходящий тренд" if not above else
             f"цена выше {', '.join(above)} и ниже {', '.join(below)} — тренд смешанный")
    L.append("")
    L.append("<b>Что говорит график</b>")
    L.append(f"• Тренд: {trend} (EMA 20 — {fnum(m['EMA 20'])}, SMA 50 — {fnum(m['SMA 50'])}, SMA 200 — {fnum(m['SMA 200'])}).")
    L.append(f"• Скользящие средние (12 шт.): день — покупать {d['ma_buy']}, продавать {d['ma_sell']}; неделя — {w['ma_buy']} / {w['ma_sell']}.")
    L.append(f"• Индикаторы (11 шт.): день — покупать {d['ind_buy']}, продавать {d['ind_sell']}; неделя — {w['ind_buy']} / {w['ind_sell']}. "
             f"Сводка как в Investing.com: день — «{LABEL[d['label']]}», неделя — «{LABEL[w['label']]}».")
    strong = "сильный" if d["adx"] >= 25 else "слабый"
    side = "нисходящий" if d["di"] < 0 else "восходящий"
    pdi = "+DI выше −DI" if d["di"] > 0 else "−DI выше +DI"
    L.append(f"• Сила тренда: ADX день {fnum(d['adx'], 1)} — {strong} {side} импульс ({pdi} на {fnum(abs(d['di']), 1)}); неделя {fnum(w['adx'], 1)}.")
    zone = ("перепроданность" if d["rsi"] < 30 else "близко к перепроданности" if d["rsi"] < 35 else "перекупленность" if d["rsi"] > 70
            else "близко к перекупленности" if d["rsi"] > 65 else "нейтральная зона")
    L.append(f"• RSI 14 — {fnum(d['rsi'], 1)} ({zone}), CCI — {fnum(d['cci'], 0)}, Williams %R — {fnum(d['willr'], 0)}. "
             "В сильном тренде перепроданность/перекупленность может держаться долго.")
    def uniq(lst, k, pref=""):
        seen, out = set(), []
        for t, nm, _ in lst:
            if nm not in seen:
                seen.add(nm)
                out.append(f"{nm} ({pref}{t:%d.%m})")
        return out[:k]
    cds, cws = uniq(d["candles"], 3), uniq(w["candles"], 2, "нед. ")
    if cds or cws:
        L.append("• Свечи: " + "; ".join(cds + cws) + ".")
    L.append(f"• Волатильность: дневной ATR {fnum(d['atr'])} ₽ ({pct(d['atr'] / c, 1, False)} за день); за 1–2 дня ход ±{fnum(d['atr'] * 1.1)} ₽ — обычный.")
    L.append("")
    L.append("<b>Уровни</b>")
    for i, g in enumerate(r["res"]):
        L.append(f"Сопротивление {i + 1}: {fnum(g['px'])}{' 💪' if g['strong'] else ''} — {g['why']} ({pct(g['px'] / c - 1)})")
    for i, g in enumerate(r["sup"]):
        L.append(f"Поддержка {i + 1}: {fnum(g['px'])}{' 💪' if g['strong'] else ''} — {g['why']} ({pct(g['px'] / c - 1)})")
    for t in r["tl"]:
        nm = "Нисходящая трендовая линия" if t["kind"] == "down" else "Восходящая трендовая линия"
        L.append(f"{nm}: сейчас ≈{fnum(t['now'])}" + (" — пробита" if t["broken"] else ""))
    sc = r["sc"]
    L.append("")
    L.append("<b>Сценарии по истории этой бумаги</b> (не гарантия)")
    sim = sc["rows"][0]["similar"]
    L.append(("Похожие дни в прошлом: тот же тренд к SMA 50 и та же зона RSI — " + f"{sc['n_sim']} дн" if sim else
              "Похожих дней мало — взяты все дни истории") + ". Куда цена уходила (80% случаев между):")
    for x in sc["rows"]:
        lab = {5: "Неделя (5 дн.)", 10: "2 недели (10 дн.)", 22: "Месяц (22 дн.)"}[x["H"]]
        L.append(f"• {lab}: {fnum(x['lo'])}–{fnum(x['hi'])}, середина {fnum(x['mid'])}; выше текущей закрывалась в {x['up']:.0%} случаев.")
    if sc.get("race"):
        rc = sc["race"]
        L.append(f"За месяц раньше дойдёт до сопротивления {fnum(rc['res'])}: ~{rc['up']:.0%}; до поддержки {fnum(rc['sup'])}: ~{rc['dn']:.0%}; "
                 f"ни туда ни туда: ~{rc['none']:.0%}.")
    L.append("")
    L.append("<i>Новости, фундамент и дивиденды не учитываются. Проверка на истории (№19): индикаторы Investing.com сами по себе почти не "
             "предсказывают движение — используйте разбор для уровней стопа и цели, а не как сигнал. "
             "Не является индивидуальной инвестиционной рекомендацией.</i>")
    return "\n".join(L)


# ---------------- отправка в Telegram ----------------
def tg_photo(PW, chat_id, png, caption=""):
    bnd = "----ta" + str(int(time.time() * 1000))
    parts = []
    for k, v in (("chat_id", str(chat_id)), ("caption", caption), ("parse_mode", "HTML")):
        parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{bnd}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"ta.png\"\r\nContent-Type: image/png\r\n\r\n".encode())
    parts.append(png + b"\r\n" + f"--{bnd}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{PW.TOKEN}/sendPhoto", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        res = json.loads(resp.read().decode())
    if not res.get("ok"):
        raise RuntimeError("sendPhoto: " + str(res.get("description")))


def send(PW, uid, ticker):
    """Картинка + текст разбора (вызывать в потоке: ISS бывает медленным)."""
    name = PW.tlabel(ticker) if hasattr(PW, "tlabel") else ticker
    try:
        r = analyze(ticker)
    except Exception as e:  # noqa: BLE001
        PW.say(uid, f"⚠️ Теханализ {html.escape(ticker)} не посчитан: {html.escape(str(e))[:200]}")
        return
    if not r.get("error"):
        try:
            png = chart(name, r["df"], r["d"], r["res"], r["sup"], r["tl"])
            tg_photo(PW, uid, png, f"📊 {html.escape(name)} — уровни, средние, трендовые линии")
        except Exception as e:  # noqa: BLE001
            PW.log("картинка теханализа не отправлена:", e) if hasattr(PW, "log") else None
    PW.say(uid, text(r, name))


def send_async(PW, uid, ticker):
    threading.Thread(target=send, args=(PW, uid, ticker), daemon=True).start()
