# -*- coding: utf-8 -*-
"""Сканер сделок для бота идей: кандидаты с уровнями (стоп, цель) — администратору в личные сообщения.

1) Вечером (19:25 МСК, будни) — сканер по дневным свечам (проверка №24, newsbot/anomaly/scan_core.py): ликвидные акции, лонг и шорт,
   вход по закрытию, стоп за уровнем / 2,5 ATR, цель 5R, срок до 60 торговых дней. Присылаются только бумаги, у которых оценка модели
   не ниже порога, при котором отбор давал плюс вне выборки; не больше K в день. Модель — ANOM_MODELS/scan_model.pkl (создаётся
   только если проверка пройдена); без неё вечерний сканер молчит.
2) Внутри дня (10:00–18:50) — сигналы стратегий виртуального счёта (paper_anom.json новостного бота), проверенных на 5-минутных
   свечах: S1 (аномальный рост → лимит на откате 50% хода, тейк +25% хода, до 5 дней), F4 и F2 с модель-фильтром «верхние 30%».
   Их выход — как в проверках; стоп в них не ставился (стопы ухудшали результат) — показывается аварийный стоп за уровнем.
Под каждым кандидатом — кнопка «📝 Оформить идею»: обычный разбор и превью для публикации. Не является индивидуальной
инвестиционной рекомендацией.
"""
import html
import json
import os
import pickle
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "anomaly"))
MSK = 3 * 3600
SCAN_AT = os.environ.get("SCAN_AT", "19:25")
_M = {}
INTRA = {"S1": "аномальный рост → покупка на откате 50% хода, тейк +25% хода, до 5 торговых дней (№12–15: ~+1,5% на сделку, ~88% прибыльных на 5-мин свечах 2020–2026)",
         "F4": "аномальное падение → покупка сразу, тейк +200% хода, до 20 торговых дней; модель-фильтр «верхние 30%» (№18, №22: ~+5,5…7,4% на сделку, 59–67% прибыльных)",
         "F2": "аномальный рост → покупка сразу, держать 10 торговых дней; модель-фильтр «верхние 30%» (№18, №22: ~+3…4% на сделку, ~52% прибыльных)"}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[сканер]", *a, flush=True)


# ---------------- аварийная остановка (по виртуальному счёту S1/F4/F2) ----------------
# Вес сделки в капитале: S1 и F4 — 10%, F2 — 5% (половинный риск: в №26 F2 на 2023–2026 не прошла тест перестановкой, p = 0,09).
WEIGHT = {"S1": 0.10, "F4": 0.10, "F2": 0.05}
KS_DAY = float(os.environ.get("KS_DAY", "-0.02"))      # убыток закрытых сделок за день, доля капитала → стоп до завтра
KS_STREAK = int(os.environ.get("KS_STREAK", "3"))       # убыточных сделок подряд (последняя — сегодня) → стоп до завтра
KS_DD = float(os.environ.get("KS_DD", "-0.10"))         # просадка от пика → стоп до команды /resume


def _paper():
    base = os.environ.get("STATE_DIR") or os.environ.get("NEWS_STATE") or ""
    try:
        return json.load(open(os.path.join(base, "channel", "paper_anom.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def risk_state(PW, st=None, today=None):
    """Состояние виртуального счёта и предохранителя. → dict(day_pnl, streak, eq, peak, dd, halted, why)."""
    st = st if st is not None else _paper()
    today = today or time.strftime("%Y-%m-%d", msk())
    ks = PW.S.setdefault("ks", {})
    out = dict(day_pnl=0.0, streak=0, eq=0.0, peak=0.0, dd=0.0, n=0, halted=False, why="")
    if not st:
        return out
    done = [x for x in st.get("trades") or [] if x.get("exit") is not None and x.get("ret") is not None
            and (x.get("strat", "S1") == "S1" or x.get("top"))]
    done.sort(key=lambda x: (x.get("exit_day") or "", x.get("sig_at") or ""))
    curve = [0.0]
    for x in done:
        curve.append(curve[-1] + WEIGHT.get(x.get("strat", "S1"), 0.1) * float(x["ret"]))
    k0 = min(int(ks.get("dd_reset_n", 0)), len(curve) - 1)   # после /resume пик считается с момента возобновления
    eq, peak = curve[-1], max(curve[k0:])
    dd = (1 + eq) / (1 + peak) - 1
    day_pnl = sum(WEIGHT.get(x.get("strat", "S1"), 0.1) * float(x["ret"]) for x in done if x.get("exit_day") == today)
    streak = 0
    for x in reversed(done):
        if float(x["ret"]) <= 0:
            streak += 1
        else:
            break
    last_today = bool(done) and done[-1].get("exit_day") == today
    out.update(day_pnl=day_pnl, streak=streak, eq=eq, peak=peak, dd=dd, n=len(done))
    if ks.get("dd_halt"):
        out.update(halted=True, why=ks.get("dd_why") or "просадка")
    elif dd <= KS_DD:
        ks.update(dd_halt=True, dd_why=f"просадка виртуального счёта {dd:.1%} от пика (порог {KS_DD:.0%})")
        out.update(halted=True, why=ks["dd_why"], new=True)
    elif ks.get("day") == today:
        out.update(halted=True, why=ks.get("why") or "")
    elif day_pnl <= KS_DAY:
        ks.update(day=today, why=f"убыток закрытых сделок за день {day_pnl:.1%} капитала (порог {KS_DAY:.0%})")
        out.update(halted=True, why=ks["why"], new=True)
    elif streak >= KS_STREAK and last_today:
        ks.update(day=today, why=f"{streak} убыточные сделки подряд (порог {KS_STREAK})")
        out.update(halted=True, why=ks["why"], new=True)
    return out


def guard(PW, admin):
    """True — сигналы можно присылать. При срабатывании — одно сообщение администратору."""
    rs = risk_state(PW)
    if rs.get("new"):
        dd_mode = PW.S["ks"].get("dd_halt")
        PW.say(admin, f"🛑 <b>Аварийная остановка</b>: {html.escape(rs['why'])}.\n"
                      + ("Сигналы остановлены до команды /resume — сначала разберитесь, что пошло не так (журнал и отчёт за день)."
                         if dd_mode else "Сигналы остановлены до 10:00 следующего торгового дня.")
                      + f"\nВиртуальный счёт: итог {rs['eq']:+.1%}, пик {rs['peak']:+.1%}, сегодня {rs['day_pnl']:+.1%}, убыточных подряд {rs['streak']}.")
        PW.save()
    return not rs["halted"]


def msk():
    return time.gmtime(time.time() + MSK)


# Правило по умолчанию (проверки №23, №24б): только по тренду (цена выше растущей SMA50 для лонга, ниже падающей — для шорта);
# способ выбора среди трендовых сигналов на истории не помог (ни признаки, ни модель не лучше случайного) — берутся 3 случайных.
# Вне выборки: 3 случайных трендовых в день — 2014–2019 +0,10R на сделку (6 из 6 лет в плюсе), 2020–2026 +0,28R (6 из 7);
# все трендовые — +0,06R / +0,23R. Лучшая «по отбору» модель — хуже (+0,02R / +0,19R), поэтому не используется.
TREND_RULE = dict(type="rule", rank="случайно", rank_idx=None, k=3, thr=-1e9, model=None,
                  late=dict(R=0.28, per_year=750, win=0.36, pos=6, years={y: 0 for y in range(2020, 2027)}, early=0.10, early_pos="6 из 6"),
                  calib=[dict(lo=-1e9, hi=1e9, n=0, R=0.28)])


def model():
    p = os.path.join(os.environ.get("ANOM_MODELS") or os.path.join(HERE, "..", "anomaly", "models"), "scan_model.pkl")
    if not os.path.exists(p):
        return TREND_RULE
    mt = os.path.getmtime(p)
    if _M.get("mt") != mt:
        _M["rec"], _M["mt"] = pickle.load(open(p, "rb")), mt
    return _M["rec"]


def _df(B):
    import pandas as pd
    df = pd.DataFrame([dict(d=b["t"][:10], o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b.get("v") or 0) for b in B if b.get("c")])
    if df.empty:
        return df
    df["d"] = pd.to_datetime(df["d"])
    return df[df["d"].dt.weekday < 5].reset_index(drop=True)


def daily(t, days=430, index=False):
    import common as C
    p = (f"engines/stock/markets/index/securities/{t}/candles.json" if index
         else f"engines/stock/markets/shares/boards/TQBR/securities/{t}/candles.json")
    return _df(C.candles(p, days, 24))


def universe(n=150):
    from common import iss, table
    js = iss("engines/stock/markets/shares/boards/TQBR/securities.json",
             **{"iss.only": "marketdata", "marketdata.columns": "SECID,VALTODAY"})
    rows = [r for r in table(js, "marketdata") if r.get("VALTODAY")]
    return [r["SECID"] for r in sorted(rows, key=lambda r: -float(r["VALTODAY"]))[:n]]


def calib(rec, p):
    for c in rec.get("calib") or []:
        if c["lo"] <= p <= c["hi"]:
            return c
    cs = rec.get("calib") or []
    return (cs[-1] if cs and p > cs[-1]["hi"] else cs[0]) if cs else None


def scan(tickers=None):
    """→ (кандидаты, сообщение-причина). Кандидат: dict(t, side, entry, stop, target, risk, level, atr, p, cal)."""
    import pandas as pd
    import scan_core as SC
    rec = model()
    if rec is None:
        return [], "модель сканера не подключена: проверка №24 не пройдена или ещё не запускалась"
    ix = daily("IMOEX", index=True)
    ixs = pd.Series(ix.c.to_numpy(float), index=ix.d)
    out = []
    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        try:
            df = daily(t)
            if len(df) < 270:
                return []
            B = SC.base_arrays(df, ixs)
            i = len(df) - 1
            if not (B["A"][i] > 0) or not (B["val_med"][i] >= 1e7) or not np.isfinite(B["d200"][i]):
                return []
            res = []
            for side in (1, -1):
                pl = SC.plan(B, i, side)
                x = np.asarray([SC.row(B, i, side, pl["risk"])], dtype=np.float32)
                if rec.get("type") == "rule":   # №24б: только по тренду (цена и наклон SMA50 в сторону сделки), ранжирование — один признак
                    if not (x[0, 1] > 0 and x[0, 2] > 0):
                        continue
                    if rec.get("rank_idx") is not None:
                        p = float(x[0, rec["rank_idx"]])
                    elif rec.get("model") is not None:
                        p = float(rec["model"].predict(x)[0])
                    else:   # случайный, но один и тот же для бумаги и дня
                        import zlib
                        p = (zlib.crc32(f"{t}{side}{df.d.iloc[i].date()}".encode()) % 10007) / 10007
                else:
                    p = float(rec["model"].predict(x)[0])
                if np.isfinite(p) and p >= rec["thr"]:
                    res.append(dict(t=t, side=side, p=p, day=str(df.d.iloc[i].date()), cal=calib(rec, p), **pl))
            return res
        except Exception as e:  # noqa: BLE001
            log(t, e)
            return []
    with ThreadPoolExecutor(4) as ex:
        for r in ex.map(one, tickers or universe()):
            out += r
    out.sort(key=lambda r: -r["p"])
    _M["n_all"] = len(out)
    return out[:int(rec.get("k") or 3)], ""


def _fmt(p):
    return f"{p:,.6g}".replace(",", " ").replace(".", ",")


def card(c, rec, PW):
    side = "ЛОНГ 🟢" if c["side"] > 0 else "ШОРТ 🔴"
    cal = c.get("cal") or {}
    risk_pct = c["risk"] / c["entry"] * 100
    L = [f"<b>{html.escape(PW.tlabel(c['t']))} — {side}</b>",
         f"Вход: ~{_fmt(c['entry'])} (закрытие {c['day']}) · стоп <b>{_fmt(c['stop'])}</b> ({'за уровнем' if c['level'] else '2,5 ATR'}, "
         f"риск {risk_pct:.1f}%)".replace(f"{risk_pct:.1f}%", f"{risk_pct:.1f}%".replace(".", ",")),
         f"Цель <b>{_fmt(c['target'])}</b> (5R) одним тейком, срок до 60 торговых дней",
         (("Отбор: сделка по тренду (цена и наклон 50-дневной средней в сторону сделки); "
           if rec.get("rank_idx") is None and rec.get("model") is None else f"Отбор: по тренду, лучшие по признаку «{rec.get('rank')}»; ")
          if rec.get("type") == "rule" else f"Оценка модели {c['p']:+.2f}R; ")
         + (f"у таких сделок вне выборки: в среднем {cal.get('R', float('nan')):+.2f}R на сделку"
            + (f", цель {cal['p_tgt']:.0%}, стоп {cal['p_stop']:.0%}, остальное — выход по сроку" if "p_tgt" in cal else
               "; большинство сделок закрывается стопом или по сроку, плюс дают редкие далёкие цели")).replace(".", ","),
         f"Размер позиции: чтобы стоп стоил 1% счёта — ≈ {min(100, 100 / risk_pct):.0f}% счёта"]
    return "\n".join(L)


def evening(PW, force=False):
    t = msk()
    day = time.strftime("%Y-%m-%d", t)
    if not force and (t.tm_wday >= 5 or time.strftime("%H:%M", t) < SCAN_AT or PW.S.get("scan_day") == day):
        return
    if not PW.ADMINS:
        return
    if not force:
        PW.S["scan_day"] = day
        PW.save()
    admin = sorted(PW.ADMINS)[0]
    if not guard(PW, admin):
        PW.say(admin, f"🔎 Вечерний сканер за {day} не запускался: действует аварийная остановка ({html.escape(PW.S['ks'].get('dd_why') or PW.S['ks'].get('why') or '')}).")
        return
    try:
        cands, why = scan()
        rec = model()
        if why:
            if force or not PW.S.get("scan_warned"):
                PW.say(admin, f"🔎 Вечерний сканер: {why}. Внутридневные сигналы S1/F4/F2 приходят как обычно.")
                PW.S["scan_warned"] = True
            return
        lt = rec.get("late") or {}
        n_all = _M.get("n_all", len(cands))
        crit = (("сделки по тренду, до 3 случайных из всех трендовых сигналов (выбор «лучших» на истории не давал преимущества)"
                 if rec.get("rank_idx") is None and rec.get("model") is None else f"сделки по тренду, {rec.get('k')} лучших по признаку «{rec.get('rank')}»")
                if rec.get("type") == "rule"
                else f"бумаги с оценкой не ниже порога ({str(round(rec['thr'], 2)).replace('.', ',')}R)")
        head = (f"🔎 <b>Сканер на закрытии {day}</b>: {crit}.\n"
                f"<i>Отбор на проверке 2020–2026 вне выборки: {lt.get('R', 0):+.2f}R на сделку, ~{lt.get('per_year', 0):.0f} сделок в год, "
                f"прибыльных {lt.get('win', 0):.0%}, лет в плюсе {lt.get('pos', 0)} из {len(lt.get('years') or {})}"
                + (f"; 2014–2019 {lt['early']:+.2f}R, лет в плюсе {lt['early_pos']}".replace(".", ",") if lt.get("early") is not None else "")
                + (f". Сигналов по тренду сегодня {n_all}, показаны {len(cands)}" if rec.get("type") == "rule" else "") + ". "
                "Цена закрытия считается по основной сессии — к утру уровни стоит сверить.</i>")
        if not cands:
            PW.say(admin, head + "\n\nСегодня ни одна бумага не прошла порог — сделок нет.\n\n<i>Не является индивидуальной инвестиционной рекомендацией.</i>")
            return
        PW.say(admin, head)
        S = PW.S.setdefault("scan", {})
        for c in cands:
            key = f"{c['t']}{'L' if c['side'] > 0 else 'S'}{day.replace('-', '')}"
            S[key] = c
            PW.say(admin, card(c, rec, PW) + "\n\n<i>Не является индивидуальной инвестиционной рекомендацией.</i>",
                   reply_markup={"inline_keyboard": [[{"text": "📝 Оформить идею", "callback_data": f"scn:{key}"}]]})
        for k in sorted(S)[:-60]:
            S.pop(k, None)
        PW.save()
        log("вечерний сканер:", len(cands), "кандидатов")
    except Exception as e:  # noqa: BLE001
        log("вечерний сканер:", e)
        try:
            PW.say(admin, f"⚠️ Вечерний сканер не отработал: {html.escape(str(e))[:200]}")
        except Exception:  # noqa: BLE001
            pass


def _emergency(t, side):
    """Аварийный стоп (за уровнем / 2,5 ATR) по дневным свечам для внутридневного сигнала."""
    try:
        import scan_core as SC
        df = daily(t, 400)
        B = SC.base_arrays(df)
        i = len(df) - 1
        return SC.plan(B, i, side)
    except Exception as e:  # noqa: BLE001
        log("аварийный стоп", t, e)
        return None


def _live(x):
    """Сделка сборной стратегии: S1 — все, F2/F4 — только «верхние 30%» модель-фильтра."""
    return x.get("strat", "S1") == "S1" or bool(x.get("top"))


def book(st):
    """Открытые позиции сборной стратегии и загрузка счёта (S1/F4 — 10% капитала, F2 — 5%); лимита позиций нет."""
    op = [x for x in st.get("trades") or [] if x.get("entry") and not x.get("exit") and _live(x)]
    load = sum(WEIGHT.get(x.get("strat", "S1"), 0.1) for x in op)
    return op, load


def _load_line(st):
    op, load = book(st)
    s = f"Открыто позиций: {len(op)}, загрузка счёта {load:.0%}".replace(".", ",")
    if load > 1.0001:
        s += (" ⚠️ больше 100% — на все сделки нужен заём (плечо). В проверках №28–29 без лимита в дни массовых сигналов загрузка доходила "
              "до 700–2300%, просадка — до −88%")
    return s


def _trade_key(x):
    return f"{x.get('strat', 'S1')}:{x['t']}:{x['day']}"


def _signals(PW, st, day, admin, allowed):
    """Новые сигналы сборной стратегии за сегодня → администратору (каждый — один раз, без ограничения числа)."""
    seen = PW.S.setdefault("intra_seen", {})
    items = [x for x in (st.get("orders") or []) + (st.get("trades") or []) if x.get("day") == day]
    for x in items:
        strat = x.get("strat", "S1")
        key = f"{strat}:{x['t']}:{day}"
        if key in seen:
            continue
        if strat in ("F2", "F4") and not x.get("top"):
            seen[key] = time.time()
            continue
        if strat == "S1" and x.get("limit") is None:
            continue
        seen[key] = time.time()
        em = _emergency(x["t"], 1)
        mv = float(x.get("move") or 0)
        if strat == "S1":
            lim = float(x["limit"])
            body = (f"Лимит на покупку: <b>{_fmt(lim)}</b> (откат 50% хода, до конца дня) · тейк <b>{_fmt(lim * (1 + 0.25 * mv))}</b> "
                    f"(+25% хода, {0.25 * mv:+.1%}) · выход не позже 5-го торгового дня после исполнения (по закрытию)")
            entry = lim
            tp = lim * (1 + 0.25 * mv)
        else:
            entry = float(x.get("entry") or x.get("p0"))
            tp = x.get("tp")
            body = (f"Покупка сразу: ~<b>{_fmt(entry)}</b> · " + (f"тейк <b>{_fmt(float(tp))}</b> (+200% хода, {float(tp) / entry - 1:+.1%}) · " if tp else "без тейка · ")
                    + f"выход не позже {x.get('last_day')} (по закрытию)" + (f" · оценка модели {float(x.get('score') or 0):+.1%}" if x.get("score") is not None else ""))
        em_stop = entry - em["risk"] if em else None   # расстояние за уровнем / 2,5 ATR — от цены входа сигнала
        stop = (f"\nАварийный стоп (в проверках стратегии стоп не ставился — он снижал результат): {_fmt(em_stop)} "
                f"({'за уровнем' if em['level'] else '2,5 ATR'}, риск {em['risk'] / entry:.1%})" if em else "")
        size = f"\nРазмер позиции: {WEIGHT.get(strat, 0.1):.0%} капитала".replace(".", ",")
        half = (" (половинный риск: на 2023–2026 F2 держится на редких крупных сделках, тест перестановкой p = 0,09)") if strat == "F2" else ""
        halt = ("" if allowed else "\n🛑 Сейчас действует аварийная остановка — сделка идёт в учёт виртуального счёта, но по правилу "
                                   "предохранителя вход пропускается (/risk, /resume).")
        txt = (f"⚡ <b>Сигнал · {strat} · {html.escape(PW.tlabel(x['t']))}</b> "
               f"({float(x.get('exc') or 0):+.1%} к индексу, {x.get('sig_at', '')[11:16]})\n{INTRA.get(strat, '')}\n\n{body}{stop}{size}{half}\n"
               f"{_load_line(st)}{halt}\n\n<i>Не является индивидуальной инвестиционной рекомендацией.</i>")
        try:
            c = dict(t=x["t"], side=1, entry=float(entry), stop=float(em_stop) if em else None, target=float(tp) if tp else None, strat=strat)
            sk = f"{x['t']}{strat}{day.replace('-', '')}"
            PW.S.setdefault("scan", {})[sk] = c
            mk = {"inline_keyboard": [[{"text": "📝 Оформить идею", "callback_data": f"scn:{sk}"}]]} if c["stop"] and c["target"] else None
            PW.say(admin, txt, **({"reply_markup": mk} if mk else {}))
        except Exception as e:  # noqa: BLE001
            log("сигнал не отправлен:", e)
    for k in [k for k, v in seen.items() if time.time() - v > 5 * 86400]:
        seen.pop(k, None)


def _stop_of(PW, x):
    c = (PW.S.get("scan") or {}).get(f"{x['t']}{x.get('strat', 'S1')}{x['day'].replace('-', '')}") or {}
    return c.get("stop")


def _lifecycle(PW, st, admin):
    """Исполнение лимита S1, неисполненные лимиты и закрытия сделок (тейк / срок) → администратору, каждое событие один раз."""
    life = PW.S.setdefault("life_seen", {})
    first = not PW.S.get("life_init")
    lo = time.strftime("%Y-%m-%d", time.gmtime(time.time() + MSK - 45 * 86400))
    for x in st.get("trades") or []:
        if not _live(x) or (x.get("day") or "") < lo:
            continue
        k = _trade_key(x)
        strat, name = x.get("strat", "S1"), html.escape(PW.tlabel(x["t"]))
        ev = []
        if strat == "S1" and x.get("entry"):
            ev.append(("fill", None))
        if strat == "S1" and x.get("status") == "не исполнена" and not x.get("entry"):
            ev.append(("nofill", None))
        if x.get("exit") is not None and x.get("ret") is not None:
            ev.append(("exit", None))
        for e, _ in ev:
            kk = f"{k}:{e}"
            if kk in life:
                continue
            life[kk] = time.time()
            if first:
                continue          # при первом запуске старые события не присылаем
            stp = _stop_of(PW, x)
            try:
                if e == "fill":
                    tp = x.get("tp")
                    PW.say(admin, f"✅ <b>Куплено · S1 · {name}</b> — лимит {_fmt(float(x['entry']))} исполнен ({str(x.get('entry_at', ''))[11:16]})\n"
                                  f"Тейк <b>{_fmt(float(tp))}</b> ({float(tp) / float(x['entry']) - 1:+.1%}) · "
                                  + (f"аварийный стоп {_fmt(float(stp))} · " if stp else "")
                                  + f"выход не позже {x.get('last_day')} (по закрытию)\nРазмер позиции: 10% капитала\n{_load_line(st)}")
                elif e == "nofill":
                    PW.say(admin, f"▫️ S1 · {name}: лимит {_fmt(float(x['limit']))} за {x['day']} не исполнился — сделки нет.")
                else:
                    w = WEIGHT.get(strat, 0.1)
                    rs = risk_state(PW, st)
                    why = {"тейк": "🎯 тейк", "срок": "⏱ по сроку"}.get(x.get("exit_why"), x.get("exit_why") or "")
                    PW.say(admin, f"🏁 <b>Закрыта · {strat} · {name}</b> — {why}\n"
                                  f"Вход {_fmt(float(x['entry']))} ({str(x.get('entry_at') or x['day'])[:10]}) → выход {_fmt(float(x['exit']))} ({x.get('exit_day')})\n"
                                  f"Результат {float(x['ret']):+.2%} (с издержками), в капитале {w * float(x['ret']):+.2%}"
                                  + (f" · аварийный стоп был {_fmt(float(stp))}" if stp else "")
                                  + f"\nВиртуальный счёт сборной стратегии: итог {rs['eq']:+.1%}, просадка от пика {rs['dd']:.1%}\n{_load_line(st)}")
            except Exception as ex:  # noqa: BLE001
                log("событие сделки не отправлено:", kk, ex)
    PW.S["life_init"] = True
    for k in [k for k, v in life.items() if time.time() - v > 60 * 86400]:
        life.pop(k, None)


def intraday(PW):
    """Сигналы и все события сделок сборной стратегии (S1 + F4/F2 «верхние 30%») → администратору; лимита позиций нет."""
    t = msk()
    hm = time.strftime("%H:%M", t)
    if not PW.ADMINS:
        return
    if time.time() - _M.get("intra_t", 0) < 60:
        return
    _M["intra_t"] = time.time()
    base = os.environ.get("STATE_DIR") or os.environ.get("NEWS_STATE") or ""
    p = os.path.join(base, "channel", "paper_anom.json")
    try:
        st = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return
    day = time.strftime("%Y-%m-%d", t)
    admin = sorted(PW.ADMINS)[0]
    allowed = guard(PW, admin)
    if t.tm_wday < 5 and "10:00" <= hm <= "18:50":
        _signals(PW, st, day, admin, allowed)
    _lifecycle(PW, st, admin)
    PW.save()


def open_text(PW):
    """/trades — открытые сделки и активные лимиты сборной стратегии со всеми уровнями."""
    st = _paper() or {}
    op, load = book(st)
    day = time.strftime("%Y-%m-%d", msk())
    L = [f"📒 <b>Сборная стратегия: открытые сделки</b> ({len(op)}, загрузка счёта {load:.0%})".replace(".", ",")]
    for x in sorted(op, key=lambda x: x.get("entry_at") or ""):
        stp = _stop_of(PW, x)
        L.append(f"• {x.get('strat', 'S1')} {html.escape(PW.tlabel(x['t']))}: вход {_fmt(float(x['entry']))} ({str(x.get('entry_at') or x['day'])[:10]})"
                 + (f", тейк {_fmt(float(x['tp']))}" if x.get("tp") else ", без тейка")
                 + (f", аварийный стоп {_fmt(float(stp))}" if stp else "") + f", выход не позже {x.get('last_day')}"
                 + f", {WEIGHT.get(x.get('strat', 'S1'), 0.1):.0%} капитала")
    orders = [o for o in st.get("orders") or [] if o.get("day") == day]
    if orders:
        L.append("\nЛимиты S1 на сегодня:")
        for o in orders:
            L.append(f"• {html.escape(PW.tlabel(o['t']))}: лимит {_fmt(float(o['limit']))}, тейк после исполнения +25% хода ({0.25 * float(o['move']):+.1%})")
    if len(L) == 1:
        L.append("Открытых сделок нет.")
    rs = risk_state(PW, st)
    L.append(f"\nВиртуальный счёт: закрыто {rs['n']}, итог {rs['eq']:+.1%}, просадка от пика {rs['dd']:.1%}. "
             "Не является индивидуальной инвестиционной рекомендацией.")
    return "\n".join(L)


def on_cb(PW, uid, key):
    """Кнопка «📝 Оформить идею»: обычный разбор и превью с кнопками публикации."""
    c = (PW.S.get("scan") or {}).get(key)
    if not c or uid not in PW.ADMINS:
        return "Кандидат устарел."
    if not c.get("stop") or not c.get("target"):
        return "Нет стопа или цели — оформите идею вручную."
    sd = "лонг" if c["side"] > 0 else "шорт"
    idea = PW.parse_idea(f"/idea {c['t']} {sd} {c['entry']:.6g} {c['stop']:.6g} {c['target']:.6g} сканер {c.get('strat') or 'дневной'}")
    if isinstance(idea, str):
        return idea[:180]
    PW.say(uid, "⏳ Разбираю кандидата: история бумаги и модель аномалий…")
    PW.judge_async(uid, idea, publish=True)
    return "Готовлю разбор"


def status_text(PW):
    rs = risk_state(PW)
    ks = PW.S.get("ks") or {}
    st = "🛑 остановлены: " + html.escape(rs["why"]) if rs["halted"] else "✅ сигналы идут"
    return (f"<b>Аварийная остановка</b>: {st}\n"
            f"Виртуальный счёт (S1 и F4 — 10% на сделку, F2 — 5%): закрыто {rs['n']}, итог {rs['eq']:+.1%}, пик {rs['peak']:+.1%}, "
            f"просадка {rs['dd']:.1%}; сегодня {rs['day_pnl']:+.1%}; убыточных подряд {rs['streak']}.\n"
            f"Пороги: день {KS_DAY:.0%}, серия {KS_STREAK}, просадка {KS_DD:.0%} (после неё — только командой /resume)."
            + ("" if not ks.get("dd_halt") else "\nЧтобы возобновить — /resume."))


def resume(PW):
    rs = risk_state(PW)
    ks = PW.S.setdefault("ks", {})
    ks.pop("dd_halt", None)
    ks.pop("dd_why", None)
    ks.pop("day", None)
    ks["dd_reset_n"] = rs["n"]   # пик для просадки считается с текущего уровня
    PW.save()
    return "▶️ Сигналы возобновлены. Пик для расчёта просадки сброшен на текущий уровень виртуального счёта."
