# -*- coding: utf-8 -*-
"""Признаки и метки для прогноза аномалий (дневной горизонт).

Аномалия дня D по бумаге:
  ценовая  — |доходность бумаги − доходность IMOEX| ≥ max(порог эшелона, 3·σ60 избыточной доходности);
             пороги эшелона как у живого детектора: 1-й эшелон 4%, 2-й 6%, 3-й 8%;
  объёмная — оборот дня ≥ 4× медианы 20 дней (3-й эшелон 6×) и не меньше 30 млн ₽.
Дни отсечки дивидендов и сессии выходного дня в метки не попадают.
Признаки — только то, что известно утром дня D до открытия: история цен/оборотов до D−1, календарь, новости до 09:55 МСК дня D.
"""
import datetime as dt
import math

import numpy as np
import pandas as pd

TYPES_GROUPS = {
    "results": ["results_up", "results_down", "guidance_up", "guidance_down", "production_up", "production_down"],
    "div": ["div_rec", "div_cut"],
    "corp": ["mna", "spo", "ipo", "buyback", "delist_redom", "management"],
    "risk": ["sanctions", "legal", "tax"],
    "analyst": ["analyst_up", "analyst_down"],
    "macro": ["cbr_cut", "cbr_hike", "cbr_hold", "ruble_weak", "ruble_strong", "oil_up", "oil_down", "geo_escal", "geo_peace"],
    "tone": ["tone"],
}
TG = {t: g for g, ts in TYPES_GROUPS.items() for t in ts}
MSK = dt.timedelta(hours=3)


def series(rows):
    """Дневные бары без сессий выходного дня."""
    df = pd.DataFrame([r[:6] for r in rows], columns=["d", "o", "h", "l", "c", "v"])
    df["d"] = pd.to_datetime(df["d"])
    df = df[df["d"].dt.weekday < 5].reset_index(drop=True)
    return df


def ex_days(df, divs):
    """Индексы дней отсечки (первый будний бар с датой ≥ даты из divhist)."""
    out = set()
    ds = df["d"].values
    for d, _amt in divs:
        j = np.searchsorted(ds, np.datetime64(d))
        if 0 < j < len(df):
            out.add(int(j))
    return out


def news_daily(events, days_by_tk, weights=None):
    """Новости → торговый день, на который они влияют (до 09:55 МСК — этот день, позже — следующий)."""
    from collections import defaultdict
    agg = defaultdict(lambda: defaultdict(float))
    for k_, e in enumerate(events):
        tk = e["tk"]
        when = dt.datetime.utcfromtimestamp(e["ts"]) + MSK
        days = days_by_tk[tk] if tk in days_by_tk else days_by_tk.get("IMOEX")
        if days is None:
            continue
        key = np.datetime64(when.strftime("%Y-%m-%d"))
        j = np.searchsorted(days, key)
        if j < len(days) and days[j] == key and (when.hour, when.minute) >= (9, 55):
            j += 1
        if j >= len(days):
            continue
        D = days[j]
        g = TG.get(e["ty"], "other")
        a = agg[(tk, D)]
        a["n"] += 1
        a["n_" + g] += 1
        a["rum"] += e.get("rum", 0)
        a["off"] += e.get("off", 0)
        wv = weights[k_] if weights is not None else 0.0
        a["w"] += wv
        a["wmax"] = max(a["wmax"], wv)
        # «поздние» новости: вечер/ночь перед открытием (после 18:45 прошлого дня)
        if when.hour >= 19 or when.hour < 10:
            a["night"] += 1
    return agg


def build(H, divhist, events, tier_of=None, news_tickers=None, weights=None, hourly=None):
    ix = series(H["ix"]["IMOEX"])
    ix["r"] = ix["c"].pct_change()
    ixr = dict(zip(ix["d"].values, ix["r"].values))
    ix["sd20"] = ix["r"].rolling(20).std()
    ix["ret5"] = ix["c"].pct_change(5)
    ixf = ix.set_index("d")[["r", "sd20", "ret5"]].rename(columns={"r": "ix_r1", "sd20": "ix_sd20", "ret5": "ix_ret5"})
    ixf = ixf.shift(1)  # известно утром
    days_by = {t: series(a)["d"].values for t, a in H["sh"].items()}
    days_by["IMOEX"] = ix["d"].values
    nd = news_daily(events, days_by, weights)
    frames = []
    # средний оборот для эшелонов: ранг по 60-дневному обороту внутри вселенной на каждую дату
    vals = {}
    for t, a in H["sh"].items():
        df = series(a)
        vals[t] = df.set_index("d")["v"].rolling(60, min_periods=20).mean()
    V = pd.DataFrame(vals)
    rank = V.rank(axis=1, ascending=False)
    for t, a in H["sh"].items():
        df = series(a)
        if len(df) < 300:
            continue
        df["r"] = df["c"].pct_change()
        df["ixr"] = df["d"].map(lambda d: ixr.get(np.datetime64(d)))
        df["x"] = df["r"] - df["ixr"].fillna(0)
        ex = ex_days(df, divhist.get(t, []))
        df["ex"] = 0
        df.loc[list(ex), "ex"] = 1
        df.loc[df["ex"] == 1, ["r", "x"]] = np.nan  # дивидендный гэп не считаем движением
        df["val"] = df["v"]
        med20 = df["val"].rolling(20, min_periods=10).median()
        df["vr"] = df["val"] / med20.shift(1)
        sd60 = df["x"].rolling(60, min_periods=30).std()
        rk = rank[t].reindex(df["d"]).values
        df["tier"] = np.where(rk <= 15, 1, np.where(rk <= 50, 2, 3))
        floor = np.select([df["tier"] == 1, df["tier"] == 2], [0.04, 0.06], 0.08)
        thr = np.maximum(floor, 3 * sd60.shift(1))
        df["y_px"] = (df["x"].abs() >= thr).astype(float)
        df["y_up"] = ((df["x"] >= thr)).astype(float)
        df["y_dn"] = ((df["x"] <= -thr)).astype(float)
        vth = np.where(df["tier"] == 3, 6, 4)
        df["y_vol"] = ((df["vr"] >= vth) & (df["val"] >= 30e6)).astype(float)
        df["y"] = ((df["y_px"] + df["y_vol"]) > 0).astype(float)
        bad = df["ex"].eq(1) | df["x"].isna() | sd60.shift(1).isna() | med20.shift(1).isna()
        for k in ("y_px", "y_up", "y_dn", "y_vol", "y"):
            df.loc[bad, k] = np.nan
        # ---- признаки (сдвиг на 1: известно к утру дня D) ----
        f = pd.DataFrame({"d": df["d"]})
        x, r = df["x"], df["r"]
        c = df["c"]
        ixc = ix.set_index("d")["c"].reindex(df["d"]).ffill().values
        for n in (1, 3, 5, 20, 60):
            f[f"xret{n}"] = (c / c.shift(n) - 1 - (ixc / np.roll(ixc, n) - 1)).shift(1)
        f["absx1"] = x.abs().shift(1)
        f["sd5"] = x.rolling(5, min_periods=4).std().shift(1)
        f["sd20"] = x.rolling(20, min_periods=15).std().shift(1)
        f["sd60"] = sd60.shift(1)
        f["sd250"] = x.rolling(250, min_periods=120).std().shift(1)
        f["sq"] = f["sd5"] / f["sd60"]
        f["sd_ratio"] = f["sd20"] / f["sd250"]
        rng = (df["h"] - df["l"]) / df["c"]
        f["rng1"] = rng.shift(1)
        f["rng_ratio"] = (rng / rng.rolling(20).mean()).shift(1)
        f["body"] = ((df["c"] - df["o"]).abs() / (df["h"] - df["l"]).replace(0, np.nan)).shift(1)
        f["upsh"] = ((df["h"] - df[["o", "c"]].max(axis=1)) / (df["h"] - df["l"]).replace(0, np.nan)).shift(1)
        f["gap1"] = (df["o"] / c.shift(1) - 1).shift(1)
        f["vr1"] = df["vr"].shift(1)
        f["vr5"] = (df["val"].rolling(5).mean() / med20.shift(5)).shift(1)
        f["vtrend"] = (df["val"].rolling(5).mean() / df["val"].rolling(60).mean()).shift(1)
        f["lval"] = np.log10(df["val"].rolling(20, min_periods=10).median().shift(1) + 1)
        f["tier"] = df["tier"]
        f["rank"] = rk
        f["lprice"] = np.log10(c.shift(1))
        hi20, lo20 = df["h"].rolling(20).max(), df["l"].rolling(20).min()
        hi250, lo250 = df["h"].rolling(250, min_periods=120).max(), df["l"].rolling(250, min_periods=120).min()
        f["dhi20"] = (c / hi20 - 1).shift(1)
        f["dlo20"] = (c / lo20 - 1).shift(1)
        f["dhi250"] = (c / hi250 - 1).shift(1)
        f["dlo250"] = (c / lo250 - 1).shift(1)
        f["brk20"] = ((c >= hi20.shift(1)).astype(int) - (c <= lo20.shift(1)).astype(int)).shift(1)
        ema20, ema50 = c.ewm(span=20).mean(), c.ewm(span=50).mean()
        f["ema20d"] = (c / ema20 - 1).shift(1)
        f["ema50d"] = (c / ema50 - 1).shift(1)
        up = (r > 0).astype(int)
        f["streak"] = up.groupby((up != up.shift()).cumsum()).cumcount().add(1).mul(np.where(up == 1, 1, -1)).shift(1)
        yy = df["y"].fillna(0)
        f["an20"] = yy.rolling(20).sum().shift(1)
        f["an60"] = yy.rolling(60).sum().shift(1)
        f["an250"] = yy.rolling(250, min_periods=60).sum().shift(1)
        last = pd.Series(np.where(yy > 0, np.arange(len(df)), np.nan)).ffill()
        f["since_an"] = (np.arange(len(df)) - last).shift(1).clip(upper=500)
        # календарь
        exi = np.array(sorted(ex) + [10 ** 6])
        pos = np.arange(len(df))
        nxt = exi[np.searchsorted(exi, pos)]
        f["to_ex"] = np.clip(nxt - pos, 0, 60)
        f["dow"] = df["d"].dt.weekday
        f["month"] = df["d"].dt.month
        f["season"] = df["d"].dt.month.isin([3, 4, 7, 8, 10, 11]).astype(int)
        f["eom"] = (df["d"].dt.day >= 25).astype(int)
        # рынок
        f = f.join(ixf, on="d")
        # новости
        dk = df["d"].values
        nn = {k: np.zeros(len(df)) for k in ["n", "night", "rum", "off", "w", "wmax"] + ["n_" + g for g in list(TYPES_GROUPS) + ["other"]]}
        mk = {k: np.zeros(len(df)) for k in ["m_n", "m_risk", "m_macro"]}
        for i, d in enumerate(dk):
            a = nd.get((t, d))
            if a:
                for k in nn:
                    nn[k][i] = a.get(k, 0)
            m = nd.get(("IMOEX", d))
            if m:
                mk["m_n"][i] = m.get("n", 0)
                mk["m_risk"][i] = m.get("n_risk", 0)
                mk["m_macro"][i] = m.get("n_macro", 0)
        for k, v in nn.items():
            f["nw_" + k] = v  # новости до открытия дня D (вечер/ночь/утро)
        for k, v in mk.items():
            f[k] = v
        n_all = pd.Series(nn["n"])
        f["nw_prev1"] = n_all.shift(1).values
        f["nw_prev5"] = n_all.rolling(5).sum().shift(1).values
        f["nw_prev60m"] = n_all.rolling(60, min_periods=10).mean().shift(1).values
        f["nw_surprise"] = f["nw_n"] / (f["nw_prev60m"] + 0.05)
        f["news_cov"] = 1 if (news_tickers is None or t in news_tickers) else 0
        f["t"] = t
        if hourly is not None:
            hq = hourly[hourly.t == t].set_index("d")
            prev = df["d"].shift(1)
            for col in ("ev_share", "ev_ret", "last_ret", "last_vsh", "hpeak", "close_pos"):
                f["h_" + col] = prev.map(hq[col]).values if len(hq) else np.nan
        for k in ("y", "y_px", "y_up", "y_dn", "y_vol", "x", "vr", "ex"):
            f[k] = df[k].values
        frames.append(f)
    if not frames:
        return pd.DataFrame(columns=["d", "t"])
    out = pd.concat(frames, ignore_index=True)
    return out


def hourly_daily(HH):
    """Профиль прошлого дня по часовым свечам: доля и доходность вечерней сессии, последний час основной, пик часового оборота."""
    rows = []
    for t, a in HH.items():
        if not a:
            continue
        df = pd.DataFrame([r[:6] for r in a], columns=["k", "o", "h", "l", "c", "v"])
        df["d"] = pd.to_datetime(df["k"].str[:10]); df["hr"] = df["k"].str[11:13].astype(int)
        df = df[df["d"].dt.weekday < 5]
        if df.empty:
            continue
        g = df.groupby("d")
        tot = g["v"].sum()
        ev = df[df.hr >= 19].groupby("d")
        main = df[(df.hr >= 10) & (df.hr <= 18)].groupby("d")
        last = df[df.hr == 18].groupby("d")
        out = pd.DataFrame({"t": t, "tot": tot})
        out["ev_share"] = ev["v"].sum().reindex(out.index).fillna(0) / out["tot"].replace(0, np.nan)
        out["ev_ret"] = (ev["c"].last() / main["c"].last() - 1).reindex(out.index)
        out["last_ret"] = (last["c"].last() / last["o"].first() - 1).reindex(out.index)
        out["last_vsh"] = last["v"].sum().reindex(out.index) / out["tot"].replace(0, np.nan)
        out["hpeak"] = g["v"].max() / g["v"].median().replace(0, np.nan)
        out["close_pos"] = ((g["c"].last() - g["l"].min()) / (g["h"].max() - g["l"].min()).replace(0, np.nan))
        rows.append(out.reset_index())
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["d", "t"])


def news_weight(e, impact):
    """Вес новости как в паблике: |средняя реакция за 5 дней| × уверенность (z/2, не больше 1)."""
    comp, sec, secof = impact["comp"], impact["sec"], impact["secOf"]
    s = e.get("s", 0)
    key = e["ty"] + ("+" if s > 0 else "-" if s < 0 else "0") if e["ty"] == "tone" else e["ty"]
    so = secof.get(e["tk"], "")
    v = comp.get(f"{e['tk']}|{key}") or comp.get(f"{e['tk']}|{e['ty']}") or sec.get(f"{so}|{key}") or sec.get(f"{so}|{e['ty']}")
    if not v or not v.get("n"):
        m = impact.get("mkt", {}).get(key) or impact.get("mkt", {}).get(e["ty"])
        if e["tk"] == "IMOEX" and m and m.get("n"):
            z = abs(m.get("up5", .5) - .5) / math.sqrt(.25 / m["n"])
            return abs(m.get("r5", 0)) * min(1, z / 2)
        return 0.0
    z = abs(v.get("up5", .5) - .5) / math.sqrt(.25 / v["n"])
    return abs(v.get("a5", 0)) * min(1, z / 2)


LABELS = ["y", "y_px", "y_up", "y_dn", "y_vol"]
NONFEAT = set(LABELS + ["d", "t", "x", "vr", "ex"])


def feature_cols(df):
    return [c for c in df.columns if c not in NONFEAT]
