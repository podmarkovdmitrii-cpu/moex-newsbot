# -*- coding: utf-8 -*-
"""Проверка №32: дыры в часовых и 5-минутных данных 2020–2026 (почему в 2025 г. в №29–30 всего ~21 сделка) и их починка.

1) Часовые свечи (кэш anomaly-data/h, их же берут сигналы внутри дня — трек A): по годам — сколько бумаг с данными, баров на бумагу,
   разрывы > 10 календарных дней (кроме закрытия биржи 25.02–23.03.2022).
2) Починка: бумаги с разрывами или без данных за последние годы перекачиваются с ISS заново с 2020-01-01 (кэш перезаписывается,
   если новых баров больше); затем — повторный счёт.
3) Сигналы трека A по годам до и после починки против дневных аномалий (y_px, оборот ≥ 10 млн).
4) 5-минутные бары ALGOPACK для сигналов 2023–2026: сколько сигналов имеют бары на день сигнала и все дни срока (до и после докачки).
Кэш anomaly-data и a18 сохраняется в конце (workflow), дальше им пользуются все проверки и бот.
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
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import train as TR  # noqa: E402

log = TR.log
CLOSED = ("2022-02-25", "2022-03-23")


def coverage(Hh, label):
    years = range(2020, 2027)
    stat = {y: [0, []] for y in years}
    gaps = {}
    for t, rows in Hh.items():
        ds = sorted(set(r[0][:10] for r in rows))
        for y in years:
            n = sum(1 for d in ds if d[:4] == str(y))
            if n:
                stat[y][0] += 1
                stat[y][1].append(n)
        g = []
        for a, b in zip(ds, ds[1:]):
            da, db = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
            if (db - da).days > 10 and not (a <= CLOSED[1] and b >= CLOSED[0] and (db - da).days < 40):
                g.append((a, b))
        last = ds[-1] if ds else ""
        if g or last < (dt.date.today() - dt.timedelta(days=10)).isoformat():
            gaps[t] = dict(gaps=g, last=last, first=ds[0] if ds else "")
    log(f"{label}: часовые ряды {len(Hh)}; по годам (бумаг / медиана дней с барами): "
        + ", ".join(f"{y}: {stat[y][0]}/{int(np.median(stat[y][1])) if stat[y][1] else 0}" for y in years))
    yrs_gap = {}
    for t, v in gaps.items():
        for a, b in v["gaps"]:
            for y in range(int(a[:4]), int(b[:4]) + 1):
                yrs_gap[y] = yrs_gap.get(y, 0) + 1
    log(f"{label}: бумаг с разрывами > 10 дн. или без свежих данных: {len(gaps)}; разрывы по годам: {dict(sorted(yrs_gap.items()))}")
    for t, v in list(gaps.items())[:12]:
        log(f"   {t}: первый {v['first']}, последний {v['last']}, разрывы {v['gaps'][:3]}")
    return gaps


def main():
    t0 = time.time()
    import exp9 as E9
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    gaps = coverage(Hh, "ДО")
    # починка
    fixed = 0
    for t, v in gaps.items():
        try:
            new = TR.fetch("h", t, "2020-01-01")
        except Exception as e:  # noqa: BLE001
            log("не скачано", t, e)
            continue
        old = Hh.get(t, [])
        if len(new) > len(old):
            p = os.path.join(TR.DATA, "h", t + ".json.gz")
            with gzip.open(p, "wt") as f:
                json.dump(new, f, separators=(",", ":"))
            Hh[t] = new
            fixed += 1
            log(f"   перекачано {t}: баров {len(old)} → {len(new)}")
    log(f"перекачано рядов: {fixed} из {len(gaps)}; {round(time.time() - t0)} с")
    coverage(Hh, "ПОСЛЕ")
    # сигналы трека A
    import exp15 as E15
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
    E = E15.events_a(X, H2, IX2, Dd)
    E = E[E.lval >= 7].reset_index(drop=True)
    D = X[(X.y_px == 1) & (X.lval >= 7)]
    log("сигналы внутри дня (трек A, ≥10 млн) / дневные аномалии (≥10 млн) по годам: "
        + ", ".join(f"{y}: {int((E.d.dt.year == y).sum())}/{int((D.d.dt.year == y).sum())}" for y in range(2018, 2027)))
    # 5-минутные бары
    import exp18 as E18
    m = E.d.dt.year >= 2023
    need, per = set(), []
    for _, r in E[m].iterrows():
        d0 = r.d.strftime("%Y-%m-%d")
        days = [x[0] for x in E18.trading_days(Dd, r.t, d0, 20)]
        keys = [(r.t, d0)] + [(r.t, d) for d in days]
        need.update(keys)
        per.append((r.d.year, keys))
    have = {k for k in need if os.path.exists(os.path.join(E18.A18, f"{k[0]}_{k[1]}.json.gz"))}

    def rep(H, label):
        out = {}
        for y, keys in per:
            o = out.setdefault(y, [0, 0, 0])
            o[0] += 1
            o[1] += keys[0] in H
            o[2] += sum(k in H for k in keys[1:6]) >= 4
        log(f"5-мин бары {label} (сигналов / есть день сигнала / есть ≥4 из 5 следующих дней): "
            + ", ".join(f"{y}: {v[0]}/{v[1]}/{v[2]}" for y, v in sorted(out.items())))
    rep(have, "ДО докачки (файлы кэша)")
    # пустые файлы в кэше (ALGOPACK когда-то вернул пусто — сбой, лимит) больше не перекачивались: удаляем их, чтобы скачать заново
    nonempty, removed = set(), 0
    for k in have:
        p = os.path.join(E18.A18, f"{k[0]}_{k[1]}.json.gz")
        try:
            with gzip.open(p, "rt") as f:
                ok = bool(json.load(f))
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            nonempty.add(k)
        else:
            os.remove(p)
            removed += 1
    rep(nonempty, f"ДО докачки (непустые файлы; пустых удалено {removed})")
    bars = E18.load_bars(need, (os.environ.get("ALGOPACK_TOKEN") or "").strip())
    got = {k for k, v in bars.items() if v}
    rep(got, "ПОСЛЕ докачки (непустые)")
    empty = {}
    for k in need - got:
        empty[k[1][:7]] = empty.get(k[1][:7], 0) + 1
    log("пустых бумаго-дней по месяцам (топ-15): " + ", ".join(f"{m_}: {n}" for m_, n in sorted(empty.items(), key=lambda z: -z[1])[:15]))
    log("===== ИТОГ ПРОВЕРКИ №32 =====", round(time.time() - t0), "с")


if __name__ == "__main__":
    main()
