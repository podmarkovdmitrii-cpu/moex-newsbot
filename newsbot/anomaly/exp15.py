# -*- coding: utf-8 -*-
"""Проверка №15: полный перебор отработки ценовых аномалий — вход, тейк, стоп, срок удержания; история с 2008 г.

ДВА ПОТОКА СИГНАЛОВ
  A. Внутри дня (часовые свечи с 17.11.2011): первый час 10…17, когда |ход к индексу от вчерашнего закрытия| ≥ порога эшелона
     (4/6/8% или 3σ). Путь цены: часы того же дня + 20 следующих торговых дней.
  B. По итогам дня (дневные свечи с 2008 г.): день, когда |изменение к индексу| ≥ порога (метка модели y_px), известно на закрытии.
     Путь: 20 следующих торговых дней.
СЕТКА (на каждую сделку все варианты)
  сторона:  по движению / против движения;
  вход A:   сразу; лимит на «лучшей» цене 10/25/38/50/62/75% хода (по движению — откат, против — продолжение) в тот же день;
            закрытие дня сигнала; открытие следующего дня;
  вход B:   закрытие дня сигнала; открытие следующего дня; лимит 25/50% хода на следующий день;
  тейк:     нет; 10/25/50/75/100/150/200% хода; фикс. 1/2/3/5/8%;
  стоп:     нет; 25/50/75/100/150% хода; фикс. 2/3/5/8/10%;
  срок:     A — тот же день, 1, 2, 3, 5, 7, 10, 15, 20 дней; B — 1, 2, 3, 5, 7, 10, 15, 20 дней;
  фильтры:  направление аномалии (рост/падение) × оборот (все / ≥10 млн ₽ / ≥100 млн ₽ в день).
Порядок внутри бара неизвестен: тейк и стоп в одном баре — считаем стоп; в баре исполнения лимита тейк/стоп не проверяем.
Гэп через стоп: если бар открылся уже за стопом, выход по цене открытия (а не по уровню стопа).
Издержки за сделку (комиссия+спред): оборот ≥100 млн — 0,1%; 10–100 млн — 0,2%; <10 млн — 0,5% (неликвид, к тому же цены сигналов там
часто нерыночные — см. №13).
ВЫБОР БЕЗ ПОДГОНКИ: для каждого года 2016–2026 берём лучший вариант по прошлым годам (оценка = среднее/разброс×√сделок, ≥100 сделок)
и смотрим его результат в этом году — склеенный результат и есть честная оценка «лучшего способа».
Плюс: лучшие варианты на всей истории с устойчивостью (лет в плюсе, соседние варианты), разрезы «тейк × стоп», «срок», только покупки,
проверка лучших ликвидных вариантов на 5-минутных свечах ALGOPACK (кэш a5 из №14, если есть).
"""
import datetime as dt
import gzip
import itertools
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
import exp9 as E9  # noqa: E402
import exp12 as E12  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
ND = 20
KH = E12.KH
HOLD_A = (0, 1, 2, 3, 5, 7, 10, 15, 20)
HOLD_B = (1, 2, 3, 5, 7, 10, 15, 20)
TPS = [None] + [("m", k) for k in (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)] + [("f", k) for k in (0.01, 0.02, 0.03, 0.05, 0.08)]
SLS = [None] + [("m", k) for k in (0.25, 0.5, 0.75, 1.0, 1.5)] + [("f", k) for k in (0.02, 0.03, 0.05, 0.08, 0.1)]
ENT_A = ["sig", 0.1, 0.25, 0.382, 0.5, 0.618, 0.75, "close0", "open1"]
ENT_B = ["close0", "open1", 0.25, 0.5]
WF_FROM = int(os.environ.get("EXP_WF_FROM", "2016"))
MIN_TR = int(os.environ.get("EXP_MIN_TRADES", "100"))


BADPATH_MODE = os.environ.get("EXP_BADPATH", "real")   # «real» (с 27.11.2026, аудит №42/45) — отбрасываем только сбои и дробления; «strict» — как раньше
ROUND_SPLITS = (2, 3, 4, 5, 10)


def classify_path(c0, nc):
    """Класс пути с подозрительным скачком: «сбой» (цена вернулась за ≤ 3 дня) / «дробление?» (круглый коэффициент, не вернулась) /
    «обвал» / «скачок вверх» / «внутридневной скачок» (закрытия спокойные, бар max/min > 2) / «нет цен»."""
    c = np.r_[c0, np.asarray(nc, float)]
    c = c[np.isfinite(c) & (c > 0)]
    if len(c) < 2:
        return "нет цен"
    r = c[1:] / c[:-1]
    k = int(np.argmax(np.abs(np.log(r))))
    jump = r[k]
    after = c[k + 1:k + 4]
    if len(after) >= 2 and (jump < 0.55 and after[1:].max() > c[k] * 0.8 or jump > 1.8 and after[1:].min() < c[k] * 1.25):
        return "сбой"
    q = 1 / jump if jump < 1 else jump
    n = round(q)
    if n in ROUND_SPLITS and abs(q - n) / n <= 0.05:
        return "дробление?"
    if c.min() / c[0] < 0.7 or jump < 0.55:
        return "обвал"
    if c.max() / c[0] > 1.3 or jump > 1.8:
        return "скачок вверх"
    return "внутридневной скачок"


def bad_path(c0, nh, nl, nc):
    """Путь, который нельзя считать. По умолчанию (EXP_BADPATH=real) — только сбой данных (цена вернулась за ≤ 3 дня), похоже на дробление
    без поправки или нет цен; настоящие обвалы и скачки ОСТАЮТСЯ в расчёте (аудит №42: среди отброшенных старым фильтром сбоев не было).
    EXP_BADPATH=strict — старое правило (bad_path_strict) для воспроизведения проверок до №42."""
    if not bad_path_strict(c0, nh, nl, nc):
        return False
    if BADPATH_MODE == "strict":
        return True
    return classify_path(c0, nc) in ("сбой", "дробление?", "нет цен")


def bad_path_strict(c0, nh, nl, nc):
    """Старое правило (до 27.11.2026): скачок закрытия за день > +80% / −45%, бар с max/min > 2, или цена ушла в 3+ раза от входа."""
    c = np.r_[c0, np.asarray(nc, float)]
    c = c[np.isfinite(c) & (c > 0)]
    if len(c) < 2:
        return True
    r = c[1:] / c[:-1]
    h, l = np.asarray(nh, float), np.asarray(nl, float)
    ok = np.isfinite(h) & np.isfinite(l) & (l > 0)
    return bool((r > 1.8).any() or (r < 0.55).any() or (ok.any() and (h[ok] / l[ok] > 2).any())
                or c.max() / c[0] > 3 or c.min() / c[0] < 1 / 3)


def cost_of(lval):
    return np.where(lval >= 8, 0.001, np.where(lval >= 7, 0.002, 0.005))


# ------------------------------------------------------------------ данные
def events_a(X, H2, IX2, Dd):
    E12.NDAYS = ND
    E = E12.events(X, H2, IX2, Dd)
    E = E[E.d >= pd.Timestamp("2011-11-18")].reset_index(drop=True)
    E12.NDAYS = 5
    bad = np.array([bool(bad_path(r.close, r.nh, r.nl, r.nc) or (len(r.fh) > 0 and (max(r.fh) / r.p0 > 2 or min(r.fl) / r.p0 < 0.5)))
                    for r in E.itertuples()])
    log(f"A: отброшено сигналов со сбоем данных (скачки после дроблений и т. п.): {int(bad.sum())} из {len(E)}")
    return E[~bad].reset_index(drop=True)


def paths_a(E):
    n, K = len(E), KH + ND
    Hm, Lm, Cm, Om = (np.full((n, K), np.nan) for _ in range(4))
    O1 = np.full(n, np.nan)
    for i, r in enumerate(E.itertuples()):
        k = min(len(r.fh), KH)
        Hm[i, :k], Lm[i, :k], Cm[i, :k] = r.fh[:k], r.fl[:k], r.fc[:k]
        m = min(len(r.nh), ND)
        Hm[i, KH:KH + m], Lm[i, KH:KH + m], Cm[i, KH:KH + m] = r.nh[:m], r.nl[:m], r.nc[:m]
        O1[i] = r.no[0] if len(r.no) else np.nan
        Om[i, KH:KH + min(len(r.no), ND)] = np.asarray(r.no, float)[:ND]
    Om[:, 1:KH] = Cm[:, :KH - 1]  # внутри дня открытие часа ≈ закрытие предыдущего часа
    day = np.r_[np.zeros(KH, int), np.arange(1, ND + 1)]
    return Hm, Lm, Cm, day, O1, Om


def events_b(X, Dd):
    """Дневные сигналы: y_px = 1 (известно на закрытии дня), путь — 20 следующих дней."""
    S = X[(X.y_px == 1) & X.x.notna()][["t", "d", "x", "tier", "sd60", "lval"]]
    rows = []
    for t, g in S.groupby("t"):
        if t not in Dd:
            continue
        D = CD.series(Dd[t]).reset_index(drop=True)
        pos = {d: i for i, d in enumerate(D.d)}
        o, h, l, c = (D[k].to_numpy(float) for k in ("o", "h", "l", "c"))
        for r in g.itertuples():
            j = pos.get(r.d)
            if j is None or j < 1 or j + ND >= len(D) or not c[j - 1]:
                continue
            rows.append(dict(t=t, d=r.d, s=1.0 if r.x > 0 else -1.0, c0=c[j], prev=c[j - 1], mv=c[j] / c[j - 1] - 1, tier=int(r.tier),
                             lval=float(r.lval) if r.lval == r.lval else 6.0, no=o[j + 1:j + 1 + ND], nh=h[j + 1:j + 1 + ND],
                             nl=l[j + 1:j + 1 + ND], nc=c[j + 1:j + 1 + ND]))
    B = pd.DataFrame(rows)
    bad = np.array([bad_path(r.c0, r.nh, r.nl, r.nc) or abs(r.mv) > 0.8 for r in B.itertuples()])
    log(f"B: отброшено сигналов со сбоем данных: {int(bad.sum())} из {len(B)}")
    B = B[~bad].reset_index(drop=True)
    log("дневных сигналов B:", len(B), "| по годам", B.d.dt.year.value_counts().sort_index().to_dict())
    return B


# ------------------------------------------------------------------ симуляция
def first_true(M):
    a = M.any(axis=1)
    return np.where(a, M.argmax(axis=1), 10 ** 6)


def levels(e, s, m, tp, sl):
    tpl = np.full(len(e), np.nan) if tp is None else e * (1 + s * (tp[1] * m if tp[0] == "m" else tp[1]))
    sll = np.full(len(e), np.nan) if sl is None else e * (1 - s * (sl[1] * m if sl[0] == "m" else sl[1]))
    return tpl, sll


def run_path(Hm, Lm, lastc, e, j0, last, s, tpl, sll, cost, Om=None):
    K = Hm.shape[1]
    idx = np.arange(K)[None, :]
    act = (idx >= j0[:, None]) & (idx <= last)
    up = s[:, None] > 0
    tp_hit = np.where(up, Hm >= tpl[:, None], Lm <= tpl[:, None]) & act & np.isfinite(tpl)[:, None]
    sl_hit = np.where(up, Lm <= sll[:, None], Hm >= sll[:, None]) & act & np.isfinite(sll)[:, None]
    jt, js = first_true(tp_hit), first_true(sl_hit)
    sx = sll
    if Om is not None:  # гэп через стоп: бар открылся уже за стопом — выход по цене открытия, а не по стопу
        oj = Om[np.arange(len(e)), np.minimum(js, K - 1)]
        sx = np.where(np.isfinite(oj), np.where(s > 0, np.minimum(sll, oj), np.maximum(sll, oj)), sll)
    xp = np.where((js < 10 ** 6) & (js <= jt), sx, np.where(jt < 10 ** 6, tpl, lastc))
    r = s * (xp / e - 1) - cost
    return np.where(np.isfinite(e) & np.isfinite(xp), r, np.nan)


def sim_a(E, P, side, ent, tp, sl, hold, cache):
    Hm, Lm, Cm, day, O1, Om = P
    n, K = Hm.shape
    s = E.s.values * (1 if side == "follow" else -1)
    m = np.abs(E.mv.values)
    p0 = E.p0.values
    if ent == "sig":
        e, j0 = p0.copy(), np.zeros(n, int)
    elif ent == "close0":
        if hold == 0:
            return None
        e, j0 = E.close.values.copy(), np.full(n, KH)
    elif ent == "open1":
        if hold == 0:
            return None
        e, j0 = O1.copy(), np.full(n, KH)
    else:
        key = ("lim", side, ent)
        if key not in cache:
            ee = p0 - s * ent * np.abs(p0 - E.prev.values)
            same = (day == 0)[None, :]
            jf = np.where(s > 0, first_true((Lm <= ee[:, None]) & same), first_true((Hm >= ee[:, None]) & same))
            ok = jf < K
            cache[key] = (np.where(ok, ee, np.nan), np.where(ok, jf + 1, 10 ** 6))
        e, j0 = cache[key]
    last = int(np.where(day <= hold)[0].max())
    lk = ("last", hold)
    if lk not in cache:
        cache[lk] = E.close.values if hold == 0 else pd.DataFrame(Cm[:, : last + 1]).ffill(axis=1).iloc[:, -1].values
    tpl, sll = levels(e, s, m, tp, sl)
    return run_path(Hm, Lm, cache[lk], e, j0, last, s, tpl, sll, cache["cost"], Om)


def sim_b(B, P, side, ent, tp, sl, hold, cache):
    Hm, Lm, Cm, O1, Om = P
    n, K = Hm.shape
    s = B.s.values * (1 if side == "follow" else -1)
    m = np.abs(B.mv.values)
    if ent == "close0":
        e, j0 = B.c0.values.copy(), np.zeros(n, int)
    elif ent == "open1":
        e, j0 = O1.copy(), np.zeros(n, int)
    else:
        key = ("lim", side, ent)
        if key not in cache:
            ee = B.c0.values - s * ent * np.abs(B.c0.values - B.prev.values)
            ok = np.where(s > 0, Lm[:, 0] <= ee, Hm[:, 0] >= ee)
            cache[key] = (np.where(ok, ee, np.nan), np.where(ok, 1, 10 ** 6))
        e, j0 = cache[key]
    last = hold - 1
    tpl, sll = levels(e, s, m, tp, sl)
    return run_path(Hm, Lm, Cm[:, last], e, j0, last, s, tpl, sll, cache["cost"], Om)


def paths_b(B):
    st = lambda k: np.vstack([np.r_[np.asarray(v, float), np.full(ND - len(v), np.nan)] for v in B[k]])
    return st("nh"), st("nl"), st("nc"), np.array([v[0] if len(v) else np.nan for v in B["no"]]), st("no")


# ------------------------------------------------------------------ агрегаты и выбор
def aggregate(r, masks, yidx, ny):
    """→ массив [фильтр, год, (n, сумма, сумма квадратов, сумма плюсов, сумма минусов)]."""
    f = np.isfinite(r)
    rr = np.where(f, r, 0.0)
    out = np.zeros((len(masks), ny, 5))
    for k, mk in enumerate(masks):
        w = mk & f
        out[k, :, 0] = np.bincount(yidx[w], minlength=ny)
        out[k, :, 1] = np.bincount(yidx[w], weights=rr[w], minlength=ny)
        out[k, :, 2] = np.bincount(yidx[w], weights=rr[w] ** 2, minlength=ny)
        out[k, :, 3] = np.bincount(yidx[w], weights=np.maximum(rr[w], 0), minlength=ny)
        out[k, :, 4] = np.bincount(yidx[w], weights=np.minimum(rr[w], 0), minlength=ny)
    return out


def stat(a):
    """a — [год, 5] или [5] (сумма по годам) → метрики."""
    a = a.sum(axis=0) if a.ndim == 2 else a
    n = a[0]
    if n < 2:
        return dict(n=int(n))
    mean = a[1] / n
    var = max(a[2] / n - mean ** 2, 1e-12)
    return dict(n=int(n), mean=mean, sd=var ** 0.5, score=mean / var ** 0.5 * n ** 0.5, pf=a[3] / max(1e-12, -a[4]), sum=a[1])


def walk_forward(AGG, names, years, fk, label):
    """Для каждого года — лучший вариант по прошлым годам; результат этого варианта в этом году."""
    res, rows = [], []
    for yi, Y in enumerate(years):
        if Y < WF_FROM:
            continue
        past = AGG[:, fk, :yi, :].sum(axis=1)          # [вариант, 5]
        n = past[:, 0]
        mean = past[:, 1] / np.maximum(n, 1)
        var = np.maximum(past[:, 2] / np.maximum(n, 1) - mean ** 2, 1e-12)
        sc = np.where(n >= MIN_TR, mean / np.sqrt(var) * np.sqrt(n), -1e9)
        b = int(np.argmax(sc))
        cur = AGG[b, fk, yi, :]
        rows.append(dict(year=int(Y), pick=names[b], train_mean=float(mean[b]), n=int(cur[0]), mean=float(cur[1] / max(cur[0], 1)),
                         cap=float(cur[1] * 0.1)))
        res.append(cur)
    if not res:
        return {}
    tot = np.sum(res, axis=0)
    s = stat(tot)
    log(f"===== БЕЗ ПОДГОНКИ ({label}): каждый год — лучший вариант по прошлым годам =====")
    for x in rows:
        log(f"  {x['year']}: {x['pick']} | в прошлом {x['train_mean']:+.2%}/сд → в этом году {x['n']} сд, {x['mean']:+.2%}/сд, капитал {x['cap']:+.1%}")
    if s.get("n", 0) > 1:
        yrs_pos = sum(1 for x in rows if x["cap"] > 0)
        log(f"  ИТОГ {WF_FROM}–{years[-1]}: {s['n']} сделок, {s['mean']:+.2%}/сделку, PF {s['pf']:.2f}, капитал {s['sum'] * 0.1 / len(rows):+.1%}/год, "
            f"лет в плюсе {yrs_pos} из {len(rows)}")
    return dict(rows=rows, total=dict(n=s.get("n"), mean=s.get("mean"), pf=s.get("pf"), cap_year=(s.get("sum", 0) * 0.1 / len(rows)) if rows else None))


def name_of(side, ent, tp, sl, hold, B=False):
    en = {"sig": "сразу", "close0": "закрытие дня сигнала", "open1": "открытие след. дня"}.get(ent) if isinstance(ent, str) else (
        f"лимит {ent:.0%} хода" + (" на след. день" if B else ""))
    tpn = "без тейка" if tp is None else (f"тейк {tp[1]:.0%} хода" if tp[0] == "m" else f"тейк {tp[1]:.0%}")
    sln = "без стопа" if sl is None else (f"стоп {sl[1]:.0%} хода" if sl[0] == "m" else f"стоп {sl[1]:.0%}")
    hn = "до закрытия дня" if hold == 0 else f"держать до {hold} дн."
    return f"{'ПО движению' if side == 'follow' else 'ПРОТИВ'}; вход {en}; {tpn}; {sln}; {hn}"


def full_report(AGG, cfgs, names, years, fnames, label, top=12):
    out = {}
    tot = AGG.sum(axis=2)                                   # [вариант, фильтр, 5]
    for fk, fn in enumerate(fnames):
        n = tot[:, fk, 0]
        mean = tot[:, fk, 1] / np.maximum(n, 1)
        var = np.maximum(tot[:, fk, 2] / np.maximum(n, 1) - mean ** 2, 1e-12)
        sc = np.where(n >= MIN_TR * 3, mean / np.sqrt(var) * np.sqrt(n), -1e9)
        order = np.argsort(-sc)[:top]
        log(f"===== {label} | {fn}: лучшие на всей истории (оценка среднее/разброс×√n; это «в выборке», см. проверку без подгонки) =====")
        rows = []
        for b in order:
            if sc[b] < -1e8:
                break
            yr = AGG[b, fk, :, :]
            ycap = yr[:, 1] * 0.1
            act = yr[:, 0] > 0
            pos = int(((ycap > 0) & act).sum())
            pf = tot[b, fk, 3] / max(1e-12, -tot[b, fk, 4])
            log(f"  {names[b]}\n     сделок {int(n[b])}, {mean[b]:+.2%}/сд, PF {pf:.2f}, лет в плюсе {pos} из {int(act.sum())}; "
                + ", ".join(f"{years[i]}: {ycap[i]:+.0%}" for i in range(len(years)) if act[i]))
            rows.append(dict(name=names[b], n=int(n[b]), mean=float(mean[b]), pf=float(pf), years_pos=pos, years=int(act.sum())))
        out[fn] = rows
    return out


def slices(AGG, cfgs, names, fnames, fk, label, holds):
    """Разрезы для лучшей стороны/входа: среднее на сделку по сетке тейк × стоп и по сроку удержания."""
    tot = AGG[:, fk, :, :].sum(axis=1)
    n = tot[:, 0]
    mean = tot[:, 1] / np.maximum(n, 1)
    var = np.maximum(tot[:, 2] / np.maximum(n, 1) - mean ** 2, 1e-12)
    sc = np.where(n >= MIN_TR * 3, mean / np.sqrt(var) * np.sqrt(n), -1e9)
    b = int(np.argmax(sc))
    if sc[b] < -1e8:
        log(f"===== РАЗРЕЗЫ {label} | {fnames[fk]}: мало сделок =====")
        return {}
    side, ent, tp0, sl0, h0 = cfgs[b]
    log(f"===== РАЗРЕЗЫ {label} | {fnames[fk]} — вокруг лучшего: {names[b]} =====")
    idx = {c: i for i, c in enumerate(cfgs)}
    tpn = lambda tp: "нет" if tp is None else (f"{tp[1]:.0%}х" if tp[0] == "m" else f"{tp[1]:.0%}")
    log("  тейк ×  стоп (среднее на сделку, срок как у лучшего): стоп → " + " | ".join(tpn(sl) for sl in SLS))
    grid = {}
    for tp in TPS:
        cells = []
        for sl in SLS:
            i = idx.get((side, ent, tp, sl, h0))
            cells.append(f"{mean[i]:+.2%}" if i is not None and n[i] >= 30 else "  —  ")
            if i is not None:
                grid[f"{tpn(tp)}|{tpn(sl)}"] = float(mean[i])
        log(f"  тейк {tpn(tp):>5}: " + " ".join(cells))
    hs = []
    for h in holds:
        i = idx.get((side, ent, tp0, sl0, h))
        if i is not None and n[i] >= 30:
            hs.append(f"{h} дн.: {mean[i]:+.2%} (n={int(n[i])})")
    log("  срок удержания (тейк и стоп как у лучшего): " + "; ".join(hs))
    return dict(best=names[b], grid=grid)


def main():
    t0 = time.time()
    os.makedirs(TR.MODELS, exist_ok=True)
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"))
    X, Dd, IX, Hh, IXH, ev, W = E9.load()
    # ---------------- B: дневные сигналы с 2008 г.
    B = events_b(X[X.d >= pd.Timestamp(TR.FROM)], Dd)
    PB = paths_b(B)
    yrB = B.d.dt.year.values
    yearsB = sorted(set(yrB))
    yidxB = np.searchsorted(yearsB, yrB)
    lvB = B.lval.values
    fnames = ["рост, все", "рост, ≥10 млн", "рост, ≥100 млн", "падение, все", "падение, ≥10 млн", "падение, ≥100 млн"]
    upB, dnB = B.s.values > 0, B.s.values < 0
    masksB = [upB, upB & (lvB >= 7), upB & (lvB >= 8), dnB, dnB & (lvB >= 7), dnB & (lvB >= 8)]
    cfgB = [c for c in itertools.product(("follow", "fade"), ENT_B, TPS, SLS, HOLD_B)]
    cache = {"cost": cost_of(lvB)}
    AGGB = np.zeros((len(cfgB), len(masksB), len(yearsB), 5))
    for k, c in enumerate(cfgB):
        r = sim_b(B, PB, *c, cache)
        AGGB[k] = aggregate(r, masksB, yidxB, len(yearsB))
        if k % 2000 == 0:
            log(f"B: {k}/{len(cfgB)} вариантов, {round(time.time() - t0)} с")
    namesB = [name_of(*c, B=True) for c in cfgB]
    out["B_full"] = full_report(AGGB, cfgB, namesB, yearsB, fnames, "B ДНЕВНЫЕ 2008–2026")
    out["B_wf"] = {fn: walk_forward(AGGB, namesB, yearsB, fk, f"B дневные, {fn}") for fk, fn in enumerate(fnames)}
    out["B_slices"] = [slices(AGGB, cfgB, namesB, fnames, fk, "B", HOLD_B) for fk in (1, 2, 4, 5)]
    json.dump(out, open(os.path.join(TR.MODELS, "exp15_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    # ---------------- A: внутри дня с 2012 г. (часовые: кэш модели с 2020 + h-old из №13)
    Ho = {}
    hdir = os.environ.get("EXP_H_DIR")
    if hdir and os.path.isdir(hdir):
        for f in os.listdir(hdir):
            if f.endswith(".json.gz"):
                with gzip.open(os.path.join(hdir, f), "rt") as fh:
                    Ho[f[:-8]] = json.load(fh)
    log("часовые до 2020 из кэша:", len(Ho), "рядов")
    IXo = Ho.pop("IMOEX", [])
    H2 = {}
    for t in set(Hh) | set(Ho):
        new = Hh.get(t, [])
        cut = new[0][0] if new else "9999"
        H2[t] = [r for r in Ho.get(t, []) if r[0] < cut] + new
    IX2 = [r for r in IXo if r[0] < (IXH[0][0] if IXH else "9999")] + IXH
    E = events_a(X, H2, IX2, Dd)
    PA = paths_a(E)
    yrA = E.d.dt.year.values
    yearsA = sorted(set(yrA))
    yidxA = np.searchsorted(yearsA, yrA)
    lvA = E.lval.values
    upA, dnA = E.s.values > 0, E.s.values < 0
    masksA = [upA, upA & (lvA >= 7), upA & (lvA >= 8), dnA, dnA & (lvA >= 7), dnA & (lvA >= 8)]
    log("сигналов A:", len(E), "| по годам", E.d.dt.year.value_counts().sort_index().to_dict(),
        "| ≥10 млн:", int((lvA >= 7).sum()), "≥100 млн:", int((lvA >= 8).sum()))
    cfgA = [c for c in itertools.product(("follow", "fade"), ENT_A, TPS, SLS, HOLD_A) if not (c[1] in ("close0", "open1") and c[4] == 0)]
    cache = {"cost": cost_of(lvA)}
    AGGA = np.zeros((len(cfgA), len(masksA), len(yearsA), 5))
    for k, c in enumerate(cfgA):
        r = sim_a(E, PA, *c, cache)
        if r is not None:
            AGGA[k] = aggregate(r, masksA, yidxA, len(yearsA))
        if k % 2000 == 0:
            log(f"A: {k}/{len(cfgA)} вариантов, {round(time.time() - t0)} с")
    namesA = [name_of(*c) for c in cfgA]
    out["A_full"] = full_report(AGGA, cfgA, namesA, yearsA, fnames, "A ВНУТРИ ДНЯ 2012–2026")
    out["A_wf"] = {fn: walk_forward(AGGA, namesA, yearsA, fk, f"A внутри дня, {fn}") for fk, fn in enumerate(fnames)}
    out["A_slices"] = [slices(AGGA, cfgA, namesA, fnames, fk, "A", HOLD_A) for fk in (1, 2, 4, 5)]
    json.dump(out, open(os.path.join(TR.MODELS, "exp15_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    # ---------------- ALGOPACK: лучшие ликвидные «рост по движению, вход сразу/лимитом, ≤5 дней» на 5-минутных свечах (кэш №14)
    try:
        validate_a5(E, AGGA, cfgA, namesA, out)
    except Exception as e:  # noqa: BLE001
        log("проверка на 5-минутных свечах пропущена:", e)
    json.dump(out, open(os.path.join(TR.MODELS, "exp15_report.json"), "w"), ensure_ascii=False, indent=1, default=str)
    log("===== ИТОГ ПРОВЕРКИ №15 =====", round(time.time() - t0), "с")


def validate_a5(E, AGGA, cfgA, namesA, out):
    a5 = os.environ.get("EXP_A5_DIR")
    if not a5 or not os.path.isdir(a5):
        log("кэша 5-минутных свечей нет — проверка ALGOPACK пропущена")
        return
    tot = AGGA[:, 1, :, :].sum(axis=1)       # рост, ≥10 млн
    n = tot[:, 0]
    mean = tot[:, 1] / np.maximum(n, 1)
    var = np.maximum(tot[:, 2] / np.maximum(n, 1) - mean ** 2, 1e-12)
    sc = np.where(n >= MIN_TR, mean / np.sqrt(var) * np.sqrt(n), -1e9)
    ok = [i for i in np.argsort(-sc) if cfgA[i][0] == "follow" and cfgA[i][4] <= 5 and cfgA[i][1] not in ("close0", "open1")][:8]
    S = E[(E.s > 0) & (E.lval >= 7) & (E.d >= pd.Timestamp("2020-01-01"))]
    log(f"===== ПРОВЕРКА НА 5-МИНУТНЫХ СВЕЧАХ ALGOPACK (рост, ≥10 млн ₽, 2020–2026, {len(S)} сигналов) =====")
    res = []
    bars_of = {}
    for _, r in S.iterrows():
        p = os.path.join(a5, f"{r.t}_{r.d.date().isoformat()}.json.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt") as f:
                B0 = [b for b in json.load(f)["bars"] if all(x is not None and x == x and x > 0 for x in b[1:5])]
            # чистка как в №14: бары вне дневного диапазона ISS (±30%) — сбой данных или масштаб до дробления
            d0 = r.d.strftime("%Y-%m-%d")
            wd = sorted({b[0][:10] for b in B0 if b[0][:10] > d0 and dt.date.fromisoformat(b[0][:10]).weekday() < 5})
            band = {d0: (min([r.L0] + list(r.fl)), max([r.H0] + list(r.fh)))}
            for k, dd_ in enumerate(wd[: len(r.nh)]):
                band[dd_] = (float(r.nl[k]), float(r.nh[k]))
            bars_of[_] = [b for b in B0 if b[0][:10] not in band or (b[3] >= 0.7 * band[b[0][:10]][0] and b[2] <= 1.3 * band[b[0][:10]][1])]
    log("5-минутные пути из кэша:", len(bars_of), "сигналов")
    for i in ok:
        side, ent, tp, sl, hold = cfgA[i]
        rr, nh = [], 0
        for _, r in S.iterrows():
            bars = bars_of.get(_)
            if not bars:
                continue
            d0 = r.d.strftime("%Y-%m-%d")
            t0 = f"{d0} {int(r.c) + 1:02d}:05"
            days = sorted({b[0][:10] for b in bars if b[0][:10] > d0 and dt.date.fromisoformat(b[0][:10]).weekday() < 5})
            if hold > 0 and len(days) < hold:
                continue
            nh += 1
            last_day = d0 if hold == 0 else days[hold - 1]
            m = abs(r.mv)
            e = r.p0 if ent == "sig" else r.p0 - ent * (r.p0 - r.prev)
            fi = None
            for j, b in enumerate(bars):
                if b[0] >= t0 and b[0][:10] == d0 and (ent == "sig" or b[3] <= e):
                    fi = j
                    break
            if fi is None:
                continue
            tpl = None if tp is None else e * (1 + (tp[1] * m if tp[0] == "m" else tp[1]))
            sll = None if sl is None else e * (1 - (sl[1] * m if sl[0] == "m" else sl[1]))
            ex = None
            for b in bars[fi + 1:]:
                if b[0][:10] > last_day:
                    break
                if sll is not None and b[3] <= sll:
                    ex = min(sll, b[1])  # гэп через стоп — по открытию бара
                    break
                if tpl is not None and b[2] >= tpl:
                    ex = tpl
                    break
            if ex is None:
                lb = [b for b in bars if b[0][:10] <= last_day][-1]
                spr = lb[6]
                ex = lb[4] * (1 - ((spr / 2e4) if spr is not None and 0 <= spr < 2000 else 0.0015))
            rr.append(ex / e - 1 - 0.001)
        x = np.array(rr)
        if len(x):
            log(f"  {namesA[i]}\n     по часовым: {mean[i]:+.2%}/сд (n={int(n[i])}, все годы) | по 5 минутам: {x.mean():+.2%}/сд, прибыльных {(x > 0).mean():.0%}, "
                f"PF {x[x > 0].sum() / max(1e-9, -x[x < 0].sum()):.2f}, худшая {x.min():+.1%}, медиана {np.median(x):+.2%}, сделок {len(x)} из {nh} сигналов с данными")
            res.append(dict(name=namesA[i], hourly=float(mean[i]), a5=float(x.mean()), n=len(x)))
    out["a5"] = res


if __name__ == "__main__":
    main()
