#!/usr/bin/env python3
"""
Напоминание: чекпоинт наблюдения 30.06.2026 (пакет 1+2, PRD vs WORLD).
Telegram + файл-флаг + штамп в .cursor/REMINDER_30_06_26.md

Ручной тест: python scripts/remind_checkpoint_30_06_26.py --force
Сервер: bash scripts/register_checkpoint_30_06_reminder.sh
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
TARGET_MONTH = 6
TARGET_DAY = 30
TARGET_HOUR = 10
TARGET_MINUTE = 0
WINDOW_MINUTES = 90
TIMEZONE_LABEL = "UTC+3 (местное)"


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
        deploy = ROOT / "deploy" / "config.production.yaml"
        if deploy.exists():
            cfg_path = deploy
        else:
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
            f"{TARGET_HOUR:02d}:{TARGET_MINUTE:02d} {TIMEZONE_LABEL}, "
            f"сейчас {now:%d.%m.%Y %H:%M})"
        )
        return 0

    flag = ROOT / "data" / "reminders" / "checkpoint_30_06_26.sent"
    if flag.exists() and not args.force:
        print("Уже отправлялось:", flag)
        return 0

    msg = (
        "<b>📅 План 30.06.2026 — полный день</b>\n\n"
        "<b>A 10:00 Торговля</b>\n"
        "• Ветки 30.06.26-PRD / AGENT-WORLD\n"
        "• Статистика с 24.06 (WR, PnL)\n"
        "• Hermes + inbox\n"
        "• Макс. 1 правка config (ZeroOne)\n\n"
        "<b>B 12–15 Инфраструктура</b>\n"
        "• GitHub Actions (pytest)\n"
        "• Тесты risk/guard\n"
        "• docs/DEPLOY.md\n"
        "• Backup журнала\n\n"
        "<b>C 16:00</b> — деплой если меняли config\n"
        "<b>D 18:00</b> — дамп + push\n\n"
        "План: <code>.cursor/PLAN_30_06_26.md</code>\n"
        "Cursor: «план 30.06»"
    )

    flag.parent.mkdir(parents=True, exist_ok=True)
    ok = asyncio.run(_send_telegram(msg))
    flag.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")

    cursor_note = ROOT / ".cursor" / "REMINDER_30_06_26.md"
    if cursor_note.exists():
        stamp = f"\n\n---\n⏰ Напоминание отправлено: {now:%Y-%m-%d %H:%M:%S}\n"
        with cursor_note.open("a", encoding="utf-8") as f:
            f.write(stamp)

    print("Telegram:", "OK" if ok else "FAIL (TELEGRAM_TOKEN / chat_id)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
