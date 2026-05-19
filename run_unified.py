#!/usr/bin/env python3
"""Точка входа PRD Unified Agent (оркестратор + Telegram-кнопки)."""
from __future__ import annotations

import asyncio
import logging
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
    try:
        await tg.run_polling()
    except KeyboardInterrupt:
        pass
    finally:
        orch.stop()
        await tg.stop()
        await orch.close()


if __name__ == "__main__":
    asyncio.run(async_main())
