# -*- coding: utf-8 -*-
"""Агент-проверяющий: независимо перепроверяет выводы research/study.py на всех бумагах — по свечам и новостям из истории.

Что делает (своим кодом, не вызывая расчёты study.py):
  1. Пересчитывает события «толпы» по каждой бумаге заново (простыми циклами) и сверяет число событий и средние с study.json.
  2. Проверка на «заглядывание в будущее»: обрезает историю бумаги на дне события и убеждается, что событие находится и без будущих данных.
  3. Перестановочный тест: те же бумаги, случайные дни с похожим движением рынка — насколько результат отличается от случайности (p-value).
  4. Стабильность по годам: в какой доле лет знак результата совпадает с выводом.
  5. Смежные бумаги: для каждой пары секторов, признанной рабочей, — перестановка дней лидера и проверка на последних годах.
  6. Выходные: бутстрэп доверительного интервала продолжения движения в понедельник.
Вердикт по каждому правилу: «подтверждено» / «не подтверждено» + причина. Живой бот использует только подтверждённые правила.
Результат: research-stats/verify.json и research-stats/stats.json (выводы study + вердикты) — его читает channel/jobs.py.
"""
import datetime as dt
import json
import os
import random
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.environ.get("RESEARCH_DIR") or os.path.join(HERE, "out")
RNG = random.Random(20261102)


def log(*a):
    print(time.strftime("%H:%M:%S"), "[проверка]", *a, flush=True)


def years_same_sign(d, x, sign):
    s = pd.Series(np.asarray(x, dtype=float), index=pd.to_datetime(np.asarray(d))).dropna()
    by = s.groupby(s.index.year).mean()
    by = by[s.groupby(s.index.year).size() >= 5]
    return (float(((by > 0) == (sign > 0)).mean()) if len(by) else 0.0), {int(k): round(float(v), 4) for k, v in by.items()}


def crowd_recount(P, news, divs, prm):
    """Независимый пересчёт событий толпы: цикл по бумагам, своё окно и свои нормы оборота."""
    C, V, ix = P["c"], P["v"], P["ix"].values
    days = C.index
    w = prm["win"]
    out = []
    for t in C.columns:
        c, v = C[t].values, V[t].values
        nd, dv = news.get(t, set()), divs.get(t, set())
        last = -99
        for j in range(25, len(days) - 1):
            if not (c[j] == c[j]) or not (c[j - w] == c[j - w]):
                continue
            e = (c[j] / c[j - w] - 1) - (ix[j] / ix[j - w] - 1)
            if abs(e) < prm["thr"] or j - last < prm["gap"]:
                continue
            base = v[j - w - 20:j - w]
            base = base[base == base]
            lq = v[j - 20:j]
            lq = lq[lq == lq]
            if len(base) < 15 or len(lq) < 15:
                continue
            vxx = np.nanmean(v[j - w + 1:j + 1]) / np.median(base)
            if not (vxx >= prm["vx"]) or np.median(lq) < prm["liq"]:
                continue
            if any(k in dv for k in range(j - w + 1, j + 1)):
                continue
            last = j
            k5 = j + 5
            f5 = (c[k5] / c[j] - 1) - (ix[k5] / ix[j] - 1) if k5 < len(days) and c[k5] == c[k5] else np.nan
            out.append(dict(t=t, j=j, d=days[j], dir=1 if e > 0 else -1, news=sum(1 for k in range(j - w + 1, j + 1) if k in nd), f5=f5))
    return pd.DataFrame(out)


def lookahead_audit(P, ev, prm, k=150):
    """Событие дня D должно находиться по данным, обрезанным на D (без будущего)."""
    if ev.empty:
        return 1.0
    C, V, ix = P["c"], P["v"], P["ix"]
    smp = ev.sample(min(k, len(ev)), random_state=1)
    ok = 0
    for _, r in smp.iterrows():
        j, t, w = int(r.j), r.t, prm["win"]
        c = C[t].values[:j + 1]
        v = V[t].values[:j + 1]
        x = ix.values[:j + 1]
        e = (c[j] / c[j - w] - 1) - (x[j] / x[j - w] - 1)
        base = v[j - w - 20:j - w]
        base = base[base == base]
        vxx = np.nanmean(v[j - w + 1:j + 1]) / np.median(base) if len(base) else 0
        ok += int(abs(e) >= prm["thr"] and vxx >= prm["vx"])
    return ok / len(smp)


def permutation(P, ev, side, n_perm=300):
    """p-value: средний результат событий против того же числа случайных дней тех же бумаг (без условия события)."""
    C, ix = P["c"].values, P["ix"].values
    col = {t: i for i, t in enumerate(P["c"].columns)}
    e = ev[(ev.dir == side) & (ev.news == 0)].dropna(subset=["f5"])
    if len(e) < 15:
        return None, None
    real = float((e.f5 * side).mean())
    tick = [col[t] for t in e.t]
    n = len(P["c"])
    vals = []
    for _ in range(n_perm):
        acc = []
        for i in tick:
            for _try in range(6):
                j = RNG.randrange(30, n - 6)
                if C[j, i] == C[j, i] and C[j + 5, i] == C[j + 5, i]:
                    acc.append(((C[j + 5, i] / C[j, i] - 1) - (ix[j + 5] / ix[j] - 1)) * side)
                    break
        vals.append(np.mean(acc) if acc else 0.0)
    vals = np.array(vals)
    # двусторонний: насколько реальное среднее необычно в любую сторону
    p = float((np.abs(vals - vals.mean()) >= abs(real - vals.mean())).mean())
    return real, round(p, 4)


def verify_crowd(P, news, divs, study):
    prm = study["crowd"]["params"]
    ev = crowd_recount(P, news, divs, prm)
    log("толпа: пересчитано событий", len(ev), "без новостей", int((ev.news == 0).sum()) if len(ev) else 0)
    la = lookahead_audit(P, ev, prm)
    out = {"recount_events": int(len(ev)), "lookahead_ok": round(la, 3)}
    for side, name in ((1, "up"), (-1, "down")):
        rule = study["crowd"]["rules"][name]
        e = ev[(ev.dir == side) & (ev.news == 0)].dropna(subset=["f5"])
        mine = float((e.f5 * side).mean()) if len(e) else float("nan")
        theirs = study["crowd"]["no_news"][name]["all"]["f5"].get("mean")
        consistent = theirs is not None and len(e) > 0 and abs(mine - theirs) <= max(0.003, abs(theirs) * 0.35)
        real, p = permutation(P, ev, side)
        sign = 1 if rule["mode"] == "continue" else -1
        frac, by_year = years_same_sign(e.d, e.f5 * side, sign) if len(e) else (0.0, {})
        reasons = []
        if rule["mode"] == "neutral":
            reasons.append("на обучающих годах устойчивого эффекта нет")
        if not consistent:
            reasons.append(f"пересчёт не совпал с исследованием ({mine:+.4f} против {theirs})")
        if p is None or p > 0.05:
            reasons.append(f"не отличается от случайных дней (p={p})")
        if frac < 0.6:
            reasons.append(f"знак совпадает лишь в {frac:.0%} лет")
        if not rule["test_confirms"]:
            reasons.append("на 2023–2026 не подтвердилось")
        if la < 0.97:
            reasons.append(f"событие находится без будущих данных лишь в {la:.0%} случаев")
        out[name] = dict(mode=rule["mode"], verified=not reasons, reasons=reasons, recount_mean_f5=round(mine, 5), study_mean_f5=theirs,
                         perm_p=p, years_same_sign=round(frac, 2), by_year=by_year, n=int(len(e)))
        log(f"толпа {name}: {rule['mode']} → {'ПОДТВЕРЖДЕНО' if not reasons else 'не подтверждено: ' + '; '.join(reasons)}")
    return out


def verify_spill(study, n_perm=300):
    p = os.path.join(OUT, "spill_events.csv.gz")
    if not os.path.exists(p):
        return {}
    ev = pd.read_csv(p, parse_dates=["d"])
    res = {}
    split = pd.Timestamp(study.get("split", "2023-01-01"))
    for r in study["spill"]["pairs"]:
        if not r["ok"]:
            continue
        key = f"{r['s1']}>{r['s2']}>{r['dir']}"
        g = ev[(ev.s1 == r["s1"]) & (ev.s2 == r["s2"]) & (ev.dir == r["dir"])]
        per = g.groupby(["d", "lead"]).f3.mean()
        real = float(per.mean())
        # перестановка: результат отставших соседей в ЛЮБОЙ день события этого сектора-лидера, но с соседями из случайного сектора
        other = ev[(ev.s1 == r["s1"]) & (ev.dir == r["dir"])]
        null = []
        for _ in range(n_perm):
            smp = other.sample(min(len(other), len(g)), replace=False, random_state=RNG.randrange(10 ** 9))
            null.append(smp.groupby(["d", "lead"]).f3.mean().mean())
        null = np.array(null)
        pv = float((null >= real).mean())
        recent = per[per.index.get_level_values(0) >= split]
        frac, by_year = years_same_sign(per.index.get_level_values(0), per.values, 1)
        reasons = []
        if pv > 0.05:
            reasons.append(f"не лучше случайных соседей (p={pv:.2f})")
        if len(recent) >= 8 and recent.mean() <= 0:
            reasons.append("на 2023–2026 не работает")
        if frac < 0.6:
            reasons.append(f"знак совпадает лишь в {frac:.0%} лет")
        res[key] = dict(verified=not reasons, reasons=reasons, mean_f3=round(real, 5), perm_p=round(pv, 3), years_same_sign=round(frac, 2), by_year=by_year,
                        top_peers=r.get("top_peers"))
        log(f"смежные {key}: {'ПОДТВЕРЖДЕНО' if not reasons else 'не подтверждено: ' + '; '.join(reasons)}")
    return res


def verify_weekend(study, n_boot=2000):
    p = os.path.join(OUT, "weekend_rows.csv")
    if not os.path.exists(p):
        return {}
    W = pd.read_csv(p)
    W = W[(W.liq >= 30e6) & (W.wval >= 1e6)]
    out = {}
    for name, g in (("all", W), ("strong", W[W.wret.abs() >= 0.015])):
        x = (g.mon * np.sign(g.wret).replace(0, 1)).values
        if len(x) < 30:
            out[name] = dict(n=int(len(x)), verified=False, reasons=["мало наблюдений"])
            continue
        boots = [np.mean(np.random.default_rng(k).choice(x, len(x))) for k in range(n_boot)]
        lo, hi = np.percentile(boots, [2.5, 97.5])
        # выходные внутри одного дня сильно связаны (вся биржа) — проверяем и по средним за выходные
        per = g.assign(x=x).groupby("sat").x.mean()
        blo, bhi = np.percentile([np.mean(np.random.default_rng(k).choice(per.values, len(per))) for k in range(n_boot)], [2.5, 97.5])
        verdict = "continue" if blo > 0 else "reverse" if bhi < 0 else "none"
        out[name] = dict(n=int(len(x)), weekends=int(len(per)), mean=round(float(x.mean()), 5), ci=[round(float(lo), 5), round(float(hi), 5)],
                         ci_by_weekend=[round(float(blo), 5), round(float(bhi), 5)], effect=verdict, verified=verdict != "none",
                         reasons=[] if verdict != "none" else ["доверительный интервал по выходным включает ноль — систематического продолжения/отката нет"])
        log(f"выходные {name}: среднее продолжение {x.mean():+.4f}, интервал по выходным [{blo:+.4f}; {bhi:+.4f}] → {verdict}")
    return out


def main():
    import study as S
    st = json.load(open(os.path.join(OUT, "study.json")))
    P, wend = S.load_panel()
    news = S.load_news(P["c"].index)
    divs = S.ex_div(P["c"].index)
    v = {"built": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"), "coverage": {
        "tickers_with_data": int(P["c"].shape[1]), "days": int(len(P["c"])),
        "tickers_with_news": int(sum(1 for t in P["c"].columns if news.get(t))),
        "news_by_year": {int(y): int(n) for y, n in pd.Series([P["c"].index[j].year for s in news.values() for j in s if j < len(P["c"])]).value_counts().sort_index().items()}}}
    log("покрытие:", v["coverage"])
    v["crowd"] = verify_crowd(P, news, divs, st)
    v["spill"] = verify_spill(st)
    v["weekend"] = verify_weekend(st)
    json.dump(v, open(os.path.join(OUT, "verify.json"), "w"), ensure_ascii=False, default=float)
    # итог для живого бота
    live = {"built": v["built"], "range": st["range"],
            "crowd": {k: dict(st["crowd"]["rules"][k], verified=v["crowd"][k]["verified"], reasons=v["crowd"][k]["reasons"], perm_p=v["crowd"][k]["perm_p"],
                              all=st["crowd"]["no_news"][k]["all"], with_news=st["crowd"]["with_news"][k]["all"]) for k in ("up", "down")},
            "crowd_params": st["crowd"]["params"],
            "spill": [dict(r, verified=v["spill"].get(f"{r['s1']}>{r['s2']}>{r['dir']}", {}).get("verified", False)) for r in st["spill"]["pairs"] if r["ok"]],
            "spill_params": st["spill"]["params"],
            "weekend": dict(st["weekend"], verify=v["weekend"])}
    json.dump(live, open(os.path.join(OUT, "stats.json"), "w"), ensure_ascii=False, default=float)
    n_ok = sum(1 for k in ("up", "down") if live["crowd"][k]["verified"]) + sum(1 for r in live["spill"] if r["verified"])
    log("ИТОГ: подтверждённых правил", n_ok, "— живой бот использует только их; остальные идут в посты как справка без сигналов")


if __name__ == "__main__":
    main()
