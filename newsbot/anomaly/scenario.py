# -*- coding: utf-8 -*-
"""Сценарии сделки: что будет с позицией после входа — тейк раньше стопа, стоп, выход по времени (в плюсе/минусе),
распределение доходности к горизонту. Обучение — на синтетических сделках по всей истории (каждый день, обе стороны,
разные стопы/тейки/горизонты), признаки — те же, что у модели аномалий (известны до входа), + параметры сделки.
Путь цены — полный доход: дивиденд в день отсечки добавляется обратно (держатель его получает, шорт — платит)."""
import numpy as np
import pandas as pd

SIGNED = ["xret1", "xret3", "xret5", "xret20", "xret60", "ema20d", "ema50d", "dhi20", "dlo20", "dhi250", "dlo250", "gap1", "h_ev_ret", "ix_r1", "ix_ret5",
          "brk20", "streak", "absx1"]
TRADE = ["side", "stop", "stop_atr", "rr", "hz", "beta", "beta_risk"]
# признаки, устойчивые по годам (проверка 2021–2026): без трендов рынка, которые переобучаются на режим
STABLE = ["sd20", "sd60", "sd5", "tier", "lval", "rng1", "sq", "sd_ratio", "vr1", "an20", "an250", "since_an", "to_ex", "nw_n", "nw_wmax", "reg_n", "reg_ind",
          "reg_n5", "mkt_an1", "ix_sd20", "dow", "month", "s_ema20d", "s_dhi20", "s_dlo20", "s_xret1", "s_gap1", "s_h_ev_ret", "s_ix_ret5", "s_ix_r1"]
CLS = ["стоп", "тейк", "по времени"]
BLEND = 0.6  # доля модели; остальное — базовая частота по конфигурации сделки (так устойчивее по годам, проверка 2021–2026)


def series(rows):
    df = pd.DataFrame([r[:6] for r in rows], columns=["d", "o", "h", "l", "c", "v"])
    df["d"] = pd.to_datetime(df["d"])
    return df[df["d"].dt.weekday < 5].reset_index(drop=True)


def div_cum(df, divs):
    """Накопленные дивиденды на акцию по дням (с даты отсечки)."""
    add = np.zeros(len(df))
    ds = df["d"].values
    for d, amt in divs:
        j = np.searchsorted(ds, np.datetime64(d))
        if 0 < j < len(df):
            add[j] += amt
    return np.cumsum(add)


def simulate_one(o, h, l, c, dc, i, side, s, rr, hz):
    """Одна сделка: вход по открытию дня i. → (класс, R, доходность к горизонту, MFE, MAE) в долях цены входа."""
    e = o[i]
    if not e or e != e:
        return None
    n = len(o)
    last = min(n - 1, i + hz - 1)
    if last < i + hz - 1:
        return None
    stop_px = e * (1 - side * s)
    take_px = e * (1 + side * s * rr)
    mfe = mae = 0.0
    for j in range(i, last + 1):
        adj = dc[j] - dc[i]
        hi, lo, op = h[j] + adj, l[j] + adj, o[j] + adj
        if side > 0:
            fav, adv = hi / e - 1, lo / e - 1
            if j > i and op <= stop_px:
                return 0, (op / e - 1) / s, (op / e - 1), max(mfe, 0), min(mae, op / e - 1)
            hit_s, hit_t = lo <= stop_px, hi >= take_px
        else:
            fav, adv = 1 - lo / e, 1 - hi / e
            if j > i and op >= stop_px:
                return 0, (1 - op / e) / s, (1 - op / e), max(mfe, 0), min(mae, 1 - op / e)
            hit_s, hit_t = hi >= stop_px, lo <= take_px
        mfe, mae = max(mfe, fav), min(mae, adv)
        if hit_s:  # стоп и тейк в один день — считаем стоп (осторожно)
            return 0, -1.0, -s, mfe, mae
        if hit_t:
            return 1, rr, s * rr, mfe, mae
    ret = side * ((c[last] + dc[last] - dc[i]) / e - 1)
    return 2, ret / s, ret, mfe, mae


def build(D, divh, X, per_day=1, seed=7, start="2013-01-01", ix_rows=None):
    """Синтетические сделки: на каждый день и бумагу — по одной случайной конфигурации на сторону."""
    rng = np.random.default_rng(seed)
    out = []
    Xi = X.set_index(["t", "d"])
    for t, rows in D.items():
        if len(rows) < 300:
            continue
        df = series(rows)
        o, h, l, c = (df[k].to_numpy(float) for k in "ohlc")
        dc = div_cum(df, divh.get(t, []))
        tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
        atr = np.concatenate([[np.nan], pd.Series(tr).rolling(14, min_periods=10).mean().to_numpy()]) / np.concatenate([[np.nan], c[:-1]])
        atr = np.concatenate([[np.nan], atr[:-1]])  # известно утром: ATR до вчера, в долях цены
        days = df["d"].to_numpy()
        beta = beta_series(df, ix_rows)
        for i in range(60, len(df) - 21):
            if days[i] < np.datetime64(start) or atr[i] != atr[i]:
                continue
            for side in (1, -1):
                for _ in range(per_day):
                    if rng.random() < 0.5:
                        s = float(np.clip(atr[i] * rng.uniform(1.0, 3.0), 0.01, 0.15))
                    else:
                        s = float(rng.choice([0.02, 0.03, 0.04, 0.05, 0.06, 0.08]))
                    rr = float(rng.choice([1.0, 1.5, 2.0, 3.0]))
                    hz = int(rng.choice([5, 10, 20]))
                    r = simulate_one(o, h, l, c, dc, i, side, s, rr, hz)
                    if r is None:
                        continue
                    out.append((t, days[i], side, s, s / atr[i], rr, hz, beta[i]) + tuple(r))
    T = pd.DataFrame(out, columns=["t", "d", "side", "stop", "stop_atr", "rr", "hz", "beta", "cls", "R", "ret", "mfe", "mae"])
    T = T.merge(X, on=["t", "d"], how="inner")
    return add_signed(T)


def beta_series(df, ix_rows):
    """Бета бумаги к IMOEX за 60 дней (до вчера): насколько позицию несёт рынок."""
    if not ix_rows:
        return np.full(len(df), np.nan)
    ix = series(ix_rows).set_index("d")["c"]
    r = df.set_index("d")["c"].pct_change()
    m = ix.reindex(r.index).ffill().pct_change()
    b = r.rolling(60, min_periods=40).cov(m) / m.rolling(60, min_periods=40).var()
    return b.shift(1).to_numpy()


def add_signed(T):
    if "beta" in T.columns and "ix_sd20" in T.columns:
        T["beta_risk"] = T["beta"] * T["ix_sd20"]
    add = {"s_" + f: T[f] * T["side"] for f in SIGNED if f in T.columns}
    T = T.drop(columns=[c for c in add if c in T.columns])
    return pd.concat([T, pd.DataFrame(add, index=T.index)], axis=1)


def feat_cols(T, base):
    return [f for f in base if f in T.columns] + TRADE + ["s_" + f for f in SIGNED if "s_" + f in T.columns]
