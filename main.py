#!/usr/bin/env python3
"""
УСТАРЕВШИЙ вход Trading Bot v9 (legacy/bot).

Продакшен и песочница: python run_unified.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from bot.state import BasketProfitState
from bot.trading_bot import TradingBot

logger = logging.getLogger("BOT")

__all__ = ["TradingBot", "BasketProfitState", "BOT_DIR", "main"]


async def main():
    pid_file = BOT_DIR / "bot.pid"
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(
                "Bot already running (PID %s)! Kill it first: kill %s",
                old_pid,
                old_pid,
            )
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
        except OSError as close_exc:
            logger.warning("Client close after startup failure failed: %s", close_exc)
        except Exception as close_exc:
            logger.warning("Client close after startup failure failed: %s", close_exc)
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    if os.environ.get("PRD_LEGACY_BOT") != "1":
        print(
            "main.py — устаревший legacy-бот.\n"
            "Используйте:  python run_unified.py\n"
            "Принудительно: PRD_LEGACY_BOT=1 python main.py",
            file=sys.stderr,
        )
        raise SystemExit(2)
    asyncio.run(main())
