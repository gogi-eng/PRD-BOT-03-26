"""Скрипт снимка сервера не должен оставлять ключи в тексте."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[2] / "scripts" / "dump_agent_world_server.py"
    spec = importlib.util.spec_from_file_location("dump_agent_world_server", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_redact_hides_filled_secrets_and_keeps_empty() -> None:
    mod = _load()
    raw = "\n".join(
        [
            "  api_key: ''",
            "  api_secret: secret-value-123",
            "bot_token: 123456:ABC",
            "TELEGRAM_TOKEN=998877:token",
            "  leverage: 10",
        ]
    ) + "\n"
    text = mod.redact_text(raw)
    assert "secret-value-123" not in text
    assert "123456:ABC" not in text
    assert "998877:token" not in text
    assert "api_key: ''" in text
    assert "leverage: 10" in text
    assert "[скрыто]" in text
