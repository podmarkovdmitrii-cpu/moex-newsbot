# -*- coding: utf-8 -*-
"""Исследования на истории для паблика (GitHub Actions, workflow research.yml; результат — research-stats/study.json).

1) Психология толпы: бумага за 3 дня обогнала индекс на 6%+ (или отстала на 6%+) на повышенном обороте, а новостей о компании
   за эти дни не было. Что дальше — импульс продолжается (толпа сильнее здравого смысла) или движение откатывается?
   Для сравнения — такие же движения, но с новостями.
2) Смежные бумаги: бумага-лидер за день обогнала индекс на 4%+ на обороте 2,5×+. Догоняют ли её в следующие 1–3 дня бумаги
   того же или смежного сектора, которые в этот день отстали? Статистика по парам «сектор лидера → сектор бумаги».
3) Торги выходного дня (сессии Мосбиржи с марта 2025 — те же, что видны в Т-Инвестициях): насколько бумаги двигаются в выходные
   и продолжает ли понедельник движение выходных.

Честность: всё, что используется для отбора события дня D, известно к закрытию дня D; результат меряется с D+1.
История — с 2008 г.: свечи Мосбиржи; новости — архив раздела «Экономика» Интерфакса с 2008 г. (research/news_hist.py) и журнал бота
с 2018 г. Условие «без новостей» проверяется с месяца, когда архив достаточно полный.
Описательная таблица: 2008–2022 против 2023–2026. Решение «давать сигнал или нет» принимает research/walkforward.py — обучение
на разных промежутках (расширяющееся окно, скользящие 3 и 5 лет) с проверкой на следующем году.
Независимая проверка — research/verify.py (другой код, перестановочный тест, стабильность по годам).
"""
import datetime as dt
import gzip
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "anomaly"))
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
OUT = os.environ.get("RESEARCH_DIR") or os.path.join(HERE, "out")
START = os.environ.get("RESEARCH_START", "2008-01-01")
NEWS_FROM = os.environ.get("RESEARCH_NEWS_FROM", "2018-01-01")  # журнал бота — с 2018 г.; с архивом Интерфакса (news_hist.py) — с 2008 г.
HIST = os.environ.get("RESEARCH_HIST") or os.path.join(os.path.dirname(OUT), "research-hist")
SPLIT = os.environ.get("RESEARCH_SPLIT", "2023-01-01")
MSK = dt.timedelta(hours=3)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


SEC_OF = None


def sec_of():
    global SEC_OF
    if SEC_OF is None:
        import re
        src = open(os.path.join(HERE, "..", "nlp.js"), encoding="utf-8").read()
        m = re.search(r"const SEC_OF = (\{.*?\});", src)
        SEC_OF = json.loads(m.group(1)) if m else {}
        SEC_OF.update({"CNRU": "tech", "RAGR": "consumer"})
    return SEC_OF


# ------------------------------------------------------------------ данные
def load_panel(tickers=None):
    """Дневные свечи из кэша anomaly-data (с докачкой с Мосбиржи). Возвращает (будни: dict матриц, выходные: сырые бары)."""
    import train as T
    if tickers is None:
        try:
            tickers = T.universe()
        except Exception as e:  # noqa: BLE001
            log("список бумаг недоступен:", e)
            tickers = [f[:-8] for f in os.listdir(os.path.join(T.DATA, "d")) if f.endswith(".json.gz")]
    tickers = [t for t in tickers if t != "IMOEX"]
    H = T.load_series("d", tickers + ["IMOEX"], START, index=("IMOEX",))
    H = backfill(H, T)
    return build_panel(H)


def _adjust(rows):
    """Склейка истории: дробления/консолидации акций (скачок цены в 3,5+ раза за ночь) — приводим прошлое к текущему масштабу."""
    rows = [list(r) for r in rows]
    for i in range(len(rows) - 1, 0, -1):
        p, b = rows[i - 1], rows[i]
        if not (p[4] and b[1] and p[4] > 0 and b[1] > 0):
            continue
        r = b[1] / p[4]
        if r > 3.5 or r < 1 / 3.5:
            for q in rows[:i]:
                for k in (1, 2, 3, 4):
                    q[k] *= r
    return rows


def fetch_market(t, T):
    """Свечи по рынку акций без привязки к режиму: у многих бумаг история TQBR начинается только в 2010–2014 гг.
    (раньше основным был режим EQBR/EQNE), а рыночные свечи Мосбиржи идут с 2008 г."""
    if t == "IMOEX":
        return T.fetch("d", t, START, index=True)
    from common import candles
    days = (dt.date.today() - dt.date.fromisoformat(START)).days + 1
    B = candles(f"engines/stock/markets/shares/securities/{t}/candles.json", days, 24)
    return [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in B]


def backfill(H, T):
    """Кэш свечей модели аномалий (режим TQBR) у многих бумаг начинается в 2012–2014 гг. Для исследования докачиваем с Мосбиржи
    рыночные свечи с START (по умолчанию 2008)
    и храним её отдельно (кэш research-hist), чтобы не менять данные обучения моделей."""
    import concurrent.futures as cf
    os.makedirs(HIST, exist_ok=True)
    lim = (dt.date.fromisoformat(START) + dt.timedelta(days=45)).isoformat()

    def one(t):
        rows = H.get(t) or []
        if rows and rows[0][0][:10] <= lim:
            return t, rows, 0
        p = os.path.join(HIST, t + ".mkt.json.gz")
        pre, got = None, 0
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                pre = json.load(f)
        else:
            try:
                full = fetch_market(t, T)
            except Exception as e:  # noqa: BLE001
                log("история до кэша недоступна:", t, e)
                return t, rows, 0
            first = rows[0][0][:10] if rows else "9999"
            pre = [r for r in full if r[0][:10] < first]
            if not rows:
                pre = full
            with gzip.open(p, "wt") as f:
                json.dump(pre, f, separators=(",", ":"))
            got = 1
        if not pre:
            return t, rows, got
        first = rows[0][0][:10] if rows else "9999"
        return t, _adjust([r for r in pre if r[0][:10] < first] + rows), got
    out, n_new = {}, 0
    with cf.ThreadPoolExecutor(4) as ex:
        for k, (t, rows, got) in enumerate(ex.map(one, list(H.keys()))):
            out[t] = rows
            n_new += got
            if k % 50 == 0:
                log("история с", START, ":", k, "/", len(H))
    early = sum(1 for r in out.values() if r and r[0][0][:10] <= lim)
    log("история: докачано", n_new, "бумаг; с", START, "есть данные у", early, "из", len(out))
    return out


def build_panel(H):
    wk, wend = {}, {}
    for t, rows in H.items():
        df = pd.DataFrame([r[:6] for r in rows], columns=["d", "o", "h", "l", "c", "v"])
        df["d"] = pd.to_datetime(df["d"].str[:10])
        df = df[df["d"] >= pd.Timestamp(START)].drop_duplicates("d").set_index("d").sort_index()
        wd = df[df.index.day_of_week < 5]
        we = df[df.index.day_of_week >= 5]
        if len(wd) > 60:
            wk[t] = wd
        if len(we):
            wend[t] = df
    if "IMOEX" not in wk:
        raise SystemExit("нет индекса IMOEX — исследование невозможно")
    idx = wk["IMOEX"].index
    P = {k: pd.DataFrame({t: d[k] for t, d in wk.items() if t != "IMOEX"}).reindex(idx) for k in ("o", "h", "l", "c", "v")}
    P["ix"] = wk["IMOEX"]["c"].reindex(idx)
    return P, wend


def load_news(days):
    """События новостей по бумагам → торговый день: до 18:45 МСК — этот день, позже — следующий. Возвращает {тикер: set(день)}."""
    import train as T
    global NEWS_FROM
    ev = T.events_all()
    ifx = ifx_events()
    if ifx:
        ev = ev + ifx
        nf = ifx_from()
        if nf and "RESEARCH_NEWS_FROM" not in os.environ:
            NEWS_FROM = nf
        log("новости Интерфакса (архив с 2008 г.):", len(ifx), "событий о компаниях; условие «без новостей» проверяется с", NEWS_FROM)
    dd = days.values.astype("datetime64[D]")
    out = {}
    for e in ev:
        tk = e.get("tk")
        if not tk or tk in ("IMOEX", "@INS", "@REG"):
            continue
        when = dt.datetime.fromtimestamp(e["ts"], dt.timezone.utc).replace(tzinfo=None) + MSK
        key = np.datetime64(when.strftime("%Y-%m-%d"))
        j = int(np.searchsorted(dd, key))
        if j < len(dd) and dd[j] == key and (when.hour, when.minute) >= (18, 45):
            j += 1
        if j >= len(dd):
            continue
        out.setdefault(tk, set()).add(j)
    log("новостей привязано к дням:", sum(len(v) for v in out.values()), "по", len(out), "бумагам")
    return out


def ifx_events():
    """События из архива Интерфакса (research/news_hist.py + ifx_events.js), если они скачаны."""
    p = os.path.join(HIST, "ifx_events.json.gz")
    if not os.path.exists(p):
        return []
    with gzip.open(p, "rt") as f:
        return [dict(tk=tk, ts=ts, ty=ty, s=s_, rum=rum, off=off, src="ifx") for tk, ts, ty, s_, rum, off in json.load(f)]


def ifx_from(min_month=250):
    """С какого месяца архив Интерфакса достаточно полный (≥ min_month заголовков в месяц), чтобы судить «новостей не было»."""
    try:
        cover = json.load(open(os.path.join(HIST, "ifx_meta.json")))
    except Exception:  # noqa: BLE001
        return None
    ok = sorted(k for k, v in cover.items() if v >= min_month)
    return ok[0] + "-01" if ok else None


def ex_div(days):
    """Дни отсечки дивидендов: {тикер: set(индекс дня)} — гэп на дивиденд не считаем движением толпы."""
    try:
        divh = json.load(open(os.path.join(HERE, "..", "divhist.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    dd = days.values.astype("datetime64[D]")
    out = {}
    for t, rows in divh.items():
        for r in rows:
            d = r[0] if isinstance(r, (list, tuple)) else r.get("d") or r.get("date")
            if not d:
                continue
            j = int(np.searchsorted(dd, np.datetime64(str(d)[:10])))
            if 0 < j < len(dd):
                out.setdefault(t, set()).update({j, j + 1})
    return out


def stats(x):
    x = np.asarray([v for v in x if v == v], dtype=float)
    if not len(x):
        return dict(n=0)
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    return dict(n=int(len(x)), mean=round(float(x.mean()), 5), med=round(float(np.median(x)), 5), hit=round(float((x > 0).mean()), 3),
                t=round(float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else 0.0, 2))


# ------------------------------------------------------------------ 1. психология толпы
CROWD = dict(win=3, thr=0.06, vx=2.0, liq=30e6, gap=5)


def crowd_events(P, news, divs, prm=CROWD):
    C, V, ix = P["c"], P["v"], P["ix"]
    days = C.index
    w = prm["win"]
    ex = C.pct_change(w, fill_method=None).sub(ix.pct_change(w, fill_method=None), axis=0)
    med20 = V.rolling(20, min_periods=15).median().shift(w)  # норма — до начала окна
    vx = V.rolling(w).mean() / med20
    liq = V.rolling(20, min_periods=15).median().shift(1)
    rows = []
    j_news = int(np.searchsorted(days.values, np.datetime64(NEWS_FROM)))
    for t in C.columns:
        e, x, lq = ex[t].values, vx[t].values, liq[t].values
        nd, dv = news.get(t, set()), divs.get(t, set())
        last = -99
        for j in range(25, len(days) - 1):
            if e[j] != e[j] or abs(e[j]) < prm["thr"] or not (x[j] >= prm["vx"]) or not (lq[j] >= prm["liq"]) or j - last < prm["gap"]:
                continue
            if any(k in dv for k in range(j - w + 1, j + 1)):
                continue
            n_news = sum(1 for k in range(j - w + 1, j + 1) if k in nd) if j - w + 1 >= j_news else -1  # −1: новостей за те годы нет в истории
            last = j
            rows.append((t, j, days[j], 1 if e[j] > 0 else -1, float(e[j]), float(x[j]), n_news))
    return pd.DataFrame(rows, columns=["t", "j", "d", "dir", "ex", "vx", "news"])


def forward(P, ev, hs=(1, 3, 5, 10)):
    """Избыточная доходность бумаги к индексу от закрытия D до закрытия D+h; и гэп открытия D+1."""
    C, O, ix = P["c"].values, P["o"].values, P["ix"].values
    col = {t: i for i, t in enumerate(P["c"].columns)}
    n = len(P["c"])
    for h in hs:
        out = []
        for t, j in zip(ev["t"], ev["j"]):
            i = col[t]
            k = j + h
            if k >= n or not (C[j, i] == C[j, i]) or not (C[k, i] == C[k, i]):
                out.append(np.nan)
                continue
            out.append((C[k, i] / C[j, i] - 1) - (ix[k] / ix[j] - 1))
        ev[f"f{h}"] = out
    g = []
    for t, j in zip(ev["t"], ev["j"]):
        i = col[t]
        g.append(O[j + 1, i] / C[j, i] - 1 if j + 1 < n and O[j + 1, i] == O[j + 1, i] and C[j, i] else np.nan)
    ev["gap"] = g
    return ev


def summarize(ev, by_dir=True):
    out = {}
    for d in ((1, -1) if by_dir else (0,)):
        e = ev[ev["dir"] == d] if d else ev
        r = {}
        for part, m in (("train", e["d"] < SPLIT), ("test", e["d"] >= SPLIT), ("all", e["d"] == e["d"])):
            s = e[m]
            # «по направлению движения»: + значит движение продолжилось, − — откатилось
            r[part] = {f"f{h}": stats(s[f"f{h}"] * (d or 1)) for h in (1, 3, 5, 10)}
            r[part]["gap"] = stats(s["gap"] * (d or 1))
        out["up" if d == 1 else "down" if d == -1 else "all"] = r
    return out


def crowd_study(P, news, divs):
    ev = forward(P, crowd_events(P, news, divs))
    log("толпа: событий", len(ev), "без новостей", int((ev.news == 0).sum()))
    res = {"params": CROWD, "no_news": summarize(ev[ev.news == 0]), "with_news": summarize(ev[ev.news > 0]),
           "any_news": summarize(ev), "news_from": NEWS_FROM}
    # сила движения: 6–10% и 10%+
    for lo, hi, name in ((0.06, 0.10, "6-10"), (0.10, 9, "10+")):
        e = ev[(ev.news == 0) & (ev.ex.abs() >= lo) & (ev.ex.abs() < hi)]
        res["no_news_" + name] = summarize(e)
    # правило для живого режима: решение — по обучающему периоду, подтверждение — на проверочном
    rules = {}
    for side in ("up", "down"):
        tr, te = res["no_news"][side]["train"]["f5"], res["no_news"][side]["test"]["f5"]
        mode = "neutral"
        if tr.get("n", 0) >= 40:
            if tr["mean"] >= 0.004 and tr["hit"] >= 0.52:
                mode = "continue"
            elif tr["mean"] <= -0.004 and tr["hit"] <= 0.48:
                mode = "reverse"
        conf = mode != "neutral" and te.get("n", 0) >= 15 and ((te["mean"] > 0) == (mode == "continue"))
        rules[side] = dict(mode=mode, test_confirms=bool(conf), train=tr, test=te)
    res["rules"] = rules
    ev[["t", "d", "dir", "ex", "vx", "news", "f1", "f3", "f5", "f10", "gap"]].assign(d=lambda x: x.d.dt.strftime("%Y-%m-%d")).to_csv(os.path.join(OUT, "crowd_events.csv"), index=False)
    return res


# ------------------------------------------------------------------ 2. смежные бумаги
SPILL = dict(thr=0.04, vx=2.5, liq=50e6, lag=0.3)


def spill_study(P):
    C, V, ix = P["c"], P["v"], P["ix"]
    so = sec_of()
    ex1 = C.pct_change(fill_method=None).sub(ix.pct_change(fill_method=None), axis=0)
    vx = V / V.rolling(20, min_periods=15).median().shift(1)
    liq = V.rolling(20, min_periods=15).median().shift(1)
    cols = [t for t in C.columns if t in so]
    days = C.index
    Cv, ixv = C[cols].values, ix.values
    E, X, L = ex1[cols].values, vx[cols].values, liq[cols].values
    n = len(days)
    sec = [so[t] for t in cols]
    rows = []
    for j in range(25, n - 3):
        lead = [i for i in range(len(cols)) if E[j, i] == E[j, i] and abs(E[j, i]) >= SPILL["thr"] and X[j, i] >= SPILL["vx"] and L[j, i] >= SPILL["liq"]]
        if not lead or len(lead) > 6:  # 7+ бумаг сразу — движение всего рынка, а не одной бумаги
            continue
        for i in lead:
            d = 1 if E[j, i] > 0 else -1
            for k in range(len(cols)):
                if k == i or not (L[j, k] >= SPILL["liq"]) or not (E[j, k] == E[j, k]):
                    continue
                if E[j, k] * d >= SPILL["lag"] * abs(E[j, i]):  # уже отреагировала
                    continue
                if not (Cv[j + 3, k] == Cv[j + 3, k]) or not (Cv[j + 1, k] == Cv[j + 1, k]):
                    continue
                f1 = (Cv[j + 1, k] / Cv[j, k] - 1) - (ixv[j + 1] / ixv[j] - 1)
                f3 = (Cv[j + 3, k] / Cv[j, k] - 1) - (ixv[j + 3] / ixv[j] - 1)
                rows.append((days[j], cols[i], cols[k], sec[i], sec[k], d, float(E[j, i]), float(E[j, k]), f1 * d, f3 * d))
    ev = pd.DataFrame(rows, columns=["d", "lead", "peer", "s1", "s2", "dir", "e_lead", "e_peer", "f1", "f3"])
    log("смежные: пар лидер→бумага", len(ev))
    out = []
    for (s1, s2, d), g in ev.groupby(["s1", "s2", "dir"]):
        tr, te = g[g.d < SPLIT], g[g.d >= SPLIT]
        # одно событие лидера даёт много бумаг-соседей — для честной выборки усредняем по событию (лидер, день)
        gtr = tr.groupby(["d", "lead"]).f3.mean()
        gte = te.groupby(["d", "lead"]).f3.mean()
        a, b = stats(gtr), stats(gte)
        ok = a.get("n", 0) >= 25 and a["mean"] >= 0.004 and a["t"] >= 2 and b.get("n", 0) >= 8 and b["mean"] > 0
        out.append(dict(s1=s1, s2=s2, dir=int(d), train=a, test=b, f1_all=stats(g.groupby(["d", "lead"]).f1.mean()), ok=bool(ok),
                        top_peers=g.groupby("peer").f3.agg(["count", "mean"]).query("count >= 8").sort_values("mean", ascending=False).head(5).round(4).reset_index().values.tolist()))
    out.sort(key=lambda r: -(r["train"].get("t") or 0))
    ev.assign(d=ev.d.dt.strftime("%Y-%m-%d")).to_csv(os.path.join(OUT, "spill_events.csv.gz"), index=False, compression="gzip")
    return {"params": SPILL, "pairs": out, "n_events": int(len(ev))}


# ------------------------------------------------------------------ 3. торги выходного дня
def weekend_rows(wend, liq_map):
    rows = []
    for t, df in wend.items():
        if t == "IMOEX":
            continue
        d = df.index
        we = df[d.day_of_week >= 5]
        if not len(we):
            continue
        for sat in sorted({(x - pd.Timedelta(days=x.weekday() - 5)).normalize() for x in we.index}):
            fri = df[(d < sat) & (d.day_of_week < 5)]
            mon = df[(d > sat + pd.Timedelta(days=1)) & (d.day_of_week < 5)]
            w = we[(we.index >= sat) & (we.index <= sat + pd.Timedelta(days=1))]
            if not len(fri) or not len(mon) or not len(w):
                continue
            F, M = fri.iloc[-1], mon.iloc[0]
            if (mon.index[0] - sat).days > 3:  # понедельник — праздник: пропускаем
                continue
            Wc, Wh, Wl, Wv = w["c"].iloc[-1], w["h"].max(), w["l"].min(), w["v"].sum()
            up, dn = Wh / F.c - 1, Wl / F.c - 1
            rows.append(dict(t=t, sat=sat.strftime("%Y-%m-%d"), wret=Wc / F.c - 1, wmax=up if abs(up) >= abs(dn) else dn, wrange=up - dn, wval=Wv,
                             liq=liq_map.get(t, 0), gap=M.o / Wc - 1, mon=M.c / Wc - 1, mon_vs_fri=M.c / F.c - 1,
                             beyond=int((M.h > Wh) if Wc >= F.c else (M.l < Wl))))
    return pd.DataFrame(rows)


def weekend_study(P, wend):
    liq = P["v"].rolling(20, min_periods=10).median().iloc[-1].to_dict()
    W = weekend_rows(wend, liq)
    if W.empty:
        log("выходные: данных нет")
        return {"n": 0}
    W = W[(W.liq >= 30e6) & (W.wval >= 1e6)]
    log("выходные: бумага×выходные", len(W), "выходных", W.sat.nunique())
    res = {"n": int(len(W)), "weekends": int(W.sat.nunique()), "first": W.sat.min(), "last": W.sat.max()}
    res["move"] = dict(abs_wret_med=round(float(W.wret.abs().median()), 4), abs_wmax_med=round(float(W.wmax.abs().median()), 4),
                       val_share_med=round(float((W.wval / W.liq).median()), 4))
    sgn = np.sign(W.wret)
    res["corr_wret_mon"] = round(float(np.corrcoef(W.wret, W.mon)[0, 1]), 3) if len(W) > 10 else None
    buckets = []
    for lo, hi, name in ((-9, -0.02, "≤−2%"), (-0.02, -0.007, "−2…−0,7%"), (-0.007, 0.007, "±0,7%"), (0.007, 0.02, "+0,7…+2%"), (0.02, 9, "≥+2%")):
        g = W[(W.wret > lo) & (W.wret <= hi)]
        s = np.sign(g.wret).replace(0, 1)
        buckets.append(dict(name=name, lo=lo, hi=hi, n=int(len(g)), mon=stats(g.mon), mon_dir=stats(g.mon * s), gap_dir=stats(g.gap * s),
                            beyond=round(float(g.beyond.mean()), 3) if len(g) else None, mon_vs_fri=stats(g.mon_vs_fri)))
    res["buckets"] = buckets
    # продолжение движения выходных в понедельник (по направлению выходных), отдельно для сильных движений
    strong = W[W.wret.abs() >= 0.015]
    res["continue_all"] = stats(W.mon * sgn.replace(0, 1))
    res["continue_strong"] = stats(strong.mon * np.sign(strong.wret))
    res["by_ticker"] = (W.groupby("t").apply(lambda g: pd.Series(dict(n=len(g), wmax=float(g.wmax.abs().median()), cont=float((g.mon * np.sign(g.wret).replace(0, 1)).mean()))), include_groups=False)
                        .query("n >= 8").sort_values("wmax", ascending=False).head(40).round(4).reset_index().to_dict("records"))
    W.to_csv(os.path.join(OUT, "weekend_rows.csv"), index=False)
    return res


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    P, wend = load_panel()
    days = P["c"].index
    log("панель:", P["c"].shape[1], "бумаг,", len(days), "дней", days[0].date(), "—", days[-1].date())
    cnt = P["c"].notna().groupby(days.year).any().sum(axis=1)
    log("бумаг с торгами по годам:", ", ".join(f"{y}: {int(n)}" for y, n in cnt.items()))
    news = load_news(days)
    divs = ex_div(days)
    res = {"built": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"), "range": [str(days[0].date()), str(days[-1].date())], "split": SPLIT,
           "tickers": int(P["c"].shape[1]), "news_days": int(sum(len(v) for v in news.values()))}
    res["crowd"] = crowd_study(P, news, divs)
    res["spill"] = spill_study(P)
    res["weekend"] = weekend_study(P, wend)
    json.dump(res, open(os.path.join(OUT, "study.json"), "w"), ensure_ascii=False, default=float)
    report(res)
    log("готово за", round((time.time() - t0) / 60, 1), "мин")


def fmtp(s, k="mean"):
    return "—" if not s or not s.get("n") else f"{s[k] * 100:+.2f}% (попаданий {s['hit']:.0%}, n={s['n']}, t={s['t']})"


def report(res):
    c = res["crowd"]
    log("=== ПСИХОЛОГИЯ ТОЛПЫ (3 дня ±6% к индексу, оборот ≥2×) ===")
    for side in ("up", "down"):
        for kind in ("no_news", "with_news"):
            r = c[kind][side]
            log(f"{side:4} {kind:9} train f5 {fmtp(r['train']['f5'])} | test f5 {fmtp(r['test']['f5'])} | test f1 {fmtp(r['test']['f1'])}")
        log("   правило:", c["rules"][side]["mode"], "подтверждено на 2023+" if c["rules"][side]["test_confirms"] else "не подтверждено на 2023+")
    s = res["spill"]
    log("=== СМЕЖНЫЕ БУМАГИ: пары секторов, где отставшие догоняли лидера за 3 дня ===")
    for r in [r for r in s["pairs"] if r["ok"]][:25]:
        log(f"{r['s1']:>9}→{r['s2']:<9} {'рост' if r['dir'] > 0 else 'падение'}: train {fmtp(r['train'])} | test {fmtp(r['test'])}")
    w = res["weekend"]
    if w.get("n"):
        log(f"=== ВЫХОДНЫЕ: {w['weekends']} выходных, {w['n']} наблюдений; медианный ход за выходные {w['move']['abs_wret_med']:.2%}, макс. {w['move']['abs_wmax_med']:.2%}")
        for b in w["buckets"]:
            log(f"   выходные {b['name']:>10}: понедельник по направлению {fmtp(b['mon_dir'])}, гэп {fmtp(b['gap_dir'])}, вышла за экстремум выходных {b['beyond']}")
        log("   продолжение (все):", fmtp(w["continue_all"]), "| сильные ≥1,5%:", fmtp(w["continue_strong"]))


if __name__ == "__main__":
    main()
