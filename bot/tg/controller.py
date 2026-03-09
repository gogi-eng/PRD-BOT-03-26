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

        self.app: Application = ApplicationBuilder().token(token).build()
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("menu", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))
        self.app.add_handler(CommandHandler("balance", self.cmd_balance))
        self.app.add_handler(CallbackQueryHandler(self.on_button))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        self.app.add_error_handler(self.on_error)

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
        enabled_btn = "АКТИВЕН" if c.enabled else "ПАУЗА"
        mode_btn = "ТЕСТ" if c.dry_run else "LIVE"

        rows = [
            [
                InlineKeyboardButton(enabled_btn, callback_data="TOGGLE_ENABLED"),
                InlineKeyboardButton(mode_btn, callback_data="TOGGLE_DRY"),
            ],
            [
                InlineKeyboardButton(f"Плечо: {c.leverage}x", callback_data="INFO_LEV"),
                InlineKeyboardButton("-", callback_data="LEV_DOWN"),
                InlineKeyboardButton("+", callback_data="LEV_UP"),
            ],
            [
                InlineKeyboardButton(f"Маржа: {c.margin_total_pct:.0f}%", callback_data="INFO_MARGIN"),
                InlineKeyboardButton("-", callback_data="MARGIN_DOWN"),
                InlineKeyboardButton("+", callback_data="MARGIN_UP"),
            ],
            [
                InlineKeyboardButton(f"Трейл: {c.trailing_stop_pct:.1f}%", callback_data="INFO_TRAIL"),
                InlineKeyboardButton("-", callback_data="TRAIL_DOWN"),
                InlineKeyboardButton("+", callback_data="TRAIL_UP"),
            ],
            [
                InlineKeyboardButton(f"ТП: {c.tp_pct:.1f}%", callback_data="INFO_TP"),
                InlineKeyboardButton(f"СЛ: {c.sl_pct:.1f}%", callback_data="INFO_SL"),
            ],
            [
                InlineKeyboardButton("Баланс", callback_data="SHOW_BALANCE"),
                InlineKeyboardButton("Статистика", callback_data="SHOW_STATS"),
            ],
            [
                InlineKeyboardButton("Панель PnL", callback_data="SHOW_PNL"),
            ],
            [
                InlineKeyboardButton("Сброс Guard", callback_data="RESET_GUARD"),
            ],
            [
                InlineKeyboardButton("АВАРИЙНАЯ ОСТАНОВКА", callback_data="EMERGENCY"),
            ],
            [
                InlineKeyboardButton("Возобновить торговлю", callback_data="RESUME"),
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
            "Стратегия: Тренд + Откат + Ликвидити Свип",
            "",
            "<b>ПАРАМЕТРЫ</b>",
            f"Плечо: <code>{c.leverage}x</code>",
            f"Маржа: <code>{c.margin_total_pct:.1f}%</code>",
            f"Трейлинг: <code>{c.trailing_stop_pct:.1f}%</code>",
            f"ТП: <code>{c.tp_pct:.1f}%</code>",
            f"СЛ: <code>{c.sl_pct:.1f}%</code>",
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
            "/help - Эта справка\n\n"
            "<b>Архитектура:</b>\n"
            "Анализ рынка -> Вход -> Риск -> Исполнение -> Выход\n\n"
            "<b>Стратегия:</b> Тренд + Откат + Ликвидити Свип\n"
            "1 Entry Engine | 1 Risk Manager | 1 Exit Engine"
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

        if action == "TOGGLE_ENABLED":
            c.enabled = not c.enabled
        elif action == "TOGGLE_DRY":
            c.dry_run = not c.dry_run
        elif action == "LEV_UP":
            c.leverage = min(50, c.leverage + 5)
        elif action == "LEV_DOWN":
            c.leverage = max(1, c.leverage - 5)
        elif action == "MARGIN_UP":
            c.margin_total_pct = min(100, c.margin_total_pct + 5)
        elif action == "MARGIN_DOWN":
            c.margin_total_pct = max(1, c.margin_total_pct - 5)
        elif action == "TRAIL_UP":
            c.trailing_stop_pct = min(10.0, c.trailing_stop_pct + 0.5)
        elif action == "TRAIL_DOWN":
            c.trailing_stop_pct = max(0.5, c.trailing_stop_pct - 0.5)
        elif action == "SHOW_BALANCE":
            balance = c.get_balance()
            await query.message.reply_text(f"<b>БАЛАНС</b>\n\n<code>${balance:.2f}</code>", parse_mode=ParseMode.HTML)
            return
        elif action == "SHOW_STATS":
            await query.message.reply_text(c.stats(), parse_mode=ParseMode.HTML)
            return
        elif action == "SHOW_PNL":
            await query.message.reply_text(c.pnl_report(), parse_mode=ParseMode.HTML)
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
        elif action == "RESUME":
            c.emergency = False
            c.enabled = True
            if c._guard:
                c._guard.resume()
            await query.message.reply_text("<b>Торговля возобновлена</b>", parse_mode=ParseMode.HTML)
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
                if pnl and pnl >= 0:
                    result_str = f"+${pnl:.2f}"
                elif pnl:
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
