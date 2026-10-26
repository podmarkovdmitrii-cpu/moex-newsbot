# -*- coding: utf-8 -*-
"""Рубрика «Трейдер»: сильные части трейдерской стратегии (сделки до 14 календарных дней, только лонг).

  1) откат после гэпа вниз на открытии (акции с оборотом от 30 млн ₽ в день);
  2) лонги фьючерсов на золото, серебро, платину и палладий.

Модель — градиентный бустинг по цене и объёму (96 признаков, обучение 2006–2026, research/t2 в исследовании),
деревья лежат в trader_model.json.gz и считаются здесь без scikit-learn. Признаки повторяют исследование:
цены с поправкой на дивиденды и сплиты, рыночный фон по IMOEX и отраслевым индексам, ранги внутри дня.

Режимы (jobs.py):
  trader_prep  — до открытия: сопровождение открытых сделок по вчерашней свече (стоп, цель, срок) и расчёт признаков;
  trader_open  — после открытия (07:20 и 10:20 МСК): гэпы и металлы → сигнал, если оценка модели ≥ порога;
  trader_week  — пятница вечером: отчёт по закрытым сделкам недели и модельному счёту.
Правила сделки: вход по цене на момент сигнала, стоп 2 ATR (1,5–10%), цель 3R, выход не позже закрытия 14-го дня
(и не больше 10 сессий). Модельный счёт 100 000 ₽, риск 1,5% на сделку, до 4 позиций, один новый вход в день."""
import datetime as dt
import gzip
import json
import math
import os
import re
import time

from common import (DEMO, MSK as MSKTZ, candles, esc, fmt, http_get, iss, load_json, log, now_msk, pc, save_json, table)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = "trader.json"
THRESH = 0.5            # порог оценки модели (R); выбран на 2012–2022
MIN_VAL = 3e7           # минимальный средний оборот акции, ₽/день
GAP_MAX = -0.01         # гэп открытия не выше −1%
METALS = {"GOLD": "Золото", "SILV": "Серебро", "PLT": "Платина", "PLD": "Палладий"}
MCODE = "FGHJKMNQUVXZ"
FCODE = {"GOLD": "GD", "SILV": "SV", "PLT": "PT", "PLD": "PD"}
SECTOR_IX = ["MCXSM", "MOEXOG", "MOEXFN", "MOEXMM", "MOEXEU", "MOEXTL", "MOEXCN", "MOEXCH"]
CAPITAL, RISK, MAXPOS, MAXPCT, GROSS = 100000.0, 0.015, 4, 0.34, 1.5
STOP_ATR, STOP_MIN, STOP_MAX, TAKE_R, HZ_DAYS, MAXS = 2.0, 0.015, 0.10, 3.0, 14, 10
COMM = {0: 0.0005, 1: 0.0003}
# итоги бэктеста этой рубрики (порог 0,5; 2012 — окт. 2026), для подписи к сигналу
BT = {"n": 445, "win": 0.58, "R": 0.40, "cagr": 0.169, "mdd": 0.238, "pf": 1.75,
      "buckets": [(0.5, 0.7, 238, 0.50, 0.10), (0.7, 1.0, 138, 0.67, 0.67), (1.0, 99, 69, 0.67, 0.89)]}

_MODEL = None


# ------------------------------------------------------------------ модель
def model():
    global _MODEL
    if _MODEL is None:
        with gzip.open(os.path.join(HERE, "trader_model.json.gz"), "rt") as f:
            _MODEL = json.load(f)
    return _MODEL


def predict(row):
    """row — словарь признаков; пропуски — None/NaN."""
    M = model()
    x = [row.get(k) for k in M["feats"]]
    x = [float("nan") if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v) for v in x]
    out = M["base"]
    for T in M["trees"]:
        k = 0
        f, t, l, r, m, leaf = T["f"], T["t"], T["l"], T["r"], T["m"], T["leaf"]
        while not leaf[k]:
            v = x[f[k]]
            if v != v:
                k = l[k] if m[k] else r[k]
            else:
                k = l[k] if v <= t[k] else r[k]
        out += T["v"][k]
    return out


# ------------------------------------------------------------------ ряды (простые списки, без pandas)
NAN = float("nan")


def nanv(x):
    return x is None or x != x


def pct_change(a, n=1):
    return [NAN if i < n or nanv(a[i]) or nanv(a[i - n]) or a[i - n] == 0 else a[i] / a[i - n] - 1 for i in range(len(a))]


def shift(a, n=1):
    return [NAN] * n + a[:-n] if n > 0 else a[:]


def rolling(a, n, fn, minp=None):
    minp = n if minp is None else minp
    out = []
    for i in range(len(a)):
        w = [v for v in a[max(0, i - n + 1):i + 1] if not nanv(v)]
        out.append(fn(w) if len(w) >= minp and i >= minp - 1 else NAN)
    return out


def r_mean(w):
    return sum(w) / len(w)


def r_std(w):  # как pandas: ddof=1
    if len(w) < 2:
        return NAN
    m = sum(w) / len(w)
    return math.sqrt(sum((v - m) ** 2 for v in w) / (len(w) - 1))


def r_median(w):
    s = sorted(w)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def ewm_mean(a, span):
    """pandas ewm(span).mean(), adjust=True."""
    al = 2 / (span + 1)
    num = den = 0.0
    out = []
    for v in a:
        num *= (1 - al)
        den *= (1 - al)
        if not nanv(v):
            num += v
            den += 1
        out.append(num / den if den else NAN)
    return out


def ewm_alpha(a, alpha):
    """pandas ewm(alpha, adjust=False).mean() — для RSI."""
    out, prev = [], None
    for v in a:
        if nanv(v):
            out.append(prev if prev is not None else NAN)
            continue
        prev = v if prev is None else prev * (1 - alpha) + v * alpha
        out.append(prev)
    return out


def rsi(c, n):
    d = [NAN] + [c[i] - c[i - 1] for i in range(1, len(c))]
    up = ewm_alpha([NAN if nanv(x) else max(x, 0) for x in d], 1 / n)
    dn = ewm_alpha([NAN if nanv(x) else max(-x, 0) for x in d], 1 / n)
    out = []
    for u, w in zip(up, dn):
        if nanv(u) or nanv(w):
            out.append(NAN)
        elif w == 0:
            out.append(NAN)
        else:
            out.append(100 - 100 / (1 + u / w))
    return out


def roll_cov_var(x, y, n, minp):
    cov, var = [], []
    for i in range(len(x)):
        pts = [(a, b) for a, b in zip(x[max(0, i - n + 1):i + 1], y[max(0, i - n + 1):i + 1]) if not nanv(a) and not nanv(b)]
        ys = [b for a, b in zip(x[max(0, i - n + 1):i + 1], y[max(0, i - n + 1):i + 1]) if not nanv(b)]
        if len(pts) >= minp and len(pts) > 1:
            mx = sum(a for a, _ in pts) / len(pts)
            my = sum(b for _, b in pts) / len(pts)
            cov.append(sum((a - mx) * (b - my) for a, b in pts) / (len(pts) - 1))
        else:
            cov.append(NAN)
        if len(ys) >= minp and len(ys) > 1:
            m = sum(ys) / len(ys)
            var.append(sum((b - m) ** 2 for b in ys) / (len(ys) - 1))
        else:
            var.append(NAN)
    return cov, var


def div0(a, b):
    return NAN if nanv(a) or nanv(b) or b == 0 else a / b


# ------------------------------------------------------------------ данные
def unsplit(B):
    """Сплиты и консолидации: скачок открытия к прошлому закрытию в 2,8+ раза."""
    fac = [1.0] * len(B)
    for i in range(1, len(B)):
        r = B[i]["o"] / B[i - 1]["c"] if B[i - 1]["c"] else 1
        if r > 2.8 or r < 0.36:
            for j in range(i):
                fac[j] *= r
    for b, k in zip(B, fac):
        for q in ("o", "h", "l", "c"):
            b[q] *= k
    return B


def adjust_divs(B, divs):
    """Обратная поправка на дивиденды по датам отсечки (как в исследовании)."""
    ds = [b["t"] for b in B]
    fac = [1.0] * len(B)
    for d, a in divs:
        k = next((i for i, x in enumerate(ds) if x >= d), None)
        if k is None or k == 0 or not B[k - 1]["c"]:
            continue
        r = 1 - a / B[k - 1]["c"]
        if 0.5 < r < 1:
            for j in range(k):
                fac[j] *= r
    for b, k in zip(B, fac):
        for q in ("o", "h", "l", "c"):
            b[q] *= k
    return B


def clean(B):
    out = []
    for b in B:
        if not b.get("c"):
            continue
        if dt.date.fromisoformat(b["t"][:10]).weekday() >= 5:
            continue
        o = b.get("o") or b["c"]
        h = max(b.get("h") or b["c"], o, b["c"])
        lo = min(b.get("l") or b["c"], o, b["c"])
        out.append(dict(t=b["t"][:10], o=o, h=h, l=lo, c=b["c"], v=b.get("v") or 0))
    return out


def fut_series(asset, days=760):
    """Непрерывный ряд по квартальным контрактам металлов со склейкой на последнем общем дне."""
    pre = FCODE[asset]
    today = dt.date.today()
    codes = []
    for k in range(-27, 4):
        y, m = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
        if m in (3, 6, 9, 12):
            codes.append(f"{pre}{MCODE[m - 1]}{y % 10}")
    series = []
    for sid in codes:
        try:
            B = candles(f"engines/futures/markets/forts/securities/{sid}/candles.json", days, 24)
        except Exception:  # noqa: BLE001
            B = []
        # код с последней цифрой года повторяется раз в 10 лет — берём только свежие свечи
        B = [b for b in B if b["t"] >= (today - dt.timedelta(days=days)).isoformat()]
        if len(B) >= 5:
            series.append((sid, B))
        time.sleep(0.05)
    series.sort(key=lambda x: x[1][-1]["t"])
    out, roll_t = [], None
    for sid, B in series:
        if not out:
            out = [dict(b, sid=sid) for b in B]
            continue
        same = {b["t"]: b for b in B}
        common_t = [b["t"] for b in out if b["t"] in same]
        if common_t:
            ct = common_t[-1]
            old = next(b for b in out if b["t"] == ct)
            r = same[ct]["c"] / old["c"] if old["c"] else 1
            out = [dict(b, o=b["o"] * r, h=b["h"] * r, l=b["l"] * r, c=b["c"] * r) for b in out if b["t"] <= ct]
            out += [dict(b, sid=sid) for b in B if b["t"] > ct]
            roll_t = ct
        else:
            out += [dict(b, sid=sid) for b in B if b["t"] > out[-1]["t"]]
    return out, roll_t


# ------------------------------------------------------------------ признаки (повторяют build_feats.py исследования)
def market_feats(ix, sect):
    """ix — бары IMOEX; sect — {код: бары}. Возвращает {дата: признаки рынка на этот день (по закрытию прошлого)}."""
    days = [b["t"] for b in ix]
    c = [b["c"] for b in ix]
    v = [b["v"] for b in ix]
    F = {}
    for n in (1, 5, 20, 60):
        F[f"m_r{n}"] = pct_change(c, n)
    r1 = pct_change(c, 1)
    F["m_sd20"] = rolling(r1, 20, r_std)
    e50, e200 = ewm_mean(c, 50), ewm_mean(c, 200)
    F["m_ema50d"] = [div0(a, b) - 1 if not nanv(div0(a, b)) else NAN for a, b in zip(c, e50)]
    F["m_ema200d"] = [div0(a, b) - 1 if not nanv(div0(a, b)) else NAN for a, b in zip(c, e200)]
    F["m_slope"] = pct_change(e50, 5)
    mx = rolling(c, 250, max, 60)
    F["m_dd250"] = [div0(a, b) - 1 if not nanv(div0(a, b)) else NAN for a, b in zip(c, mx)]
    v5, v60 = rolling(v, 5, r_mean), rolling(v, 60, r_mean)
    F["m_val"] = [math.log10(a / b) if not nanv(a) and not nanv(b) and a > 0 and b > 0 else NAN for a, b in zip(v5, v60)]
    for code, bars in sect.items():
        mp = {b["t"]: b["c"] for b in bars}
        s, last = [], NAN
        for d in days:
            last = mp.get(d, last)
            s.append(last)
        name = code.lower() if code != "MCXSM" else "mcx_sm"
        F[f"{name}_r5"] = pct_change(s, 5)
        F[f"{name}_r20"] = pct_change(s, 20)
        es = ewm_mean(s, 50)
        F[f"{name}_e50"] = [div0(a, b) - 1 if not nanv(div0(a, b)) else NAN for a, b in zip(s, es)]
    # сдвиг: известно утром дня
    out = {}
    for i, d in enumerate(days):
        out[d] = {k: (arr[i - 1] if i > 0 else NAN) for k, arr in F.items()}
    # следующий торговый день (сегодня), если его ещё нет в рядах
    nxt = {k: arr[-1] for k, arr in F.items()}
    return out, nxt


def inst_feats(B, ixc_by_day, kind):
    """Признаки инструмента по закрытию последнего бара (для входа в следующий день) — словарь + служебные поля."""
    n = len(B)
    if n < 260:
        return None
    o = [b["o"] for b in B]
    h = [b["h"] for b in B]
    lo = [b["l"] for b in B]
    c = [b["c"] for b in B]
    v = [b["v"] for b in B]
    days = [b["t"] for b in B]
    r = pct_change(c)
    tr = [NAN] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, n)]
    atr = rolling(tr, 14, r_mean, 10)
    i = n - 1  # последний закрытый день; признаки «на завтра» = значения дня i
    f = {}
    for m in (1, 3, 5, 10, 20, 60, 120, 250):
        f[f"r{m}"] = c[i] / c[i - m] - 1 if i - m >= 0 and c[i - m] else NAN
    rr = r
    f["sd5"] = r_std([x for x in rr[i - 4:i + 1] if not nanv(x)]) if len([x for x in rr[i - 4:i + 1] if not nanv(x)]) >= 5 else NAN
    sd20w = [x for x in rr[i - 19:i + 1] if not nanv(x)]
    f["sd20"] = r_std(sd20w) if len(sd20w) >= 20 else NAN
    sd60w = [x for x in rr[i - 59:i + 1] if not nanv(x)]
    f["sd60"] = r_std(sd60w) if len(sd60w) >= 60 else NAN
    f["sq"] = div0(f["sd5"], f["sd60"])
    f["atrp"] = div0(atr[i], c[i])
    rng = [(h[k] - lo[k]) / c[k] if c[k] else NAN for k in range(n)]
    f["rng"] = rng[i]
    f["rng_r"] = div0(rng[i], r_mean(rng[i - 19:i + 1]))
    f["body"] = div0(c[i] - o[i], h[i] - lo[i])
    f["clpos"] = div0(c[i] - lo[i], h[i] - lo[i])
    f["gap"] = div0(o[i], c[i - 1]) - 1
    for m in (20, 55, 250):
        mp = int(m * .6)
        wh, wl = h[max(0, i - m + 1):i + 1], lo[max(0, i - m + 1):i + 1]
        f[f"dhi{m}"] = c[i] / max(wh) - 1 if len(wh) >= mp else NAN
        f[f"dlo{m}"] = c[i] / min(wl) - 1 if len(wl) >= mp else NAN
    ema = {m: ewm_mean(c, m) for m in (10, 20, 50, 100, 200)}
    for m in (10, 20, 50, 100, 200):
        f[f"e{m}"] = c[i] / ema[m][i] - 1
    w200 = c[max(0, i - 199):i + 1]
    sma200 = sum(w200) / len(w200) if len(w200) >= 120 else NAN
    f["sma200"] = div0(c[i], sma200) - 1 if not nanv(sma200) else NAN
    f["rsi2"] = rsi(c, 2)[i]
    f["rsi14"] = rsi(c, 14)[i]
    # серия дней роста/падения
    up = [1 if (not nanv(x) and x > 0) else 0 for x in r]
    k, s = i, 0
    while k >= 0 and up[k] == up[i]:
        s += 1
        k -= 1
    f["streak"] = s if up[i] == 1 else -s
    med20 = rolling(v, 20, r_median, 10)
    vr = [div0(v[k], med20[k - 1]) if k > 0 else NAN for k in range(n)]
    f["vr1"] = vr[i]
    f["vr5"] = div0(r_mean(v[i - 4:i + 1]), med20[i - 5])
    f["vtr"] = div0(r_mean(v[i - 4:i + 1]), r_mean(v[i - 59:i + 1]))
    f["lval"] = math.log10(med20[i] + 1) if not nanv(med20[i]) else NAN
    # аномалии и избыточная доходность к IMOEX
    ixs, last = [], NAN
    for d in days:
        last = ixc_by_day.get(d, last)
        ixs.append(last)
    ixr = pct_change(ixs)
    x = [NAN if nanv(a) or nanv(b) else a - b for a, b in zip(r, ixr)]
    sdx = rolling(x, 60, r_std, 30)
    an = []
    for k2 in range(n):
        thr = max(0.04, 3 * sdx[k2 - 1]) if k2 > 0 and not nanv(sdx[k2 - 1]) else 0.04
        big = (not nanv(x[k2]) and abs(x[k2]) >= thr) if (k2 > 0 and not nanv(sdx[k2 - 1])) else (not nanv(x[k2]) and abs(x[k2]) >= 0.04 and False)
        an.append(1.0 if big or (not nanv(vr[k2]) and vr[k2] >= 4) else 0.0)
    f["an20"] = sum(an[i - 19:i + 1])
    w250 = an[max(0, i - 249):i + 1]
    f["an250"] = sum(w250) if len(w250) >= 60 else NAN
    f["x1"] = x[i]
    f["x5"] = (c[i] / c[i - 5] - 1) - (ixs[i] / ixs[i - 5] - 1) if not nanv(ixs[i - 5]) else NAN
    f["x20"] = (c[i] / c[i - 20] - 1) - (ixs[i] / ixs[i - 20] - 1) if not nanv(ixs[i - 20]) else NAN
    cov, var = roll_cov_var(r[i - 59:i + 1], ixr[i - 59:i + 1], 60, 40)
    f["beta"] = div0(cov[-1], var[-1])
    hh20 = max(h[i - 20:i]) if i >= 20 else NAN
    ll20 = min(lo[i - 20:i]) if i >= 20 else NAN
    hh55 = max(h[i - 55:i]) if i >= 55 else NAN
    ll55 = min(lo[i - 55:i]) if i >= 55 else NAN
    f["f_brk20"] = int(c[i] >= hh20) - int(c[i] <= ll20)
    f["f_brk55"] = int(c[i] >= hh55) - int(c[i] <= ll55)
    e10, e50 = ema[10][i], ema[50][i]
    f["f_pull"] = int(e10 > e50 and lo[i] <= e10 * 1.02 and c[i] > o[i] and (not nanv(f["sma200"]) and f["sma200"] > 0))
    f["f_mom126"] = (c[i] / c[i - 126] - 1) - (c[i] / c[i - 5] - 1) if i >= 126 else NAN
    f["kind"] = kind
    stop = min(STOP_MAX, max(STOP_MIN, STOP_ATR * atr[i] / c[i])) if not nanv(atr[i]) else NAN
    f["stop"] = stop
    meta = dict(last_t=days[i], close=c[i], val20=med20[i], stop=stop)
    return f, meta


def iso(d):
    return d.isoformat() if isinstance(d, dt.date) else str(d)[:10]


# ------------------------------------------------------------------ подготовка до открытия
def ref_tables():
    first = json.load(open(os.path.join(HERE, "trader_first.json"), encoding="utf-8"))
    divs = json.load(open(os.path.join(HERE, "trader_divs.json"), encoding="utf-8"))
    return first, divs


def eq_list():
    js = iss("engines/stock/markets/shares/boards/TQBR/securities.json", **{"iss.only": "securities", "securities.columns": "SECID,SHORTNAME,INSTRID,LOTSIZE"})
    return [r for r in table(js, "securities") if r.get("INSTRID") == "EQIN"]


def build_features(days_back=760):
    """Признаки всех акций и металлов по вчерашнему закрытию + рыночный фон. Возвращает словарь для состояния."""
    first, divs = ref_tables()
    ix = clean(candles("engines/stock/markets/index/securities/IMOEX/candles.json", days_back, 24))
    sect = {}
    for code in SECTOR_IX:
        try:
            sect[code] = clean(candles(f"engines/stock/markets/index/securities/{code}/candles.json", days_back, 24))
        except Exception as e:  # noqa: BLE001
            log("индекс", code, e)
    mk_by_day, mk_next = market_feats(ix, sect)
    ixc = {b["t"]: b["c"] for b in ix}
    rows, names = {}, {}
    eq = eq_list()
    short = []
    log("трейдер: акций в списке TQBR", len(eq), "; IMOEX баров", len(ix), "; отраслевых индексов", len(sect))
    for r in eq:
        t = r["SECID"]
        names[t] = r.get("SHORTNAME") or t
        try:
            B = clean(candles(f"engines/stock/markets/shares/boards/TQBR/securities/{t}/candles.json", days_back, 24))
        except Exception as e:  # noqa: BLE001
            log("свечи", t, e)
            continue
        if len(B) < 260:
            short.append(f"{t}:{len(B)}")
            continue
        B = unsplit(B)
        B = adjust_divs(B, divs.get(t, []))
        res = inst_feats(B, ixc, 0)
        if not res:
            short.append(f"{t}:feats")
            continue
        f, meta = res
        f0 = first.get(t) or B[0]["t"]
        meta["first"] = f0
        meta["lot"] = r.get("LOTSIZE") or 1
        rows[t] = dict(f=f, m=meta)
        time.sleep(0.03)
    log("трейдер: акций с признаками", len(rows), "; пропущено", len(short), ":", ", ".join(short[:12]))
    for a in METALS:
        try:
            B, roll_t = fut_series(a, days_back)
            B = clean(B)
        except Exception as e:  # noqa: BLE001
            log("фьючерс", a, e)
            continue
        res = inst_feats(B, ixc, 1)
        if not res:
            continue
        f, meta = res
        meta["first"] = first.get("F:" + a) or B[0]["t"]
        meta["roll"] = roll_t
        rows["F:" + a] = dict(f=f, m=meta)
        names["F:" + a] = METALS[a]
    # ширина рынка — по всем акциям
    sh = [x["f"] for t, x in rows.items() if not t.startswith("F:") and not nanv(x["m"]["val20"]) and x["m"]["val20"] >= 5e6]
    e50 = [x["e50"] for x in sh if not nanv(x["e50"])]
    r5 = sorted(x["r5"] for x in sh if not nanv(x["r5"]))
    a20 = [x["an20"] for x in sh if not nanv(x["an20"])]
    br = dict(br_e50=sum(1 for v in e50 if v > 0) / len(e50) if e50 else NAN,
              br_r5=(r5[len(r5) // 2] if len(r5) % 2 else (r5[len(r5) // 2 - 1] + r5[len(r5) // 2]) / 2) if r5 else NAN,
              br_an=sum(a20) / len(a20) if a20 else NAN)
    # ранги: среди акций с оборотом от 5 млн ₽ (фьючерсы — положение в распределении акций)
    base = {t: x for t, x in rows.items() if not t.startswith("F:") and not nanv(x["m"]["val20"]) and x["m"]["val20"] >= 5e6}
    for col in ("r20", "r60", "r120", "x20", "vr1", "lval"):
        vals = sorted(x["f"][col] for x in base.values() if not nanv(x["f"][col]))
        for t, x in rows.items():
            v = x["f"][col]
            if nanv(v) or not vals:
                x["f"]["rk_" + col] = NAN
            else:
                lo_, hi_ = 0, len(vals)
                while lo_ < hi_:
                    mid = (lo_ + hi_) // 2
                    if vals[mid] <= v:
                        lo_ = mid + 1
                    else:
                        hi_ = mid
                x["f"]["rk_" + col] = lo_ / len(vals)
    last_ix = ix[-1]["t"] if ix else None
    return dict(made=now_msk().isoformat(timespec="minutes"), last_day=last_ix, mk=mk_next, br=br, rows=rows, names=names)


# ------------------------------------------------------------------ сопровождение сделок
def trading_days_after(d0, B):
    return [b for b in B if b["t"] >= d0]


def manage_open(st, include_today=False):
    """По дневным свечам: стоп, цель, выход по сроку. include_today — учесть и сегодняшнюю (ещё не закрытую) свечу:
    так пятничный отчёт видит сделки, закрытые в пятницу. Незакрытый день не помечается проверенным."""
    closed = []
    keep = []
    for p in st.get("open", []):
        try:
            if p["t"].startswith("F:"):
                B = clean(candles(f"engines/futures/markets/forts/securities/{p['sid']}/candles.json", 40, 24))
            else:
                B = clean(candles(f"engines/stock/markets/shares/boards/TQBR/securities/{p['t']}/candles.json", 40, 24))
        except Exception as e:  # noqa: BLE001
            log("свечи для сопровождения", p["t"], e)
            keep.append(p)
            continue
        today = now_msk().date().isoformat()
        bars = [b for b in B if p["d0"] <= b["t"] and (b["t"] < today or include_today)]  # закрытые дни (+ сегодня по запросу)
        done = None
        prevb = None
        for k, b in enumerate(bars):
            if b["t"] > p["last_day"]:  # праздник в последний день срока — выход по закрытию последней сессии в сроке
                if prevb:
                    done = (prevb["t"], prevb["c"], "срок")
                break
            if b["t"] <= p.get("checked", ""):
                prevb = b
                continue
            sp, tp = p["stop_px"], p["take_px"]
            if k > 0 and b["o"] <= sp:
                done = (b["t"], b["o"], "стоп (гэп)")
            elif k > 0 and b["o"] >= tp:
                done = (b["t"], b["o"], "цель (гэп)")
            elif b["l"] <= sp:
                done = (b["t"], sp, "стоп")
            elif b["h"] >= tp:
                done = (b["t"], tp, "цель")
            elif b["t"] >= p["last_day"] or k + 1 >= MAXS:
                done = (b["t"], b["c"], "срок")
            if b["t"] < today:
                p["checked"] = b["t"]
            p["last_px"] = b["c"]
            prevb = b
            if done:
                break
        if done:
            d1, px, why = done
            kind = 1 if p["t"].startswith("F:") else 0
            ret = px / p["entry"] - 1 - 2 * COMM[kind] - p.get("slip", 0.001)
            R = ret / p["stop"]
            pnl = p["notional"] * ret
            st["equity"] = st.get("equity", CAPITAL) + pnl
            c = dict(p, d1=d1, exit=px, why=why, ret=ret, R=R, pnl=pnl,
                     days=(dt.date.fromisoformat(d1) - dt.date.fromisoformat(p["d0"])).days + 1)
            st.setdefault("closed", []).append(c)
            closed.append(c)
        else:
            keep.append(p)
    st["open"] = keep
    return closed


# ------------------------------------------------------------------ сигнал после открытия
def market_open_prices(names):
    js = iss("engines/stock/markets/shares/boards/TQBR/securities.json",
             **{"iss.only": "marketdata", "marketdata.columns": "SECID,OPEN,LAST,UPDATETIME,VALTODAY"})
    out = {r["SECID"]: r for r in table(js, "marketdata")}
    js2 = iss("engines/futures/markets/forts/securities.json", **{"iss.only": "securities,marketdata",
              "securities.columns": "SECID,ASSETCODE,LASTTRADEDATE", "marketdata.columns": "SECID,OPEN,LAST,UPDATETIME,VALTODAY,OPENPOSITION"})
    md = {r["SECID"]: r for r in table(js2, "marketdata")}
    soon = (dt.date.today() + dt.timedelta(days=HZ_DAYS + 3)).isoformat()  # контракт должен прожить всю сделку
    fronts = {}
    for r in table(js2, "securities"):
        a = r.get("ASSETCODE")
        if a not in METALS or (r.get("LASTTRADEDATE") or "9") <= soon:
            continue
        m = md.get(r["SECID"]) or {}
        if a not in fronts or (m.get("OPENPOSITION") or 0) > (fronts[a].get("OPENPOSITION") or 0):
            fronts[a] = dict(m, SECID=r["SECID"])
    for a, m in fronts.items():
        try:  # вчерашнее закрытие именно этого контракта — для гэпа
            B = clean(candles(f"engines/futures/markets/forts/securities/{m['SECID']}/candles.json", 10, 24))
            B = [b for b in B if b["t"] < dt.date.today().isoformat()]
            m["PREV"] = B[-1]["c"] if B else None
        except Exception:  # noqa: BLE001
            m["PREV"] = None
        out["F:" + a] = m
    return out


def dividend_today(t, today):
    """Отсечка дивиденда сегодня/вчера по dohod.ru (если новой выплаты нет в нашем файле). Возвращает сумму или 0."""
    try:
        h = http_get(f"https://www.dohod.ru/ik/analytics/dividend/{t.lower()}", timeout=20, tries=2).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return 0.0
    tot = 0.0
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", h, flags=re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        if len(cells) < 3:
            continue
        m = re.search(r"(\d\d)\.(\d\d)\.(\d{4})", cells[1]) if len(cells) > 1 else None
        if not m:
            continue
        d = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if 0 <= (today - d).days <= 1:  # с T+1 гэп отсечки приходится на день закрытия реестра
            try:
                tot += float(cells[-1].replace(",", ".").split()[0])
            except ValueError:
                pass
    return tot


def score_bucket(p):
    for lo, hi, n, win, R in BT["buckets"]:
        if lo <= p < hi:
            return n, win, R
    return None


def last_exit_day(d0):
    """Последний торговый день не позже d0+13 (календарных) — по будням; праздники уточняются при сопровождении."""
    lim = d0 + dt.timedelta(days=HZ_DAYS - 1)
    d, k, last = d0, 0, d0
    while d <= lim and k < MAXS:
        if d.weekday() < 5:
            last = d
            k += 1
        d += dt.timedelta(days=1)
    return last


def find_signal(st, feats, now):
    """Кандидаты дня: гэп вниз у ликвидных акций и металлы; лучшая оценка модели выше порога."""
    today = now.date()
    md = market_open_prices(feats["names"])
    held = {p["t"] for p in st.get("open", [])}
    cands = []
    for t, x in feats["rows"].items():
        if t in held:
            continue
        m = md.get(t) or {}
        op, last = m.get("OPEN"), m.get("LAST")
        if not op or not last:
            continue
        f = dict(x["f"])
        meta = x["m"]
        kind = 1 if t.startswith("F:") else 0
        prev = meta["close"]
        og = op / prev - 1
        if kind == 0:
            if nanv(meta.get("val20")) or meta["val20"] < MIN_VAL or og > GAP_MAX:
                continue
            dv = dividend_today(t, today) if og <= GAP_MAX else 0
            if dv:
                og = (op + dv) / prev - 1
                if og > GAP_MAX:
                    log("трейдер:", t, "гэп — дивидендная отсечка, пропуск")
                    continue
        else:
            if meta.get("roll") and (today - dt.date.fromisoformat(meta["roll"])).days <= 3:
                continue
            if not m.get("PREV"):
                continue
            og = op / m["PREV"] - 1  # гэп по самому контракту, а не по склеенному ряду
        f["ogap"] = og
        f["dow"] = today.weekday()
        f["month"] = today.month
        f.update(feats["mk"])
        f.update(feats["br"])
        f["age"] = (today - dt.date.fromisoformat(meta["first"])).days / 365
        p = predict(f)
        cands.append(dict(t=t, p=p, og=og, open=op, last=last, stop=meta["stop"], kind=kind, sid=m.get("SECID"), upd=m.get("UPDATETIME")))
    cands.sort(key=lambda c: -c["p"])
    log("трейдер: кандидатов", len(cands), "; лучшие:", ", ".join(f"{c['t']} {c['p']:+.2f} (гэп {c['og'] * 100:+.1f}%)" for c in cands[:6]))
    return [c for c in cands if c["p"] >= THRESH]


def open_trade(st, c, now):
    eq = st.get("equity", CAPITAL)
    stop = c["stop"]
    notional = min(eq * RISK / stop, eq * MAXPCT)
    gross = sum(p["notional"] for p in st.get("open", []))
    notional = min(notional, max(0.0, eq * GROSS - gross))
    if notional < eq * 0.03:
        return None
    entry = c["last"]
    d0 = now.date()
    p = dict(t=c["t"], kind=c["kind"], sid=c.get("sid"), d0=d0.isoformat(), entry=entry, stop=stop, stop_px=entry * (1 - stop),
             take_px=entry * (1 + TAKE_R * stop), last_day=last_exit_day(d0).isoformat(), notional=notional, p=c["p"], gap=c["og"],
             open_px=c["open"], slip=0.003 if c["kind"] == 0 else 0.0006, posted=now.strftime("%H:%M"))
    st.setdefault("open", []).append(p)
    st.setdefault("entries", {})[d0.isoformat()] = c["t"]
    return p


# ------------------------------------------------------------------ тексты
def name_of(t, names):
    return names.get(t, t) if names else t


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def dmy(s):
    d = dt.date.fromisoformat(s)
    return d.strftime("%d.%m")


def signal_text(p, names, eq):
    t = p["t"]
    kind = p["kind"]
    nm = name_of(t, names)
    tick = t[2:] if t.startswith("F:") else t
    if kind == 0:
        head = f"⚡ <b>Трейдер: покупка {esc(tick)}</b> ({esc(nm)})"
        why = f"Откат после гэпа: бумага открылась на {pc(p['gap'])} к вчерашнему закрытию. Модель ждёт отскока в ближайшие дни."
    else:
        head = f"⚡ <b>Трейдер: покупка фьючерса на {esc(nm.lower())}</b>"
        why = f"Металлы: модель видит продолжение движения (гэп открытия {pc(p['gap'])})."
    b = score_bucket(p["p"])
    hist = (f"Сделки с такой оценкой в 2012–2026: {b[0]}, прибыльных {round(b[1] * 100)}%, в среднем {'+' if b[2] >= 0 else ''}{fmt(b[2], 2)}R."
            if b else "")
    share = p["notional"] / eq
    lines = [head, "",
             why, "",
             f"Вход ≈ <b>{fmt(p['entry'], 4 if p['entry'] < 1 else 2)}</b> (цена в {p['posted']} МСК с задержкой биржевых данных 15 мин)",
             f"Стоп <b>{fmt(p['stop_px'], 4 if p['entry'] < 1 else 2)}</b> (−{fmt(p['stop'] * 100, 1)}%) · цель <b>{fmt(p['take_px'], 4 if p['entry'] < 1 else 2)}</b> (+{fmt(p['stop'] * TAKE_R * 100, 1)}%, 1:3)",
             f"Выход не позже закрытия {dmy(p['last_day'])} — сделки рубрики не дольше 2 недель.",
             f"Оценка модели: {'+' if p['p'] >= 0 else ''}{fmt(p['p'], 2)}R. {hist}",
             f"Модельный счёт: позиция {round(share * 100)}% капитала, риск {fmt(RISK * 100, 1)}%."]
    return "\n".join(lines)


def close_text(cs, names):
    out = ["🧾 <b>Трейдер: закрытые сделки</b>", ""]
    for c in cs:
        tick = c["t"][2:] if c["t"].startswith("F:") else c["t"]
        mark = "✅" if c["R"] > 0 else "❌"
        out.append(f"{mark} {esc(tick)} ({esc(name_of(c['t'], names))}): {'+' if c['R'] >= 0 else ''}{fmt(c['R'], 2)}R ({pc(c['ret'])}), "
                   f"{c['why']}, {c['days']} дн. Вход {fmt(c['entry'], 2)} → выход {fmt(c['exit'], 2)}")
    return "\n".join(out)


def week_text(st, names, now):
    mon = (now.date() - dt.timedelta(days=now.weekday())).isoformat()
    wk = [c for c in st.get("closed", []) if not c.get("wk")]  # всё закрытое, что ещё не попадало в недельный отчёт
    allc = st.get("closed", [])
    eq = st.get("equity", CAPITAL)
    lines = [f"📊 <b>Трейдер: итоги недели {dmy(mon)}–{now.strftime('%d.%m')}</b>", ""]
    for c in wk:
        c["wk"] = mon
    if wk:
        s = sum(c["R"] for c in wk)
        win = sum(1 for c in wk if c["R"] > 0)
        lines.append(f"Закрыто {len(wk)} {plural(len(wk), 'сделка', 'сделки', 'сделок')}, прибыльных {win}, итог {'+' if s >= 0 else ''}{fmt(s, 2)}R ({'+' if sum(c['pnl'] for c in wk) >= 0 else ''}{fmt(sum(c['pnl'] for c in wk), 0)} ₽ на модельном счёте).")
        lines.append("")
        for c in wk:
            tick = c["t"][2:] if c["t"].startswith("F:") else c["t"]
            lines.append(f"{'✅' if c['R'] > 0 else '❌'} {esc(tick)}: {'+' if c['R'] >= 0 else ''}{fmt(c['R'], 2)}R ({pc(c['ret'])}), {c['why']}, {dmy(c['d0'])}→{dmy(c['d1'])}")
    else:
        lines.append("За неделю закрытых сделок нет. Сигналы рубрики редкие: в истории — 1–2 в месяц, бывают недели без них.")
    op = st.get("open", [])
    if op:
        lines += ["", "<b>Открыты:</b>"]
        for p in op:
            tick = p["t"][2:] if p["t"].startswith("F:") else p["t"]
            lp = p.get("last_px") or p["entry"]
            lines.append(f"• {esc(tick)}: вход {fmt(p['entry'], 2)} ({dmy(p['d0'])}), сейчас {fmt(lp, 2)} ({pc(lp / p['entry'] - 1)}), стоп {fmt(p['stop_px'], 2)}, цель {fmt(p['take_px'], 2)}, выход до {dmy(p['last_day'])}")
    if allc:
        s = sum(c["R"] for c in allc)
        win = sum(1 for c in allc if c["R"] > 0)
        lines += ["", f"С запуска рубрики ({dmy(st.get('start', allc[0]['d0']))}): {len(allc)} {plural(len(allc), 'сделка', 'сделки', 'сделок')}, прибыльных {round(win / len(allc) * 100)}%, "
                  f"итог {'+' if s >= 0 else ''}{fmt(s, 2)}R, модельный счёт {fmt(eq, 0)} ₽ ({pc(eq / CAPITAL - 1)})."]
    lines += ["", f"<i>Для сравнения — история рубрики на 2012–2026: {BT['n']} сделок, {round(BT['win'] * 100)}% прибыльных, в среднем +{fmt(BT['R'], 2)}R на сделку, "
              f"{fmt(BT['cagr'] * 100, 1)}% годовых при просадке до {fmt(BT['mdd'] * 100, 0)}%. Это бэктест, а не обещание.</i>"]
    return "\n".join(lines)


# ------------------------------------------------------------------ режимы
def _save(pub, st):
    if getattr(pub, "dry", False):
        log("трейдер: проверочный прогон — состояние не сохраняю")
        return
    save_json(STATE_FILE, st)


def state():
    st = load_json(STATE_FILE, {})
    st.setdefault("equity", CAPITAL)
    st.setdefault("start", now_msk().date().isoformat())
    return st


def prep(pub):
    st = state()
    cs = manage_open(st)
    names = (st.get("feats") or {}).get("names", {})
    if cs:
        for c in cs:
            c["closed_at"] = now_msk().date().isoformat()
        pub.post(close_text(cs, names), ai_lead=False)
    feats = build_features()
    st["feats"] = feats
    _save(pub, st)
    log("трейдер: признаки на", feats["last_day"], "бумаг", len(feats["rows"]), "открыто", len(st.get("open", [])))


def open_(pub):
    now = now_msk()
    st = state()
    today = now.date().isoformat()
    feats = st.get("feats")
    if not feats or feats.get("made", "")[:10] != today:
        log("трейдер: признаки не готовы — считаю сейчас")
        feats = build_features()
        st["feats"] = feats
    if feats.get("last_day") == today:
        log("трейдер: последняя свеча — сегодняшняя, торги, видимо, уже идут давно; пропуск")
        _save(pub, st)
        return
    if (st.get("entries") or {}).get(today):
        log("трейдер: вход сегодня уже был:", st["entries"][today])
        _save(pub, st)
        return
    if len(st.get("open", [])) >= MAXPOS:
        log("трейдер: все", MAXPOS, "места заняты")
        _save(pub, st)
        return
    sig = find_signal(st, feats, now)
    if sig:
        p = open_trade(st, sig[0], now)
        if p:
            pub.post(signal_text(p, feats["names"], st["equity"]), ai_lead=False)
    else:
        log("трейдер: сигналов выше порога", THRESH, "нет")
    _save(pub, st)


def week(pub):
    st = state()
    names = (st.get("feats") or {}).get("names", {})
    cs = manage_open(st, include_today=True)
    for c in cs:
        c["closed_at"] = now_msk().date().isoformat()
    if cs:
        pub.post(close_text(cs, names), ai_lead=False)
    pub.post(week_text(st, names, now_msk()), ai_lead=False)
    _save(pub, st)
