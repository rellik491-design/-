#!/usr/bin/env python3
"""
Telegram Companion AI - Cross-platform 24/7 Supervisor & Watchdog.
Monitors the bot process, auto-restarts upon crashes, handles OS termination signals,
and prevents crash loops with intelligent backoff.
"""

import os
import sys
import time
import signal
import subprocess
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
WATCHDOG_LOG = LOGS_DIR / "watchdog.log"


def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [WATCHDOG] {msg}"
    print(formatted, flush=True)
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass


def run_supervisor():
    log("=" * 60)
    log("🚀 Запуск супервизора непрерывной работы Telegram Companion AI (24/7)")
    log(f"   Рабочая директория: {ROOT_DIR}")
    log(f"   Файл логов супервизора: {WATCHDOG_LOG}")
    log("=" * 60)

    # Detect python executable or uv
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR / "src")
    env["PYTHONUNBUFFERED"] = "1"

    cmd = ["uv", "run", "python", "main.py"]

    consecutive_fast_crashes = 0
    backoff = 2
    child_proc = None

    def handle_signal(sig, frame):
        log(f"Получен системный сигнал {sig}. Корректное завершение супервизора и бота...")
        if child_proc and child_proc.poll() is None:
            try:
                child_proc.terminate()
                child_proc.wait(timeout=5)
            except Exception:
                try:
                    child_proc.kill()
                except Exception:
                    pass
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    while True:
        log("▶️ Старт процесса бота: " + " ".join(cmd))
        start_time = time.time()

        try:
            child_proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT_DIR),
                env=env,
            )
            ret_code = child_proc.wait()
        except FileNotFoundError:
            # Fallback to python directly if uv not found in PATH
            cmd = [sys.executable, "main.py"]
            log("Команда 'uv' не найдена в PATH, переключение на системный python: " + " ".join(cmd))
            continue
        except Exception as e:
            log(f"Критическая ошибка при запуске подпроцесса: {e}")
            time.sleep(3)
            continue

        uptime = time.time() - start_time
        log(f"⏹ Процесс бота завершился. Код выхода: {ret_code}. Время работы: {uptime:.1f} сек.")

        if ret_code == 0:
            log("Бот завершил работу штатно по запросу пользователя. Остановка супервизора.")
            break

        # Check if crash occurred very quickly (< 10 seconds)
        if uptime < 10.0:
            consecutive_fast_crashes += 1
            backoff = min(backoff * 2, 60)
            log(f"⚠️ Быстрое падение #{consecutive_fast_crashes}. Задержка перед рестартом: {backoff} сек.")
        else:
            consecutive_fast_crashes = 0
            backoff = 2

        time.sleep(backoff)


if __name__ == "__main__":
    run_supervisor()
