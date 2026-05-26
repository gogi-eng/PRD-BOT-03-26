#!/usr/bin/env python3
"""
Напоминание о работах среднего приоритета (31.05.2026 14:00).
Telegram + файл-флаг для Cursor/логов.

Запуск вручную: python scripts/remind_medium_priority_work.py --force
Cron: см. register_medium_priority_reminder.sh
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGET_YEAR = 2026
TARGET_MONTH = 5
TARGET_DAY = 31
TARGET_HOUR = 14
TARGET_MINUTE = 0
WINDOW_MINUTES = 30


def _in_window(now: datetime, *, force: bool) -> bool:
    if force:
        return True
    if now.year != TARGET_YEAR or now.month != TARGET_MONTH or now.day != TARGET_DAY:
        return False
    start = TARGET_HOUR * 60 + TARGET_MINUTE
    cur = now.hour * 60 + now.minute
    return start <= cur < start + WINDOW_MINUTES


async def _send_telegram(text: str) -> bool:
    from prd_agent.config import load_config
    from prd_agent.telegram.notifier import TelegramNotifier

    cfg_path = ROOT / "config.yaml"
    if not cfg_path.exists():
        print("config.yaml не найден")
        return False
    cfg = load_config(cfg_path)
    notifier = TelegramNotifier(cfg)
    return await notifier.send(text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Отправить сейчас (тест)")
    args = parser.parse_args()

    now = datetime.now()
    if not _in_window(now, force=args.force):
        print(
            f"Не время напоминания (нужно {TARGET_DAY:02d}.{TARGET_MONTH:02d}.{TARGET_YEAR} "
            f"{TARGET_HOUR:02d}:{TARGET_MINUTE:02d}, сейчас {now:%d.%m.%Y %H:%M})"
        )
        return 0

    flag = ROOT / "data" / "reminders" / "medium_priority_31_05_26.sent"
    if flag.exists() and not args.force:
        print("Уже отправлялось сегодня:", flag)
        return 0

    msg = (
        "<b>📅 Напоминание PRD-BOT</b>\n"
        "31.05.2026 — фаза 2 улучшения входа:\n"
        "• советник входа (anti-FOMO)\n"
        "• лимит TG + таймаут\n"
        "• метаданные TA для плеча\n"
        "• параллельные klines\n\n"
        "Документ: <code>docs/PLAN_MEDIUM_PRIORITY_31_05_26.md</code>\n"
        "Cursor: <code>.cursor/REMINDER_31_05_26_14_00.md</code>"
    )

    flag.parent.mkdir(parents=True, exist_ok=True)
    ok = asyncio.run(_send_telegram(msg))
    flag.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    cursor_note = ROOT / ".cursor" / "REMINDER_31_05_26_14_00.md"
    if cursor_note.exists():
        stamp = f"\n\n---\n⏰ Напоминание отправлено: {now:%Y-%m-%d %H:%M:%S}\n"
        with cursor_note.open("a", encoding="utf-8") as f:
            f.write(stamp)
    print("Telegram:", "OK" if ok else "FAIL (проверьте TELEGRAM_TOKEN/chat_id)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
