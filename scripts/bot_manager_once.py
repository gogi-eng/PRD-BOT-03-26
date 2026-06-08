#!/usr/bin/env python3
"""Один прогон AI-менеджера бота (без торговли)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config
from prd_agent.engine.orchestrator import UnifiedOrchestrator


async def main() -> None:
    cfg = load_config(ROOT / "config.yaml")
    orch = UnifiedOrchestrator(cfg)
    try:
        text = await orch.get_bot_manager_review()
        print(text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    finally:
        await orch.close()


if __name__ == "__main__":
    asyncio.run(main())
