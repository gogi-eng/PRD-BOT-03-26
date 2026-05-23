"""
Telegram-управление кнопками (python-telegram-bot).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict
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
                    InlineKeyboardButton("📈 Статистика", callback_data="act:stats"),
                ],
                [
                    InlineKeyboardButton("📨 Отчёт сейчас", callback_data="act:report"),
                    InlineKeyboardButton("🧠 Макро", callback_data="act:macro"),
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
        table = await self.orch.build_status_table()
        await update.message.reply_html(
            table + "\n\n<i>Кнопки управления:</i>",
            reply_markup=self._main_keyboard(),
        )

    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user or not self._allowed(query.from_user.id):
            return
        await query.answer()
        action = (query.data or "").split(":", 1)[-1]
        text = await self._handle_action(action)
        html_actions = {"status", "stats", "macro"}
        if action in html_actions:
            await query.edit_message_text(
                text, reply_markup=self._main_keyboard(), parse_mode="HTML"
            )
        else:
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
            self.orch._block_notify_sent = False
            return "Риск-стоп сброшен."
        if action == "rollback":
            path = self.orch.improver.rollback_last_config()
            self.orch.reload_config()
            return f"Откат config: {path or 'нет резервной копии'}"
        if action == "status":
            can_trade, block_reason = self.orch.risk.can_trade()
            pos = await self.orch.exchange.get_positions()
            return await self.orch.build_status_table(
                positions=pos,
                block_reason="" if can_trade else block_reason,
            )
        if action == "report":
            pos = await self.orch.exchange.get_positions()
            await self.orch._bi_hourly_report(pos)
            return "Отчёт отправлен в канал."
        if action == "stats":
            return self.orch.get_trade_stats_report()
        if action == "macro":
            return await self.orch.get_macro_briefing()
        return "Неизвестная команда."

    async def _shutdown_app(self) -> None:
        app = self.app
        if not app:
            return
        try:
            if getattr(app.updater, "running", False):
                await app.updater.stop()
        except Exception as exc:
            logger.warning("TG updater stop: %s", exc)
        try:
            await app.stop()
        except Exception as exc:
            logger.warning("TG app stop: %s", exc)
        try:
            await app.shutdown()
        except Exception as exc:
            logger.warning("TG shutdown: %s", exc)

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

        async def _on_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            if isinstance(context.error, Conflict):
                logger.error(
                    "Telegram Conflict: тот же bot_token уже опрашивается другим процессом. "
                    "Остановите дубликат (pkill -f run_unified) или в config.yaml задайте "
                    "telegram_signal_agent.control_panel_enabled: false"
                )
                self._stop.set()

        self.app.add_error_handler(_on_error)
        try:
            await self.app.initialize()
            await self.app.start()
            try:
                await self.app.bot.delete_webhook(drop_pending_updates=True)
            except Exception as exc:
                logger.warning("delete_webhook: %s", exc)
            await self.app.updater.start_polling(drop_pending_updates=True)
            await self._stop.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown_app()

    async def run_polling_sync(self) -> None:
        """Совместимость со старыми run_unified на сервере."""
        await self.run_polling()

    async def stop(self) -> None:
        stop = getattr(self, "_stop", None)
        if stop and not stop.is_set():
            stop.set()
        await self._shutdown_app()
