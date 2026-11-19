# -*- coding: utf-8 -*-
"""Вечерний разбор ошибок по закрытым в минус идеям (бот закрытых идей) — файл Excel администратору в 19:15 МСК.

Для каждой убыточной идеи, закрытой за день, проверяется набор факторов (подготовка идеи, исполнение, рынок). Каждый фактор,
который присутствует в сделке, проходит шесть уровней проверки:
  У1 Данные  — нужные свечи/поля есть и покрывают период (вход → закрытие), без пропусков;
  У2 Сверка  — значение подтверждено вторым независимым расчётом или источником (часовые ↔ дневные свечи, поля идеи ↔ журнал изменений,
               цена выхода по итогу ↔ цены, до которых реально доходили свечи);
  У3 Время   — для факторов до входа: используются только данные, известные до входа (без заглядывания вперёд);
               для факторов после входа: событие случилось после входа и до закрытия;
  У4 Рынок   — связь фактора с убытками доказана на истории рынка: 20 самых ликвидных акций (тот же стоп и цель, данные до дня входа)
               или проверки проекта (№11, №13, №20, №23);
  У5 Бумага  — то же на истории самой бумаги (до дня входа) либо механическая проверка по этой сделке;
  У6 Ваши сделки — на ваших закрытых идеях фактор чаще встречается у убыточных (нужно ≥ 5 идей с фактором и без).
Вердикт: «подтверждён» — пройдены все шесть; «подтверждён, ваших сделок мало» — У1–У5 пройдены, для У6 мало данных;
«не подтверждён» — хотя бы один уровень не пройден (в файле видно, какой и почему).
Порог «связи» на истории: ожидание сделки (в R, выход по стопу, цели или через 60 дней, издержки 0,1%) с фактором хуже, чем без него,
на ≥ 0,05R и больше двух погрешностей (с поправкой на перекрытие окон), ≥ 30 случаев, и в обеих половинах истории знак тот же. Новости не проверяются — нет надёжного архива с временем публикации.
Это статистика прошлого, а не гарантия; файл — для самоанализа, не инвестиционная рекомендация.
"""
import datetime as dt
import io
import json
import os
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MSK = 3 * 3600
POOL = ["SBER", "GAZP", "LKOH", "GMKN", "NVTK", "ROSN", "TATN", "SNGS", "PLZL", "YDEX", "MGNT", "MTSS", "ALRS", "CHMF", "NLMK",
        "MOEX", "VTBR", "AFLT", "RUAL", "PHOR"]
MIN_N, MIN_DIFF = 30, 0.05
OK, BAD, FEW = "✓", "✗", "?"
_POOL = {}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[разбор ошибок]", *a, flush=True)


def msk_date(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + MSK))


def msk_dt(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(ts) + MSK))


# ---------------- данные ----------------
def _J():
    import idea_judge as J
    return J


def _arr(B):
    return _J()._arr(B)


def index_data():
    import common as C
    p = "engines/stock/markets/index/securities/IMOEX/candles.json"
    d = _arr(C.candles(p, 365 * 10, 24))
    try:
        h = _arr(C.candles(p, 150, 60))
    except Exception:  # noqa: BLE001
        h = None
    return d, h


def pool_data():
    """Дневные свечи 20 ликвидных бумаг (для уровня «Рынок»); кэш на процесс."""
    if _POOL:
        return _POOL
    J = _J()
    for t in POOL:
        try:
            _POOL[t] = J.load(t)[0]
        except Exception as e:  # noqa: BLE001
            log("нет свечей", t, e)
    return _POOL


def cut(a, date):
    """Свечи строго до даты date (YYYY-MM-DD)."""
    n = sum(1 for x in a["t"] if x[:10] < date)
    return {k: v[:n] for k, v in a.items()}


def window(a, t0, t1):
    """Свечи с временем в [t0, t1] (строки 'YYYY-MM-DD HH:MM'); для дневных сравнивается дата."""
    if a is None:
        return None
    intraday = bool(a["t"]) and len(a["t"][0]) > 10
    if intraday:
        idx = [i for i, x in enumerate(a["t"]) if t0[:16] <= x[:16] <= t1[:16]]
    else:
        idx = [i for i, x in enumerate(a["t"]) if t0[:10] <= x[:10] <= t1[:10]]
    if not idx:
        return {k: (np.array([]) if k != "t" else []) for k in a}
    i0, i1 = idx[0], idx[-1] + 1
    return {k: v[i0:i1] for k, v in a.items()}


# ---------------- признаки состояния на каждом баре ----------------
def states(a):
    J = _J()
    c, h, l = a["c"], a["h"], a["l"]
    atr = J.atr_series(a)
    tr, sl, pos = J.state(a)
    import pandas as pd
    d = pd.Series(c).diff()
    up, dn = d.clip(lower=0).rolling(14).mean(), (-d.clip(upper=0)).rolling(14).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).values
    mom5 = (c - np.r_[np.full(5, np.nan), c[:-5]]) / atr
    atrp = pd.Series(atr / c).rolling(250, min_periods=120).rank(pct=True).values
    val = pd.Series(a["v"]).rolling(60, min_periods=20).median().values
    return dict(atr=atr, tr=tr, sl=sl, pos=pos, rsi=rsi, mom5=mom5, atrp=atrp, val=val)


STATE_COND = {
    "против тренда": lambda s, side: (s["tr"] == -side) & (s["sl"] == -side),
    "вход после сильного хода (догоняние)": lambda s, side: s["mom5"] * side > 2,
    "вход у края 60-дневного диапазона": lambda s, side: (s["pos"] > 0.9) if side > 0 else (s["pos"] < 0.1),
    "перекупленность / перепроданность (RSI)": lambda s, side: (s["rsi"] > 70) if side > 0 else (s["rsi"] < 30),
    "высокая волатильность бумаги": lambda s, side: s["atrp"] > 0.8,
}


def ev_counts(a, atr, side, a_s, a_t, idx, H=60, cost=0.001):
    """Среднее R на сделку: вход по закрытию бара i, стоп a_s·ATR, цель a_t·ATR, выход по закрытию через H баров, если ничего
    не задето (стоп и цель в одном баре — стоп); издержки на круг. Учитываются только входы с полным окном H. → (R, число)."""
    from numpy.lib.stride_tricks import sliding_window_view
    idx = np.asarray([i for i in idx if i + H < len(a["c"])], dtype=int)
    if not len(idx):
        return 0.0, 0, 0.0
    h, l, c = a["h"], a["l"], a["c"]
    Wh = sliding_window_view(h[1:], H)[idx]
    Wl = sliding_window_view(l[1:], H)[idx]
    E, A = c[idx], atr[idx]
    S = (E - side * a_s * A)[:, None]
    T = (E + side * a_t * A)[:, None]
    stop, take = (Wl <= S, Wh >= T) if side > 0 else (Wh >= S, Wl <= T)
    big = H + 1
    fs = np.where(stop.any(1), stop.argmax(1), big)
    ft = np.where(take.any(1), take.argmax(1), big)
    R = (c[idx + H] - E) * side / (a_s * A)
    R = np.where((fs < big) & (fs <= ft), -1.0, np.where(ft < fs, a_t / a_s, R))
    R = R - cost * E / (a_s * A)
    return float(R.mean()), int(len(R)), float((R ** 2).mean())


def hist_test(series, side, a_s, a_t, cond=None, alt=None):
    """Сравнение ожидания (R на сделку) на истории: cond — маска фактора (с фактором vs без); alt — (a_s, a_t) рекомендованной
    геометрии (ваша геометрия vs рекомендованная на тех же днях). series — список дневных рядов (уже обрезанных до дня входа).
    → dict(diff, n, halves=(d1, d2), ok, note)."""
    acc = {"w": [0.0, 0, 0.0], "wo": [0.0, 0, 0.0], "h1": [[0.0, 0, 0.0], [0.0, 0, 0.0]], "h2": [[0.0, 0, 0.0], [0.0, 0, 0.0]]}

    def add(slot, ev, n, sq=0.0):
        slot[0] += ev * n
        slot[1] += n
        slot[2] += sq * n
    for a in series:
        if len(a["c"]) < 300:
            continue
        s = states(a)
        atr = s["atr"]
        n = len(a["c"])
        base = np.arange(260, n - 2, 2)
        base = base[np.isfinite(atr[base]) & (atr[base] > 0)]
        if not len(base):
            continue
        mid = base[len(base) // 2]
        if cond is not None:
            m = np.nan_to_num(cond(s, side)[base].astype(float)) > 0
            g1, g2 = base[m], base[~m]
            for idx_, key in ((g1, "w"), (g2, "wo")):
                add(acc[key], *ev_counts(a, atr, side, a_s, a_t, idx_))
            for hk, part in (("h1", base < mid), ("h2", base >= mid)):
                add(acc[hk][0], *ev_counts(a, atr, side, a_s, a_t, base[part & m]))
                add(acc[hk][1], *ev_counts(a, atr, side, a_s, a_t, base[part & ~m]))
        else:
            add(acc["w"], *ev_counts(a, atr, side, a_s, a_t, base))
            add(acc["wo"], *ev_counts(a, atr, side, alt[0], alt[1], base))
            for hk, part in (("h1", base < mid), ("h2", base >= mid)):
                add(acc[hk][0], *ev_counts(a, atr, side, a_s, a_t, base[part]))
                add(acc[hk][1], *ev_counts(a, atr, side, alt[0], alt[1], base[part]))

    def m(s):
        return s[0] / s[1] if s[1] else float("nan")
    ew, ewo = m(acc["w"]), m(acc["wo"])
    d = ew - ewo
    h = (m(acc["h1"][0]) - m(acc["h1"][1]), m(acc["h2"][0]) - m(acc["h2"][1]))
    n = acc["w"][1]

    def var_eff(sl):  # окна по 60 дней с шагом 2 перекрываются: независимых наблюдений ≈ n·2/60
        if not sl[1]:
            return float("inf")
        mu = sl[0] / sl[1]
        return max(0.0, sl[2] / sl[1] - mu * mu) / max(1.0, sl[1] * 2 / 60)
    se = float(np.sqrt(var_eff(acc["w"]) + var_eff(acc["wo"]))) if cond is not None else float(np.sqrt(var_eff(acc["w"]) * 0.5))
    ok = n >= MIN_N and d <= -MIN_DIFF and d <= -2 * se and all(x == x and x < 0 for x in h)
    lab = ("с фактором" if cond is not None else "ваша геометрия"), ("без" if cond is not None else "рекомендованная (2,5 ATR, 5R)")
    note = (f"{lab[0]} {ew:+.2f}R, {lab[1]} {ewo:+.2f}R (разница {d:+.2f}R, двойная погрешность ±{2 * se:.2f}R, случаев {n}; "
            f"половины истории {h[0]:+.2f} / {h[1]:+.2f})"
            if n else "нет случаев на истории")
    if n and n < MIN_N:
        note += f" — меньше {MIN_N} случаев"
    return dict(diff=d, n=n, halves=h, ok=ok, note=note.replace(".", ",").replace("nan", "н/д"))


# ---------------- контекст сделки ----------------
def context(iid, v, ideas, idx_data, divh, sec_of):
    import ideas_calc as IC
    J = _J()
    side, E = int(v["side"]), float(v["entry"])
    S0 = float(v.get("stop0") or v["stop"])
    T = [float(x) for x in v["takes"]]
    t_open, t_close = int(v.get("t") or 0), int(v.get("closed_t") or v.get("t") or 0)
    d0, d1 = msk_date(t_open), msk_date(t_close)
    res = IC.result(v)
    lv_pct, _ = IC.by_levels(dict(v, closed=True))
    d, hh = J.load(v["ticker"])
    pre = cut(d, d0)
    st = states(pre) if len(pre["c"]) > 60 else None
    A = float(st["atr"][-1]) if st is not None and np.isfinite(st["atr"][-1]) else None
    path_d = window(d, d0, d1)
    path_h = window(hh, msk_dt(t_open), msk_dt(t_close)) if hh is not None else None
    risk = abs(E - S0)
    k1 = sum(1 for x in d["t"] if x[:10] <= d1)
    after = {k: val[k1:] for k, val in d.items()}   # дни после дня закрытия
    return dict(iid=iid, v=v, ideas=ideas, side=side, E=E, S0=S0, T=T, t_open=t_open, t_close=t_close, d0=d0, d1=d1, res=res, lv_pct=lv_pct,
                d=d, hh=hh, pre=pre, st=st, A=A, path_d=path_d, path_h=path_h, risk=risk, after=after, idx=idx_data, divh=divh, sec_of=sec_of,
                rr=abs(T[-1] - E) / risk if risk else None)


def lev(code, ok, note):
    return (code, ok, note)


def personal(ctx, pred):
    """У6: доля убыточных среди ваших закрытых идей с фактором и без. pred(v) → True/False/None (None — не определить)."""
    import ideas_calc as IC
    w, wo = [], []
    for k, v in ctx["ideas"].items():
        if not v.get("closed"):
            continue
        try:
            f = pred(v)
        except Exception:  # noqa: BLE001
            f = None
        if f is None:
            continue
        (w if f else wo).append(IC.result(v)["pct"] < 0)
    if len(w) < 5 or len(wo) < 5:
        return lev("У6", FEW, f"мало ваших сделок: с фактором {len(w)}, без {len(wo)} (нужно ≥5 и ≥5)")
    a, b = np.mean(w), np.mean(wo)
    return lev("У6", OK if a > b else BAD, f"убыточных с фактором {a:.0%} ({len(w)}), без {b:.0%} ({len(wo)})")


def _state_at_entry(v):
    """Признаки состояния на день входа для любой вашей идеи (для У6)."""
    J = _J()
    d, _ = J.load(v["ticker"])
    pre = cut(d, msk_date(int(v.get("t") or 0)))
    if len(pre["c"]) < 300:
        return None
    s = states(pre)
    return {k: (x[-1] if hasattr(x, "__len__") else x) for k, x in s.items()}, pre


def factor_state(ctx, name):
    st, side, A = ctx["st"], ctx["side"], ctx["A"]
    cond = STATE_COND[name]
    present = bool(np.nan_to_num(cond({k: v[-1:] for k, v in st.items()}, side).astype(float))[0] > 0) if st is not None else False
    if not present:
        return dict(present=False)
    s_last = {k: float(v[-1]) for k, v in st.items()}
    val = {"против тренда": f"цена {'ниже' if s_last['tr'] < 0 else 'выше'} SMA50, наклон {'вниз' if s_last['sl'] < 0 else 'вверх'}",
           "вход после сильного хода (догоняние)": f"ход за 5 дней {s_last['mom5']:+.1f} ATR",
           "вход у края 60-дневного диапазона": f"положение в диапазоне {s_last['pos']:.0%}",
           "перекупленность / перепроданность (RSI)": f"RSI14 = {s_last['rsi']:.0f}",
           "высокая волатильность бумаги": f"ATR {A / ctx['pre']['c'][-1]:.1%} цены — выше 80% дней за год"}[name]
    L = [lev("У1", OK if len(ctx["pre"]["c"]) >= 300 else BAD, f"дневных свечей до входа: {len(ctx['pre']['c'])}")]
    # У2: тот же признак по часовым свечам последнего дня (закрытие часовой ≈ закрытие дня) — сверка цены закрытия
    hh = ctx["hh"]
    if hh is not None:
        prev = [i for i, t in enumerate(hh["t"]) if t[:10] < ctx["d0"]]
        if prev:
            ch = float(hh["c"][prev[-1]])
            cd = float(ctx["pre"]["c"][-1])
            dev = abs(ch / cd - 1)
            L.append(lev("У2", OK if dev < 0.01 else BAD, f"закрытие дня {cd:g} ↔ последняя часовая {ch:g} (расхождение {dev:.2%})"))
        else:
            L.append(lev("У2", BAD, "нет часовых свечей до входа для сверки"))
    else:
        L.append(lev("У2", BAD, "нет часовых свечей для сверки"))
    L.append(lev("У3", OK, f"по закрытию {ctx['pre']['t'][-1][:10]} — до входа {msk_dt(ctx['t_open'])}"))
    a_s, a_t = ctx["risk"] / A, abs(ctx["T"][-1] - ctx["E"]) / A
    pool = [cut(a, ctx["d0"]) for t, a in pool_data().items() if t != ctx["v"]["ticker"]]
    r4 = hist_test(pool, side, a_s, a_t, cond=cond)
    study = " Проверка №23: входы против тренда убыточны при любых стопах и тейках." if name == "против тренда" else ""
    L.append(lev("У4", OK if r4["ok"] else BAD, f"20 ликвидных акций: {r4['note']}.{study}"))
    r5 = hist_test([ctx["pre"]], side, a_s, a_t, cond=cond)
    L.append(lev("У5", OK if r5["ok"] else BAD, f"история {ctx['v']['ticker']}: {r5['note']}"))

    def pred(v):
        x = _state_at_entry(v)
        if x is None:
            return None
        s, _ = x
        return bool(np.nan_to_num(cond({k: np.array([val]) for k, val in s.items()}, int(v["side"])).astype(float))[0] > 0)
    L.append(personal(ctx, pred))
    return dict(present=True, value=val, levels=L)


def factor_geom(ctx, name):
    A, risk, rr = ctx["A"], ctx["risk"], ctx["rr"]
    if not A:
        return dict(present=False)
    if name == "стоп в зоне шума (< 1,5 ATR)":
        present, val = risk / A < 1.5, f"стоп {risk / A:.2f} ATR от входа"
        study = "Проверка №23: стопы 1–1,5 ATR и 3% — худшие, 2–2,5 ATR и «за уровнем» — лучшие."
        pred = lambda v: abs(float(v["entry"]) - float(v.get("stop0") or v["stop"])) / _atr_at(v) < 1.5  # noqa: E731
    else:
        present, val = rr is not None and rr < 1.5, f"цель {rr:.2f}R" if rr else "—"
        study = "Проверки №20, №23: тейки 1–1,5R убыточны, далёкий тейк (4–5R) лучше."
        pred = lambda v: abs(float(v["takes"][-1]) - float(v["entry"])) / abs(float(v["entry"]) - float(v.get("stop0") or v["stop"])) < 1.5  # noqa: E731
    if not present:
        return dict(present=False)
    v = ctx["v"]
    L = [lev("У1", OK if ctx["st"] is not None else BAD, f"ATR на день входа {A:g}, вход {ctx['E']:g}, стоп {ctx['S0']:g}, цель {ctx['T'][-1]:g}")]
    stop_hist = v.get("stop0") is not None
    L.append(lev("У2", OK if stop_hist and abs(float(v.get("risk_pct") or 0) - risk / ctx["E"] * 100) < 0.05 else BAD,
                 f"риск по полям идеи {v.get('risk_pct')}% ↔ пересчёт {risk / ctx['E'] * 100:.2f}% (первоначальный стоп {'есть' if stop_hist else 'не сохранён'})"))
    L.append(lev("У3", OK, "уровни заданы при публикации, ATR — по закрытию дня до входа"))
    a_s, a_t = risk / A, abs(ctx["T"][-1] - ctx["E"]) / A
    pool = [cut(a, ctx["d0"]) for t, a in pool_data().items() if t != v["ticker"]]
    r4 = hist_test(pool, ctx["side"], a_s, a_t, alt=(2.5, 12.5))
    L.append(lev("У4", OK if r4["ok"] else BAD, f"20 ликвидных акций: {r4['note']}. {study}"))
    r5 = hist_test([ctx["pre"]], ctx["side"], a_s, a_t, alt=(2.5, 12.5))
    L.append(lev("У5", OK if r5["ok"] else BAD, f"история {v['ticker']}: {r5['note']}"))
    L.append(personal(ctx, pred))
    return dict(present=True, value=val, levels=L)


_ATR = {}


def _atr_at(v):
    key = (v["ticker"], msk_date(int(v.get("t") or 0)))
    if key not in _ATR:
        x = _state_at_entry(v)
        _ATR[key] = float(x[0]["atr"]) if x else float("nan")
    return _ATR[key]


def factor_far_entry(ctx):
    A = ctx["A"]
    if not A:
        return dict(present=False)
    last = float(ctx["pre"]["c"][-1])
    dist = abs(ctx["E"] - last) / A
    if dist <= 1:
        return dict(present=False)
    return dict(present=True, value=f"вход {ctx['E']:g} в {dist:.1f} ATR от закрытия {last:g}", levels=[
        lev("У1", OK, "закрытие до входа и ATR есть"),
        lev("У2", OK if ctx["hh"] is not None else BAD, "сверка закрытия по часовым — см. факторы состояния" if ctx["hh"] is not None else "нет часовых"),
        lev("У3", OK, "известно до входа"),
        lev("У4", BAD, "на истории рынка не проверено (нет исследования лимитных входов далеко от цены)"),
        lev("У5", BAD, "на истории бумаги не проверяется"),
        personal(ctx, lambda v: abs(float(v["entry"]) - float(_state_at_entry(v)[1]["c"][-1])) / _atr_at(v) > 1 if _state_at_entry(v) else None)])


def factor_judge(ctx, name):
    pl = ctx["v"].get("plan") or {}
    if name == "разбор бота давал низкую вероятность цели":
        p = pl.get("p")
        if p is None or p >= 0.35:
            return dict(present=False)
        return dict(present=True, value=f"вероятность цели по разбору {p:.0%}", levels=[
            lev("У1", OK, "разбор сохранён в идее"),
            lev("У2", OK if pl.get("takes") else BAD, "план тейков из того же разбора сохранён" if pl.get("takes") else "кроме вероятности, разбор не сохранён"),
            lev("У3", OK, "разбор сделан до публикации"),
            lev("У4", BAD, "калибровка вероятностей разбора на рынке не проверялась"),
            lev("У5", BAD, "на истории бумаги не проверяется"),
            personal(ctx, lambda v: ((v.get("plan") or {}).get("p") or 1) < 0.35 if (v.get("plan") or {}).get("p") is not None else None)])
    an = pl.get("anom") or {}
    if not an.get("against"):
        return dict(present=False)
    return dict(present=True, value="модель аномалий ждала движения против идеи", levels=[
        lev("У1", OK, "сводка модели сохранена в идее"),
        lev("У2", BAD, "второго источника нет — сохранена только итоговая метка"),
        lev("У3", OK, "прогноз на утро дня публикации"),
        lev("У4", OK, "точность направления модели на 2021–2026 — 74% (проверка №21)"),
        lev("У5", BAD, "по отдельной бумаге точность не считается"),
        personal(ctx, lambda v: bool(((v.get("plan") or {}).get("anom") or {}).get("against")) if (v.get("plan") or {}).get("anom") is not None else None)])


def factor_tilt(ctx):
    import ideas_calc as IC
    prev = [v for k, v in ctx["ideas"].items() if v.get("closed") and v is not ctx["v"] and
            0 <= ctx["t_open"] - int(v.get("closed_t") or 0) <= 48 * 3600 and IC.result(v)["pct"] < 0]
    if not prev:
        return dict(present=False)
    return dict(present=True, value=f"за 48 ч до входа закрыто в минус: {len(prev)} ({', '.join(v['ticker'] for v in prev)})", levels=[
        lev("У1", OK, "время закрытия прошлых идей сохранено"),
        lev("У2", OK if all(v.get("result") for v in prev) else BAD, "минус подтверждён итогом, который вы написали при закрытии" if all(v.get("result") for v in prev)
            else "у части прошлых идей нет итога автора — минус посчитан только по уровням"),
        lev("У3", OK, "убыток был до входа"),
        lev("У4", BAD, "на истории рынка не проверяется (поведенческий фактор)"),
        lev("У5", BAD, "к бумаге не относится"),
        personal(ctx, lambda v: any(w.get("closed") and w is not v and 0 <= int(v.get("t") or 0) - int(w.get("closed_t") or 0) <= 48 * 3600
                                    and IC.result(w)["pct"] < 0 for w in ctx["ideas"].values()))])


def factor_dividend(ctx):
    side = ctx["side"]
    rows = [r for r in ctx["divh"].get(ctx["v"]["ticker"], []) if ctx["d0"] < r[0][:10] <= ctx["d1"]]
    if not rows or side < 0:
        return dict(present=False)
    ex, amt = rows[0][0][:10], float(rows[0][1])
    d = ctx["d"]
    i = next((k for k, t in enumerate(d["t"]) if t[:10] >= ex), None)
    L = [lev("У1", OK if i else BAD, f"отсечка {ex}, дивиденд {amt:g} ₽")]
    if i:
        gap = float(d["c"][i - 1] - d["o"][i])
        L.append(lev("У2", OK if gap > 0.5 * amt else BAD, f"гэп в день отсечки {gap:+.2f} ₽ при дивиденде {amt:g} ₽"))
    else:
        L.append(lev("У2", BAD, "нет свечи дня отсечки"))
    L.append(lev("У3", OK, "дата отсечки известна заранее; отсечка — после входа и до закрытия"))
    L.append(lev("У4", OK, "цена в день отсечки снижается примерно на дивиденд — механика рынка"))
    past = [r for r in ctx["divh"].get(ctx["v"]["ticker"], []) if r[0][:10] < ctx["d0"]]
    gaps = []
    for r in past[-6:]:
        j = next((k for k, t in enumerate(d["t"]) if t[:10] >= r[0][:10]), None)
        if j:
            gaps.append((float(d["c"][j - 1] - d["o"][j])) / float(r[1]))
    L.append(lev("У5", OK if gaps and np.median(gaps) > 0.5 else BAD, f"прошлые отсечки бумаги: гэп в среднем {np.median(gaps):.0%} дивиденда" if gaps else "прошлых отсечек нет"))
    L.append(personal(ctx, lambda v: any(msk_date(int(v.get("t") or 0)) < r[0][:10] <= msk_date(int(v.get("closed_t") or 0))
                                         for r in ctx["divh"].get(v["ticker"], [])) and int(v["side"]) > 0))
    return dict(present=True, value=f"отсечка {ex} внутри сделки (лонг)", levels=L)


def _ret(a, d0, d1):
    w = window(a, d0, d1)
    if w is None or len(w["c"]) < 1:
        return None
    pre = cut(a, d0)
    if not len(pre["c"]):
        return None
    return float(w["c"][-1]) / float(pre["c"][-1]) - 1


def _beta_corr(x, y):
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 40:
        return None, None
    x, y = x[m], y[m]
    return float(np.cov(x, y)[0, 1] / np.var(y)), float(np.corrcoef(x, y)[0, 1])


def _rets_aligned(a, b, n=120):
    da = {t[:10]: c for t, c in zip(a["t"], a["c"])}
    db = {t[:10]: c for t, c in zip(b["t"], b["c"])}
    days = sorted(set(da) & set(db))[-n - 1:]
    x = np.diff(np.log([da[k] for k in days]))
    y = np.diff(np.log([db[k] for k in days]))
    return x, y


def factor_market(ctx):
    idx_d, idx_h = ctx["idx"]
    side = ctx["side"]
    mr = _ret(idx_d, ctx["d0"], ctx["d1"])
    tr_ = ctx["res"]["pct"]
    if mr is None or mr * side > -0.01:
        return dict(present=False)
    pre_i = cut(idx_d, ctx["d0"])
    b, c = _beta_corr(*_rets_aligned(ctx["pre"], pre_i))
    expl = (b or 0) * mr * side / tr_ if tr_ else 0
    L = [lev("У1", OK, f"индекс МосБиржи за время сделки {mr:+.1%}")]
    if idx_h is not None and len(idx_h["c"]):
        wh = window(idx_h, msk_dt(ctx["t_open"]), msk_dt(ctx["t_close"]))
        if wh is not None and len(wh["c"]) > 1:
            mrh = float(wh["c"][-1] / wh["o"][0] - 1)
            L.append(lev("У2", OK if np.sign(mrh) == np.sign(mr) else BAD, f"по часовым свечам индекса {mrh:+.1%} (по дневным {mr:+.1%})"))
        else:
            L.append(lev("У2", BAD, "часовых свечей индекса за период нет"))
    else:
        L.append(lev("У2", BAD, "часовых свечей индекса нет"))
    L.append(lev("У3", OK, "движение индекса — после входа и до закрытия"))
    pc = []
    for t, a in pool_data().items():
        bb, cc = _beta_corr(*_rets_aligned(cut(a, ctx["d0"]), pre_i))
        if cc is not None:
            pc.append(cc)
    L.append(lev("У4", OK if pc and np.median(pc) > 0.4 else BAD, f"медианная корреляция 20 ликвидных акций с индексом {np.median(pc):.2f}" if pc else "нет данных"))
    L.append(lev("У5", OK if c is not None and c > 0.4 and (b or 0) > 0.5 else BAD,
                 f"бета {ctx['v']['ticker']} к индексу {b:.2f}, корреляция {c:.2f} (нужно >0,5 и >0,4); рынок объясняет ~{min(1, max(0, expl)):.0%} убытка"
                 if b is not None else "мало общих дней для беты"))
    L.append(personal(ctx, lambda v: (_ret(idx_d, msk_date(int(v.get("t") or 0)), msk_date(int(v.get("closed_t") or 0))) or 0) * int(v["side"]) < -0.01))
    return dict(present=True, value=f"индекс {mr:+.1%} против позиции", levels=L, external=True)


def factor_sector(ctx):
    sec = ctx["sec_of"].get(ctx["v"]["ticker"])
    if not sec:
        return dict(present=False)
    peers = [t for t, s in ctx["sec_of"].items() if s == sec and t != ctx["v"]["ticker"]][:6]
    J = _J()
    rs = []
    data = {}
    for t in peers:
        try:
            a = J.load(t)[0]
        except Exception:  # noqa: BLE001
            continue
        r = _ret(a, ctx["d0"], ctx["d1"])
        if r is not None:
            rs.append(r)
            data[t] = a
    if len(rs) < 2:
        return dict(present=False)
    sr = float(np.median(rs))
    if sr * ctx["side"] > -0.015:
        return dict(present=False)
    agree = sum(1 for r in rs if r * ctx["side"] < 0)
    L = [lev("У1", OK, f"сектор «{sec}»: {len(rs)} бумаг с данными"),
         lev("У2", OK if agree >= 0.6 * len(rs) else BAD, f"против позиции {agree} из {len(rs)} бумаг сектора"),
         lev("У3", OK, "движение сектора — после входа и до закрытия")]
    cs = []
    pre = ctx["pre"]
    for t, a in data.items():
        x, y = _rets_aligned(pre, cut(a, ctx["d0"]))
        if len(x) > 40:
            cs.append(float(np.corrcoef(x, y)[0, 1]))
    L.append(lev("У4", OK, "бумаги одного сектора движутся вместе — общий фактор (нефть, металлы, ставка)"))
    L.append(lev("У5", OK if cs and np.median(cs) > 0.4 else BAD, f"корреляция {ctx['v']['ticker']} с бумагами сектора до входа {np.median(cs):.2f} (нужно >0,4)" if cs else "нет данных"))
    L.append(lev("У6", FEW, "по сектору личная статистика не считается"))
    return dict(present=True, value=f"сектор «{sec}» {sr:+.1%} против позиции", levels=L, external=True)


def _stop_day(ctx):
    """Первый день, когда цена задела первоначальный стоп (по дневным), и первая такая часовая."""
    side, S0 = ctx["side"], ctx["S0"]
    pd_, ph = ctx["path_d"], ctx["path_h"]
    di = next((i for i in range(len(pd_["c"])) if (pd_["l"][i] <= S0 if side > 0 else pd_["h"][i] >= S0)), None)
    hi = next((i for i in range(len(ph["c"])) if (ph["l"][i] <= S0 if side > 0 else ph["h"][i] >= S0)), None) if ph is not None and len(ph["c"]) else None
    return di, hi


def factor_gap(ctx):
    side, S0 = ctx["side"], ctx["S0"]
    di, hi = _stop_day(ctx)
    pd_ = ctx["path_d"]
    if di is None or not ((pd_["o"][di] - S0) * side <= 0):
        return dict(present=False)
    L = [lev("У1", OK, f"открытие {pd_['t'][di][:10]} {pd_['o'][di]:g} за стопом {S0:g}")]
    ph = ctx["path_h"]
    if ph is not None and hi is not None:
        L.append(lev("У2", OK if (ph["o"][hi] - S0) * side <= 0 else BAD, f"первая часовая свеча за стопом открылась по {ph['o'][hi]:g}"))
    else:
        L.append(lev("У2", BAD, "нет часовых свечей для сверки"))
    L.append(lev("У3", OK, "гэп — после входа"))
    L.append(lev("У4", OK, "стоп не защищает от гэпа: исполнение по цене открытия (так считались проверки №15, №23)"))
    d = ctx["pre"]
    gaps = np.abs(d["o"][1:] / d["c"][:-1] - 1)
    fr = float(np.mean(gaps > ctx["risk"] / ctx["E"])) if len(gaps) else float("nan")
    L.append(lev("У5", OK if fr == fr else BAD, f"на истории бумаги гэп больше вашего риска случался в {fr:.1%} дней" if fr == fr else "нет истории"))
    L.append(lev("У6", FEW, "по гэпам личная статистика не считается"))
    return dict(present=True, value="гэп через стоп", levels=L, external=True)


def factor_spike(ctx):
    side = ctx["side"]
    pd_ = ctx["path_d"]
    pre = ctx["pre"]
    if len(pre["c"]) < 70 or not len(pd_["c"]):
        return dict(present=False)
    r = np.diff(np.log(pre["c"]))[-60:]
    sd = float(np.std(r))
    full_c = np.r_[pre["c"][-1], pd_["c"]]
    dr = np.diff(np.log(full_c))
    i = int(np.argmin(dr * side))
    mv = float(dr[i])
    if mv * side > -max(3 * sd, 0.04):
        return dict(present=False)
    L = [lev("У1", OK, f"{pd_['t'][i][:10]}: {np.expm1(mv):+.1%} при обычном дневном разбросе {sd:.1%}")]
    ph = ctx["path_h"]
    if ph is not None and len(ph["c"]):
        day = [k for k, t in enumerate(ph["t"]) if t[:10] == pd_["t"][i][:10]]
        if day:
            hr = float(ph["c"][day[-1]] / ph["o"][day[0]] - 1)
            L.append(lev("У2", OK if hr * side < 0 else BAD, f"по часовым свечам того дня {hr:+.1%}"))
        else:
            L.append(lev("У2", BAD, "нет часовых свечей того дня"))
    else:
        L.append(lev("У2", BAD, "нет часовых свечей"))
    L.append(lev("У3", OK, "движение после входа"))
    pool_fr = []
    for t, a in pool_data().items():
        a = cut(a, ctx["d0"])
        rr = np.diff(np.log(a["c"]))
        if len(rr) > 300:
            s_ = np.array([np.std(rr[max(0, k - 60):k]) for k in range(60, len(rr))])
            pool_fr.append(float(np.mean(np.abs(rr[60:]) > np.maximum(3 * s_, 0.04))))
    L.append(lev("У4", OK if pool_fr and np.median(pool_fr) < 0.03 else BAD, f"такие дни у ликвидных акций — {np.median(pool_fr):.1%} дней: редкое событие" if pool_fr else "нет данных"))
    rr = np.diff(np.log(pre["c"]))
    s_ = np.array([np.std(rr[max(0, k - 60):k]) for k in range(60, len(rr))])
    fr = float(np.mean(np.abs(rr[60:]) > np.maximum(3 * s_, 0.04))) if len(s_) else float("nan")
    L.append(lev("У5", OK if fr == fr and fr < 0.05 else BAD, f"у {ctx['v']['ticker']} такие дни — {fr:.1%}" if fr == fr else "нет истории"))
    L.append(lev("У6", FEW, "по резким движениям личная статистика не считается"))
    return dict(present=True, value=f"резкое движение против позиции {np.expm1(mv):+.1%} ({pd_['t'][i][:10]})", levels=L, external=True)


def factor_stop_moved(ctx):
    v = ctx["v"]
    E, side = ctx["E"], ctx["side"]
    s_fin = float(v["stop"])
    moved = (ctx["S0"] - s_fin) * side > 1e-9 and not v.get("be")
    notes = [n for n in (v.get("status") or []) if "стоп изменён" in n]
    if not moved:
        return dict(present=False)
    L = [lev("У1", OK if v.get("stop0") is not None else BAD, f"первоначальный стоп {ctx['S0']:g}, итоговый {s_fin:g}"),
         lev("У2", OK if notes else BAD, ("в журнале изменений: " + "; ".join(notes)) if notes else "в журнале изменений записи о переносе нет (журнал хранит 6 последних)"),
         lev("У3", OK, "перенос — после публикации")]
    R = ctx["res"]["R"]
    L.append(lev("У4", OK, "стоп — граница риска сделки: перенос дальше увеличивает убыток сверх 1R (во всех проверках стоп фиксирован)"))
    L.append(lev("У5", OK if R is not None and R < -1.05 else BAD, f"фактический результат {R:+.2f}R (убыток больше плана, если меньше −1,05R)" if R is not None else "R не посчитан"))
    L.append(personal(ctx, lambda w: (float(w.get("stop0") or w["stop"]) - float(w["stop"])) * int(w["side"]) > 1e-9 and not w.get("be")))
    return dict(present=True, value=f"стоп перенесён дальше: {ctx['S0']:g} → {s_fin:g}", levels=L)


def factor_early(ctx):
    v = ctx["v"]
    di, hi = _stop_day(ctx)
    if di is not None or ctx["res"]["pct"] >= 0:
        return dict(present=False)
    L = [lev("У1", OK if len(ctx["path_d"]["c"]) else BAD, f"свечей за время сделки: дневных {len(ctx['path_d']['c'])}, часовых {len(ctx['path_h']['c']) if ctx['path_h'] is not None else 0}")]
    L.append(lev("У2", OK if ctx["path_h"] is not None and len(ctx["path_h"]["c"]) and hi is None else BAD,
                 "и по часовым свечам стоп не задет" if ctx["path_h"] is not None and len(ctx["path_h"]["c"]) else "нет часовых свечей для сверки"))
    L.append(lev("У3", OK, f"закрыто вручную {msk_dt(ctx['t_close'])}, стоп до этого не задет"))
    L.append(lev("У4", OK, "проверки №20, №23: ранняя фиксация и маленькие выходы на истории хуже удержания по плану"))
    a = ctx["after"]
    side, T, S0 = ctx["side"], ctx["T"][-1], ctx["S0"]
    first = None
    for i in range(min(60, len(a["c"]))):
        st_ = a["l"][i] <= S0 if side > 0 else a["h"][i] >= S0
        tk_ = a["h"][i] >= T if side > 0 else a["l"][i] <= T
        if st_:
            first = "стоп"
            break
        if tk_:
            first = "цель"
            break
    if first is None:
        note = f"после закрытия прошло {len(a['c'])} дн.: ни стоп, ни цель пока не достигнуты — проверка повторится" if len(a["c"]) < 60 else "за 60 дн. после закрытия ни стоп, ни цель"
        L.append(lev("У5", FEW if len(a["c"]) < 60 else BAD, note))
    else:
        L.append(lev("У5", OK if first == "цель" else BAD, f"после закрытия цена первой дошла до: {first}" + (" — выход стоил прибыли" if first == "цель" else " — выход уберёг от большего убытка")))

    L.append(lev("У6", FEW, "личная статистика ранних выходов накапливается в листе «Повторяемость»"))
    return dict(present=True, value=f"закрыто вручную в минус ({ctx['res']['pct']:+.1%}) до стопа", levels=L)


def factor_mfe(ctx):
    ph = ctx["path_h"] if ctx["path_h"] is not None and len(ctx["path_h"]["c"]) else ctx["path_d"]
    if not len(ph["c"]):
        return dict(present=False)
    side, E, risk = ctx["side"], ctx["E"], ctx["risk"]
    mfe = float(((ph["h"].max() - E) if side > 0 else (E - ph["l"].min())) / risk)
    if mfe < 1:
        return dict(present=False)
    pdd = ctx["path_d"]
    mfe_d = float(((pdd["h"].max() - E) if side > 0 else (E - pdd["l"].min())) / risk) if len(pdd["c"]) else None
    return dict(present=True, value=f"позиция была в плюсе на {mfe:.1f}R, закрыта в минус", levels=[
        lev("У1", OK, "свечи за время сделки есть"),
        lev("У2", OK if mfe_d is not None and abs(mfe_d - mfe) < 0.3 else BAD, f"по дневным свечам максимум {mfe_d:.1f}R" if mfe_d is not None else "нет дневных свечей"),
        lev("У3", OK, "после входа, до закрытия"),
        lev("У4", BAD, "проверка №23: перенос в безубыток и частичная фиксация на истории НЕ улучшали результат — это не ошибка по статистике"),
        lev("У5", BAD, "на истории бумаги не проверяется"),
        personal(ctx, lambda w: None)])


def factor_loss_gt_plan(ctx):
    R = ctx["res"]["R"]
    if R is None or R >= -1.15:
        return dict(present=False)
    side, E = ctx["side"], ctx["E"]
    exit_px = E * (1 + ctx["res"]["pct"] * side)
    p_ = ctx["path_h"] if ctx["path_h"] is not None and len(ctx["path_h"]["c"]) else ctx["path_d"]
    reach = (float(p_["l"].min()) if side > 0 else float(p_["h"].max())) if len(p_["c"]) else None
    tradable = reach is not None and ((reach <= exit_px * 1.003) if side > 0 else (reach >= exit_px * 0.997))
    return dict(present=True, value=f"убыток {R:+.2f}R — больше запланированного 1R", levels=[
        lev("У1", OK, f"итог: {ctx['v'].get('result') or 'по уровням'}"),
        lev("У2", OK if tradable else BAD, (f"цена выхода по итогу ≈ {exit_px:g}; свечи за время сделки доходили до {reach:g} — "
                                            + ("такой выход был возможен" if tradable else "до этой цены цена не доходила, проверьте итог"))
            if exit_px else "нет цены выхода для сверки"),
        lev("У3", OK, "итог — при закрытии"),
        lev("У4", OK, "при стопе на месте и без гэпа убыток ограничен ~1R + издержки — превышение указывает на перенос стопа, гэп или поздний выход"),
        lev("У5", OK if R < -1.15 else BAD, f"превышение {abs(R) - 1:.0%} плана"),
        personal(ctx, lambda w: (__import__("ideas_calc").result(w)["R"] or 0) < -1.15)])


FACTORS = [("подготовка", "против тренда", lambda c: factor_state(c, "против тренда")),
           ("подготовка", "вход после сильного хода (догоняние)", lambda c: factor_state(c, "вход после сильного хода (догоняние)")),
           ("подготовка", "вход у края 60-дневного диапазона", lambda c: factor_state(c, "вход у края 60-дневного диапазона")),
           ("подготовка", "перекупленность / перепроданность (RSI)", lambda c: factor_state(c, "перекупленность / перепроданность (RSI)")),
           ("подготовка", "высокая волатильность бумаги", lambda c: factor_state(c, "высокая волатильность бумаги")),
           ("подготовка", "стоп в зоне шума (< 1,5 ATR)", lambda c: factor_geom(c, "стоп в зоне шума (< 1,5 ATR)")),
           ("подготовка", "цель слишком близко (< 1,5R)", lambda c: factor_geom(c, "цель слишком близко (< 1,5R)")),
           ("подготовка", "вход далеко от рынка (> 1 ATR)", factor_far_entry),
           ("подготовка", "разбор бота давал низкую вероятность цели", lambda c: factor_judge(c, "разбор бота давал низкую вероятность цели")),
           ("подготовка", "модель аномалий против идеи", lambda c: factor_judge(c, "модель аномалий против идеи")),
           ("дисциплина", "вход после убытка (≤ 48 ч, риск тильта)", factor_tilt),
           ("дисциплина", "стоп перенесён дальше", factor_stop_moved),
           ("дисциплина", "ранний выход в минус до стопа", factor_early),
           ("дисциплина", "был плюс ≥ 1R, закрыто в минус", factor_mfe),
           ("исполнение", "убыток больше плана", factor_loss_gt_plan),
           ("исполнение", "гэп через стоп", factor_gap),
           ("внешнее", "отсечка дивидендов внутри лонга", factor_dividend),
           ("внешнее", "рынок (индекс) против позиции", factor_market),
           ("внешнее", "сектор против позиции", factor_sector),
           ("внешнее", "резкое движение (аномалия) против позиции", factor_spike)]


def verdict(levels):
    st = {c: s for c, s, _ in levels}
    if any(s == BAD for s in st.values()):
        bad = [c for c, s in st.items() if s == BAD]
        return "не подтверждён (" + ", ".join(bad) + ")"
    if st.get("У6") == FEW and all(st.get(c) == OK for c in ("У1", "У2", "У3", "У4", "У5")):
        return "подтверждён, ваших сделок мало"
    if all(st.get(c) == OK for c in ("У1", "У2", "У3", "У4", "У5", "У6")):
        return "подтверждён"
    return "не подтверждён (мало данных)"


# ---------------- отчёт ----------------
def analyze(ideas, day, divh=None, sec_of=None):
    import ideas_calc as IC
    losers = [(k, v) for k, v in ideas.items() if v.get("closed") and msk_date(int(v.get("closed_t") or v.get("t") or 0)) == day
              and IC.result(v)["pct"] < 0]
    if not losers:
        return []
    if divh is None:
        divh = json.load(open(os.path.join(HERE, "..", "divhist.json"), encoding="utf-8"))
    if sec_of is None:
        sec_of = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8")).get("secOf", {})
    idx = index_data()
    out = []
    for k, v in losers:
        try:
            ctx = context(k, v, ideas, idx, divh, sec_of)
        except Exception as e:  # noqa: BLE001
            log("идея", k, "не разобрана:", e)
            out.append(dict(iid=k, v=v, error=str(e)[:200], factors=[]))
            continue
        fs = []
        for grp, name, fn in FACTORS:
            try:
                r = fn(ctx)
            except Exception as e:  # noqa: BLE001
                r = dict(present=None, error=str(e)[:150])
            r.update(group=grp, name=name)
            if r.get("present"):
                r["verdict"] = verdict(r["levels"])
            fs.append(r)
        out.append(dict(iid=k, v=v, ctx=ctx, factors=fs))
    return out


def recurrence(ideas):
    """Лист «Повторяемость»: по всем вашим закрытым идеям — как часто фактор (из тех, что считаются без пути цены) у убыточных и прибыльных."""
    import ideas_calc as IC
    rows = []
    preds = {
        "против тренда": lambda v, s: (s["tr"] == -int(v["side"])) and (s["sl"] == -int(v["side"])),
        "вход после сильного хода (догоняние)": lambda v, s: s["mom5"] * int(v["side"]) > 2,
        "вход у края 60-дневного диапазона": lambda v, s: (s["pos"] > 0.9) if int(v["side"]) > 0 else (s["pos"] < 0.1),
        "перекупленность / перепроданность (RSI)": lambda v, s: (s["rsi"] > 70) if int(v["side"]) > 0 else (s["rsi"] < 30),
        "высокая волатильность бумаги": lambda v, s: s["atrp"] > 0.8,
        "стоп в зоне шума (< 1,5 ATR)": lambda v, s: abs(float(v["entry"]) - float(v.get("stop0") or v["stop"])) / s["atr"] < 1.5,
        "цель слишком близко (< 1,5R)": lambda v, s: abs(float(v["takes"][-1]) - float(v["entry"])) / abs(float(v["entry"]) - float(v.get("stop0") or v["stop"])) < 1.5,
        "стоп перенесён дальше": lambda v, s: (float(v.get("stop0") or v["stop"]) - float(v["stop"])) * int(v["side"]) > 1e-9 and not v.get("be"),
    }
    data = []
    for k, v in ideas.items():
        if not v.get("closed"):
            continue
        try:
            x = _state_at_entry(v)
        except Exception:  # noqa: BLE001
            x = None
        if x is None:
            continue
        data.append((v, x[0], IC.result(v)["pct"] < 0))
    for name, p in preds.items():
        lw = [p(v, s) for v, s, loss in data if loss]
        ww = [p(v, s) for v, s, loss in data if not loss]
        rows.append((name, len(lw), sum(lw), len(ww), sum(ww)))
    return rows, len(data)


def to_xlsx(day, res, rec):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    bold = Font(bold=True)
    green, red, yellow = (PatternFill("solid", fgColor=c) for c in ("C6EFCE", "FFC7CE", "FFEB9C"))
    wrap = Alignment(wrap_text=True, vertical="top")
    ws.append([f"Разбор убыточных идей за {day}"])
    ws["A1"].font = Font(bold=True, size=14)
    tot = sum((r["ctx"]["res"]["R"] or 0) for r in res if r.get("ctx"))
    ws.append([f"Закрыто в минус: {len(res)}; сумма {tot:+.2f}R"])
    ws.append([])
    ws.append(["Идея", "Бумага", "Сторона", "Результат", "R", "Подтверждённые факторы", "Не подтверждённые (присутствуют)"])
    for c in ws[4]:
        c.font = bold
    for r in res:
        v = r["v"]
        conf = [f["name"] for f in r["factors"] if f.get("present") and f["verdict"].startswith("подтверждён")]
        unc = [f["name"] for f in r["factors"] if f.get("present") and not f["verdict"].startswith("подтверждён")]
        R = r["ctx"]["res"]["R"] if r.get("ctx") else None
        ws.append([f"#{r['iid']}", v["ticker"], "лонг" if int(v["side"]) > 0 else "шорт", v.get("result") or "", round(R, 2) if R is not None else None,
                   "; ".join(conf) or "—", "; ".join(unc) or "—"])
    ws.append([])
    ws.append(["Как читать: фактор «подтверждён», только если прошёл все 6 уровней проверки (лист «Уровни проверки»). "
               "Внешние факторы (рынок, сектор, гэп, отсечка) — не ваша ошибка, а риск, который стоит учитывать в размере позиции."])
    ws.append(["Это статистика прошлого, а не гарантия. Не является индивидуальной инвестиционной рекомендацией."])
    for col, w in zip("ABCDEFG", (8, 10, 9, 28, 8, 60, 60)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=5):
        for c in row:
            c.alignment = wrap
    # Сделки
    ws2 = wb.create_sheet("Сделки")
    hdr = ["Идея", "Бумага", "Сторона", "Вход", "Стоп при входе", "Стоп при закрытии", "Тейки", "Открыта (МСК)", "Закрыта (МСК)", "Дней",
           "Итог автора", "Результат, %", "R", "Источник итога", "ATR на входе", "Стоп, ATR", "Цель, R", "Макс. против, R", "Макс. в плюс, R",
           "Вероятность цели (разбор)", "Комментарий"]
    ws2.append(hdr)
    for c in ws2[1]:
        c.font = bold
    for r in res:
        v = r["v"]
        if not r.get("ctx"):
            ws2.append([f"#{r['iid']}", v["ticker"], "", "", "", "", "", "", "", "", v.get("result"), "", "", "", "", "", "", "", "", "", "ошибка: " + r.get("error", "")])
            continue
        c = r["ctx"]
        ph = c["path_h"] if c["path_h"] is not None and len(c["path_h"]["c"]) else c["path_d"]
        mae = mfe = None
        if len(ph["c"]) and c["risk"]:
            if c["side"] > 0:
                mae, mfe = (c["E"] - ph["l"].min()) / c["risk"], (ph["h"].max() - c["E"]) / c["risk"]
            else:
                mae, mfe = (ph["h"].max() - c["E"]) / c["risk"], (c["E"] - ph["l"].min()) / c["risk"]
        ws2.append([f"#{r['iid']}", v["ticker"], "лонг" if c["side"] > 0 else "шорт", c["E"], c["S0"], float(v["stop"]), " / ".join(f"{x:g}" for x in c["T"]),
                    msk_dt(c["t_open"]), msk_dt(c["t_close"]), round((c["t_close"] - c["t_open"]) / 86400, 1), v.get("result") or "",
                    round(c["res"]["pct"] * 100, 2), round(c["res"]["R"], 2) if c["res"]["R"] is not None else None, c["res"]["src"],
                    round(c["A"], 4) if c["A"] else None, round(c["risk"] / c["A"], 2) if c["A"] else None, round(c["rr"], 2) if c["rr"] else None,
                    round(-float(mae), 2) if mae is not None else None, round(float(mfe), 2) if mfe is not None else None,
                    (v.get("plan") or {}).get("p"), v.get("comment") or ""])
    for i, w in enumerate((7, 8, 7, 9, 9, 9, 14, 16, 16, 6, 24, 9, 7, 9, 9, 8, 8, 9, 9, 9, 30)):
        ws2.column_dimensions[chr(65 + i)].width = w
    # Факторы
    ws3 = wb.create_sheet("Факторы")
    ws3.append(["Идея", "Бумага", "Группа", "Фактор", "Есть в сделке", "Значение", "Вердикт",
                "У1 Данные", "У2 Сверка", "У3 Время", "У4 Рынок", "У5 Бумага", "У6 Ваши сделки"])
    for c in ws3[1]:
        c.font = bold
    for r in res:
        for f in r["factors"]:
            if f.get("present") is None:
                ws3.append([f"#{r['iid']}", r["v"]["ticker"], f["group"], f["name"], "ошибка расчёта", f.get("error", ""), "—"])
                continue
            if not f.get("present"):
                ws3.append([f"#{r['iid']}", r["v"]["ticker"], f["group"], f["name"], "нет", "", "—"])
                continue
            lv = {code: f"{s} {note}" for code, s, note in f["levels"]}
            ws3.append([f"#{r['iid']}", r["v"]["ticker"], f["group"], f["name"], "да", f["value"], f["verdict"]] +
                       [lv.get(k, "") for k in ("У1", "У2", "У3", "У4", "У5", "У6")])
            row = ws3.max_row
            ws3.cell(row, 7).fill = green if f["verdict"] == "подтверждён" else yellow if f["verdict"].startswith("подтверждён") else red
            for j, k in enumerate(("У1", "У2", "У3", "У4", "У5", "У6")):
                s = lv.get(k, "")[:1]
                ws3.cell(row, 8 + j).fill = green if s == OK else red if s == BAD else yellow
    for i, w in enumerate((7, 8, 11, 34, 9, 34, 28, 30, 36, 30, 60, 50, 36)):
        ws3.column_dimensions[chr(65 + i)].width = w
    for row in ws3.iter_rows(min_row=2):
        for c in row:
            c.alignment = wrap
    ws3.freeze_panes = "E2"
    ws3.auto_filter.ref = ws3.dimensions
    # Повторяемость
    ws4 = wb.create_sheet("Повторяемость")
    rows, n = rec
    ws4.append([f"Все ваши закрытые идеи с историей свечей: {n}. Как часто фактор встречался у убыточных и у остальных."])
    ws4.append(["Фактор", "Убыточных", "из них с фактором", "Доля", "Остальных", "из них с фактором", "Доля", "Чаще у убыточных?"])
    for c in ws4[2]:
        c.font = bold
    for name, nl, kl, nw, kw in rows:
        a, b = (kl / nl if nl else None), (kw / nw if nw else None)
        ws4.append([name, nl, kl, round(a, 3) if a is not None else None, nw, kw, round(b, 3) if b is not None else None,
                    ("да" if a > b else "нет") if a is not None and b is not None and nl >= 5 and nw >= 5 else "мало данных"])
    ws4.column_dimensions["A"].width = 40
    # Уровни
    ws5 = wb.create_sheet("Уровни проверки")
    for line in (__doc__ or "").strip().split("\n"):
        ws5.append([line])
    ws5.column_dimensions["A"].width = 160
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def evening(PW, day=None, force=False):
    """Вызывается из главного цикла paywall: в 19:15 МСК — файл первому администратору; без убыточных идей — короткое сообщение."""
    t = time.gmtime(time.time() + MSK)
    day = day or time.strftime("%Y-%m-%d", t)
    if not force and (time.strftime("%H:%M", t) < os.environ.get("ERRORS_AT", "19:15") or PW.S.get("err_day") == day):
        return
    if not PW.ADMINS:
        return
    if not force:
        PW.S["err_day"] = day   # ручной /errors не отменяет вечернюю отправку
        PW.save()
    admin = sorted(PW.ADMINS)[0]
    try:
        res = analyze(PW.S.get("ideas") or {}, day)
        if not res:
            PW.say(admin, f"📉 Разбор ошибок за {day}: идей, закрытых в минус, нет.")
            return
        rec = recurrence(PW.S.get("ideas") or {})
        data = to_xlsx(day, res, rec)
        conf = {}
        for r in res:
            for f in r["factors"]:
                if f.get("present") and f["verdict"].startswith("подтверждён"):
                    conf[f["name"]] = conf.get(f["name"], 0) + 1
        top = ", ".join(f"{k} ({n})" for k, n in sorted(conf.items(), key=lambda x: -x[1])[:4]) or "ни один фактор не прошёл все уровни проверки"
        PW.tg_upload_doc(admin, f"errors_{day}.xlsx", data,
                         f"📉 Разбор убыточных идей за {day}: {len(res)} шт. Подтверждено: {top}. Подробно — в файле. Не ИИР.",
                         ctype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        log("отправлен разбор за", day, "идей:", len(res))
    except Exception as e:  # noqa: BLE001
        log("разбор не отправлен:", e)
        try:
            PW.say(admin, f"⚠️ Разбор ошибок за {day} не сформирован: {str(e)[:200]}")
        except Exception:  # noqa: BLE001
            pass
