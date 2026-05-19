#!/usr/bin/env python3
"""Telegram Bot API панель (кнопки) для управления TelegramSignalAgent параллельно Telethon."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, CommandHandler

CONTROL_PREFIX = "tgsa:"

LOG = logging.getLogger("TG_AGENT_PANEL")


def _allowed_chat(update: Update, chat_id_expect: str) -> bool:
    if not chat_id_expect or not update.effective_chat:
        return False
    try:
        return int(update.effective_chat.id) == int(str(chat_id_expect).strip())
    except (TypeError, ValueError):
        return False


def _runtime(agent: Any) -> dict[str, Any]:
    state = getattr(agent, "state", None)
    if not isinstance(state, dict):
        return {}
    node = state.get("agent_runtime_controls")
    if isinstance(node, dict):
        return node
    blank: dict[str, Any] = {}
    state["agent_runtime_controls"] = blank
    return blank


def _panel_markup(agent: Any) -> InlineKeyboardMarkup:
    rtc = _runtime(agent)
    pause = bool(rtc.get("pause_all_execution"))
    ch_on = getattr(agent, "_effective_channel_auto_execute", lambda: False)()
    sc_on = getattr(agent, "_effective_market_scanner_auto_execute", lambda: False)()

    pause_label = "⏸ ПАУЗА: ВКЛ" if pause else "▶️ ПАУЗА: ВЫКЛ"
    ch_label = f"📣 Каналы auto: {'ВКЛ' if ch_on else 'ВЫКЛ'}"
    sc_label = f"📡 Сканер→Bybit: {'ВКЛ' if sc_on else 'ВЫКЛ'}"

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Статус", callback_data=f"{CONTROL_PREFIX}status"),
                InlineKeyboardButton("🔄 Обновить", callback_data=f"{CONTROL_PREFIX}panel"),
            ],
            [InlineKeyboardButton(ch_label, callback_data=f"{CONTROL_PREFIX}toggle:channel")],
            [InlineKeyboardButton(sc_label, callback_data=f"{CONTROL_PREFIX}toggle:scanner")],
            [InlineKeyboardButton(pause_label, callback_data=f"{CONTROL_PREFIX}toggle:pause")],
        ]
    )


def _status_text(agent: Any) -> str:
    rtc = _runtime(agent)
    ch_raw = rtc.get("channel_auto_execute")
    sc_raw = rtc.get("market_scanner_auto_execute")
    ch_eff = getattr(agent, "_effective_channel_auto_execute", lambda: False)()
    sc_eff = getattr(agent, "_effective_market_scanner_auto_execute", lambda: False)()
    any_live = getattr(agent, "_any_live_execution_enabled", lambda: False)()
    return (
        "<b>PRD TELEGRAM SIGNAL AGENT</b>\n"
        f"Пауза всех входов: <code>{bool(rtc.get('pause_all_execution'))}</code>\n"
        f"Каналы (сообщения+AI→Bybit): в памяти <code>{bool(ch_raw)}</code> эффект. <code>{ch_eff}</code>\n"
        f"MARKET SCANNER→Bybit: в памяти <code>{bool(sc_raw)}</code> эффект. <code>{sc_eff}</code>\n"
        f"Нужны ключи Bybit для live (хоть один канал вкл): <code>{bool(any_live)}</code>\n"
        "<i>Сканер: без OpenRouter, SL=«Отмена», TP=«Цель» из уведомления.</i>"
    )


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: Any, allowed_chat_id: str) -> None:
    if not update.effective_chat or not _allowed_chat(update, allowed_chat_id):
        if update.effective_message:
            await update.effective_message.reply_text("Доступ запрещён: TELEGRAM_CHAT_ID не совпадает.")
        return
    if not update.effective_message:
        return
    fn = getattr(agent, "_ensure_runtime_controls_defaults", None)
    if callable(fn):
        fn()
    intro = (
        "<b>Панель агента</b>\n"
        "Управление автоисполнением. Каналы по-прежнему проходят OpenRouter+AI и риск-ворота при включённом авто.\n"
        "MARKET SCANNER→Bybit: без ИИ постов, только технические фильтры + спред.\n\n"
    )
    await update.effective_message.reply_html(intro + _status_text(agent), reply_markup=_panel_markup(agent))


async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, agent: Any, allowed_chat_id: str) -> None:
    query = update.callback_query
    if query is None:
        return
    if not _allowed_chat(update, allowed_chat_id):
        await query.answer("Доступ запрещён", show_alert=True)
        return

    getattr(agent, "_ensure_runtime_controls_defaults", lambda: None)()
    rtc = _runtime(agent)
    data = str(query.data or "")

    if data == f"{CONTROL_PREFIX}status":
        await query.answer()
        msg = query.message
        if msg:
            await msg.reply_html(_status_text(agent), reply_markup=_panel_markup(agent))
        return

    if data == f"{CONTROL_PREFIX}panel":
        await query.answer("Обновлено")
        msg = query.message
        try:
            if msg:
                await msg.reply_html("<b>Панель</b>\n" + _status_text(agent), reply_markup=_panel_markup(agent))
        except Exception:
            if msg:
                await msg.reply_text("Не удалось обновить панель.")
            else:
                await query.answer("Не удалось обновить", show_alert=True)
        return

    if data == f"{CONTROL_PREFIX}toggle:channel":
        cur = bool(rtc.get("channel_auto_execute", getattr(agent, "auto_execute", False)))
        rtc["channel_auto_execute"] = not cur
        await query.answer("Каналы: переключено")
    elif data == f"{CONTROL_PREFIX}toggle:scanner":
        cur = bool(rtc.get("market_scanner_auto_execute", getattr(agent, "market_scanner_auto_execute_default", False)))
        rtc["market_scanner_auto_execute"] = not cur
        await query.answer("Сканер: переключено")
    elif data == f"{CONTROL_PREFIX}toggle:pause":
        cur = bool(rtc.get("pause_all_execution"))
        rtc["pause_all_execution"] = not cur
        await query.answer("Пауза: переключено")
    else:
        await query.answer()
        return

    sync = getattr(agent, "_sync_execution_dry_run", None)
    if callable(sync):
        sync()
    save = getattr(agent, "_save_state", None)
    if callable(save):
        save()
    try:
        msg = query.message
        if msg:
            await msg.reply_html("<b>Панель</b>\n" + _status_text(agent), reply_markup=_panel_markup(agent))
    except Exception:
        pass


async def run_signal_agent_control_panel(agent: Any, *, token: str, allowed_chat_id: str) -> None:
    """Блокирует до отмены: polling Bot API параллельно Telethon."""
    if not token or not allowed_chat_id:
        LOG.warning("panel: TELEGRAM_TOKEN/chat_id missing, skip")
        return

    app = (
        Application.builder()
        .token(token)
        .connect_timeout(45.0)
        .read_timeout(45.0)
        .write_timeout(45.0)
        .pool_timeout(45.0)
        .build()
    )

    async def start_w(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _cmd_start(update, context, agent, allowed_chat_id)

    async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _on_callback(update, context, agent, allowed_chat_id)

    app.add_handler(CommandHandler("start", start_w))
    app.add_handler(CommandHandler("panel", start_w))
    app.add_handler(CallbackQueryHandler(cb))

    panel_stop = asyncio.Event()

    async def _panel_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        if isinstance(err, Conflict):
            LOG.error(
                "Панель: Telegram Conflict — с этим TELEGRAM_TOKEN уже идёт getUpdates (второй процесс, другой сервер или webhook). "
                "Остановите дубликат или задайте в config telegram_signal_agent.control_panel_enabled: false. "
                "Агент (Telethon + сканер) продолжит без кнопок до перезапуска."
            )
            panel_stop.set()

    app.add_error_handler(_panel_error)

    await app.initialize()
    await app.start()
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception as exc:
        LOG.warning("delete_webhook перед polling: %s", exc)
    LOG.info("Control panel polling started")
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    try:
        await panel_stop.wait()
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await app.updater.stop()
        except Exception:
            pass
        try:
            await app.stop()
        except Exception:
            pass
        try:
            await app.shutdown()
        except Exception:
            pass
        LOG.info("Control panel polling stopped")


def start_control_panel_task(agent: Any) -> asyncio.Task[None]:
    import os

    async def runner() -> None:
        tok = os.getenv("TELEGRAM_TOKEN", "").strip()
        chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        try:
            await run_signal_agent_control_panel(agent, token=tok, allowed_chat_id=chat)
        except Conflict as exc:
            LOG.error(
                "Control panel: Conflict при старте — тот же BOT токен опрашивает другой процесс. "
                "pkill -f telegram_signal_agent; проверьте второй VPS и @BotFather webhook. Детали: %s",
                exc,
            )
        except Exception as exc:
            LOG.warning("Control panel died: %s", exc)

    return asyncio.create_task(runner())
