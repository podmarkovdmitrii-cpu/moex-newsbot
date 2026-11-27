# -*- coding: utf-8 -*-
"""Проверка №43 (аудит, партия 3): бумаги, ушедшие с биржи (смещение выживших в составе).

Исследования брали состав из ТЕКУЩЕГО списка TQBR (TR.universe), поэтому компании, ушедшие с биржи в 2008–2026, в них почти отсутствуют (№42: 5 из 261).
Здесь:
  1) история ISS: все бумаги, когда-либо торговавшиеся в режиме TQBR (history/…/boards/TQBR/listing), с датами первой и последней торговли;
     ушедшие = нет в текущем списке или последняя торговля раньше чем 45 дней назад; по годам ухода;
  2) свечи по ушедшим (тот же загрузчик, что у исследований) — есть ли история, сколько из них ликвидны (оборот ≥ 10 млн ₽/день хотя бы год);
  3) дневной расчёт №42 (текущие правила выхода, ликвидные сигналы) на ПОЛНОМ составе: сделки по ушедшим бумагам отдельно — их число и результат;
     портфель 2011–2026 (одна позиция / как в исследованиях): «выжившие» → «+ ушедшие» → «+ обвалы» (№42) → «всё вместе»;
  4) устойчивость поправки: бутстрэп по годам и 300 подвыборок половины добавленных сделок (интервал, а не точка);
  5) версия данных — с датой снимка и отдельным хешем списка тикеров.
Ограничение: часовых/5-мин свечей у ушедших бумаг в кэше нет — 5-мин набор этим не проверяется.
Не является индивидуальной инвестиционной рекомендацией.
"""
import datetime as dt
import hashlib
import json
import os
import random
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import exp35 as E35  # noqa: E402
import exp40 as E40  # noqa: E402
import exp42 as E42  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
N_SUB = int(os.environ.get("N_SUB", "300"))


def tqbr_history():
    """Все бумаги, торговавшиеся в TQBR: SECID → (первая, последняя дата торгов)."""
    from common import iss, table
    out, start = {}, 0
    for _ in range(200):
        js = iss("history/engines/stock/markets/shares/boards/TQBR/listing.json", start=start)
        part = table(js, "securities")
        if not part:
            break
        for r in part:
            s = r.get("SECID")
            if s:
                out[s] = (str(r.get("history_from") or "")[:10], str(r.get("history_till") or "")[:10], r.get("SHORTNAME") or "")
        start += len(part)
    return out


def version(Dd):
    v = E42.data_version(Dd)
    v["tickers_hash"] = hashlib.sha256("|".join(sorted(Dd)).encode()).hexdigest()[:12]
    v["snapshot"] = dt.date.today().isoformat()
    return v


def liquid_years(rows):
    """Число календарных лет, где медианный оборот ≥ 10 млн ₽ (v — оборот в ₽ у свечей ISS)."""
    df = pd.DataFrame([(r[0][:4], r[5]) for r in rows], columns=["y", "v"])
    return int((df.groupby("y").v.median() >= 1e7).sum()) if len(df) else 0


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    rep = {}
    cur = TR.universe()
    H = tqbr_history()
    today = dt.date.today()
    cut = (today - dt.timedelta(days=45)).isoformat()
    import re
    import linkcheck as LC
    fund = re.compile(r"ETF|БПИФ|ПИФ|фонд", re.I)
    drre = re.compile(r"гдр|адр|\bДР\b|GDR|ADR", re.I)
    skipped_fund = sorted(s for s, (a, b, nm) in H.items() if fund.search(nm or ""))
    renamed = sorted(s for s in H if s in LC.RENAME)
    gone = sorted(s for s, (a, b, nm) in H.items() if b and "2008-01-01" <= b < cut and s not in LC.RENAME and not fund.search(nm or ""))
    dr = {s for s in gone if drre.search(H[s][2] or "")}
    log(f"история TQBR: бумаг {len(H)}, сейчас в списке {len(cur)}; фонды (не акции) {len(skipped_fund)}; переименованные (уже склеены) {len(renamed)}: {', '.join(renamed)}")
    log(f"ушедших (последние торги 2008…{cut}): {len(gone)}, из них депозитарные расписки {len(dr)} (часто редомициляция, а не потеря): {', '.join(sorted(dr))}")
    log("  ушедшие по году последних торгов: " + ", ".join(f"{y}: {n}" for y, n in sorted(pd.Series([H[s][1][:4] for s in gone]).value_counts().items())))
    rep["история"] = dict(all=len(H), current=len(cur), funds=len(skipped_fund), dr=sorted(dr), gone=len(gone), by_year=pd.Series([H[s][1][:4] for s in gone]).value_counts().sort_index().to_dict())
    # полный состав: подменяем universe; часовые — только по текущим (чтобы не менять признаки выживших)
    orig_u, orig_ls = TR.universe, TR.load_series
    TR.universe = lambda: list(cur) + [s for s in gone if s not in cur]

    n_h = int(os.environ.get("ANOM_HOURLY_N", "160"))
    os.environ["ANOM_HOURLY_N"] = str(n_h + len(gone))   # ушедшие могут занять места в топ-оборота — берём первые n_h текущих, как без них

    def ls(kind, tickers, start, index=()):
        if kind == "h":
            tickers = [t for t in tickers if t in cur and t not in index][:n_h] + [t for t in tickers if t in index]
        return orig_ls(kind, tickers, start, index=index)
    TR.load_series = ls
    import exp9 as E9
    X, Dd, IX, Hh, IXH, ev, Wn = E9.load()
    TR.universe, TR.load_series = orig_u, orig_ls
    os.environ["ANOM_HOURLY_N"] = str(n_h)
    rep["версия данных"] = version(Dd)
    log("ВЕРСИЯ ДАННЫХ:", rep["версия данных"])
    have = [s for s in gone if s in Dd and Dd[s]]
    liq = [s for s in have if liquid_years(Dd[s]) >= 1]
    log(f"ушедших со свечами: {len(have)} из {len(gone)}; из них ликвидных (≥ 1 года с медианным оборотом ≥ 10 млн ₽): {len(liq)}")
    log("  ликвидные ушедшие: " + ", ".join(f"{s} ({H[s][2]}, до {H[s][1]})" for s in liq)[:3000])
    rep["ушедшие"] = dict(with_bars=len(have), liquid=len(liq), liquid_list=liq)
    ok, drop, stopped = E42.daily_part(X, Dd, IX)
    gset = set(gone)
    real_drop = [x for x in drop if x["why"] not in ("конец данных",) and not x["why"].endswith("сбой") and not x["why"].endswith("дробление?")]
    T_ok = E42.trades_of(ok)
    T_drop = E42.trades_of(real_drop)
    g_ok = [z for z in T_ok if z["t"] in gset]
    s_ok = [z for z in T_ok if z["t"] not in gset]
    g_drop = [z for z in T_drop if z["t"] in gset]
    s_drop = [z for z in T_drop if z["t"] not in gset]
    log("===== Сделки по текущим правилам: выжившие против ушедших =====")
    for strat in ("S1", "F2", "F4"):
        a = [z["ret"] for z in s_ok if z["strat"] == strat]
        b = [z["ret"] for z in g_ok + g_drop if z["strat"] == strat]
        bn = [z["ret"] for z in g_ok + g_drop if z["strat"] == strat and z["t"] not in dr]
        c = [z["ret"] for z in s_ok + s_drop + g_ok + g_drop if z["strat"] == strat]
        log(f"  {strat}: выжившие {len(a)} сд., средняя {np.mean(a):+.2%}; по ушедшим {len(b)} сд." + (f", средняя {np.mean(b):+.2%}, медиана {np.median(b):+.2%}, худшая {min(b):+.1%}" if b else "")
            + (f" (без расписок {len(bn)} сд., средняя {np.mean(bn):+.2%})" if bn else "") + f"; всё вместе (с обвалами) {np.mean(c):+.2%} (сдвиг {np.mean(c) - np.mean(a):+.2%})")
        rep[strat] = dict(n_surv=len(a), mean_surv=float(np.mean(a)), n_gone=len(b), mean_gone=float(np.mean(b)) if b else None, mean_all=float(np.mean(c)))
    if g_ok + g_drop:
        gg = pd.DataFrame(g_ok + g_drop)
        log("  по ушедшим по годам (сделок / средняя): " + ", ".join(f"{y}: {len(x)}/{x.ret.mean():+.1%}" for y, x in gg.groupby(gg.d_in.str[:4])))
    CL = {}
    for t in Dd:
        D = CD.series(Dd[t])
        CL[t] = {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(D.d, D.c)}
    days = sorted({r[0][:10] for r in IX if pd.Timestamp(r[0][:10]).weekday() < 5 and r[0][:10] >= "2011-01-01"})
    for z in T_ok + T_drop:
        z["e"] = CL.get(z["t"], {}).get(z["d_in"])
    sel = lambda L: [z for z in L if z["d_in"] >= "2011-01-01" and z["e"]]  # noqa: E731
    nd = lambda L: [z for z in L if z["t"] not in dr]  # noqa: E731
    sets = {"выжившие (как в исследованиях)": s_ok, "+ ушедшие акции (без расписок)": s_ok + nd(g_ok), "+ ушедшие с расписками": s_ok + g_ok,
            "+ обвалы (№42)": s_ok + s_drop, "всё вместе": s_ok + s_drop + g_ok + g_drop, "всё вместе без расписок": s_ok + s_drop + nd(g_ok + g_drop)}
    log("===== Портфель 2011–2026 (текущие правила выхода, доли 5/5/2,5%, потолок 150%) =====")
    for rule in ("V1", "R"):
        res = {}
        for name, L in sets.items():
            s, tk, sk, cutn, kd = E40.port(sel(L), CL, days, rule)
            m = E35.metr(s)
            res[name] = dict(cagr=m["cagr"], dd=m["dd"], worst=m["worst"], taken=tk, years=m["years"])
            log(f"  {rule} {name}: {m['cagr']:+.2%} в год, просадка {m['dd']:.0%}, худший год {m['worst']:+.1%}, сделок {tk}")
        rep[f"портфель {rule}"] = res
    # устойчивость поправки (одна позиция)
    base = rep["портфель V1"]["выжившие (как в исследованиях)"]
    full = rep["портфель V1"]["всё вместе"]
    ys = sorted(base["years"])
    dif = np.array([full["years"].get(y, 0) - base["years"].get(y, 0) for y in ys])
    rng = np.random.default_rng(43)
    bs = np.array([dif[rng.integers(0, len(dif), len(dif))].mean() for _ in range(5000)])
    log(f"  поправка «всё вместе − выжившие», по годам: средняя {dif.mean():+.2%}/год, бутстрэп 90% [{np.percentile(bs, 5):+.2%}; {np.percentile(bs, 95):+.2%}]; "
        + ", ".join(f"{y}: {d:+.1%}" for y, d in zip(ys, dif)))
    added = [z for z in s_drop + g_ok + g_drop]
    random.seed(43)
    vals = []
    for _ in range(N_SUB):
        sub = random.sample(added, len(added) // 2) if len(added) > 1 else added
        s, *_ = E40.port(sel(s_ok + sub), CL, days, "V1")
        vals.append(E35.metr(s)["cagr"] - base["cagr"])
    vals = np.array(vals)
    log(f"  половина добавленных сделок ({len(added) // 2} из {len(added)}), {N_SUB} раз: поправка медиана {np.median(vals):+.2%}, 90% [{np.percentile(vals, 5):+.2%}; {np.percentile(vals, 95):+.2%}]"
        f" → на все добавленные ≈ ×2")
    rep["поправка"] = dict(by_years_mean=float(dif.mean()), lo90=float(np.percentile(bs, 5)), hi90=float(np.percentile(bs, 95)),
                           half_med=float(np.median(vals)), half_lo90=float(np.percentile(vals, 5)), half_hi90=float(np.percentile(vals, 95)))
    json.dump(rep, open(os.path.join(TR.MODELS, "exp43_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №43 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
