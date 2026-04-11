#!/usr/bin/env python3
"""Standalone Telegram bot for paid signal distribution."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

try:
    from .signal_sales_store import SignalPayload as StoreSignalPayload, SignalSalesStore, parse_iso
except ImportError:  # pragma: no cover - script mode
    from signal_sales_store import SignalPayload as StoreSignalPayload, SignalSalesStore, parse_iso

UTC = timezone.utc


def _escape_html(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@dataclass
class SignalPayload:
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    exchange: str
    signal_ts: datetime
    source: str = "main-bot"
    leverage: str = "10x"
    order_type: str = "Limit"
    rr_ratio: float = 0.0
    chart_url: str = ""
    subscribe_url: str = ""
    levels_note: str = ""


def format_signal_message(payload: SignalPayload, warning_text: str) -> str:
    ts = payload.signal_ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    side_upper = payload.side.upper()
    direction_icon = "📈" if side_upper in {"BUY", "LONG"} else "📉"
    order_type = (payload.order_type or "Limit").strip().title()
    chart_line = ""
    if payload.chart_url:
        chart_line = f"\n<b>📊 График:</b> <a href=\"{_escape_html(payload.chart_url)}\">TradingView</a>"
    subscribe_line = ""
    if payload.subscribe_url:
        subscribe_line = (
            f"\n<b>🔔 Подписка на сигналы:</b> "
            f"<a href=\"{_escape_html(payload.subscribe_url)}\">Оформить подписку</a>"
        )
    levels_line = ""
    if payload.levels_note:
        levels_line = f"\n<b>🧩 Уровни:</b> <code>{_escape_html(payload.levels_note)}</code>"
    rr_text = f"{payload.rr_ratio:.2f}" if payload.rr_ratio > 0 else "N/A"
    return (
        "🚨🚨🚨 <b>ПЛАТНЫЙ ТОРГОВЫЙ СИГНАЛ</b> 🚨🚨🚨\n\n"
        f"<blockquote><b>{direction_icon} ПАРА: { _escape_html(payload.symbol.upper()) } ({_escape_html(side_upper)})</b></blockquote>\n"
        f"<blockquote><b>✅ ТОЧКА ВХОДА: {payload.entry:.6f}</b></blockquote>\n"
        f"<blockquote><b>🛑 СТОП-ЛОСС: {payload.stop_loss:.6f}</b></blockquote>\n"
        f"<blockquote><b>🎯 ТЕЙК-ПРОФИТ: {payload.take_profit:.6f}</b></blockquote>\n\n"
        f"<b>⚙️ Плечо:</b> <code>{_escape_html(payload.leverage)}</code>\n"
        f"<b>📐 R:R:</b> <code>1:{rr_text}</code>\n"
        f"<b>🧾 Тип ордера:</b> <code>{_escape_html(order_type)}</code>{chart_line}{levels_line}{subscribe_line}\n"
        f"<b>🗓 Дата/время сигнала:</b> <code>{ts}</code>\n"
        f"<b>🏦 Биржа:</b> <code>{_escape_html(payload.exchange)}</code>\n\n"
        "⚠️ <b>ПРЕДУПРЕЖДЕНИЕ О РИСКАХ</b>\n"
        f"{_escape_html(warning_text)}"
    )


class PaidSignalsBot:
    def __init__(self):
        load_dotenv(override=False)
        self.logger = logging.getLogger("signal-sales-bot")
        self.token = os.getenv("SIGNAL_SALES_TELEGRAM_TOKEN", "").strip()
        if not self.token:
            raise RuntimeError("SIGNAL_SALES_TELEGRAM_TOKEN is required")

        self.admin_chat_id = int(os.getenv("SIGNAL_SALES_ADMIN_CHAT_ID", "0") or 0)
        if self.admin_chat_id <= 0:
            raise RuntimeError("SIGNAL_SALES_ADMIN_CHAT_ID is required")

        self.subscription_days = int(os.getenv("SIGNAL_SALES_SUBSCRIPTION_DAYS", "30") or 30)
        self.subscription_price_usdt = float(os.getenv("SIGNAL_SALES_PRICE_USDT", "79") or 79)
        self.exchange_label = os.getenv("SIGNAL_SALES_EXCHANGE", "BYBIT").strip() or "BYBIT"
        self.payment_network = os.getenv("SIGNAL_SALES_NETWORK", "TRC20").strip() or "TRC20"
        self.bybit_wallet = os.getenv("SIGNAL_SALES_BYBIT_WALLET", "").strip()
        self.support_contact = os.getenv("SIGNAL_SALES_SUPPORT_CONTACT", "@support").strip() or "@support"
        self.db_path = os.getenv("SIGNAL_SALES_DB_PATH", "bot/signal_sales.db").strip() or "bot/signal_sales.db"
        self.warning_text = (
            os.getenv("SIGNAL_SALES_RISK_WARNING")
            or "Финансовые рынки несут риск потери капитала. Каждый трейдер принимает решения самостоятельно и несет личную ответственность."
        )
        self.delivery_poll_sec = max(2, int(os.getenv("SIGNAL_SALES_DELIVERY_POLL_SEC", "8") or 8))
        self.broadcast_enabled = os.getenv("SIGNAL_SALES_BROADCAST_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.default_leverage = os.getenv("SIGNAL_SALES_DEFAULT_LEVERAGE", "10x").strip() or "10x"
        self.default_order_type = os.getenv("SIGNAL_SALES_DEFAULT_ORDER_TYPE", "Limit").strip() or "Limit"
        self.tv_exchange_prefix = os.getenv("SIGNAL_SALES_TV_EXCHANGE_PREFIX", "BYBIT").strip() or "BYBIT"
        self.tv_layout_id = os.getenv("SIGNAL_SALES_TV_LAYOUT_ID", "").strip()
        self.tv_interval = os.getenv("SIGNAL_SALES_TV_INTERVAL", "5").strip() or "5"
        self.subscribe_url = os.getenv("SIGNAL_SALES_SUBSCRIBE_URL", "").strip()

        self.store = SignalSalesStore(self.db_path)
        self.app: Application = ApplicationBuilder().token(self.token).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("plans", self.cmd_plans))
        self.app.add_handler(CommandHandler("pay", self.cmd_pay))
        self.app.add_handler(CommandHandler("tx", self.cmd_tx))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("broadcast_signal", self.cmd_broadcast_signal))
        self.app.add_handler(CommandHandler("set_result", self.cmd_set_result))
        self.app.add_handler(CommandHandler("weekly_report", self.cmd_weekly_report))
        self.app.add_handler(CommandHandler("approve", self.cmd_approve))
        self.app.add_handler(CommandHandler("reject", self.cmd_reject))
        self.app.add_handler(CommandHandler("list_pending", self.cmd_list_pending))
        self.app.add_error_handler(self.on_error)

    @staticmethod
    def _is_admin(user_id: int, admin_id: int) -> bool:
        return int(user_id or 0) == int(admin_id or -1)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat:
            return
        chat_id = update.effective_chat.id
        self.store.upsert_subscriber(
            chat_id=chat_id,
            username=(update.effective_user.username if update.effective_user else None),
            first_name=(update.effective_user.first_name if update.effective_user else None),
        )
        await update.effective_message.reply_text(
            (
                "<b>Signal Sales Bot</b>\n\n"
                "Платные сигналы от основного торгового алгоритма.\n\n"
                "/plans — условия подписки\n"
                "/pay — реквизиты оплаты\n"
                "/tx &lt;hash&gt; [сумма] [coin] — отправить TX hash\n"
                "/status — статус подписки\n"
                "/help — помощь"
            ),
            parse_mode=ParseMode.HTML,
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(
            (
                "<b>Как подключиться</b>\n\n"
                "1) Оплатите подписку через /pay\n"
                "2) Отправьте TX hash через /tx\n"
                "3) Дождитесь подтверждения администратора\n"
                "4) Получайте сигналы автоматически\n\n"
                f"Поддержка: {_escape_html(self.support_contact)}"
            ),
            parse_mode=ParseMode.HTML,
        )

    async def cmd_plans(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(
            (
                "<b>Тариф сигнального канала</b>\n\n"
                f"Цена: <b>{self.subscription_price_usdt:.2f} USDT</b>\n"
                f"Срок: <b>{self.subscription_days} дней</b>\n"
                f"Сеть оплаты: <b>{_escape_html(self.payment_network)}</b>\n"
                f"Биржа-кошелек: <b>{_escape_html(self.exchange_label)}</b>"
            ),
            parse_mode=ParseMode.HTML,
        )

    async def cmd_pay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.bybit_wallet:
            await update.effective_message.reply_text("Реквизиты временно недоступны, обратитесь в поддержку.")
            return
        await update.effective_message.reply_text(
            (
                "<b>Оплата подписки</b>\n\n"
                f"Сумма: <b>{self.subscription_price_usdt:.2f} USDT</b>\n"
                f"Сеть: <b>{_escape_html(self.payment_network)}</b>\n"
                f"BYBIT кошелек: <code>{_escape_html(self.bybit_wallet)}</code>\n\n"
                "После оплаты отправьте:\n"
                "<code>/tx 0xВАШ_ХЕШ [сумма] [coin]</code>"
            ),
            parse_mode=ParseMode.HTML,
        )

    async def cmd_tx(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat:
            return
        args = context.args or []
        if not args:
            await update.effective_message.reply_text("Формат: /tx <tx_hash> [amount] [coin]")
            return
        tx_hash = args[0].strip()
        amount = self.subscription_price_usdt
        coin = "USDT"
        if len(args) >= 2:
            try:
                amount = float(args[1])
            except ValueError:
                pass
        if len(args) >= 3:
            coin = args[2].strip().upper()

        chat_id = update.effective_chat.id
        username = update.effective_user.username if update.effective_user else None
        self.store.upsert_subscriber(chat_id=chat_id, username=username)
        payment_id = self.store.create_payment_request(
            chat_id=chat_id,
            tx_hash=tx_hash,
            amount_usdt=amount,
            asset=coin,
            network=self.payment_network,
            wallet_address=self.bybit_wallet,
            note=f"user={username or 'unknown'}",
        )
        if not payment_id:
            await update.effective_message.reply_text("TX hash уже зарегистрирован или некорректен.")
            return

        await update.effective_message.reply_text(
            f"Заявка принята. ID=<code>{payment_id}</code>. Ожидайте подтверждение администратора.",
            parse_mode=ParseMode.HTML,
        )
        try:
            await self.app.bot.send_message(
                chat_id=self.admin_chat_id,
                text=(
                    "<b>Новая заявка на оплату</b>\n"
                    f"id=<code>{payment_id}</code>\n"
                    f"chat_id=<code>{chat_id}</code>\n"
                    f"tx=<code>{_escape_html(tx_hash)}</code>\n"
                    f"amount=<code>{amount:.4f}</code> {_escape_html(coin)}\n\n"
                    f"<code>/approve {payment_id}</code>\n"
                    f"<code>/reject {payment_id} reason</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            self.logger.warning("admin notify failed: %s", exc)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat:
            return
        sub = self.store.get_subscriber(update.effective_chat.id)
        if not sub:
            await update.effective_message.reply_text("Подписка не активна. Используйте /plans и /pay.")
            return
        until = parse_iso(sub.get("subscription_until"))
        now = datetime.now(UTC)
        active = bool(int(sub.get("is_active", 0))) and bool(until and until > now)
        left_hours = int(max(0.0, ((until - now).total_seconds() if until else 0) / 3600))
        await update.effective_message.reply_text(
            (
                "<b>Статус подписки</b>\n\n"
                f"Активна: <b>{'YES' if active else 'NO'}</b>\n"
                f"До: <code>{until.isoformat() if until else '-'}</code>\n"
                f"Осталось: <code>{left_hours} ч</code>"
            ),
            parse_mode=ParseMode.HTML,
        )

    async def cmd_list_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not self._is_admin(user_id, self.admin_chat_id):
            await update.effective_message.reply_text("Только для администратора.")
            return
        rows = self.store.list_pending_payments(limit=20)
        if not rows:
            await update.effective_message.reply_text("Нет pending-заявок.")
            return
        lines = ["<b>Pending payments</b>"]
        for row in rows:
            lines.append(
                f"id=<code>{row['id']}</code> chat=<code>{row['chat_id']}</code> tx=<code>{_escape_html(row['tx_hash'])}</code>"
            )
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def cmd_approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not self._is_admin(user_id, self.admin_chat_id):
            await update.effective_message.reply_text("Только для администратора.")
            return
        args = context.args or []
        if not args:
            await update.effective_message.reply_text("Формат: /approve <payment_id> [days]")
            return
        try:
            payment_id = int(args[0])
            days = int(args[1]) if len(args) > 1 else self.subscription_days
        except ValueError:
            await update.effective_message.reply_text("Некорректные параметры.")
            return
        payment = self.store.get_payment(payment_id)
        if not payment:
            await update.effective_message.reply_text("Заявка не найдена.")
            return
        ok, msg = self.store.approve_payment(payment_id=payment_id, reviewer_chat_id=user_id, duration_days=days)
        if not ok:
            await update.effective_message.reply_text(f"approve error: {msg}")
            return
        sub = self.store.get_subscriber(int(payment["chat_id"])) or {}
        await update.effective_message.reply_text(f"Approved. active_until={sub.get('subscription_until', '-')}")
        await self.app.bot.send_message(
            chat_id=int(payment["chat_id"]),
            text=(
                "<b>Подписка активирована</b>\n\n"
                f"Срок: <b>{days} дней</b>\n"
                f"До: <code>{_escape_html(sub.get('subscription_until', '-'))}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )

    async def cmd_reject(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not self._is_admin(user_id, self.admin_chat_id):
            await update.effective_message.reply_text("Только для администратора.")
            return
        args = context.args or []
        if not args:
            await update.effective_message.reply_text("Формат: /reject <payment_id> [reason]")
            return
        try:
            payment_id = int(args[0])
        except ValueError:
            await update.effective_message.reply_text("payment_id должен быть числом.")
            return
        reason = " ".join(args[1:]).strip() or "payment_not_confirmed"
        payment = self.store.get_payment(payment_id)
        if not payment:
            await update.effective_message.reply_text("Заявка не найдена.")
            return
        ok, msg = self.store.reject_payment(payment_id=payment_id, reviewer_chat_id=user_id, note=reason)
        if not ok:
            await update.effective_message.reply_text(f"reject error: {msg}")
            return
        await update.effective_message.reply_text("Заявка отклонена.")
        await self.app.bot.send_message(
            chat_id=int(payment["chat_id"]),
            text=f"Платеж не подтвержден. Причина: <code>{_escape_html(reason)}</code>",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_broadcast_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not self._is_admin(user_id, self.admin_chat_id):
            await update.effective_message.reply_text("Только для администратора.")
            return
        args = context.args or []
        if len(args) < 5:
            await update.effective_message.reply_text(
                "Формат: /broadcast_signal <SYMBOL> <SIDE> <ENTRY> <SL> <TP> [EXCHANGE] [LEVERAGE] [ORDER_TYPE]"
            )
            return
        try:
            side = args[1].upper()
            entry = float(args[2])
            stop_loss = float(args[3])
            take_profit = float(args[4])
            rr_ratio = 0.0
            risk = abs(entry - stop_loss)
            reward = abs(take_profit - entry)
            if risk > 0:
                rr_ratio = reward / risk
            payload = SignalPayload(
                symbol=args[0].upper(),
                side=side,
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                exchange=(args[5].upper() if len(args) > 5 else self.exchange_label),
                signal_ts=datetime.now(UTC),
                source="manual-admin",
                leverage=(args[6] if len(args) > 6 else self.default_leverage),
                order_type=(args[7] if len(args) > 7 else self.default_order_type),
                rr_ratio=rr_ratio,
                chart_url=self._build_tradingview_url(
                    symbol=args[0].upper(),
                    side=side,
                    entry=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                ),
                subscribe_url=self.subscribe_url,
                levels_note=self._build_levels_note(entry=entry, stop_loss=stop_loss, take_profit=take_profit),
            )
        except ValueError:
            await update.effective_message.reply_text("ENTRY/SL/TP должны быть числами.")
            return
        await self.broadcast_signal(payload)
        await update.effective_message.reply_text("Сигнал отправлен.")

    async def cmd_set_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not self._is_admin(user_id, self.admin_chat_id):
            await update.effective_message.reply_text("Только для администратора.")
            return
        args = context.args or []
        if len(args) < 2:
            await update.effective_message.reply_text("Формат: /set_result <signal_id> <win|loss|breakeven> [max_pnl_pct] [max_dd_pct] [note]")
            return
        try:
            signal_id = int(args[0])
        except ValueError:
            await update.effective_message.reply_text("signal_id должен быть числом.")
            return
        outcome = args[1].lower()
        max_pnl = float(args[2]) if len(args) >= 3 else None
        max_dd = float(args[3]) if len(args) >= 4 else None
        note = " ".join(args[4:]) if len(args) >= 5 else ""
        try:
            self.store.set_signal_result(signal_id, outcome, max_pnl_pct=max_pnl, max_drawdown_pct=max_dd, note=note)
            await update.effective_message.reply_text("Результат сигнала сохранен.")
        except Exception as exc:
            await update.effective_message.reply_text(f"Ошибка: {exc}")

    async def broadcast_signal(self, payload: SignalPayload):
        if not self.broadcast_enabled:
            self.logger.info("broadcast disabled")
            return
        msg = format_signal_message(payload, self.warning_text)
        store_payload = StoreSignalPayload(
            symbol=payload.symbol,
            side=payload.side,
            entry_price=payload.entry,
            stop_loss=payload.stop_loss,
            take_profit=payload.take_profit,
            exchange=payload.exchange,
            source=payload.source,
            created_at=payload.signal_ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            leverage=payload.leverage,
            order_type=payload.order_type,
            rr_ratio=payload.rr_ratio,
            chart_url=payload.chart_url,
        )
        signal_id = self.store.insert_signal(store_payload)
        delivered = 0
        for chat_id in self.store.get_active_subscriber_chat_ids():
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
                self.store.mark_delivery(signal_id, chat_id, status="sent")
                delivered += 1
                await asyncio.sleep(0.04)
            except Exception as exc:
                self.store.mark_delivery(signal_id, chat_id, status="failed", error=str(exc))
        self.logger.info("broadcast signal_id=%s delivered=%s", signal_id, delivered)

    async def process_pending_signal_deliveries(self):
        pending = self.store.list_pending_deliveries(limit=300)
        for item in pending:
            created = parse_iso(item.get("created_at")) or datetime.now(UTC)
            payload = SignalPayload(
                symbol=str(item["symbol"]),
                side=str(item["side"]),
                entry=float(item["entry_price"]),
                stop_loss=float(item["stop_loss"]),
                take_profit=float(item["take_profit"]),
                exchange=str(item.get("exchange", self.exchange_label)),
                signal_ts=created,
                source=str(item.get("source", "main-bot")),
                leverage=str(item.get("leverage", self.default_leverage) or self.default_leverage),
                order_type=str(item.get("order_type", self.default_order_type) or self.default_order_type),
                rr_ratio=float(item.get("rr_ratio", 0.0) or 0.0),
                chart_url=str(item.get("chart_url", "") or ""),
                subscribe_url=self.subscribe_url,
                levels_note=self._build_levels_note(
                    entry=float(item["entry_price"]),
                    stop_loss=float(item["stop_loss"]),
                    take_profit=float(item["take_profit"]),
                ),
            )
            msg = format_signal_message(payload, self.warning_text)
            chat_id = int(item["chat_id"])
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.HTML)
                self.store.mark_delivery(int(item["signal_id"]), chat_id, status="sent")
            except Exception as exc:
                self.store.mark_delivery(int(item["signal_id"]), chat_id, status="failed", error=str(exc))
            await asyncio.sleep(0.03)

    async def run_delivery_worker(self):
        while True:
            try:
                await self.process_pending_signal_deliveries()
            except Exception as exc:
                self.logger.warning("delivery worker error: %s", exc)
            await asyncio.sleep(self.delivery_poll_sec)

    def _build_weekly_report_text(self, summary: dict) -> str:
        if int(summary.get("total_signals", 0) or 0) == 0:
            return "<b>Weekly signal report</b>\n\nЗа последние 7 дней нет закрытых сигналов."
        return (
            "<b>Weekly signal report (7d)</b>\n\n"
            f"Сигналов: <b>{int(summary.get('total_signals', 0) or 0)}</b>\n"
            f"Побед: <b>{int(summary.get('wins', 0) or 0)}</b>\n"
            f"Убыточных: <b>{int(summary.get('losses', 0) or 0)}</b>\n"
            f"Безубыток: <b>{int(summary.get('breakevens', 0) or 0)}</b>\n"
            f"Процент побед: <b>{float(summary.get('win_rate_pct', 0.0) or 0.0):.2f}%</b>\n"
            f"Макс. прибыль: <b>{float(summary.get('max_profit_pct', 0.0) or 0.0):+.2f}%</b>\n"
            f"Макс. убыток: <b>{float(summary.get('max_loss_pct', 0.0) or 0.0):+.2f}%</b>\n"
            f"Средний результат: <b>{float(summary.get('avg_return_pct', 0.0) or 0.0):+.2f}%</b>"
        )

    async def cmd_weekly_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if not self._is_admin(user_id, self.admin_chat_id):
            await update.effective_message.reply_text("Только для администратора.")
            return
        now = datetime.now(UTC)
        summary = self.store.compute_weekly_summary(now - timedelta(days=7), now)
        await update.effective_message.reply_text(self._build_weekly_report_text(summary), parse_mode=ParseMode.HTML)

    async def publish_weekly_report_to_subscribers(self):
        now = datetime.now(UTC)
        summary = self.store.compute_weekly_summary(now - timedelta(days=7), now)
        self.store.save_weekly_report(summary)
        text = self._build_weekly_report_text(summary)
        for chat_id in self.store.get_active_subscriber_chat_ids():
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
            except Exception as exc:
                self.logger.warning("weekly report failed chat_id=%s err=%s", chat_id, exc)
            await asyncio.sleep(0.03)

    async def run_weekly_scheduler(self):
        while True:
            now = datetime.now(UTC)
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            while target.weekday() != 0 or target <= now:
                target += timedelta(days=1)
            await asyncio.sleep(max(1, int((target - now).total_seconds())))
            await self.publish_weekly_report_to_subscribers()

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        self.logger.exception("telegram error: %s", context.error)

    def _build_levels_note(self, entry: float, stop_loss: float, take_profit: float) -> str:
        return f"Entry={entry:.6f}; SL={stop_loss:.6f}; TP={take_profit:.6f}"

    def _build_tradingview_url(self, symbol: str, side: str, entry: float, stop_loss: float, take_profit: float) -> str:
        cleaned = (symbol or "").strip().upper()
        if not cleaned:
            return ""
        symbol_q = f"{self.tv_exchange_prefix}:{cleaned}"
        direction = "LONG" if str(side).upper() in {"BUY", "LONG"} else "SHORT"
        levels = self._build_levels_note(entry=entry, stop_loss=stop_loss, take_profit=take_profit)
        base = "https://www.tradingview.com/chart/"
        params = f"?symbol={symbol_q}&interval={_escape_html(self.tv_interval)}"
        if self.tv_layout_id:
            params += f"&layout={_escape_html(self.tv_layout_id)}"
        params += f"&desc={_escape_html(direction)}|{_escape_html(levels)}"
        return f"{base}{params}"

    async def _run_polling_async(self):
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        self.logger.info("Paid signals bot polling started")
        scheduler_task = asyncio.create_task(self.run_weekly_scheduler())
        delivery_task = asyncio.create_task(self.run_delivery_worker())
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            scheduler_task.cancel()
            delivery_task.cancel()
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    def run(self):
        asyncio.run(self._run_polling_async())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paid signals Telegram bot")
    parser.add_argument("--send-weekly-now", action="store_true", help="Send weekly report once and exit")
    return parser.parse_args()


async def _run_once_weekly_report():
    bot = PaidSignalsBot()
    await bot.app.initialize()
    try:
        await bot.publish_weekly_report_to_subscribers()
        print("Weekly report sent")
    finally:
        await bot.app.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
    args = parse_args()
    if args.send_weekly_now:
        asyncio.run(_run_once_weekly_report())
    else:
        PaidSignalsBot().run()
