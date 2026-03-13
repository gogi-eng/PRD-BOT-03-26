#!/usr/bin/env python3
"""
Telegram Controller — управление ботом через Telegram.
Адаптирован под новую чистую архитектуру. Русский язык.
"""
from __future__ import annotations
import asyncio
import logging
import threading
from typing import Optional, TYPE_CHECKING

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler,
    CommandHandler, ContextTypes, MessageHandler, filters,
)

if TYPE_CHECKING:
    from core.live_controls import LiveControls

logger = logging.getLogger(__name__)


class TelegramController:
    """Управление ботом через Telegram с кнопками."""

    def __init__(self, token: str, controls: "LiveControls", *,
                 allowed_chat_id: Optional[int] = None,
                 stop_event: Optional[threading.Event] = None):
        self.token = token
        self.controls = controls
        self.allowed_chat_id = allowed_chat_id
        self.stop_event = stop_event
        self._last_menu_id: dict[int, int] = {}
        self._profit_lock = None

        self.app: Application = ApplicationBuilder().token(token).build()
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("menu", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))
        self.app.add_handler(CommandHandler("balance", self.cmd_balance))
        self.app.add_handler(CommandHandler("profitlock", self.cmd_profitlock))
        self.app.add_handler(CallbackQueryHandler(self.on_button))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        self.app.add_error_handler(self.on_error)

    def set_profit_lock(self, profit_lock):
        self._profit_lock = profit_lock

    def start(self):
        print("[TG] Telegram бот запущен")
        self.app.run_polling(close_loop=False)

    async def start_async(self):
        print("[TG] Telegram бот запускается (async)...")
        await asyncio.sleep(1)
        try:
            await self.app.initialize()
        except Exception as e:
            print(f"[TG] Ошибка инициализации: {e}, повтор...")
            await asyncio.sleep(3)
            await self.app.initialize()
        try:
            await self.app.bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            print(f"[TG] Ошибка вебхука (не критично): {e}")
        await asyncio.sleep(1)
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        print("[TG] Polling активен")

    async def stop_async(self):
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    def _allowed(self, update: Update) -> bool:
        if self.allowed_chat_id is None:
            return True
        chat_id = update.effective_chat.id if update.effective_chat else None
        return chat_id == self.allowed_chat_id

    async def _deny(self, update: Update):
        if update.message:
            await update.message.reply_text("Доступ запрещён")
        elif update.callback_query:
            await update.callback_query.answer("Доступ запрещён", show_alert=True)

    def _build_keyboard(self) -> InlineKeyboardMarkup:
        c = self.controls
        ai_btn = f"AI {'ON' if c.ai_enabled else 'OFF'}"
        rl_btn = f"RL {'ON' if c.rl_enabled else 'OFF'}"

        rows = [
            [
                InlineKeyboardButton("START BOT", callback_data="START_BOT"),
                InlineKeyboardButton("STOP BOT", callback_data="STOP_BOT"),
            ],
            [
                InlineKeyboardButton(f"RISK {c.risk_per_trade_pct:.2f}%", callback_data="INFO_RISK"),
                InlineKeyboardButton("-", callback_data="RISK_DOWN"),
                InlineKeyboardButton("+", callback_data="RISK_UP"),
            ],
            [
                InlineKeyboardButton(ai_btn, callback_data="TOGGLE_AI"),
                InlineKeyboardButton(rl_btn, callback_data="TOGGLE_RL"),
            ],
            [
                InlineKeyboardButton("VIEW HEATMAP", callback_data="SHOW_HEATMAP"),
                InlineKeyboardButton("VIEW POSITIONS", callback_data="SHOW_POSITIONS"),
            ],
            [
                InlineKeyboardButton("Баланс", callback_data="SHOW_BALANCE"),
                InlineKeyboardButton("Статистика", callback_data="SHOW_STATS"),
            ],
            [
                InlineKeyboardButton("Панель PnL", callback_data="SHOW_PNL"),
                InlineKeyboardButton("Profit Lock", callback_data="SHOW_PROFITLOCK"),
            ],
            [
                InlineKeyboardButton("Сброс Guard", callback_data="RESET_GUARD"),
            ],
            [
                InlineKeyboardButton("АВАРИЙНАЯ ОСТАНОВКА", callback_data="EMERGENCY"),
            ],
            [
                InlineKeyboardButton("Обновить", callback_data="REFRESH"),
            ],
        ]
        return InlineKeyboardMarkup(rows)

    def _menu_text(self) -> str:
        c = self.controls
        if c.emergency:
            status = "АВАРИЙНАЯ ОСТАНОВКА"
        elif c.enabled:
            status = "АКТИВЕН"
        else:
            status = "ПАУЗА"

        mode = "ТЕСТ" if c.dry_run else "LIVE"

        lines = [
            "<b>ТОРГОВЫЙ БОТ v8.0</b>",
            "<i>Чистая Архитектура</i>",
            "",
            f"Статус: <b>{status}</b>",
            f"Режим: {mode}",
            "Стратегия: Transformer + Heatmap + Orderflow",
            f"AI: <code>{'ON' if c.ai_enabled else 'OFF'}</code> | RL: <code>{'ON' if c.rl_enabled else 'OFF'}</code>",
            "",
            "<b>ПАРАМЕТРЫ</b>",
            f"Плечо: <code>{c.leverage}x</code>",
            f"Маржа: <code>{c.margin_total_pct:.1f}%</code>",
            f"Риск на сделку: <code>{c.risk_per_trade_pct:.2f}%</code>",
            f"Трейлинг: <code>{c.trailing_stop_pct:.1f}%</code>",
            f"Макс. позиций: <code>{c.max_positions}</code>",
        ]

        try:
            snap = c.guard_snapshot()
            if snap:
                lines.append("")
                lines.append("<b>РИСК GUARD</b>")
                lines.append(f"PnL сегодня: <code>${snap['pnl_today']:.2f}</code>")
                lines.append(f"Сделок: <code>{snap['trades_today']}</code>")
                if snap.get("blocked"):
                    reason = snap.get("block_reason", "")
                    lines.append(f"<b>ЗАБЛОКИРОВАН</b>: {reason}")
        except Exception:
            pass

        return "\n".join(lines)

    async def _send_or_edit(self, update: Update, text: str, reply_markup=None):
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None:
            return
        if update.callback_query and update.callback_query.message:
            try:
                await update.callback_query.message.edit_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
                return
            except Exception:
                pass
        msg = await self.app.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        self._last_menu_id[chat_id] = msg.message_id

    async def send_message(self, text: str, parse_mode: str = None):
        if self.allowed_chat_id and self.app and self.app.bot:
            try:
                mode = ParseMode.HTML if parse_mode in [None, "HTML"] else parse_mode
                await self.app.bot.send_message(chat_id=self.allowed_chat_id, text=text, parse_mode=mode)
            except Exception as e:
                logger.error(f"Ошибка отправки: {e}")

    async def render_menu(self, update: Update):
        await self._send_or_edit(update, self._menu_text(), reply_markup=self._build_keyboard())

    # === Команды ===

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return await self._deny(update)
        await self.render_menu(update)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return await self._deny(update)
        help_text = (
            "<b>ПОМОЩЬ</b>\n\n"
            "/start - Главное меню\n"
            "/stats - Статистика\n"
            "/balance - Баланс\n"
            "/profitlock - Статус Portfolio Profit Lock\n"
            "/help - Эта справка\n\n"
            "<b>Архитектура:</b>\n"
            "Data Layer -> Features -> Transformer -> Entry -> RL -> Execution\n\n"
            "<b>Стратегия:</b> Transformer + Liquidation Heatmap + Orderflow\n"
            "1 Entry Engine | 1 Capital Allocator | 1 RL Agent\n\n"
            "<b>Profit Lock:</b>\n"
            "Если общая прибыль >= 5% депо — защита активна.\n"
            "Снижение на 20% от пика 5 мин подряд — закрывает всё."
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return await self._deny(update)
        await update.message.reply_text(self.controls.stats(), parse_mode=ParseMode.HTML)

    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return await self._deny(update)
        balance = self.controls.get_balance()
        await update.message.reply_text(f"<b>БАЛАНС</b>\n\nДоступно: <code>${balance:.2f}</code>", parse_mode=ParseMode.HTML)

    async def cmd_profitlock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return await self._deny(update)
        if self._profit_lock:
            await update.message.reply_text(self._profit_lock.get_report(), parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("Profit Lock не подключён", parse_mode=ParseMode.HTML)

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return await self._deny(update)
        await self.render_menu(update)

    # === Кнопки ===

    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._allowed(update):
            return await self._deny(update)

        query = update.callback_query
        await query.answer()
        action = query.data
        c = self.controls

        if action == "START_BOT":
            c.emergency = False
            c.enabled = True
        elif action == "STOP_BOT":
            c.enabled = False
        elif action == "RISK_UP":
            c.risk_per_trade_pct = min(2.0, c.risk_per_trade_pct + 0.1)
        elif action == "RISK_DOWN":
            c.risk_per_trade_pct = max(0.1, c.risk_per_trade_pct - 0.1)
        elif action == "TOGGLE_AI":
            c.ai_enabled = not c.ai_enabled
        elif action == "TOGGLE_RL":
            c.rl_enabled = not c.rl_enabled
        elif action == "SHOW_BALANCE":
            balance = c.get_balance()
            await query.message.reply_text(f"<b>БАЛАНС</b>\n\n<code>${balance:.2f}</code>", parse_mode=ParseMode.HTML)
            return
        elif action == "SHOW_STATS":
            await query.message.reply_text(c.stats(), parse_mode=ParseMode.HTML)
            return
        elif action == "SHOW_HEATMAP":
            await query.message.reply_text(c.heatmap_report(), parse_mode=ParseMode.HTML)
            return
        elif action == "SHOW_POSITIONS":
            await query.message.reply_text(c.positions_report(), parse_mode=ParseMode.HTML)
            return
        elif action == "SHOW_PNL":
            await query.message.reply_text(c.pnl_report(), parse_mode=ParseMode.HTML)
            return
        elif action == "SHOW_PROFITLOCK":
            if self._profit_lock:
                await query.message.reply_text(self._profit_lock.get_report(), parse_mode=ParseMode.HTML)
            else:
                await query.message.reply_text("Profit Lock не подключён", parse_mode=ParseMode.HTML)
            return
        elif action == "RESET_GUARD":
            if c._guard:
                c._guard.reset_guard()
                await query.message.reply_text("<b>Guard сброшен!</b>\nСерия убытков обнулена.", parse_mode=ParseMode.HTML)
            else:
                await query.message.reply_text("Guard не подключён", parse_mode=ParseMode.HTML)
        elif action == "EMERGENCY":
            c.enabled = False
            c.emergency = True
            if c._guard:
                c._guard.emergency_stop()
            await query.message.reply_text("<b>АВАРИЙНАЯ ОСТАНОВКА!</b>\nТорговля остановлена.", parse_mode=ParseMode.HTML)
        elif action == "REFRESH":
            pass

        await self.render_menu(update)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"[TG ОШИБКА] {context.error}")

    # === Уведомления ===

    async def send_trade_notification(self, symbol: str, side: str, qty: float, price: float,
                                      pnl: Optional[float] = None, is_open: bool = True, reason: str = ""):
        if not self.allowed_chat_id:
            return
        try:
            if is_open:
                direction = "ЛОНГ" if side.upper() in ["BUY", "LONG"] else "ШОРТ"
                text = (
                    f"<b>НОВАЯ СДЕЛКА</b>\n\n"
                    f"Монета: <code>{symbol}</code>\n"
                    f"Направление: <b>{direction}</b>\n"
                    f"Объём: <code>{qty}</code>\n"
                    f"Цена: <code>${price:.4f}</code>"
                )
                if reason:
                    text += f"\n\nПричина:\n{reason}"
            else:
                if pnl is not None and pnl >= 0:
                    result_str = f"+${pnl:.2f}"
                elif pnl is not None:
                    result_str = f"${pnl:.2f}"
                else:
                    result_str = ""
                text = (
                    f"<b>СДЕЛКА ЗАКРЫТА</b>\n\n"
                    f"Монета: <code>{symbol}</code>\n"
                    f"Цена: <code>${price:.4f}</code>\n"
                    f"Результат: {result_str}"
                )
            await self.app.bot.send_message(chat_id=self.allowed_chat_id, text=text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Ошибка уведомления: {e}")

    async def send_alert(self, message: str):
        if not self.allowed_chat_id:
            return
        try:
            await self.app.bot.send_message(chat_id=self.allowed_chat_id, text=f"ОПОВЕЩЕНИЕ: {message}", parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Ошибка алерта: {e}")
