#!/usr/bin/env python3
"""Список Telegram-каналов: whitelist бота, игнор и остальные подписки."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)


def _peer_id(mod, entity) -> int | None:
    tu = getattr(mod, "telethon_utils", None)
    if tu is None:
        return None
    try:
        return int(tu.get_peer_id(entity))
    except Exception:
        return None


async def main() -> int:
    try:
        from telethon import TelegramClient
    except ImportError:
        print("Установите: ./venv/bin/pip install telethon")
        return 1

    import importlib.util

    mod_path = ROOT / "scripts" / "telegram_signal_agent.py"
    mod_name = "telegram_signal_agent_list_channels"
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    if spec is None or spec.loader is None:
        print("Не удалось загрузить telegram_signal_agent.py")
        return 1
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    agent = mod.TelegramSignalAgent(ROOT)
    api_id = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    if api_id <= 0 or not api_hash:
        print("Нет TELEGRAM_API_ID/HASH в .env")
        return 1

    session = str(agent.agent_cfg.get("session_name", "telegram_user_signal_agent"))
    client = TelegramClient(str(ROOT / session), api_id, api_hash)

    allowed_raw = list(agent.agent_cfg.get("allowed_chats", []) or [])
    whitelist: list[str] = []
    skip: list[str] = []
    other: list[str] = []
    peer_ids: set[int] = set()

    async with client:
        if allowed_raw:
            try:
                resolved = await agent._resolve_allowed_peer_ids(client)
                peer_ids = resolved or set()
            except Exception as exc:
                print(f"Ошибка allowed_chats: {exc}")

        async for dialog in client.iter_dialogs():
            if not getattr(dialog, "is_channel", False):
                continue
            name = agent._chat_source_label(dialog.entity)
            pk = _peer_id(mod, dialog.entity)
            if agent._is_ignored_source(name):
                skip.append(name)
            elif allowed_raw:
                if pk is not None and pk in peer_ids:
                    whitelist.append(name)
                else:
                    other.append(name)
            else:
                other.append(name)

    if allowed_raw:
        print(
            f"=== Бот СЛУШАЕТ только whitelist ({len(whitelist)} из {len(allowed_raw)} в config) ==="
        )
        print("(то же, что peer_ids:N в логе telegram_signal_agent)\n")
        for n in sorted(whitelist):
            print(f"  + {n}")
        missing = [
            x
            for x in allowed_raw
            if not any(x.lower() in w.lower() or w.lower() in x.lower() for w in whitelist)
        ]
        if missing:
            print("\n  ! Не найдены в Telegram (проверьте имя в config):")
            for m in missing:
                print(f"    ? {m}")
        print(f"\n=== Игнор ({len(skip)}) ===")
        for n in sorted(skip):
            print(f"  - {n}")
        print(f"\n=== Остальные подписки — бот НЕ слушает ({len(other)}) ===")
        print("Убрать: отписка в Telegram ИЛИ строка в ignored_chats в config.yaml\n")
        for n in sorted(other)[:40]:
            print(f"  · {n}")
        if len(other) > 40:
            print(f"  … и ещё {len(other) - 40} (всего {len(other)})")
    else:
        print("allowed_chats пуст — бот слушает ВСЕ каналы кроме игнора!\n")
        print(f"=== Сканируются ({len(other)}) ===")
        for n in sorted(other):
            print(f"  + {n}")
        print(f"\n=== Игнор ({len(skip)}) ===")
        for n in sorted(skip):
            print(f"  - {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
