# -*- coding: utf-8 -*-
"""Проверка №42 (аудит, партия 3): смещение выживших в исследованиях.

Что исследования отбрасывали молча:
  1) состав бумаг: TR.universe() — текущий список TQBR; бумаги, ушедшие с биржи, попадают только через старый кэш/историю 2008–2014;
  2) сигнал, после которого меньше 20 торговых дней (exp31/exp15/exp12: j + ND >= len(D)) — торги прекратились ИЛИ просто конец данных;
  3) exp15.bad_path: за день закрытие > +80% / < −45%, бар max/min > 2, цена ушла в 3+ раза от входа (против сбоев и дроблений — но и настоящие обвалы);
  4) ход дня > 80%.
Здесь (дневные аналоги №31, текущие правила выхода без выбора, только ликвидные lval ≥ 7):
  • сколько сигналов отброшено каждым фильтром — по S1/F2 (рост) и F4 (падение), по годам;
  • класс отброшенного пути: сбой (цена вернулась за ≤ 3 дня), похоже на дробление (скачок ≈ 1/2, 1/3, 1/4, 1/5, 1/10 и цена не вернулась), обвал (остальное);
  • результат этих сделок по реальным данным (для «торги прекратились» — по последней цене, и худший вариант −100%);
  • портфель (те же правила, что в №40, одна позиция и «как в исследованиях»): без них и с ними;
  • 5-мин набор (события exp15.events_a, на которых построены №29/30/40/41): сколько отброшено bad_path, по сторонам и классам.
В начале — «версия данных» (число рядов, строк, последняя дата, хеш), чтобы прогоны можно было сравнивать.
Не является индивидуальной инвестиционной рекомендацией.
"""
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import core_daily as CD  # noqa: E402
import exp15 as E15  # noqa: E402
import exp31 as E31  # noqa: E402
import exp35 as E35  # noqa: E402
import exp40 as E40  # noqa: E402
import scan_core as SC  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
ND = E31.ND


def data_version(Dd, extra=""):
    """Отпечаток данных: число рядов, строк, последняя дата, хеш (тикер, число строк, последняя строка)."""
    h = hashlib.sha256()
    n = 0
    last = ""
    for t in sorted(Dd):
        v = Dd[t]
        n += len(v)
        if v:
            last = max(last, v[-1][0][:10])
            h.update(f"{t}|{len(v)}|{v[-1][0][:10]}|{v[-1][4]}".encode())
    h.update(extra.encode())
    return dict(series=len(Dd), rows=n, last=last, hash=h.hexdigest()[:12])


def classify(c0, nc):
    return E15.classify_path(c0, nc)


def daily_part(X, Dd, IX):
    S = X[(X.y_px == 1) & X.x.notna() & (X.d >= pd.Timestamp(TR.FROM))].copy()
    last_all = max(v[-1][0][:10] for v in Dd.values() if v)
    cut_end = (pd.Timestamp(last_all) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    stopped = {t for t, v in Dd.items() if v and v[-1][0][:10] < cut_end}
    log(f"состав: рядов {len(Dd)}, торги прекращены (последний бар раньше {cut_end}): {len(stopped)}; "
        f"из них с последним баром после 2015: {sum(1 for t in stopped if Dd[t][-1][0][:4] >= '2015')}")
    log("  прекращённые по году последнего бара: " + ", ".join(f"{y}: {n}" for y, n in sorted(pd.Series([Dd[t][-1][0][:4] for t in stopped]).value_counts().items())))
    ok, drop = [], []
    for t, g in S.groupby("t"):
        if t not in Dd:
            continue
        D = CD.series(Dd[t]).reset_index(drop=True)
        if len(D) < 80:
            continue
        pos = {d: i for i, d in enumerate(D.d)}
        o, h, l, c = (D[k].to_numpy(float) for k in ("o", "h", "l", "c"))
        A = SC.atr(h, l, c)
        ds = [d.strftime("%Y-%m-%d") for d in D.d]
        for r in g.itertuples():
            j = pos.get(r.d)
            if j is None or j < 60 or not c[j - 1] or not A[j] > 0:
                continue
            lv = float(r.lval) if r.lval == r.lval else 6.0
            if lv < 7:
                continue
            x = dict(t=t, d=r.d, y=r.d.year, s=1.0 if r.x > 0 else -1.0, c0=c[j], prev=c[j - 1], mv=abs(c[j] / c[j - 1] - 1), A=float(A[j]),
                     o=o[j + 1:j + 1 + ND], h=h[j + 1:j + 1 + ND], l=l[j + 1:j + 1 + ND], c=c[j + 1:j + 1 + ND], dts=ds[j + 1:j + 1 + ND], d0=ds[j],
                     cost=float(E15.cost_of(np.array([lv]))[0]))
            why = None
            if j + ND >= len(D):
                why = "торги прекратились" if t in stopped else "конец данных"
            elif E15.bad_path_strict(c[j], x["h"], x["l"], x["c"]):
                why = "bad_path:" + classify(c[j], x["c"])
            elif abs(c[j] / c[j - 1] - 1) > 0.8:
                why = "ход дня > 80%"
            x["why"] = why
            (drop if why else ok).append(x)
    log(f"ликвидных сигналов: принято {len(ok)}, отброшено {len(drop)}")
    return ok, drop, stopped


def outcome(x, strat):
    """Результат по текущим правилам; путь короче срока — выход по последней цене (и отдельно «худший» −100%)."""
    n = len(x["c"])
    if n == 0:
        return None
    xx = dict(x)
    hold = E31.OWN[strat]
    if n < hold:          # укороченный путь: дополняем последней ценой, чтобы trade() отработал
        pad = hold - n
        for k in ("o", "h", "l", "c"):
            xx[k] = np.r_[x[k], np.full(pad, x["c"][-1])]
        xx["dts"] = list(x["dts"]) + [x["dts"][-1]] * pad
    z = E31.trade(xx, strat, "нет", "как сейчас", "свой")
    if z is None:
        return None
    return dict(strat=strat, t=x["t"], d_in=z[2], d_out=xx["dts"][z[1]], ret=float(z[0]), e=None, why=x.get("why"), y=x["y"])


def trades_of(rows):
    out = []
    for x in rows:
        for strat in (("S1", "F2") if x["s"] > 0 else ("F4",)):
            z = outcome(x, strat)
            if z:
                out.append(z)
    return out


def five_min_part(X, Dd, Hh, IXH):
    """События 5-мин набора: сколько отбросил bad_path (по сторонам и классам)."""
    import gzip
    Ho = {}
    hdir = os.environ.get("EXP_H_DIR")
    if hdir and os.path.isdir(hdir):
        for f in os.listdir(hdir):
            if f.endswith(".json.gz"):
                with gzip.open(os.path.join(hdir, f), "rt") as fh:
                    Ho[f[:-8]] = json.load(fh)
    IXo = Ho.pop("IMOEX", [])
    H2 = {}
    for t in set(Hh) | set(Ho):
        new = Hh.get(t, [])
        cut = new[0][0] if new else "9999"
        H2[t] = [r for r in Ho.get(t, []) if r[0] < cut] + new
    IX2 = [r for r in IXo if r[0] < (IXH[0][0] if IXH else "9999")] + IXH
    import exp12 as E12
    E12.NDAYS = E15.ND
    E = E12.events(X, H2, IX2, Dd)
    E12.NDAYS = 5
    E = E[(E.d >= pd.Timestamp("2020-01-01"))].reset_index(drop=True)
    lv = E.get("lval")
    if lv is None:
        E = E.merge(X[["t", "d", "lval"]].drop_duplicates(["t", "d"]), on=["t", "d"], how="left")
    E = E[E.lval >= 7].reset_index(drop=True)
    bad = np.array([bool(E15.bad_path_strict(r.close, r.nh, r.nl, r.nc) or (len(r.fh) > 0 and (max(r.fh) / r.p0 > 2 or min(r.fl) / r.p0 < 0.5))) for r in E.itertuples()])
    B = E[bad]
    cls = [classify(r.close, r.nc) for r in B.itertuples()]
    out = dict(events=int(len(E)), dropped=int(bad.sum()))
    log(f"5-мин набор 2020–2026 (ликвидные события): {len(E)}, отброшено bad_path {int(bad.sum())} ({bad.mean():.1%})")
    for s, nm in ((1.0, "рост (S1/F2)"), (-1.0, "падение (F4)")):
        m = (B.s == s).values
        cc = pd.Series([c for c, k in zip(cls, m) if k]).value_counts().to_dict()
        tot = int((E.s == s).sum())
        log(f"  {nm}: отброшено {int(m.sum())} из {tot} ({m.sum() / max(1, tot):.1%}); классы: " + ", ".join(f"{k} {v}" for k, v in cc.items()))
        out[nm] = dict(n=int(m.sum()), total=tot, classes=cc)
    if len(B):
        log("  отброшенные: " + "; ".join(f"{r.t} {str(r.d)[:10]} {'↑' if r.s > 0 else '↓'} {c}" for r, c in zip(B.itertuples(), cls))[:3000])
    return out


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    rep = {}
    import exp9 as E9
    X, Dd, IX, Hh, IXH, ev, Wn = E9.load()
    rep["версия данных"] = data_version(Dd)
    log("ВЕРСИЯ ДАННЫХ:", rep["версия данных"])
    ok, drop, stopped = daily_part(X, Dd, IX)
    dd = pd.DataFrame([dict(why=x["why"], s=x["s"], y=x["y"], t=x["t"], d=str(x["d"])[:10]) for x in drop])
    log("===== Отброшенные дневные сигналы (ликвидные, 2008–2026) =====")
    for (why, s), g in dd.groupby(["why", "s"]):
        log(f"  {why:28s} {'рост' if s > 0 else 'падение'}: {len(g)}; по годам " + ", ".join(f"{y}: {n}" for y, n in g.y.value_counts().sort_index().items()))
    rep["отброшено"] = {f"{w}|{'рост' if s > 0 else 'падение'}": int(len(g)) for (w, s), g in dd.groupby(["why", "s"])}
    real = [x for x in drop if x["why"] != "конец данных" and not x["why"].endswith("сбой") and not x["why"].endswith("дробление?")]
    log("  примеры «торги прекратились» и «обвал»: " + "; ".join(f"{x['t']} {x['d0']} {'↑' if x['s'] > 0 else '↓'} {x['why']}" for x in real)[:3000])
    T_ok = trades_of(ok)
    T_real = trades_of(real)
    log("===== Результат отброшенных сделок (кроме сбоев, дроблений и конца данных), текущие правила =====")
    for strat in ("S1", "F2", "F4"):
        a = [z for z in T_ok if z["strat"] == strat]
        b = [z for z in T_real if z["strat"] == strat]
        ma = np.mean([z["ret"] for z in a]) if a else float("nan")
        if b:
            rb = np.array([z["ret"] for z in b])
            worst = [(-1.0 if z["why"] == "торги прекратились" else z["ret"]) for z in b]
            mix = np.mean([z["ret"] for z in a] + [z["ret"] for z in b])
            mixw = np.mean([z["ret"] for z in a] + worst)
            log(f"  {strat}: принятых {len(a)}, средняя {ma:+.2%}; отброшенных {len(b)}, средняя {rb.mean():+.2%} (медиана {np.median(rb):+.2%}, худшая {rb.min():+.1%}); "
                f"вместе {mix:+.2%} (сдвиг {mix - ma:+.2%}); если «торги прекратились» = −100%: {mixw:+.2%} (сдвиг {mixw - ma:+.2%})")
            rep[strat] = dict(n_ok=len(a), mean_ok=float(ma), n_drop=len(b), mean_drop=float(rb.mean()), mean_all=float(mix), mean_all_worst=float(mixw))
        else:
            log(f"  {strat}: принятых {len(a)}, средняя {ma:+.2%}; отброшенных реальных нет")
    # портфель
    CL = {}
    for t in Dd:
        D = CD.series(Dd[t])
        CL[t] = {d.strftime("%Y-%m-%d"): float(c) for d, c in zip(D.d, D.c)}
    days = sorted({r[0][:10] for r in IX if pd.Timestamp(r[0][:10]).weekday() < 5 and r[0][:10] >= "2011-01-01"})
    for z in T_ok + T_real:
        z["e"] = CL.get(z["t"], {}).get(z["d_in"])
    worst = [dict(z, ret=-1.0) if z["why"] == "торги прекратились" else z for z in T_real]
    sel = lambda L: [z for z in L if z["d_in"] >= "2011-01-01" and z["e"]]  # noqa: E731
    log("===== Портфель 2011–2026 (текущие правила выхода без выбора, доли 5/5/2,5%, потолок 150%) =====")
    for rule in ("V1", "R"):
        res = {}
        for name, L in (("без отброшенных", sel(T_ok)), ("с отброшенными", sel(T_ok + T_real)), ("с отброшенными, прекращённые = −100%", sel(T_ok + worst))):
            s, tk, sk, cut, kd = E40.port(L, CL, days, rule)
            m = E35.metr(s)
            res[name] = dict(cagr=m["cagr"], dd=m["dd"], worst=m["worst"], taken=tk)
            log(f"  {rule} {name}: {m['cagr']:+.2%} в год, просадка {m['dd']:.0%}, худший год {m['worst']:+.1%}, сделок {tk}")
        rep[f"портфель {rule}"] = res
    log(f"{round(time.time() - t0)} с")
    try:
        rep["5-мин"] = five_min_part(X, Dd, Hh, IXH)
    except Exception as e:  # noqa: BLE001
        log("5-мин часть не выполнена:", repr(e)[:300])
    json.dump(rep, open(os.path.join(TR.MODELS, "exp42_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №42 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
