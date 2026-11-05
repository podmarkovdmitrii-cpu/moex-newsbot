# -*- coding: utf-8 -*-
"""Строгий разбор торговой идеи: вероятность цели, план частичных тейков, уровни подтверждения.

Вход: тикер, сторона, вход, стоп, тейк (общая цель). Всё считается по истории самой бумаги на Мосбирже (ISS):

  1. Вероятность цели. Геометрия идеи переводится в ATR (стоп = a_s·ATR, цель = a_t·ATR от входа) и прогоняется по
     каждому дню истории бумаги: вход по закрытию дня, дальше по high/low — что раньше, цель или стоп (если в одном баре
     оба — считается стоп). Отдельно — по дням, похожим на текущий (тот же тренд относительно SMA50 и наклон SMA50,
     похожее положение в диапазоне 60 дней); итог — сглаженная смесь «похожих» и «всех» дней. Если стоп меньше
     0,6 дневного ATR — считается на часовых свечах (дневные слишком грубые для короткого стопа).
  2. Строгие проверки: соотношение прибыль/риск и безубыточная вероятность, стоп внутри шума, цель слишком далеко
     для горизонта, вход далеко от цены, идея против тренда, препятствия (сильные уровни) между входом и целью,
     стоп сразу за уровнем или перед ним, ликвидность.
  3. План тейков (по проверке схем частичной фиксации на 2008–2026, см. claude/set-forget-course-test.md):
     прибыль/риск < 2 — один тейк; 2–3 — 50% на половине пути (не ближе 1R), затем стоп в безубыток;
     ≥ 3 — по трети на ~33/66/100%, после Т1 стоп в безубыток, после Т2 стоп на Т1. Тейки сдвигаются чуть раньше
     ближайшего сильного уровня (фиксация до уровня, а не на нём). Для каждого тейка — вероятность дойти до него.
  4. Уровни подтверждения: ближайший сильный уровень по ходу сделки между входом и целью. Закрепление за ним (закрытие
     дня или два закрытия часовых) — с пересчитанной вероятностью дойти до цели уже от этого уровня.

Это статистика прошлого, а не гарантия: формулировки «точно пойдём» бот не использует.
"""
import math

import numpy as np

try:
    import common as C
except Exception:  # noqa: BLE001  (для автономных тестов)
    C = None

H_D = 60     # горизонт, дневных баров (~3 месяца)
H_H = 140    # горизонт, часовых баров (~2–3 недели)
K_SHRINK = 60


# ---------------- данные ----------------
def _arr(B):
    o = np.array([b["o"] for b in B], float)
    h = np.array([b["h"] for b in B], float)
    l = np.array([b["l"] for b in B], float)
    c = np.array([b["c"] for b in B], float)
    v = np.array([b.get("v") or 0 for b in B], float)
    return dict(o=o, h=h, l=l, c=c, v=v, t=[b["t"] for b in B])


_CACHE = {}


def load(ticker):
    """Дневные свечи за 10 лет и часовые за ~5 месяцев; кэш на 30 минут."""
    import time
    hit = _CACHE.get(ticker)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    d = C.candles(f"engines/stock/markets/shares/boards/TQBR/securities/{ticker}/candles.json", 365 * 10, 24)
    try:
        hh = _arr(C.candles(f"engines/stock/markets/shares/boards/TQBR/securities/{ticker}/candles.json", 150, 60))
    except Exception:  # noqa: BLE001  — без часовых считаем по дневным
        hh = None
    res = (_arr(d), hh)
    _CACHE[ticker] = (time.time(), res)
    return res


# ---------------- индикаторы ----------------
def atr_series(a, n=14):
    h, l, c = a["h"], a["l"], a["c"]
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    out = np.full(len(c), np.nan)
    if len(c) > n:
        k = np.convolve(tr, np.ones(n) / n, mode="valid")
        out[n - 1:] = k
    return out


def sma(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.convolve(x, np.ones(n) / n, mode="valid")
    return out


def state(a):
    """Состояние на каждом баре: тренд к SMA50, наклон SMA50, позиция в диапазоне 60 баров."""
    c = a["c"]
    m50 = sma(c, 50)
    slope = np.r_[np.full(10, np.nan), m50[10:] - m50[:-10]]
    import pandas as pd
    hi = pd.Series(a["h"]).rolling(60).max().values
    lo = pd.Series(a["l"]).rolling(60).min().values
    pos = (c - lo) / np.where(hi - lo > 0, hi - lo, np.nan)
    return np.sign(c - m50), np.sign(slope), pos


# ---------------- вероятность ----------------
LAST_WIN_BARS = []  # сколько баров шли до цели в выигрышных случаях последнего расчёта (для оценки срока идеи)


def first_touch(a, atr, side, a_s, a_t, H, idx):
    """Для каждого бара i из idx: вход по закрытию, стоп a_s·ATR_i, цель a_t·ATR_i. Возвращает (цель, стоп, не дошло)."""
    LAST_WIN_BARS.clear()
    h, l, c = a["h"], a["l"], a["c"]
    n = len(c)
    win = loss = tout = 0
    for i in idx:
        A = atr[i]
        if not (A > 0) or i + 1 >= n:
            continue
        E = c[i]
        S = E - side * a_s * A
        T = E + side * a_t * A
        res = 0
        for k in range(i + 1, min(n, i + 1 + H)):
            if (side > 0 and l[k] <= S) or (side < 0 and h[k] >= S):
                res = -1
                break
            if (side > 0 and h[k] >= T) or (side < 0 and l[k] <= T):
                res = 1
                LAST_WIN_BARS.append(k - i)
                break
        if res > 0:
            win += 1
        elif res < 0:
            loss += 1
        elif i + H < n:
            tout += 1
    return win, loss, tout


def prob(a, side, a_s, a_t, H, step=1, similar=None):
    atr = atr_series(a)
    n = len(a["c"])
    idx_all = [i for i in range(60, n - 2, step) if atr[i] > 0]
    w, lo, to = first_touch(a, atr, side, a_s, a_t, H, idx_all)
    tot = w + lo + to
    p_all = w / tot if tot else float("nan")
    res = dict(p_all=p_all, n_all=tot, p_timeout=to / tot if tot else float("nan"),
               bars_med=float(np.median(LAST_WIN_BARS)) if LAST_WIN_BARS else None,
               bars_q=(float(np.percentile(LAST_WIN_BARS, 25)), float(np.percentile(LAST_WIN_BARS, 75))) if len(LAST_WIN_BARS) >= 8 else None)
    if similar is not None:
        idx_s = [i for i in idx_all if similar[i]]
        w2, lo2, to2 = first_touch(a, atr, side, a_s, a_t, H, idx_s)
        t2 = w2 + lo2 + to2
        p_s = w2 / t2 if t2 else float("nan")
        res.update(p_sim=p_s, n_sim=t2)
        res["p"] = (t2 * p_s + K_SHRINK * p_all) / (t2 + K_SHRINK) if t2 and tot else p_all
    else:
        res["p"] = p_all
    return res


def p_touch(a, dist_atr, H):
    """Вероятность, что цена хотя бы раз отойдёт на dist_atr ATR в нужную сторону за H баров (для исполнения входа)."""
    atr = atr_series(a)
    h, l, c = a["h"], a["l"], a["c"]
    n = len(c)
    hit = tot = 0
    for i in range(60, n - H - 1):
        A = atr[i]
        if not A > 0:
            continue
        tot += 1
        lvl = c[i] + dist_atr * A
        seg = slice(i + 1, i + 1 + H)
        if (dist_atr > 0 and h[seg].max() >= lvl) or (dist_atr < 0 and l[seg].min() <= lvl):
            hit += 1
    return hit / tot if tot else float("nan")


# ---------------- уровни ----------------
def levels(d, hh, atr_now):
    """Уровни: развороты дневных (последние 250 дней) и часовых (последние ~60 дней) свечей + узлы объёма часовых.
    Близкие (ближе 0,35 ATR) склеиваются; сила = касания + объём."""
    pts = []
    for a, look, w, k in ((d, 250, 2.0, 3), (hh, 900, 1.0, 5)):
        if a is None or len(a["c"]) < 30:
            continue
        h, l = a["h"][-look:], a["l"][-look:]
        for i in range(k, len(h) - k):
            if h[i] == h[i - k:i + k + 1].max():
                pts.append((h[i], w))
            if l[i] == l[i - k:i + k + 1].min():
                pts.append((l[i], w))
    if hh is not None and len(hh["c"]) > 100:
        c, v = hh["c"][-900:], hh["v"][-900:]
        bins = max(10, int((c.max() - c.min()) / (0.25 * atr_now)) + 1)
        hist, edges = np.histogram(c, bins=bins, weights=v)
        if hist.sum() > 0:
            for j in np.argsort(hist)[-6:]:
                pts.append(((edges[j] + edges[j + 1]) / 2, 3.0 * hist[j] / hist.max()))
    pts.sort()
    cl = []
    for p, w in pts:
        if cl and p - cl[-1]["hi"] <= 0.35 * atr_now:
            g = cl[-1]
            g["s"] += w
            g["n"] += 1
            g["hi"] = p
            g["sum"] += p * w
        else:
            cl.append(dict(lo=p, hi=p, s=w, n=1, sum=p * w))
    out = [dict(px=g["sum"] / g["s"], strength=g["s"], touches=g["n"]) for g in cl]
    if out:
        med = np.median([x["strength"] for x in out])
        for x in out:
            x["strong"] = x["strength"] >= max(4.0, 1.5 * med)
    return out


# ---------------- разбор ----------------
def rnd(p, tick):
    return round(round(p / tick) * tick, 6)


def tick_of(price):
    if price >= 1000:
        return 0.5
    if price >= 100:
        return 0.01
    if price >= 10:
        return 0.005
    if price >= 1:
        return 0.001
    return 0.00001


def plan_takes(side, E, S, T, lv, atr_now):
    risk = abs(E - S)
    path = abs(T - E)
    rr = path / risk
    tick = tick_of(E)
    if rr < 2:
        fr = [(1.0, 1.0)]
        rules = ["Цель ближе 2R — делить не стоит: два тейка на коротком пути почти обнуляют прибыль."]
    elif rr < 3:
        fr = [(max(0.5, 1 / rr), 0.5), (1.0, 0.5)]
        rules = ["На Т1 закрыть 50% и перенести стоп в безубыток (на вход)."]
    else:
        fr = [(max(1 / 3, 1 / rr), 1 / 3), (2 / 3, 1 / 3), (1.0, 1 / 3)]
        rules = ["На Т1 закрыть треть и перенести стоп в безубыток.", "На Т2 закрыть ещё треть и перенести стоп на Т1."]
    takes = []
    for f, w in fr:
        p = E + side * f * path
        moved = None
        if f < 1.0:  # фиксация чуть раньше сильного уровня, если он рядом (в пределах 0,5 ATR до тейка)
            for x in lv:
                if not x.get("strong"):
                    continue
                d = (x["px"] - p) * side  # >0 — уровень дальше тейка по ходу
                ahead = (x["px"] - E) * side
                if 0 < ahead and -0.1 * atr_now <= d <= 0.5 * atr_now:
                    q = x["px"] - side * 0.1 * atr_now
                    if (q - E) * side >= risk * 0.9:
                        p, moved = q, x["px"]
                    break
        takes.append(dict(px=T if f >= 1.0 else rnd(p, tick), share=w, frac=abs(p - E) / path, before_level=moved))
    if len(takes) > 1:
        rules.append("Безубыток — только после исполнения Т1: перенос «по пути» без фиксации на истории хуже обоих вариантов.")
    return takes, rules


def analyze(ticker, side, E, S, T, data=None, last=None):
    d, hh = data if data is not None else load(ticker)
    if len(d["c"]) < 250:
        return {"error": "истории по бумаге меньше года — статистика ненадёжна"}
    atr_d = atr_series(d)
    A = float(atr_d[-1])
    last = float(last or d["c"][-1])
    risk, path = abs(E - S), abs(T - E)
    rr = path / risk
    a_s, a_t = risk / A, path / A
    tr, sl, pos = state(d)
    cur = (tr[-1], sl[-1], pos[-1])
    similar = (tr == cur[0]) & (sl == cur[1]) & (np.abs(pos - cur[2]) <= 0.2)

    use_h = a_s < 0.6 and hh is not None and len(hh["c"]) > 400
    if use_h:
        atr_h = atr_series(hh)
        Ah = float(np.nanmedian(atr_h[-60:]))
        base, tf, Hz, scale = hh, "часовым свечам", H_H, Ah
        sim_mask = None
    else:
        base, tf, Hz, scale = d, "дневным свечам", H_D, A
        sim_mask = similar
    g_s, g_t = risk / scale, path / scale
    P = prob(base, side, g_s, g_t, Hz, step=1 if len(base["c"]) < 4000 else 2, similar=sim_mask)
    p = P["p"]
    be = risk / (risk + path)
    per_day = 1.0 if not use_h else max(1.0, len(hh["c"]) / max(1, len({x[:10] for x in hh["t"]})))
    term = None
    if P.get("bars_q"):
        lo_d, hi_d = (max(1, round(x / per_day)) for x in P["bars_q"])
        term = (lo_d, max(lo_d, hi_d))

    lv = levels(d, hh, A)
    lo_, hi_ = min(E, T), max(E, T)
    between = sorted([x for x in lv if lo_ < x["px"] < hi_], key=lambda x: abs(x["px"] - E))
    obstacles = [x for x in between if x.get("strong")]
    beyond_stop = [x for x in lv if (x["px"] - E) * side < 0 and abs(x["px"] - S) <= 0.5 * A]

    takes, rules = plan_takes(side, E, S, T, lv, A)
    step = 1 if len(base["c"]) < 4000 else 2
    for t in takes:
        if abs(t["px"] - T) < 1e-9:
            t["p"] = p
            continue
        t["p"] = prob(base, side, g_s, abs(t["px"] - E) / scale, Hz, step=step, similar=sim_mask)["p"]

    confirm = None
    zone = [x for x in between if 0.2 <= abs(x["px"] - E) / path <= 0.7]
    if zone:
        zs = [x for x in zone if x.get("strong")]
        L = (zs or zone)[0]
        dist_from_L = abs(T - L["px"]) / scale
        risk_from_L = abs(L["px"] - S) / scale
        r = prob(base, side, risk_from_L, dist_from_L, Hz, step=step, similar=sim_mask)
        confirm = dict(px=rnd(L["px"], tick_of(E)), strong=L.get("strong"), p=r["p"])

    # строгие проверки
    flags, score = [], 0
    if rr < 1.5:
        flags.append(("❌", f"Прибыль/риск {fmtp(round(rr, 2))} — мало: нужен процент попаданий выше {be:.0%}."))
        score -= 2
    elif rr < 2:
        flags.append(("⚠️", f"Прибыль/риск {fmtp(round(rr, 2))} — на грани; безубыточная вероятность {be:.0%}."))
        score -= 1
    else:
        flags.append(("✅", f"Прибыль/риск {fmtp(round(rr, 2))}; безубыточная вероятность {be:.0%}."))
        score += 1
    edge = p - be
    if edge >= 0.05:
        flags.append(("✅", f"Вероятность цели {p:.0%} выше безубыточной на {edge * 100:.0f} п.п."))
        score += 2
    elif edge >= 0:
        flags.append(("⚠️", f"Вероятность цели {p:.0%} — почти на уровне безубыточной ({be:.0%}): преимущества мало."))
    else:
        flags.append(("❌", f"Вероятность цели {p:.0%} ниже безубыточной ({be:.0%}): по истории бумаги такая геометрия в минусе."))
        score -= 2
    if a_s < 0.5:
        flags.append(("❌", f"Стоп {fmtp(round(a_s, 2))} дневного ATR — внутри дневного шума, велик шанс случайного выбивания."))
        score -= 1
    elif a_s < 0.8:
        flags.append(("⚠️", f"Стоп {fmtp(round(a_s, 2))} дневного ATR — короткий для свинга."))
    if a_t > 8:
        flags.append(("⚠️", f"Цель {fmtp(round(a_t, 1))} дневного ATR — далеко: {P['p_timeout']:.0%} похожих сделок не доходят ни до цели, ни до стопа за ~{Hz} баров."))
        score -= 1
    dl = (E - last) / A
    if abs(dl) > 1.5:
        pf = p_touch(d, dl, 10)
        flags.append(("⚠️", f"Вход в {fmtp(round(abs(dl), 1))} ATR от текущей цены {fmtp(last)}: шанс, что цена дойдёт до входа за 10 дней, ~{pf:.0%}."))
    if (side < 0 and cur[0] > 0 and cur[1] > 0) or (side > 0 and cur[0] < 0 and cur[1] < 0):
        flags.append(("⚠️", "Идея против тренда: цена " + ("выше растущей" if side < 0 else "ниже падающей") + " SMA50."))
        score -= 1
    elif cur[0] == -side and cur[1] == -side:
        pass
    elif cur[0] == side or cur[1] == side:
        flags.append(("✅", "Идея по направлению тренда (SMA50)."))
        score += 1
    if obstacles:
        n_o = len(obstacles)
        head = "До цели сильный уровень " if n_o == 1 else (f"До цели {n_o} сильных уровня: " if n_o < 5 else f"До цели {n_o} сильных уровней: ")
        flags.append(("⚠️", head + ", ".join(fmtp(rnd(x["px"], tick_of(E))) for x in obstacles[:3]) + " — цена может развернуться раньше."))
        score -= 1 if n_o >= 2 else 0
        near_t = [x for x in obstacles if 0 <= (T - x["px"]) * side <= 0.5 * A]
        if near_t:
            q = rnd(near_t[-1]["px"] - side * 0.1 * A, tick_of(E))
            flags.append(("⚠️", f"Цель стоит сразу за сильным уровнем {fmtp(rnd(near_t[-1]['px'], tick_of(E)))}: реалистичнее цель {fmtp(q)} — перед уровнем."))
    if beyond_stop:
        st = min(beyond_stop, key=lambda x: abs(x["px"] - S))
        if (st["px"] - S) * side > 0:  # уровень между стопом и входом, стоп за ним
            flags.append(("✅", f"Стоп за уровнем {fmtp(rnd(st['px'], tick_of(E)))} — хорошо."))
        else:
            q = rnd(st["px"] - side * 0.15 * A, tick_of(E))
            flags.append(("⚠️", f"Уровень {fmtp(rnd(st['px'], tick_of(E)))} сразу за стопом — стоп лучше вынести за него: {fmtp(q)}."))
    med_val = float(np.median(d["v"][-20:]))
    if 0 < med_val < 30e6:
        flags.append(("⚠️", f"Низкая ликвидность: медианный оборот {med_val / 1e6:.0f} млн ₽/день — проскальзывание."))
        score -= 1
    verdict = ("Сильная идея" if score >= 3 else "Приемлемая идея" if score >= 1 else
               "Слабая идея" if score >= -1 else "Не рекомендую публиковать")
    return dict(term=term, potential=path / E, ticker=ticker, side=side, E=E, S=S, T=T, last=last, atr=A, rr=rr, a_s=a_s, a_t=a_t, p=p, be=be, P=P, tf=tf, H=Hz,
                takes=takes, rules=rules, confirm=confirm, between=between[:6], obstacles=obstacles, flags=flags, score=score,
                verdict=verdict, levels=lv)


def fmtp(x):
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def report(r):
    if r.get("error"):
        return "Разбор невозможен: " + r["error"]
    side = "ЛОНГ" if r["side"] > 0 else "ШОРТ"
    P = r["P"]
    try:
        import names
        tl = names.label(r["ticker"])
    except Exception:  # noqa: BLE001
        tl = r["ticker"]
    L = [f"<b>Разбор идеи {tl} {side}</b>  вход {fmtp(r['E'])} · стоп {fmtp(r['S'])} · цель {fmtp(r['T'])}",
         f"Сейчас {fmtp(r['last'])}, дневной ATR {fmtp(round(r['atr'], 4))}. Стоп {fmtp(round(r['a_s'], 2))} ATR, цель {fmtp(round(r['a_t'], 2))} ATR.",
         "",
         f"<b>Вердикт: {r['verdict']}</b>",
         f"Вероятность дойти до цели раньше стопа: <b>{r['p']:.0%}</b> (горизонт ~{r['H']} баров по {r['tf']}).",
         (f"Срок идеи: обычно {r['term'][0]}–{r['term'][1]} торг. дн. (когда цель достигалась) · потенциал {fmtp(round(r['potential'] * 100, 1))}%" if r.get("term") else f"Потенциал {fmtp(round(r['potential'] * 100, 1))}%"),
         f"  все дни истории: {P['p_all']:.0%} из {P['n_all']}" + (f"; похожие на сейчас: {P['p_sim']:.0%} из {P['n_sim']}" if P.get('n_sim') else "")]
    L.append("")
    for ic, t in r["flags"]:
        L.append(f"{ic} {t}")
    L.append("")
    L.append("<b>План тейков</b>")
    for i, t in enumerate(r["takes"], 1):
        extra = f", перед уровнем {fmtp(rnd(t['before_level'], tick_of(r['E'])))}" if t.get("before_level") else ""
        L.append(f"Т{i} {fmtp(t['px'])} — закрыть {t['share']:.0%} ({t['frac']:.0%} пути{extra}); вероятность дойти {t['p']:.0%}")
    L += [f"• {x}" for x in r["rules"]]
    c = r.get("confirm")
    if c:
        L.append("")
        word = "ниже" if r["side"] < 0 else "выше"
        L.append(f"<b>Уровень подтверждения:</b> закрепление {word} {fmtp(c['px'])} (закрытие дня или два закрытия часовых "
                 f"{word}) — тогда вероятность дойти до цели раньше стопа ~{c['p']:.0%}" + (" (сильный уровень)" if c.get("strong") else "") + ".")
        L.append("Возврат за уровень после закрепления — сигнал фиксировать часть и подтянуть стоп.")
    if r["between"]:
        L.append("Уровни между входом и целью: " + ", ".join(f"{fmtp(rnd(x['px'], tick_of(r['E'])))}{'*' if x.get('strong') else ''}" for x in r["between"]) + "  (* — сильный)")
    L.append("")
    L.append("<i>Оценка — статистика прошлого этой бумаги, а не гарантия. Не является индивидуальной инвестиционной рекомендацией.</i>")
    return "\n".join(L)
