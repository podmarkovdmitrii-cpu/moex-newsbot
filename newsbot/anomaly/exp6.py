# -*- coding: utf-8 -*-
"""Проверка №6: покупки/продажи и поведение стакана — полезны ли для предсказания аномалий.

Ленты сделок и стакана за 2008–2019 в открытом доступе нет, поэтому две ветки:
  1) с 2008 г. — оценки ликвидности и стакана по дневным свечам (годы проверки 2009–2026):
     L1 спред: Corwin–Schultz (2012, по максимумам/минимумам двух дней), Abdi–Ranaldo (2017, закрытие и середина диапазона),
        Roll (1984, автоковариация изменений цены); уровень за 20 дней, отклонение от 120 дней, рост 5 дней к 60;
     L2 влияние на цену: неликвидность Amihud (|доходность| / оборот), её всплеск к 20 и 250 дням, «цена движения»
        |r|/√оборот (Kyle), устойчивость давления (доля дней роста при растущем обороте);
  2) с 2020 г. — настоящие данные ALGOPACK (algopack.py, нужен секрет ALGOPACK_TOKEN), годы проверки 2021–2026:
     AP-T сделки: чистые покупки (покупки−продажи)/(покупки+продажи) за день, 5 дней, последний и первый час,
          перевес по числу сделок, размер сделки покупателя к продавцу («крупный покупатель»), дисбаланс disb;
     AP-O заявки: перевес выставленных заявок на покупку, доля снятых заявок (и её всплеск), перевес снятых;
     AP-B стакан: спред и его всплеск, спред на 1 млн ₽, дисбаланс стакана (средний и на закрытии), глубина покупок к продажам,
          изменение суммарной глубины;
     AP-H концентрация участников (HI2, индекс Херфиндаля по агрессивным сделкам и др.) и его всплеск;
     AP-A сигналы биржи об аномальных событиях (MEGA ALERTS): число сигналов вчера и за 5 дней, вверх/вниз, по цене/объёму.
Все признаки — по данным до D−1 включительно. База — боевая модель (свежие 3 г., среднее 5 моделей, доля денег).
Части: a — L1, b — L2, c — L1+L2, d — таблицы зависимости (L1, L2); ap — ALGOPACK (модель + таблицы).
"""
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "channel"))
import exp3 as E3  # noqa: E402
import exp4 as E4  # noqa: E402
import exp5 as E5  # noqa: E402
import experiment as EX  # noqa: E402
import core_daily as CD  # noqa: E402
import train as TR  # noqa: E402

log = TR.log
MODELS = TR.MODELS
PART = os.environ.get("EXP_PART", "all")
YEARS_BACK = int(os.environ.get("EXP_YEARS", "18"))


def on(p):
    return PART == "all" or PART == p


def _stack(feats):
    F = pd.concat([M.replace([np.inf, -np.inf], np.nan).stack(future_stack=True).rename(k) for k, M in feats.items()], axis=1)
    F.index.names = ["d", "t"]
    return F.reset_index()


def _z(M, n, mp=None):
    return (M - M.rolling(n, min_periods=mp or n // 2).mean()) / M.rolling(n, min_periods=mp or n // 2).std()


# ------------------------------------------------------------------ L1: оценки спреда по свечам
def g_spread(X, P):
    piv = {k: P.pivot_table(index="d", columns="t", values=k).sort_index() for k in ("h", "l", "c")}
    H, L, C = (np.log(piv[k].where(piv[k] > 0)) for k in ("h", "l", "c"))
    # Corwin–Schultz: β = Σ(ln H/L)² за 2 дня, γ = (ln max H / min L за 2 дня)²
    beta = (H - L) ** 2 + ((H - L) ** 2).shift(1)
    gam = (np.maximum(H, H.shift(1)) - np.minimum(L, L.shift(1))) ** 2
    k = 3 - 2 * np.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gam / k)
    cs = (2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))).clip(lower=0)
    # Abdi–Ranaldo: s² = 4(c_{t−1} − η_{t−1})(c_{t−1} − η_t), η — середина диапазона в логах
    eta = (H + L) / 2
    ar2 = 4 * (C.shift(1) - eta.shift(1)) * (C.shift(1) - eta)
    # Roll: s = 2√(−cov(Δp_t, Δp_{t−1})) за 20 дней
    dp = C.diff()
    cov = (dp * dp.shift(1)).rolling(20, min_periods=12).mean() - dp.rolling(20, min_periods=12).mean() * dp.shift(1).rolling(20, min_periods=12).mean()
    roll = 2 * np.sqrt((-cov).clip(lower=0))
    feats = {}
    for name, M in (("cs", cs), ("ar", np.sqrt(ar2.clip(lower=0)))):
        lvl = M.rolling(20, min_periods=10).mean()
        feats[f"lq_{name}20"] = np.log(lvl + 1e-5).shift(1)
        feats[f"lq_{name}_z"] = ((lvl - M.rolling(120, min_periods=60).mean()) / M.rolling(120, min_periods=60).std()).shift(1)
        feats[f"lq_{name}_ch"] = np.log((M.rolling(5, min_periods=3).mean() + 1e-5) / (M.rolling(60, min_periods=30).mean() + 1e-5)).shift(1)
        feats[f"lq_{name}_rk"] = lvl.rank(axis=1, pct=True).shift(1)
    feats["lq_roll20"] = np.log(roll + 1e-5).shift(1)
    feats["lq_roll_z"] = _z(roll, 120).shift(1)
    return E3._put(X, _stack(feats), list(feats))


# ------------------------------------------------------------------ L2: влияние сделок на цену
def g_impact(X, P):
    C = P.pivot_table(index="d", columns="t", values="c").sort_index()
    V = P.pivot_table(index="d", columns="t", values="v").sort_index().where(lambda v: v > 0)
    r = np.log(C).diff()
    ami = r.abs() / V * 1e6
    kyle = r.abs() / np.sqrt(V)
    la = np.log(ami.rolling(20, min_periods=10).mean() + 1e-9)
    feats = {
        "im_ami20": la.shift(1),
        "im_ami_z250": ((la - la.rolling(250, min_periods=120).mean()) / la.rolling(250, min_periods=120).std()).shift(1),
        "im_ami_shock": np.log((ami + 1e-9) / (ami.rolling(20, min_periods=10).median() + 1e-9)).shift(1),
        "im_ami_ch": np.log((ami.rolling(5, min_periods=3).mean() + 1e-9) / (ami.rolling(60, min_periods=30).mean() + 1e-9)).shift(1),
        "im_kyle_z": _z(np.log(kyle.rolling(20, min_periods=10).mean() + 1e-9), 250).shift(1),
        "im_ami_rk": la.rank(axis=1, pct=True).shift(1),
    }
    # устойчивость давления: доля из 10 дней, где знак доходности совпал со знаком изменения оборота
    vu = np.sign(np.log(V).diff())
    feats["im_press10"] = (np.sign(r) * vu).rolling(10, min_periods=6).mean().shift(1)
    return E3._put(X, _stack(feats), list(feats))


# ------------------------------------------------------------------ ALGOPACK: настоящие покупки/продажи, заявки, стакан
def g_algopack(X, A):
    if A is None or not len(A):
        return {}
    A = A.copy()
    A = A[A.d.dt.weekday < 5]

    def piv(c):
        return A.pivot_table(index="d", columns="t", values=c).sort_index() if c in A.columns else None

    def safe_ratio(a, b):
        return (a - b) / (a + b).where((a + b) > 0)
    groups = {}
    vb, vs, tb, ts = piv("val_b"), piv("val_s"), piv("trades_b"), piv("trades_s")
    if vb is not None and vs is not None:
        net = safe_ratio(vb, vs)
        f = {"ap_net": net.shift(1),
             "ap_net5": safe_ratio(vb.rolling(5, min_periods=3).sum(), vs.rolling(5, min_periods=3).sum()).shift(1),
             "ap_net_z": _z(net, 20).shift(1),
             "ap_net_rk": net.rank(axis=1, pct=True).shift(1)}
        lb, ls, eb, es = piv("lb"), piv("ls"), piv("eb"), piv("es")
        if lb is not None:
            f["ap_net_late"] = safe_ratio(lb, ls).shift(1)
            f["ap_net_early"] = safe_ratio(eb, es).shift(1)
        if tb is not None:
            f["ap_tr_net"] = safe_ratio(tb, ts).shift(1)
            f["ap_bigbuyer"] = np.log((vb / tb.where(tb > 0)) / (vs / ts.where(ts > 0))).shift(1)
            f["ap_bigbuyer5"] = f["ap_bigbuyer"].rolling(5, min_periods=3).mean()
        dm = piv("disb_m")
        if dm is not None:
            f["ap_disb"] = dm.shift(1)
        groups["AP-T сделки: покупки/продажи"] = f
    pb, ps, cb, cs = piv("put_val_b"), piv("put_val_s"), piv("cancel_val_b"), piv("cancel_val_s")
    if pb is not None and ps is not None:
        canc = (cb + cs) / (pb + ps).where((pb + ps) > 0)
        groups["AP-O заявки: выставлено/снято"] = {
            "ap_put_net": safe_ratio(pb, ps).shift(1),
            "ap_put_net5": safe_ratio(pb.rolling(5, min_periods=3).sum(), ps.rolling(5, min_periods=3).sum()).shift(1),
            "ap_cancel": canc.shift(1),
            "ap_cancel_z": _z(canc, 20).shift(1),
            "ap_cancel_net": safe_ratio(cb, cs).shift(1),
            "ap_put_z": _z(np.log((pb + ps).where((pb + ps) > 0)), 20).shift(1),
        }
    sp = piv("ob_spread_bbo")
    if sp is not None:
        f = {"ap_spread": np.log(sp.where(sp > 0)).shift(1),
             "ap_spread_z": _z(np.log(sp.where(sp > 0)), 20).shift(1)}
        s1 = piv("ob_spread_1mio")
        if s1 is not None:
            f["ap_spread1m_z"] = _z(np.log(s1.where(s1 > 0)), 20).shift(1)
        for c, k in (("ob_imbalance_vol", "ap_ob_imb"), ("obl_imbalance_vol", "ap_ob_imb_close"), ("ob_imbalance_val_bbo", "ap_ob_imb_bbo")):
            M = piv(c)
            if M is not None:
                f[k] = M.shift(1)
        ob_b, ob_s = piv("ob_vol_b"), piv("ob_vol_s")
        if ob_b is not None and ob_s is not None:
            f["ap_depth_net"] = np.log(ob_b.where(ob_b > 0) / ob_s.where(ob_s > 0)).shift(1)
            f["ap_depth_z"] = _z(np.log((ob_b + ob_s).where((ob_b + ob_s) > 0)), 20).shift(1)
            f["ap_ob_imb5"] = f.get("ap_ob_imb", f["ap_depth_net"]).rolling(5, min_periods=3).mean()
        groups["AP-B стакан: спред, глубина, дисбаланс"] = f
    hcols = [c for c in A.columns if c.startswith("hi2_")]
    if hcols:
        f = {}
        for c in hcols:
            M = piv(c)
            k = "ap_" + c
            f[k] = M.shift(1)
            f[k + "_z"] = _z(M, 20).shift(1)
        groups["AP-H концентрация участников (HI2)"] = f
    if "al_n" in A.columns:
        f = {}
        for c in ("al_n", "al_up", "al_dn", "al_pr", "al_vol"):
            M = piv(c)
            if M is None:
                continue
            M = M.reindex(index=vb.index if vb is not None else M.index).fillna(0)   # нет сигнала — 0
            f["ap_" + c] = M.shift(1)
            f["ap_" + c + "5"] = M.rolling(5, min_periods=1).sum().shift(1)
        groups["AP-A сигналы биржи об аномалиях (MEGA ALERTS)"] = f
    out = {}
    for name, f in groups.items():
        out[name] = E3._put(X, _stack(f), list(f))
    return out


def main():
    os.makedirs(MODELS, exist_ok=True)
    X, Dd, IX, ev, impact = EX.load_all()
    FD = CD.feature_cols(X)
    yr = int(X.d.dt.year.max())
    out = dict(started=dt.datetime.utcnow().isoformat(timespec="minutes"), part=PART, runs=[], groups={})
    path = os.path.join(MODELS, f"exp6_report_{PART}.json")

    def add(r):
        if r:
            out["runs"].append(r)
        json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
        return r

    if PART == "ap":
        import algopack as AP
        A = AP.load()
        log("ALGOPACK: строк", len(A), "дней", A.d.nunique() if len(A) else 0, "бумаг", A.t.nunique() if len(A) else 0)
        if not len(A):
            raise SystemExit("нет данных ALGOPACK")
        groups = g_algopack(X, A)
        first = int(A.d.dt.year.min())
        years = [y for y in range(first + 1, yr + 1)]
        out["years"] = years
        for name, cols in groups.items():
            out["groups"][name] = {c: round(float(X.loc[X.d.dt.year >= first + 1, c].notna().mean()), 3) for c in cols}
            log("группа", name, out["groups"][name])
        allc = [c for v in groups.values() for c in v]
        sub = X[X.d.dt.year >= first].copy()
        out["lift"] = E4.lift_tables(sub, allc)
        add(None)
        # 1) обучение на всей истории (с 2008 г.; до 2020 признаки ALGOPACK пустые)
        base = add(E5.walk(X, FD, years, "база = боевая, обучение с 2008 г."))
        for name, cols in groups.items():
            add(E5.walk(X, FD + cols, years, "с 2008 г. + " + name, base=base))
        add(E5.walk(X, FD + allc, years, "с 2008 г. + все данные ALGOPACK", base=base))
        # 2) обучение только на годах с данными ALGOPACK
        base2 = add(E5.walk(sub, FD, years, f"база = боевая, обучение только с {first} г."))
        for name, cols in groups.items():
            add(E5.walk(sub, FD + cols, years, f"с {first} г. + " + name, base=base2))
        add(E5.walk(sub, FD + allc, years, f"с {first} г. + все данные ALGOPACK", base=base2))
    else:
        P = EX.prices(Dd)
        years = [y for y in range(yr - YEARS_BACK + 1, yr + 1) if y > int(X.d.dt.year.min())]
        out["years"] = years
        groups = {}
        if on("a") or PART in ("c", "d"):
            groups["L1 спред по свечам (Corwin–Schultz, Abdi–Ranaldo, Roll)"] = g_spread(X, P)
        if on("b") or PART in ("c", "d"):
            groups["L2 влияние на цену (Amihud, Kyle, давление)"] = g_impact(X, P)
        for name, cols in groups.items():
            out["groups"][name] = {c: round(float(X[c].notna().mean()), 3) for c in cols}
            log("группа", name, out["groups"][name])
        if PART == "d":
            out["lift"] = E4.lift_tables(X, [c for v in groups.values() for c in v])
            add(None)
            return
        base = add(E5.walk(X, FD, years, "база = боевая (свежие 3 г. + среднее 5 моделей + доля денег)"))
        if PART == "c":
            add(E5.walk(X, FD + [c for v in groups.values() for c in v], years, "база + L1 + L2", base=base))
        else:
            for name, cols in groups.items():
                add(E5.walk(X, FD + cols, years, "база + " + name, base=base))
    log("===== ИТОГ ПРОВЕРКИ №6, часть", PART, "(годы", out["years"][0], "–", out["years"][-1], ") =====")
    for r in out["runs"]:
        log(f"{r['name']}: AUC {r['auc']:.4f}, топ-5 дня {r['day5']:.4f}, топ-10 дня {r['day10']:.4f}"
            + (f"; Δтоп-5 дня {r['d_day5']:+.4f}, Δтоп-10 дня {r['d_day10']:+.4f}, ΔAUC {r['d_auc']:+.4f}, лучше в {r['yrs_better']}/{r['yrs']} лет" if "d_day5" in r else ""))


if __name__ == "__main__":
    main()
