# -*- coding: utf-8 -*-
"""Перезапуск долгоживущих Python-ботов (закрытые идеи, Пульс) на новом коде без остановки цепочки.

Новостной бот (bot.js) раз в несколько минут подтягивает коммиты из GitHub в рабочую копию и скачивает свежие модели
аномалий. Этот модуль следит за временем изменения .py-файлов newsbot/channel и newsbot/anomaly и файла моделей
(ANOM_MODELS/meta.json); если что-то поменялось — бот сохраняет состояние и перезапускает себя (os.execv, тот же PID,
поэтому рабочий процесс GitHub по-прежнему может его остановить в конце запуска).
"""
import glob
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIRS = [HERE, os.path.join(HERE, "..", "anomaly")]


def _snapshot():
    m = 0.0
    for d in DIRS:
        for p in glob.glob(os.path.join(d, "*.py")):
            try:
                m = max(m, os.path.getmtime(p))
            except OSError:
                pass
    mp = os.path.join(os.environ.get("ANOM_MODELS") or "", "meta.json")
    if os.path.exists(mp):
        m = max(m, os.path.getmtime(mp))
    return m


class Watcher:
    def __init__(self, every=30):
        self.t0 = _snapshot()
        self.every = every
        self.last = time.time()
        self.seen_at = None

    def changed(self):
        """True, если код или модели обновились (проверка не чаще раза в every секунд)."""
        if os.environ.get("HOT_RELOAD") == "0" or time.time() - self.last < self.every:
            return False
        self.last = time.time()
        if _snapshot() <= self.t0:
            return False
        # файлы могли записаться не все сразу — ждём ещё один цикл проверки после первого замеченного изменения
        if self.seen_at is None:
            self.seen_at = time.time()
            return False
        # не обрываем фоновые задачи (разбор идеи): ждём их окончания, но не дольше 5 минут
        busy = threading.active_count() > 1
        return not busy or time.time() - self.seen_at > 300


def restart(log, env=None):
    """Перезапуск процесса на новом коде; env — переменные для нового процесса (например, оставшееся время работы)."""
    for k, v in (env or {}).items():
        os.environ[k] = str(v)
    log("код или модели обновились на GitHub — перезапускаюсь на новой версии")
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable] + sys.argv)
