"""Операции: AI-менеджер бота, runtime-флаги telegram_signal_agent."""
from prd_agent.ops.bot_manager import BotManagerAgent
from prd_agent.ops.runtime_controls import (
    load_runtime_controls,
    runtime_controls_status_text,
    save_runtime_controls,
    toggle_runtime_flag,
)

__all__ = [
    "BotManagerAgent",
    "load_runtime_controls",
    "save_runtime_controls",
    "toggle_runtime_flag",
    "runtime_controls_status_text",
]
