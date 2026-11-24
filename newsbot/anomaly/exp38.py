# -*- coding: utf-8 -*-
"""Проверка №38: режимный фильтр F4 из №37 — устойчивость к порогам, «ИЛИ» / «И», пересечение, бутстрэп по дням.

Сделки F4 те же, что в №37: А) 5-мин 2020–2026 по правилам бота; Б) дневные аналоги 2014–2026.
Условия (значения на день до сигнала):
  • рубль: рост USD/RUB ЦБ за 20 торговых дней (28 календарных) > 2 / 3 / 4 / 5%; окно 10 и 40 дней при пороге 3%;
  • ставка: ключевая выросла за 60 / 90 / 120 дней (> 0,25 п.п.);
  • «рубль 3% ИЛИ ставка 90», «рубль 3% И ставка 90», «только рубль, без роста ставки», «только ставка, без ослабления рубля»;
  • плацебо: случайная метка с той же долей сделок (200 раз) — какой прирост бывает случайно.
Для каждого условия: сделок «в режиме» и «вне», средняя, медиана, доля прибыльных, сумма (прибыль в % на сделку × число),
90% бутстрэп-интервал разницы «в режиме − все» (по дням, 5000 повторов), по годам. Периоды как в №37 (подбор / проверка).
Не является индивидуальной инвестиционной рекомендацией.
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp37 as E37  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
RNG = np.random.default_rng(38)


def feats(C, d, lag):
    d = pd.Timestamp(d)
    f = {}
    for days, nm in ((14, "usd10"), (28, "usd20"), (56, "usd40")):
        a, b = E37.chg(C["usd"], d, days, lag)
        f[nm] = a / b - 1 if a == a and b == b and b else np.nan
    for days in (60, 90, 120):
        a, b = E37.chg(C["rate"], d, days, lag)
        f[f"rate{days}"] = a - b if a == a and b == b else np.nan
    return f


def conds(thr_rate):
    R = lambda days: (lambda f: f[f"rate{days}"] > thr_rate)  # noqa: E731
    U = lambda nm, p: (lambda f: f[nm] > p)  # noqa: E731
    return [
        ("рубль >2% за 20 дн.", U("usd20", 0.02)),
        ("рубль >3% за 20 дн. (как в №37)", U("usd20", 0.03)),
        ("рубль >4% за 20 дн.", U("usd20", 0.04)),
        ("рубль >5% за 20 дн.", U("usd20", 0.05)),
        ("рубль >3% за 10 дн.", U("usd10", 0.03)),
        ("рубль >3% за 40 дн.", U("usd40", 0.03)),
        ("ставка выросла за 60 дн.", R(60)),
        ("ставка выросла за 90 дн. (как в №37)", R(90)),
        ("ставка выросла за 120 дн.", R(120)),
        ("рубль 3% ИЛИ ставка 90", lambda f: U("usd20", 0.03)(f) or R(90)(f)),
        ("рубль 3% И ставка 90", lambda f: U("usd20", 0.03)(f) and R(90)(f)),
        ("только рубль 3% (ставка не росла)", lambda f: U("usd20", 0.03)(f) and not R(90)(f)),
        ("только ставка 90 (рубль не слабел)", lambda f: R(90)(f) and not U("usd20", 0.03)(f)),
        ("рубль 2% ИЛИ ставка 60", lambda f: U("usd20", 0.02)(f) or R(60)(f)),
        ("рубль 4% ИЛИ ставка 120", lambda f: U("usd20", 0.04)(f) or R(120)(f)),
    ]


def boot(g, m, n=5000):
    """90% интервал разницы средней «в режиме» и всех сделок; ресэмплинг дней."""
    days = g.day.values
    u, inv = np.unique(days, return_inverse=True)
    ret, mk = g.ret.values, m.astype(float)
    s_all = np.bincount(inv, ret, len(u))
    c_all = np.bincount(inv, None, len(u))
    s_in = np.bincount(inv, ret * mk, len(u))
    c_in = np.bincount(inv, mk, len(u))
    out = []
    for _ in range(n):
        w = np.bincount(RNG.integers(0, len(u), len(u)), minlength=len(u))
        ci = (w * c_in).sum()
        if ci == 0:
            continue
        out.append((w * s_in).sum() / ci - (w * s_all).sum() / (w * c_all).sum())
    return np.percentile(out, [5, 95]) if out else (np.nan, np.nan)


def placebo(g, share, n=200):
    """Прирост средней у случайной метки той же доли (по дням: метка на весь день, как у режимных фильтров)."""
    u = g.day.unique()
    base = g.ret.mean()
    res = []
    for _ in range(n):
        pick = set(RNG.choice(u, max(1, int(round(len(u) * share))), replace=False))
        m = g.day.isin(pick)
        if m.sum():
            res.append(g.ret[m].mean() - base)
    return np.percentile(res, [50, 95])


def run(name, T, split, CS):
    log(f"===== {name}: F4 сделок {len(T)}; подбор < {split}, проверка ≥ {split} =====")
    rep = {}
    for per, g in (("подбор", T[T.y < split]), ("проверка", T[T.y >= split]), ("всё", T)):
        g = g.reset_index(drop=True)
        log(f"  — {per}: все {len(g)} сд., средняя {g.ret.mean():+.2%}, медиана {g.ret.median():+.2%}, прибыльных {(g.ret > 0).mean():.0%}, "
            f"сумма {g.ret.sum():+.0%}")
        for nm, fn in CS:
            m = g.f.map(lambda f, fn=fn: bool(fn(f))).values
            a, b = g[m], g[~m]
            if len(a) < 3:
                log(f"     {nm}: {len(a)} сд. — мало")
                continue
            lo, hi = boot(g, m) if per != "всё" else (np.nan, np.nan)
            pl50, pl95 = placebo(g, m.mean()) if per == "проверка" else (np.nan, np.nan)
            rep[(per, nm)] = dict(n_in=int(len(a)), n_out=int(len(b)), m_in=float(a.ret.mean()), m_out=float(b.ret.mean()) if len(b) else None,
                                  d=float(a.ret.mean() - g.ret.mean()), lo=float(lo), hi=float(hi), sum_in=float(a.ret.sum()), sum_all=float(g.ret.sum()))
            extra = f", 90%: {lo:+.2%}…{hi:+.2%}" if lo == lo else ""
            extra += f", случайная метка той же доли: медиана {pl50:+.2%}, 95-й {pl95:+.2%}" if pl50 == pl50 else ""
            log(f"     {nm}: {len(a)} сд. ({m.mean():.0%}), средняя {a.ret.mean():+.2%} (к всем {a.ret.mean() - g.ret.mean():+.2%}{extra}), "
                f"медиана {a.ret.median():+.2%}, прибыльных {(a.ret > 0).mean():.0%}, сумма {a.ret.sum():+.0%} из {g.ret.sum():+.0%}; "
                f"вне: {len(b)} сд., {b.ret.mean() if len(b) else float('nan'):+.2%}")
    for nm in ("рубль >3% за 20 дн. (как в №37)", "ставка выросла за 90 дн. (как в №37)", "рубль 3% ИЛИ ставка 90"):
        fn = dict(CS)[nm]
        T["m"] = T.f.map(lambda f, fn=fn: bool(fn(f)))
        log(f"  по годам «{nm}» (в режиме сд./средняя | вне сд./средняя): " + "; ".join(
            f"{y}: {int(g.m.sum())}/{g[g.m].ret.mean() if g.m.any() else float('nan'):+.1%} | {int((~g.m).sum())}/{g[~g.m].ret.mean() if (~g.m).any() else float('nan'):+.1%}"
            for y, g in T.groupby("y")))
    return rep


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    import exp9 as E9
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    C = E37.make_ctx(ev, IX)
    thr = 0.25 if C["src"] == "cbr.ru" else 0.5
    CS = conds(thr)
    import exp31 as E31
    B, _ = E31.build(X, Dd, IX)
    rows = []
    for x in B.to_dict("records"):
        if x["y"] < 2014 or x["s"] > 0:
            continue
        z = E31.trade(x, "F4", "нет", "как сейчас", "свой")
        if z and z != "skip":
            rows.append(dict(y=int(x["y"]), day=str(pd.Timestamp(x["d"]).date()), ret=float(z[0]), f=feats(C, x["d"], 0)))
    TB = pd.DataFrame(rows)
    rep = {"rate_src": C["src"], "daily": {}, "5min": {}}
    rep["daily"] = {" | ".join(k): v for k, v in run("Б) ДНЕВНЫЕ 2014–2026", TB, 2020, CS).items()}
    import exp29 as E29
    import exp30 as E30
    E, R, FE, Dd2, bars, cost = E29.load_all()
    F = E29.Filt(E, FE)
    rws = E30.build(E, R, F, Dd2, bars, cost)
    cur = E30.variants(rws)[("нет", "как сейчас", "свой")]
    rows = [dict(y=x["d"].year, day=str(pd.Timestamp(x["d"]).date()), ret=float(v[0]), f=feats(C, x["d"], 1))
            for v, x in zip(cur, rws) if v is not None and x["strat"] == "F4"]
    TA = pd.DataFrame(rows)
    rep["5min"] = {" | ".join(k): v for k, v in run("А) 5-МИН 2020–2026, ПРАВИЛА БОТА", TA, 2023, CS).items()}
    last = pd.Timestamp.today().normalize() + pd.Timedelta(days=1)
    now = feats(C, last, 1)
    log("===== СЕЙЧАС: " + ", ".join(f"{k} {v:+.2%}" if k.startswith("usd") else f"{k} {v:+.2f} п.п." for k, v in now.items()) + " =====")
    rep["now"] = now
    json.dump(rep, open(os.path.join(TR.MODELS, "exp38_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №38 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
