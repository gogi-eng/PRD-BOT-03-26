#!/usr/bin/env python3
"""Проверка config.yaml перед запуском бота. Запуск: python3 scripts/validate_config_yaml.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CFG = ROOT / "config.yaml"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEFAULT_CFG))
    args = ap.parse_args()
    cfg_path = Path(args.path)
    if not cfg_path.is_absolute():
        cfg_path = ROOT / cfg_path
    if not cfg_path.exists():
        print(f"Нет файла: {cfg_path}")
        return 1
    try:
        import yaml
    except ImportError:
        print("Установите PyYAML: pip install pyyaml")
        return 1
    try:
        from prd_agent.config_validate import validate_config_data
    except ImportError as exc:
        if "pydantic" in str(exc).lower():
            print("Установите pydantic: ./venv/bin/pip install 'pydantic>=2.6.4'")
            print("  или: ./venv/bin/pip install -r requirements-unified.txt")
            return 1
        raise
    text = cfg_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        print("ОШИБКА в config.yaml:")
        print(exc)
        print("\nЧастые причины:")
        print("  • двоеточие : в тексте без кавычек (оберните в '...')")
        print("  • две настройки в одной строке")
        print("  • табуляция вместо пробелов")
        print("\nПоказать строки 8–18:")
        for i, line in enumerate(text.splitlines(), 1):
            if 8 <= i <= 18:
                print(f"{i:3}: {line}")
        return 1
    ok, errors = validate_config_data(data or {})
    if not ok:
        print("ОШИБКА проверки параметров config.yaml:")
        for err in errors:
            print(f"  • {err}")
        return 1
    print(f"OK: {cfg_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
