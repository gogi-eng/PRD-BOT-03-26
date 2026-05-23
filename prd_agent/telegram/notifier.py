"""Telegram-уведомления о всех действиях бота."""
from __future__ import annotations

from typing import Any, Dict, Optional

import aiohttp


class TelegramNotifier:
    def __init__(self, cfg: Dict[str, Any]):
        from prd_agent.signals.confidence_filter import load_min_analysis_confidence

        tg = cfg.get("telegram", {})
        self.token = tg.get("bot_token", "")
        self.chat_id = tg.get("chat_id") or tg.get("channel_id", "")
        self._min_signal_conf = load_min_analysis_confidence(cfg)

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
        except Exception:
            return False

    async def signal_received(self, symbol: str, side: str, conf: float, source: str, reason: str = "") -> None:
        from prd_agent.signals.confidence_filter import meets_threshold

        if not meets_threshold(conf, self._min_signal_conf):
            return
        await self.send(
            f"📥 <b>Сигнал</b> {symbol} {side}\n"
            f"conf={conf:.0%} | {source}\n"
            f"{reason[:300] if reason else ''}"
        )

    async def signal_skipped(self, symbol: str, side: str, reason: str) -> None:
        await self.send(f"⏭ <b>Пропуск</b> {symbol} {side}\n{reason[:400]}")

    async def order_placed(self, symbol: str, side: str, qty: float, order_id: str = "") -> None:
        await self.send(
            f"✅ <b>Ордер</b> {symbol} {side}\nqty={qty:.4f}" + (f"\nid={order_id}" if order_id else "")
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
