#!/usr/bin/env python3
"""
Добавляет запись логов в bot.log рядом с main.py (плюс stderr для systemd).

Запуск на сервере из корня проекта:
  python3 scripts/fix_bot_log_filehandler.py
  python3 scripts/fix_bot_log_filehandler.py --repo /root/PRD-SCALP

После правки: sudo systemctl restart trading_bot.service
  (и отдельно второй бот, если есть: PRD-LONG и т.д.)
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

OLD_SNIPPET = (
    'logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", '
    'datefmt="%H:%M:%S")\n'
    'logger = logging.getLogger("BOT")'
)

NEW_SNIPPET = r'''_LOG_FMT = logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")


def _configure_logging() -> None:
    """Console (systemd/journal) + ``bot.log`` in project root — same lines to both."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    log_path = (resolve_bot_dir() / "bot.log").resolve()

    def _has_stderr() -> bool:
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr:
                return True
        return False

    def _has_bot_log_file() -> bool:
        for h in root.handlers:
            if isinstance(h, logging.FileHandler):
                try:
                    if Path(h.baseFilename).resolve() == log_path:
                        return True
                except (OSError, ValueError):
                    pass
        return False

    if not _has_stderr():
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(_LOG_FMT)
        root.addHandler(sh)
    if not _has_bot_log_file():
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(_LOG_FMT)
        root.addHandler(fh)


_configure_logging()
logger = logging.getLogger("BOT")'''


def main() -> int:
    ap = argparse.ArgumentParser(description="Patch bot/trading_bot_imports.py for bot.log file logging.")
    ap.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Корень репозитория (где лежит main.py). По умолчанию: текущая папка.",
    )
    args = ap.parse_args()
    repo = args.repo.resolve()
    target = repo / "bot" / "trading_bot_imports.py"
    if not target.is_file():
        print(f"ERROR: не найден файл: {target}")
        return 1

    text = target.read_text(encoding="utf-8")
    if "_configure_logging" in text and "_has_bot_log_file" in text:
        print(f"OK: уже исправлено, правки не нужны: {target}")
        return 0

    if OLD_SNIPPET not in text:
        print(
            "ERROR: в файле нет ожидаемого фрагмента logging.basicConfig(...).\n"
            "Возможно, файл уже меняли вручную или версия другая. Откройте файл и сравните с репозиторием."
        )
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = target.with_suffix(target.suffix + f".bak-{stamp}")
    shutil.copy2(target, backup)
    print(f"Бэкап: {backup}")

    new_text = text.replace(OLD_SNIPPET, NEW_SNIPPET, 1)
    target.write_text(new_text, encoding="utf-8")
    print(f"OK: записан файл: {target}")
    print("Дальше: sudo systemctl restart trading_bot.service  (и tail -f bot.log)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
