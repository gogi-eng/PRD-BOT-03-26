#!/usr/bin/env python3
"""
Config loader — загрузка config.yaml с удобным доступом.
"""
import yaml
from pathlib import Path
from typing import Any


class BotConfig:
    """Конфигурация бота из YAML."""

    def __init__(self, data: dict):
        self._data = data

    @classmethod
    def load(cls, path: str = "config.yaml") -> "BotConfig":
        p = Path(path)
        if not p.exists():
            print(f"[CONFIG] {path} not found, using defaults")
            return cls({})
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        print(f"[CONFIG] Loaded from {path}")
        return cls(data)

    def get(self, *keys, default: Any = None) -> Any:
        """
        Получить значение по вложенным ключам.
        cfg.get("trading", "leverage", default=10)
        """
        node = self._data
        for key in keys:
            if isinstance(node, dict):
                node = node.get(key)
                if node is None:
                    return default
            else:
                return default
        return node if node is not None else default

    @property
    def raw(self) -> dict:
        return self._data
