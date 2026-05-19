"""
Telegram-управление кнопками (python-telegram-bot).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from prd_agent.risk.guard import GuardStatus

if TYPE_CHECKING:
    from prd_agent.engine.orchestrator import UnifiedOrchestrator

logger = logging.getLogger("prd_agent.tg")


class ControlBot:
    def __init__(self, cfg: Dict[str, Any], orchestrator: "UnifiedOrchestrator"):
        self.cfg = cfg
        tg = cfg.get("telegram", {})
        self.token = tg.get("bot_token", "")
        self.allowed: List[int] = [int(x) for x in tg.get("allowed_user_ids", [])]
        self.orch = orchestrator
        self.app: Optional[Application] = None

    def _allowed(self, user_id: Optional[int]) -> bool:
        if not self.allowed:
            return True
        return user_id in self.allowed

    def _main_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("▶️ Старт торговли", callback_data="act:start"),
                    InlineKeyboardButton("⏸ Стоп", callback_data="act:stop"),
                ],
                [
                    InlineKeyboardButton("📊 Статус", callback_data="act:status"),
                    InlineKeyboardButton("📨 Отчёт сейчас", callback_data="act:report"),
                ],
                [
                    InlineKeyboardButton("🛑 Emergency stop", callback_data="act:emergency"),
                    InlineKeyboardButton("♻️ Сброс риска", callback_data="act:reset_risk"),
                ],
                [
                    InlineKeyboardButton("↩️ Откат config", callback_data="act:rollback"),
                ],
            ]
        )

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not self._allowed(update.effective_user.id):
            return
        await update.message.reply_text(
            "PRD Unified Agent — панель управления.\nВыберите действие:",
            reply_markup=self._main_keyboard(),
        )

    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user or not self._allowed(query.from_user.id):
            return
        await query.answer()
        action = (query.data or "").split(":", 1)[-1]
        text = await self._handle_action(action)
        await query.edit_message_text(text, reply_markup=self._main_keyboard())

    async def _handle_action(self, action: str) -> str:
        if action == "start":
            if not self.orch._running:
                asyncio.create_task(self.orch.start())
            return "Торговый цикл запущен."
        if action == "stop":
            self.orch.stop()
            return "Торговый цикл остановлен."
        if action == "emergency":
            self.orch.risk.status = GuardStatus.EMERGENCY
            self.orch.risk.stop_reason = "Manual Telegram"
            self.orch.stop()
            return "EMERGENCY STOP: торговля и цикл остановлены."
        if action == "reset_risk":
            self.orch.risk.status = GuardStatus.ACTIVE
            self.orch.risk.stop_reason = ""
            return "Риск-стоп сброшен."
        if action == "rollback":
            path = self.orch.improver.rollback_last_config()
            self.orch.reload_config()
            return f"Откат config: {path or 'нет резервной копии'}"
        if action == "status":
            snap = self.orch.risk.snapshot()
            bal = await self.orch.exchange.get_balance()
            pos = await self.orch.exchange.get_positions()
            return (
                f"Баланс: {bal:.2f} USDT\n"
                f"Позиций: {len(pos)}\n"
                f"Риск: {snap}\n"
                f"PRD Bybit: {self.orch.exchange.uses_prd_client}"
            )
        if action == "report":
            pos = await self.orch.exchange.get_positions()
            await self.orch._bi_hourly_report(pos)
            return "Отчёт отправлен в канал."
        return "Неизвестная команда."

    async def run_polling(self) -> None:
        if not self.token:
            logger.warning("Telegram bot_token не задан")
            return
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("panel", self.cmd_start))
        self.app.add_handler(CallbackQueryHandler(self.on_button))
        logger.info("Telegram control bot polling...")
        self._stop = asyncio.Event()
        async with self.app:
            await self.app.start()
            await self.app.updater.start_polling(drop_pending_updates=True)
            await self._stop.wait()

    async def stop(self) -> None:
        stop = getattr(self, "_stop", None)
        if stop and not stop.is_set():
            stop.set()
