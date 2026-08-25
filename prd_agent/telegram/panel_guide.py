"""Тексты справки для Telegram-панели (для пользователя без программирования)."""
from __future__ import annotations

from typing import Any, Dict

from prd_agent.ops.runtime_controls import load_runtime_controls
from pathlib import Path


def build_panel_help_text(cfg: Dict[str, Any], root: Path) -> str:
    rtc = load_runtime_controls(root)
    signal_only = bool(rtc.get("signal_only_mode", False)) or bool(
        (cfg.get("bot") or {}).get("signal_only", False)
    )
    mode = "SIGNAL-ONLY (без ордеров)" if signal_only else "LIVE (ордера на Bybit)"
    return (
        "<b>📖 Справка — панель PRD-BOT</b>\n\n"
        f"<b>Режим:</b> <code>{mode}</code>\n\n"
        "<b>Каждый день</b>\n"
        "▶️ Старт / ⏸ Стоп — включить или остановить цикл\n"
        "📊 Статус — позиции, баланс, блокировки\n"
        "📈 Статистика — winrate и PnL за сутки\n"
        "🧪 Лаборатория — почему бот пропустил сигналы\n\n"
        "<b>Риск</b>\n"
        "🛡 Консерв — меньше сделок, жёстче фильтры\n"
        "⚖️ Норма — сбалансированный режим\n"
        "🚀 Агресс — больше входов, выше риск\n"
        "🛑 Emergency — немедленная остановка торговли\n"
        "💰 Сбросить убыток — обнулить дневной минус (осторожно)\n\n"
        "<b>Песочница / обучение</b>\n"
        "🔭 Signal-only — только уведомления, без ордеров\n"
        "⏸ Пауза входов — анализ идёт, сделки не открываются\n\n"
        "<b>Защита депозита</b>\n"
        "🛡 Защита ликв. — расстояние до ликвидации по позициям\n"
        "Трейлинг SL — подтягивает стоп в прибыли\n"
        "🖐 Ручные — ВКЛ: бот сопровождает и ручные сделки; ВЫКЛ: только свои\n\n"
        "<i>Команды: /start или /panel — открыть клавиатуру</i>"
    )
