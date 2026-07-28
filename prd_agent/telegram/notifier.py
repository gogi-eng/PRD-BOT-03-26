"""Telegram-уведомления о всех действиях бота."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger("prd_agent.notifier")


class TelegramNotifier:
    def __init__(self, cfg: Dict[str, Any]):
        tg = cfg.get("telegram", {})
        self._cfg = cfg
        self.token = tg.get("bot_token", "")
        self.chat_id = tg.get("chat_id") or tg.get("channel_id", "")

    async def send(self, text: str, *, silent: bool = False) -> bool:
        if not self.token or not self.chat_id:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text[:4096],
            "disable_notification": silent,
            "parse_mode": "HTML",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=25)) as r:
                    data = await r.json()
                    return bool(data.get("ok"))
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
            return False

    async def signal_received(
        self,
        symbol: str,
        side: str,
        conf: float,
        source: str,
        reason: str = "",
        raw: Optional[Dict[str, Any]] = None,
    ) -> None:
        from prd_agent.signals.confidence_filter import passes_emit_gate
        from prd_agent.signals.types import UnifiedSignal

        sig = UnifiedSignal(
            symbol=symbol,
            side=side,
            confidence=conf,
            source=source,
            reason=reason,
            raw=raw or {},
        )
        if not passes_emit_gate(sig, self._cfg):
            return
        await self.send(
            f"📥 <b>Сигнал</b> {symbol} {side}\n"
            f"conf={conf:.0%} | {source}\n"
            f"{reason[:300] if reason else ''}"
        )

    async def signal_skipped(self, symbol: str, side: str, reason: str) -> None:
        await self.send(f"⏭ <b>Пропуск</b> {symbol} {side}\n{reason[:400]}")

    async def signal_only_preview(
        self,
        symbol: str,
        side: str,
        conf: float,
        entry: float,
        sl: float,
        tp: float,
        leverage: int,
        reason: str = "",
    ) -> None:
        await self.send(
            f"🔭 <b>Signal-only</b> (ордер НЕ отправлен)\n"
            f"{symbol} {side} conf={conf:.0%}\n"
            f"entry≈<code>{entry:.6g}</code> sl=<code>{sl:.6g}</code> "
            f"tp=<code>{tp:.6g}</code> lev={leverage}x\n"
            f"{reason[:350]}"
        )

    async def order_placed(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_id: str = "",
        *,
        leverage: int = 0,
        leverage_requested: int = 0,
        advisor_reason: str = "",
    ) -> None:
        if leverage > 0:
            if leverage_requested > 0 and leverage_requested != leverage:
                lev_line = (
                    f"\nплечо на бирже: <code>{leverage}x</code> "
                    f"(супервизор просил {leverage_requested}x)"
                )
            else:
                lev_line = f"\nплечо на бирже: <code>{leverage}x</code>"
        else:
            lev_line = ""
        adv_line = f"\n<i>{advisor_reason[:280]}</i>" if advisor_reason else ""
        await self.send(
            f"✅ <b>Ордер</b> {symbol} {side}\nqty={qty:.4f}{lev_line}{adv_line}"
            + (f"\nid={order_id}" if order_id else "")
        )

    async def order_failed(self, symbol: str, error: str) -> None:
        await self.send(f"❌ <b>Ордер не прошёл</b> {symbol}\n{error[:400]}")

    async def risk_event(self, text: str) -> None:
        await self.send(f"🛡 <b>Риск</b>\n{text}")

    async def position_update(self, symbol: str, side: str, upnl: float, size: float) -> None:
        await self.send(f"📈 <b>Позиция</b> {symbol} {side}\nuPnL={upnl:+.2f} USDT | size={size}")

    async def config_change(self, summary: str, justification: str = "") -> None:
        await self.send(f"🛠 <b>Config</b> {summary}\n{justification[:350]}")

    async def global_report(self, text: str) -> None:
        await self.send(f"🌍 <b>Глобальный анализ</b>\n{text[:3800]}")

    async def wallet_flow_advice(
        self,
        symbol: str,
        bias: str,
        conf: float,
        reason: str = "",
        label: str = "",
        usd_volume: float = 0.0,
    ) -> bool:
        """Advisory-совет Wallet Tracker (ордер НЕ ставится)."""
        bias_l = (bias or "").lower()
        bias_show = {
            "long": "LONG",
            "short": "SHORT",
            "neutral": "НЕЙТРАЛЬНО",
        }.get(bias_l, (bias or "?").upper())
        label_line = f"Кошелёк / метка: {label}\n" if label else ""
        vol_line = f"Объём (оценка): ${usd_volume:,.0f}\n" if usd_volume > 0 else ""
        return await self.send(
            f"💼 <b>Совет Wallet Tracker</b>\n"
            f"<i>Это СОВЕТ, не ордер — бот сам сделку не открывает</i>\n\n"
            f"Символ: <code>{symbol}</code>\n"
            f"Направление: <b>{bias_show}</b>\n"
            f"Уверенность: {conf:.0%}\n"
            f"{label_line}{vol_line}"
            f"Причина: {(reason or '')[:280]}"
        )
