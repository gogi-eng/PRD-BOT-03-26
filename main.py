#!/usr/bin/env python3
"""TRADING BOT v9.0 — entry point (implementation in ``bot`` package)."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

# Project root (tests may monkeypatch ``main.BOT_DIR``)
BOT_DIR = Path(__file__).resolve().parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from bot.state import BasketProfitState
from bot.trading_bot import TradingBot

logger = logging.getLogger("BOT")

__all__ = ["TradingBot", "BasketProfitState", "BOT_DIR", "main"]


async def main():
    # PID lock — защита от двойного запуска
    pid_file = BOT_DIR / "bot.pid"
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f"Bot already running (PID {old_pid})! Kill it first: kill {old_pid}")
            return
        except (OSError, ValueError):
            pass
    pid_file.write_text(str(os.getpid()))

    bot = TradingBot()
    startup_failed = False

    def handle_signal(sig, frame):
        logger.info("Shutting down...")
        bot.stop()
        pid_file.unlink(missing_ok=True)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        await bot.run()
    except Exception:
        startup_failed = True
        raise
    finally:
        try:
            if startup_failed:
                await bot.client.close()
        except Exception as close_exc:
            logger.warning(f"Client close after startup failure failed: {close_exc}")
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
