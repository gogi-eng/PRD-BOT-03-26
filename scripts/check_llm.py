#!/usr/bin/env python3
"""Проверка AI-шлюза (OpenRouter / Free Claude Code)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prd_agent.ai.llm_gateway import chat_async, health_check, load_llm_settings
from prd_agent.config import load_config


async def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    llm = load_llm_settings(cfg)
    print(f"provider={llm.provider}")
    ok, msg = await health_check(llm)
    print(f"health: {msg}")
    if not ok:
        return 1
    text, err = await chat_async(
        llm,
        system="Reply in one short Russian sentence.",
        user="Say that PRD-BOT-ALL AI gateway works.",
        max_tokens=60,
    )
    if err:
        print(f"chat FAIL: {err}")
        return 1
    print(f"chat OK: {text[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
