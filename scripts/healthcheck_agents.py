#!/usr/bin/env python3
"""
Проверка окружения перед запуском на сервере:
  python scripts/healthcheck_agents.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    errors: list[str] = []

    try:
        import telethon  # noqa: F401
    except ImportError:
        errors.append("telethon не установлен: pip install -r requirements-unified.txt")

    try:
        from prd_agent.config import load_config  # noqa: F401

        cfg = load_config(ROOT / "config.yaml")
        if not cfg.get("bybit", {}).get("api_key"):
            print("WARN: bybit.api_key пуст — заполните config.yaml или .env")
    except Exception as exc:
        errors.append(f"prd_agent.config: {exc}")

    inbox = ROOT / "reports" / "telegram_signals" / "signals_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    if not inbox.exists():
        inbox.touch()
        print(f"OK: создан {inbox}")

    trades_dir = ROOT / "data" / "trades"
    trades_dir.mkdir(parents=True, exist_ok=True)
    print(f"OK: каталог сделок {trades_dir}")

    if errors:
        print("FAIL:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("OK: healthcheck пройден")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
