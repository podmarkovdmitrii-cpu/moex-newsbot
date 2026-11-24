# -*- coding: utf-8 -*-
"""Проверка №37: макро- и секторальные драйверы как контекстные фильтры для S1 / F4 / F2.

Факторы на день сигнала (только то, что известно до входа: значения на предыдущий торговый день):
  • ключевая ставка ЦБ — изменение за 90 дней (снижается / растёт / без изменений); история — cbr.ru, запасной вариант — новости cbr_cut/cbr_hike;
  • нефть Brent — изменение за 20 торговых дней (растёт > +5% / падает < −5% / ровно) — FRED DCOILBRENTEU;
  • курс доллара ЦБ — изменение за 20 дней (рубль слабеет > +3% / крепнет < −3% / ровно) — cbr.ru;
  • режим рынка — IMOEX выше / ниже 50-дневной средней;
  • геополитика — новости «эскалация» и «санкции» за 3 дня (много / мало; граница — медиана по годам отбора);
  • секторальные связки: нефтяник × нефть, экспортёр (нефть, металлы, химия) × рубль, банк × ставка, чувствительные к ставке
    (девелоперы, IT/телеком, энергетика, ритейл, транспорт, банки) × ставка; F4 — падение вместе с рынком (индекс в день сигнала < −1%).
Сделки: А) 5-минутные свечи 2020–2026, правила бота (как №30); Б) дневные аналоги 2014–2026 (как №31, все сигналы, правила без выбора).
Фильтр «только когда X» / «кроме когда X» принимается, если средняя сделка лучше всех сделок на ≥ 0,2 п.п. и на годах отбора, и на годах
проверки (А: 2020–22 → 2023–26; Б: 2014–19 → 2020–26), остаётся ≥ 30% сделок и ≥ 15 сделок на проверке; и только если тот же фильтр
подтвердился в обоих наборах (А и Б) — иначе слишком велик шанс случайной находки при ~150 сравнениях.
Не является индивидуальной инвестиционной рекомендацией.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
SECT = json.load(open(os.path.join(HERE, "..", "impact.json"), encoding="utf-8")).get("secOf", {})
RATE_SENS = {"devel", "tech", "power", "retail", "transport", "bank"}
EXPORT = {"oil", "metal", "chem"}


def get(url, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research script)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            log("загрузка", url[:80], e)
            time.sleep(3 * (k + 1))
    return ""


def key_rate(ev):
    """Ключевая ставка по дням: cbr.ru; если недоступно — ступенчатый ряд из новостей cbr_cut/cbr_hike (только направление)."""
    to = dt.date.today().strftime("%d.%m.%Y")
    h = get(f"https://www.cbr.ru/hd_base/KeyRate/?UniDbQuery.Posted=True&UniDbQuery.From=17.09.2013&UniDbQuery.To={to}")
    rows = re.findall(r"<td>(\d\d\.\d\d\.\d{4})</td>\s*<td>([\d,]+)</td>", h)
    if len(rows) > 500:
        s = pd.Series({pd.Timestamp(dt.datetime.strptime(d, "%d.%m.%Y")): float(v.replace(",", ".")) for d, v in rows}).sort_index()
        log(f"ключевая ставка cbr.ru: {len(s)} дней, {s.index[0].date()} – {s.index[-1].date()}, сейчас {s.iloc[-1]}")
        return s, "cbr.ru"
    lvl, out = 0.0, {}
    for e in ev:
        if e.get("ty") in ("cbr_cut", "cbr_hike"):
            d = pd.Timestamp(dt.datetime.utcfromtimestamp(e["ts"]).date())
            if d not in out:
                lvl += -1 if e["ty"] == "cbr_cut" else 1
                out[d] = lvl
    s = pd.Series(out).sort_index()
    log(f"ключевая ставка: cbr.ru недоступен — направление по новостям ({len(s)} событий)")
    return s, "news"


def usd_rub():
    to = dt.date.today().strftime("%d/%m/%Y")
    x = get(f"https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1=01/01/2008&date_req2={to}&VAL_NM_RQ=R01235")
    rows = re.findall(r'Date="(\d\d\.\d\d\.\d{4})"[^>]*>.*?<Value>([\d,]+)</Value>', x, re.S)
    if len(rows) < 500:
        log("курс доллара ЦБ недоступен")
        return None
    s = pd.Series({pd.Timestamp(dt.datetime.strptime(d, "%d.%m.%Y")): float(v.replace(",", ".")) for d, v in rows}).sort_index()
    log(f"курс доллара ЦБ: {len(s)} дней, {s.index[0].date()} – {s.index[-1].date()}")
    return s


def brent():
    t = get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU")
    rows = [ln.split(",") for ln in t.splitlines()[1:] if "," in ln]
    s = pd.Series({pd.Timestamp(d): float(v) for d, v in rows if re.match(r"^[\d.]+$", v.strip())}).sort_index()
    if len(s) < 500:
        log("Brent (FRED) недоступен")
        return None
    log(f"Brent FRED: {len(s)} дней, {s.index[0].date()} – {s.index[-1].date()}")
    return s


def asof(s, d, lag=1):
    """Значение ряда строго до дня d (lag=1) или на день d (lag=0)."""
    if s is None or not len(s):
        return np.nan
    i = s.index.searchsorted(pd.Timestamp(d), side="left" if lag else "right") - 1
    return float(s.iloc[i]) if i >= 0 else np.nan


def chg(s, d, days, lag=1):
    a = asof(s, d, lag)
    b = asof(s, pd.Timestamp(d) - pd.Timedelta(days=days), lag)
    return a, b


def make_ctx(ev, IX):
    rate, src = key_rate(ev)
    usd, br = usd_rub(), brent()
    ix = pd.Series({pd.Timestamp(r[0][:10]): float(r[4]) for r in IX}).sort_index()
    ix_ma = ix.rolling(50).mean()
    ixr = ix.pct_change()
    geo = {}
    for e in ev:
        if e.get("ty") in ("geo_escal", "sanctions"):
            d = dt.datetime.utcfromtimestamp(e["ts"]).date()
            geo[d] = geo.get(d, 0) + 1
    return dict(rate=rate, src=src, usd=usd, br=br, ix=ix, ix_ma=ix_ma, ixr=ixr, geo=geo)


def states(C, d, t, lag, side, mkt=None):
    """Набор меток-условий для сделки (тикер t, день сигнала d)."""
    d = pd.Timestamp(d)
    out = set()
    a, b = chg(C["rate"], d, 90, lag)
    if a == a and b == b:
        thr = 0.25 if C["src"] == "cbr.ru" else 0.5
        out.add("ставка снижается (90 дн.)" if a - b < -thr else "ставка растёт (90 дн.)" if a - b > thr else "ставка без изменений (90 дн.)")
    a, b = chg(C["br"], d, 28, lag)
    if a == a and b == b and b:
        r = a / b - 1
        out.add("нефть растёт (> +5% за 20 дн.)" if r > 0.05 else "нефть падает (< −5% за 20 дн.)" if r < -0.05 else "нефть ровно (±5%)")
    a, b = chg(C["usd"], d, 28, lag)
    if a == a and b == b and b:
        r = a / b - 1
        out.add("рубль слабеет (> +3% за 20 дн.)" if r > 0.03 else "рубль крепнет (< −3% за 20 дн.)" if r < -0.03 else "рубль ровно (±3%)")
    i_ = asof(C["ix"], d, lag)
    m_ = asof(C["ix_ma"], d, lag)
    if i_ == i_ and m_ == m_:
        out.add("рынок выше 50-дн. средней" if i_ > m_ else "рынок ниже 50-дн. средней")
    g = sum(C["geo"].get((d - pd.Timedelta(days=k)).date(), 0) for k in range(1, 4))
    out.add("геополитика: много новостей (3 дн.)" if g >= C.get("geo_med", 1) else "геополитика: мало новостей (3 дн.)")
    sec = SECT.get(t)
    base = list(out)
    if sec == "oil":
        for s_ in base:
            if s_.startswith("нефть"):
                out.add("нефтяник × " + s_)
    if sec in EXPORT:
        for s_ in base:
            if s_.startswith("рубль"):
                out.add("экспортёр × " + s_)
    if sec == "bank":
        for s_ in base:
            if s_.startswith("ставка"):
                out.add("банк × " + s_)
    if sec in RATE_SENS:
        for s_ in base:
            if s_.startswith("ставка"):
                out.add("чувствит. к ставке × " + s_)
    r0 = asof(C["ixr"], d, 0) if mkt is None else mkt     # 5-мин: индекс к часу сигнала (без заглядывания в закрытие дня)
    if side < 0 and r0 == r0:
        out.add("падение вместе с рынком (индекс < −1% в день сигнала)" if r0 < -0.01 else "падение без рынка (индекс ≥ −1%)")
    return out


def evaluate(name, T, split):
    """T: DataFrame strat, y, ret, st (set). → {(strat, cond, mode): dict}"""
    res = {}
    for strat, g in T.groupby("strat"):
        tr, te = g[g.y < split], g[g.y >= split]
        if len(tr) < 20 or len(te) < 15:
            continue
        b_tr, b_te = tr.ret.mean(), te.ret.mean()
        conds = sorted(set().union(*g.st))
        log(f"  {name} {strat}: все сделки {len(tr)} → {len(te)}, средняя {b_tr:+.2%} → {b_te:+.2%}")
        rows = []
        for c in conds:
            for mode in ("только", "кроме"):
                f = (lambda s, c=c: c in s) if mode == "только" else (lambda s, c=c: c not in s)
                a, b = tr[tr.st.map(f)], te[te.st.map(f)]
                if len(a) < 8 or len(b) < 5:
                    continue
                share = (len(a) + len(b)) / len(g)
                d_tr, d_te = a.ret.mean() - b_tr, b.ret.mean() - b_te
                ok = bool(d_tr >= 0.002 and d_te >= 0.002 and share >= 0.3 and len(b) >= 15)
                res[(strat, c, mode)] = dict(n_tr=len(a), n_te=len(b), m_tr=a.ret.mean(), m_te=b.ret.mean(), d_tr=d_tr, d_te=d_te, share=share, ok=ok,
                                             win_te=float((b.ret > 0).mean()))
                rows.append((ok, d_te, c, mode, len(a), len(b), a.ret.mean(), b.ret.mean(), share))
        rows.sort(key=lambda z: (-z[0], -z[1]))
        for ok, dte, c, mode, na, nb, ma, mb, sh in rows[:8]:
            log(f"     {'✔' if ok else ' '} {mode} «{c}»: {na} → {nb} сд., {ma:+.2%} → {mb:+.2%} (к всем {ma - b_tr:+.2%} / {mb - b_te:+.2%}), остаётся {sh:.0%}")
    return res


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    import exp9 as E9
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    C = make_ctx(ev, IX)
    bd = pd.bdate_range("2014-01-01", "2019-12-31")
    s3 = [sum(C["geo"].get((d - pd.Timedelta(days=k)).date(), 0) for k in range(1, 4)) for d in bd]
    C["geo_med"] = max(1, int(np.median(s3)))
    log(f"геополитика: дней с новостями «эскалация/санкции» {len(C['geo'])}, граница «много» за 3 дня ≥ {C['geo_med']}; {round(time.time() - t0)} с")
    rep = {"rate_src": C["src"]}
    # ---------- Б: дневные аналоги ----------
    import exp31 as E31
    B, _ = E31.build(X, Dd, IX)
    rows = []
    for x in B.to_dict("records"):
        if x["y"] < 2014:
            continue
        for strat in (("S1", "F2") if x["s"] > 0 else ("F4",)):
            z = E31.trade(x, strat, "нет", "как сейчас", "свой")
            if z and z != "skip":
                rows.append(dict(strat=strat, y=int(x["y"]), ret=float(z[0]), st=states(C, x["d"], x["t"], 0, x["s"])))
    TB = pd.DataFrame(rows)
    log(f"===== Б) ДНЕВНЫЕ АНАЛОГИ 2014–2026: сделок {len(TB)} ({', '.join(f'{k} {v}' for k, v in TB.strat.value_counts().items())}); отбор 2014–19, проверка 2020–26 =====")
    RB = evaluate("дневные", TB, 2020)
    # ---------- А: 5-мин, правила бота ----------
    import exp29 as E29
    import exp30 as E30
    E, R, FE, Dd2, bars, cost = E29.load_all()
    F = E29.Filt(E, FE)
    rws = E30.build(E, R, F, Dd2, bars, cost)
    V = E30.variants(rws)
    cur = V[("нет", "как сейчас", "свой")]
    rows = [dict(strat=x["strat"], y=x["d"].year, ret=float(v[0]),
                 st=states(C, x["d"], x["t"], 1, -1 if x["strat"] == "F4" else 1, mkt=-x["mkt"] if x["strat"] == "F4" else None))
            for v, x in zip(cur, rws) if v is not None]
    TA = pd.DataFrame(rows)
    log(f"===== А) 5-МИН, ПРАВИЛА БОТА 2020–2026: сделок {len(TA)} ({', '.join(f'{k} {v}' for k, v in TA.strat.value_counts().items())}); отбор 2020–22, проверка 2023–26 =====")
    RA = evaluate("5-мин", TA, 2023)
    # ---------- подтверждение в обоих наборах ----------
    log("===== ФИЛЬТРЫ, ПОДТВЕРДИВШИЕСЯ И НА 5-МИН (2020–26), И НА ДНЕВНЫХ (2014–26) =====")
    both = []
    for k, a in RA.items():
        b = RB.get(k)
        if a["ok"] and b and b["ok"]:
            both.append(k)
            log(f"  ✔ {k[0]}: {k[2]} «{k[1]}» — 5-мин проверка {a['m_te']:+.2%} ({a['d_te']:+.2%} к всем, {a['n_te']} сд.), "
                f"дневные проверка {b['m_te']:+.2%} ({b['d_te']:+.2%}, {b['n_te']} сд.)")
    if not both:
        log("  нет ни одного фильтра, подтверждённого в обоих наборах")
    near = [(k, RA[k], RB[k]) for k in RA if k in RB and RA[k]["d_te"] > 0 and RB[k]["d_te"] > 0 and RA[k]["d_tr"] > 0 and RB[k]["d_tr"] > 0
            and RA[k]["share"] >= 0.3 and RB[k]["share"] >= 0.3]
    near.sort(key=lambda z: -(z[1]["d_te"] + z[2]["d_te"]))
    log("  в одну сторону во всех четырёх периодах (без порога 0,2 п.п.), лучшие: " + "; ".join(
        f"{k[0]} {k[2]} «{k[1]}» ({a['d_te']:+.2%} / {b['d_te']:+.2%})" for k, a, b in near[:8]))
    rep.update(both=[list(k) for k in both], near=[[list(k), a, b] for k, a, b in near[:15]])
    json.dump(rep, open(os.path.join(TR.MODELS, "exp37_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №37 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
