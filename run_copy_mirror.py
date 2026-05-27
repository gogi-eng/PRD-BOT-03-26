#!/usr/bin/env python3
"""
Отдельный процесс: зеркало Copy Trading → субаккаунт.
НЕ запускает run_unified.py и НЕ мешает trading_bot.

  ./venv/bin/python3 run_copy_mirror.py

Конфиг: config.copy_mirror.yaml
Лог:  copy_mirror.log
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.copy_mirror.engine import run_from_config


def setup_logging() -> None:
    log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(log_fmt))
    root.addHandler(console)
    fh = RotatingFileHandler(
        ROOT / "copy_mirror.log",
        maxBytes=8 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(log_fmt))
    root.addHandler(fh)


def main() -> None:
    setup_logging()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    task = loop.create_task(run_from_config())

    def _stop(*_args):
        task.cancel()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
