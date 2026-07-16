#!/usr/bin/env python3
"""Однократный запуск Bybit AI-монитора (для cron или ручной проверки)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config
from prd_agent.engine.orchestrator import UnifiedOrchestrator


async def _main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    orch = UnifiedOrchestrator(cfg)
    try:
        text = await orch.get_bybit_monitor_report()
        print(text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
        return 0
    finally:
        await orch.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
