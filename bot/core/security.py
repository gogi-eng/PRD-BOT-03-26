#!/usr/bin/env python3
"""
Безопасное хранение ключей из .env.
"""
import os
from dotenv import load_dotenv


class SecureStore:
    """Загружает и маскирует ключи из окружения."""

    REQUIRED_KEYS = ["BYBIT_API_KEY", "BYBIT_API_SECRET"]
    OPTIONAL_KEYS = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "EMERGENT_LLM_KEY"]

    def __init__(self):
        load_dotenv(override=True)

    def get_key(self, name: str) -> str:
        return os.getenv(name, "")

    def validate_bybit_keys(self) -> tuple:
        key = self.get_key("BYBIT_API_KEY")
        secret = self.get_key("BYBIT_API_SECRET")
        if not key or not secret:
            return False, "BYBIT_API_KEY or BYBIT_API_SECRET missing"
        return True, ""

    def validate_telegram_keys(self) -> tuple:
        token = self.get_key("TELEGRAM_TOKEN")
        chat_id = self.get_key("TELEGRAM_CHAT_ID")
        if not token:
            return False, "TELEGRAM_TOKEN missing"
        if not chat_id:
            return False, "TELEGRAM_CHAT_ID missing"
        return True, ""

    def status(self) -> dict:
        result = {}
        for key in self.REQUIRED_KEYS + self.OPTIONAL_KEYS:
            val = self.get_key(key)
            if val:
                result[key] = val[:4] + "..." + val[-4:] if len(val) > 8 else "***"
            else:
                result[key] = "NOT SET"
        return result
