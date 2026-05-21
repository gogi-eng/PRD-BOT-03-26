#!/usr/bin/env python3
"""Список Telegram-каналов: что сканируется и что в игноре (без ордеров)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)


async def main() -> int:
    try:
        from telethon import TelegramClient
    except ImportError:
        print("Установите: ./venv/bin/pip install telethon")
        return 1

    import importlib.util

    mod_path = ROOT / "scripts" / "telegram_signal_agent.py"
    spec = importlib.util.spec_from_file_location("telegram_signal_agent", mod_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    agent = mod.TelegramSignalAgent(ROOT)
    api_id = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    if api_id <= 0 or not api_hash:
        print("Нет TELEGRAM_API_ID/HASH в .env")
        return 1

    session = str(agent.agent_cfg.get("session_name", "telegram_user_signal_agent"))
    client = TelegramClient(str(ROOT / session), api_id, api_hash)

    scan: list[str] = []
    skip: list[str] = []
    async with client:
        async for dialog in client.iter_dialogs():
            if not getattr(dialog, "is_channel", False):
                continue
            name = agent._chat_source_label(dialog.entity)
            if agent._is_ignored_source(name):
                skip.append(name)
            else:
                scan.append(name)

    print(f"=== Сканируются ({len(scan)}) ===")
    for n in sorted(scan):
        print(f"  + {n}")
    print(f"\n=== Игнор ({len(skip)}) ===")
    for n in sorted(skip):
        print(f"  - {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
