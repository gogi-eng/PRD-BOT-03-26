#!/usr/bin/env python3
"""Точка входа PRD Unified Agent (оркестратор + Telegram-кнопки)."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config
from prd_agent.engine.orchestrator import UnifiedOrchestrator
from prd_agent.telegram.control_bot import ControlBot


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def async_main() -> None:
    setup_logging()
    cfg = load_config(ROOT / "config.yaml")
    orch = UnifiedOrchestrator(cfg)
    tg = ControlBot(cfg, orch)

    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()

    def _request_shutdown() -> None:
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            pass

    poll_task = asyncio.create_task(tg.run_polling())
    try:
        await shutdown.wait()
    finally:
        orch.stop()
        await tg.stop()
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        await orch.close()


if __name__ == "__main__":
    asyncio.run(async_main())
