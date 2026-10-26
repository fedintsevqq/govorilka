"""Точка входа Говорилки: настройка журнала и запуск интерфейса (Qt) или самопроверки."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

from govorilka.config import DATA_DIR, FROZEN

BACKGROUND_FLAG = "--background"


def setup_logging() -> None:
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    # у оконного .exe и у pythonw нет stdout/stderr, а прогресс-бар скачивания модели пишет в stderr
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if FROZEN:
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        # у собранного приложения нет консоли — пишем лог в файл, чтобы было что прислать при сбое
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler

        # лог не растёт бесконечно: 3 файла по 2 МБ хватает, чтобы разобрать любую проблему
        handler = RotatingFileHandler(DATA_DIR / "govorilka.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, format=log_format, handlers=[handler])
        enable_crash_trace()
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)
    # исключение в фоновом потоке не должно пропасть молча: пишем его в журнал с полным стеком
    threading.excepthook = lambda args: logging.getLogger("thread").error(
        "Необработанная ошибка в потоке %s", args.thread.name if args.thread else "?",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def enable_crash_trace() -> None:
    """Если программа падает целиком (сбой памяти, abort), Python успевает записать, где был каждый поток, в crash.log:
    отчёт macOS и Windows этого не показывает. Файл держим открытым всё время работы — так требует faulthandler."""
    import faulthandler
    import time

    try:
        path = DATA_DIR / "crash.log"
        if path.exists() and path.stat().st_size > 1_000_000:  # старые сбои не копим бесконечно
            path.unlink()
        trace = open(path, "a", encoding="utf-8")  # noqa: SIM115 — живёт до конца процесса
        trace.write(f"\n=== запуск {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        trace.flush()
        faulthandler.enable(file=trace, all_threads=True)
    except OSError:
        logging.getLogger(__name__).exception("Не включил запись сбоев")


def main() -> None:
    setup_logging()
    from govorilka import net

    net.use_system_certificates()  # до первого запроса: модели качает и huggingface_hub, у него свои сертификаты
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        from govorilka.selftest import run

        sys.exit(run(Path(sys.argv[2])))
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest-ui":
        from govorilka.selftest import run_ui

        sys.exit(run_ui())
    from govorilka import version
    from govorilka.qt.app import run

    logging.getLogger(__name__).info("Говорилка %s, %s", version.VERSION, sys.platform)  # версия нужна в любом отчёте о сбое
    from govorilka.installer import UPDATED_FLAG

    sys.exit(run(background=BACKGROUND_FLAG in sys.argv, updated=UPDATED_FLAG in sys.argv, live_check="--selftest-live" in sys.argv))


if __name__ == "__main__":
    main()
