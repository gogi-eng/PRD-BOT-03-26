"""Conflict не должен гасить Telegram-панель (/panel)."""
from __future__ import annotations

from telegram.error import Conflict, NetworkError, TimedOut

from prd_agent.telegram.control_bot import ControlBot


class _FakeOrch:
    root = "."
    position_steward = type("PS", (), {"enabled": True})()


def _bot() -> ControlBot:
    return ControlBot({"telegram": {"bot_token": "1:AAA", "allowed_user_ids": []}}, _FakeOrch())


def test_conflict_does_not_halt_panel():
    bot = _bot()
    assert bot.on_polling_error(Conflict("terminated by other getUpdates")) is False


def test_network_timeout_does_not_halt_panel():
    bot = _bot()
    assert bot.on_polling_error(NetworkError("boom")) is False
    assert bot.on_polling_error(TimedOut("slow")) is False


def test_none_error_safe():
    bot = _bot()
    assert bot.on_polling_error(None) is False
