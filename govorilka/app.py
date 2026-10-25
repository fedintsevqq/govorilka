"""Точка входа Говорилки: настройка журнала и запуск интерфейса (Qt) или самопроверки."""

from __future__ import annotations

import logging
import os
import sys
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
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)


def main() -> None:
    setup_logging()
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

    sys.exit(run(background=BACKGROUND_FLAG in sys.argv, updated=UPDATED_FLAG in sys.argv))


if __name__ == "__main__":
    main()
