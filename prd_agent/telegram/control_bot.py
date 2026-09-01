"""
Telegram-управление кнопками (python-telegram-bot).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict, NetworkError, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from prd_agent.ops.runtime_controls import (
    effective_trailing_enabled,
    load_runtime_controls,
    runtime_controls_status_text,
    toggle_runtime_flag,
)
from prd_agent.ops.log_redact import apply_log_safety
from prd_agent.telegram.panel_guide import build_panel_help_text
from prd_agent.risk.guard import GuardStatus, StopKind

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
        self._tg_shutdown_done = False
        self._stop: Optional[asyncio.Event] = None

    def _allowed(self, user_id: Optional[int]) -> bool:
        if not self.allowed:
            return True
        return user_id in self.allowed

    def _trailing_button(self) -> InlineKeyboardButton:
        if effective_trailing_enabled(self.orch.cfg, self.orch.root):
            return InlineKeyboardButton(
                "🚫 Отключить трейлинг", callback_data="act:trailing_off"
            )
        return InlineKeyboardButton(
            "✅ Включить трейлинг", callback_data="act:trailing_on"
        )

    def _adopt_manual_button(self) -> InlineKeyboardButton:
        if self.orch.position_steward.adopt_manual:
            return InlineKeyboardButton(
                "🖐 Ручные: ВКЛ", callback_data="act:adopt_manual_off"
            )
        return InlineKeyboardButton(
            "🖐 Ручные: ВЫКЛ", callback_data="act:adopt_manual_on"
        )

    def _runtime_button_labels(self) -> tuple[str, str, str, str]:
        rtc = load_runtime_controls(self.orch.root)
        ch = "ВКЛ" if rtc.get("channel_auto_execute") else "ВЫКЛ"
        sc = "ВКЛ" if rtc.get("market_scanner_auto_execute") else "ВЫКЛ"
        pause = "ВКЛ" if rtc.get("pause_all_execution") else "ВЫКЛ"
        sig = "ВКЛ" if rtc.get("signal_only_mode") else "ВЫКЛ"
        return (
            f"📣 Каналы auto: {ch}",
            f"📡 Сканер auto: {sc}",
            f"⏸ Пауза входов: {pause}",
            f"🔭 Signal-only: {sig}",
        )

    def _main_keyboard(self) -> InlineKeyboardMarkup:
        ch_lbl, sc_lbl, pause_lbl, sig_lbl = self._runtime_button_labels()
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
                    InlineKeyboardButton("📖 Справка", callback_data="act:help"),
                    InlineKeyboardButton("🛡 Ликвидация", callback_data="act:liq_guard"),
                ],
                [
                    InlineKeyboardButton(
                        "📊 Качество сделок", callback_data="act:portfolio_quality"
                    ),
                ],
                [
                    InlineKeyboardButton("📅 По дням", callback_data="act:daily_pnl"),
                    InlineKeyboardButton("🧪 Лаборатория", callback_data="act:skipped_lab"),
                ],
                [
                    InlineKeyboardButton("📐 GARCH правила", callback_data="act:garch_rules"),
                ],
                [
                    InlineKeyboardButton(ch_lbl, callback_data="act:toggle_channel"),
                    InlineKeyboardButton(sc_lbl, callback_data="act:toggle_scanner"),
                ],
                [
                    InlineKeyboardButton(pause_lbl, callback_data="act:toggle_pause"),
                    InlineKeyboardButton(sig_lbl, callback_data="act:toggle_signal_only"),
                ],
                [
                    InlineKeyboardButton("🤖 Совет менеджера", callback_data="act:bot_manager"),
                    InlineKeyboardButton("📨 Отчёт сейчас", callback_data="act:report"),
                ],
                [
                    InlineKeyboardButton("📉 TA-скан", callback_data="act:ta_scan"),
                    InlineKeyboardButton("🧠 Макро", callback_data="act:macro"),
                ],
                [
                    InlineKeyboardButton("📡 Bybit AI", callback_data="act:bybit_monitor"),
                ],
                [
                    self._trailing_button(),
                ],
                [
                    self._adopt_manual_button(),
                ],
                [
                    InlineKeyboardButton("🛑 Emergency stop", callback_data="act:emergency"),
                    InlineKeyboardButton("♻️ Сброс риска", callback_data="act:reset_risk"),
                ],
                [
                    InlineKeyboardButton(
                        "💰 Сбросить убыток", callback_data="act:reset_daily_loss"
                    ),
                ],
                [
                    InlineKeyboardButton("🛡 Консерв", callback_data="act:preset_conservative"),
                    InlineKeyboardButton("⚖️ Норма", callback_data="act:preset_normal"),
                    InlineKeyboardButton("🚀 Агресс", callback_data="act:preset_aggressive"),
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
        flags = runtime_controls_status_text(self.orch.root)
        await update.message.reply_html(
            table
            + "\n\n<b>Панель управления ботом</b>\n"
            + flags
            + "\n\n<i>🤖 Bot Manager — советы по управлению (не торгует сам).</i>",
            reply_markup=self._main_keyboard(),
        )

    def _app_ready(self) -> bool:
        app = self.app
        if not app:
            return False
        return bool(getattr(app, "running", False))

    async def _safe_edit(
        self, query, text: str, *, html: bool = False
    ) -> None:
        if not self._app_ready():
            logger.warning("TG edit пропущен: Application не запущен")
            return
        body = (text or "")[:4090]
        kwargs: Dict[str, Any] = {"reply_markup": self._main_keyboard()}
        if html:
            kwargs["parse_mode"] = "HTML"
        try:
            await query.edit_message_text(body, **kwargs)
        except (NetworkError, TimedOut) as exc:
            logger.warning("TG edit_message (сеть): %s", exc)
        except Exception as exc:
            logger.warning("TG edit_message: %s", exc)

    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.from_user or not self._allowed(query.from_user.id):
            return
        action = (query.data or "").split(":", 1)[-1]
        html_actions = {
            "status",
            "stats",
            "portfolio_quality",
            "daily_pnl",
            "skipped_lab",
            "garch_rules",
            "hermes",
            "macro",
            "ta_scan",
            "bybit_monitor",
            "bot_manager",
            "panel_flags",
            "help",
            "liq_guard",
            "preset_conservative",
            "preset_normal",
            "preset_aggressive",
        }
        try:
            if action == "bot_manager":
                await query.answer("🤖 Менеджер анализирует…")
                await self._safe_edit(
                    query,
                    "⏳ <b>Bot Manager</b>\n\nЧитаю логи, позиции и риск…",
                    html=True,
                )
                text = await self.orch.get_bot_manager_review()
                await self._safe_edit(query, text, html=True)
                return
            if action in ("toggle_channel", "toggle_scanner", "toggle_pause", "toggle_signal_only"):
                key_map = {
                    "toggle_channel": "channel_auto_execute",
                    "toggle_scanner": "market_scanner_auto_execute",
                    "toggle_pause": "pause_all_execution",
                    "toggle_signal_only": "signal_only_mode",
                }
                new_val, _ = toggle_runtime_flag(self.orch.root, key_map[action])
                await query.answer("Переключено")
                flags = runtime_controls_status_text(self.orch.root)
                await self._safe_edit(
                    query,
                    f"<b>Панель агента</b>\n\n{flags}\n\n<i>Флаги сохранены в state JSON.</i>",
                    html=True,
                )
                return
            if action == "bybit_monitor":
                await query.answer("📡 Bybit AI")
                await self._safe_edit(
                    query,
                    "⏳ <b>Bybit AI</b>\n\nЧитаю позиции и графики (read-only)…",
                    html=True,
                )
                text = await self.orch.get_bybit_monitor_report()
                await self._safe_edit(query, text, html=True)
                return
            if action == "ta_scan":
                await query.answer("📉 TA-скан")
                cache_age = self.orch.ta_cache_age_sec()
                if cache_age < 120:
                    text = await self.orch.get_ta_scan_report(prefer_cache=True)
                    await self._safe_edit(query, text, html=True)
                    return
                await self._safe_edit(
                    query,
                    "⏳ <b>TA-скан</b>\n\nПервый запуск — читаю графики (до ~30 сек)…",
                    html=True,
                )
                text = await self.orch.get_ta_scan_report(force=True)
                await self._safe_edit(query, text, html=True)
                return
            await query.answer()
            text = await self._handle_action(action)
            html_reply = (
                action in html_actions
                or action.startswith("trailing_")
                or action.startswith("adopt_manual_")
            )
            await self._safe_edit(query, text, html=html_reply)
        except Exception as exc:
            logger.exception("on_button %s: %s", action, exc)
            await self._safe_edit(
                query,
                f"⚠️ Ошибка кнопки <b>{action}</b>\n\n<code>{str(exc)[:500]}</code>",
                html=True,
            )

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
            return self.orch.reset_risk_stops()
        if action == "reset_daily_loss":
            return self.orch.reset_daily_loss()
        if action == "rollback":
            path = self.orch.improver.rollback_last_config()
            self.orch.reload_config()
            return f"Откат config: {path or 'нет резервной копии'}"
        if action == "preset_conservative":
            return self.orch.apply_risk_preset("conservative")
        if action == "preset_normal":
            return self.orch.apply_risk_preset("normal")
        if action == "preset_aggressive":
            return self.orch.apply_risk_preset("aggressive")
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
        if action == "portfolio_quality":
            return self.orch.get_portfolio_quality_report()
        if action == "daily_pnl":
            return self.orch.get_daily_pnl_report()
        if action == "skipped_lab":
            return self.orch.get_skipped_lab_report()
        if action == "garch_rules":
            return self.orch.get_manual_trailing_garch_report()
        if action == "hermes":
            return (
                "<b>📊 Hermes отключён</b>\n\n"
                "Советы Hermes больше не используются.\n"
                "Смотрите <b>📅 По дням</b> и <b>🧪 Лаборатория</b>."
            )
        if action == "help":
            return build_panel_help_text(self.cfg, self.orch.root)
        if action == "liq_guard":
            return await self.orch.get_liquidation_safety_report()
        if action == "macro":
            return await self.orch.get_macro_briefing()
        if action == "bybit_monitor":
            return await self.orch.get_bybit_monitor_report()
        if action == "ta_scan":
            return await self.orch.get_ta_scan_report(prefer_cache=True)
        if action == "trailing_off":
            return self.orch.set_trailing_enabled(False)
        if action == "trailing_on":
            return self.orch.set_trailing_enabled(True)
        if action == "adopt_manual_off":
            return self.orch.set_adopt_manual(False)
        if action == "adopt_manual_on":
            return self.orch.set_adopt_manual(True)
        if action == "bot_manager":
            return await self.orch.get_bot_manager_review()
        if action == "toggle_channel":
            toggle_runtime_flag(self.orch.root, "channel_auto_execute")
            return runtime_controls_status_text(self.orch.root)
        if action == "toggle_scanner":
            toggle_runtime_flag(self.orch.root, "market_scanner_auto_execute")
            return runtime_controls_status_text(self.orch.root)
        if action == "toggle_pause":
            toggle_runtime_flag(self.orch.root, "pause_all_execution")
            return runtime_controls_status_text(self.orch.root)
        if action == "toggle_signal_only":
            toggle_runtime_flag(self.orch.root, "signal_only_mode")
            on = load_runtime_controls(self.orch.root).get("signal_only_mode")
            state = "ВКЛ — ордера не отправляются" if on else "ВЫКЛ — live ордера"
            return f"<b>Signal-only</b>\n{state}\n\n{runtime_controls_status_text(self.orch.root)}"
        return "Неизвестная команда."

    async def _shutdown_app(self) -> None:
        if self._tg_shutdown_done:
            return
        app = self.app
        if not app:
            return
        self._tg_shutdown_done = True
        try:
            if getattr(app.updater, "running", False):
                await app.updater.stop()
        except Exception as exc:
            logger.warning("TG updater stop: %s", exc)
        if getattr(app, "running", False):
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
        self._tg_shutdown_done = False
        apply_log_safety()
        self.app = Application.builder().token(self.token).build()
        apply_log_safety()
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
            apply_log_safety()
            await self.app.start()
            apply_log_safety()
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
        """Остановить polling; shutdown выполняется в finally run_polling()."""
        if self._stop and not self._stop.is_set():
            self._stop.set()
