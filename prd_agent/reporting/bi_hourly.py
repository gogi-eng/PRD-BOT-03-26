"""
Отчёт каждые 2 часа в Telegram-канал.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp


class BiHourlyReporter:
    def __init__(self, cfg: Dict[str, Any]):
        tg = cfg.get("telegram", {})
        self.token = tg.get("bot_token", "")
        self.channel_id = tg.get("channel_id") or tg.get("chat_id", "")
        rep = cfg.get("reporter", {})
        self.interval_hours = float(rep.get("interval_hours", 2))
        self.high_conf = float(rep.get("high_confidence_threshold", 0.75))

    async def send_message(self, text: str) -> bool:
        if not self.token or not self.channel_id:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.channel_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
                return bool(data.get("ok"))

    def format_report(
        self,
        *,
        positions: List[Dict],
        report_2h: Dict[str, Any],
        report_24h: Dict[str, Any],
        high_conf_signals: List[Dict],
        code_changes: List[Dict],
        risk_snapshot: Dict[str, Any],
        balance: float,
        supervisor_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"<b>📊 PRD Agent — отчёт за 2ч</b> ({now})",
            "",
            f"<b>Баланс USDT:</b> {balance:.2f}",
            f"<b>Риск:</b> {risk_snapshot.get('status', '?')} | "
            f"PnL сегодня: {risk_snapshot.get('pnl_today_usdt', 0):+.2f} USDT",
        ]
        if risk_snapshot.get("blocked"):
            lines.append(f"⚠️ <b>Торговля заблокирована:</b> {risk_snapshot.get('block_reason', '')}")

        lines.extend(["", "<b>Результаты за 2 часа</b>"])
        lines.append(
            f"• PnL: {report_2h.get('pnl_usdt', 0):+.2f} USDT | "
            f"закрыто: {report_2h.get('closed_trades', 0)} | "
            f"win rate: {report_2h.get('win_rate_pct', 0)}%"
        )
        lines.append(
            f"• Сигналов: {report_2h.get('signals_total', 0)} "
            f"(высокая уверенность: {report_2h.get('high_confidence_signals', 0)})"
        )

        lines.extend(["", "<b>Результаты за 24 часа</b>"])
        lines.append(
            f"• PnL: {report_24h.get('pnl_usdt', 0):+.2f} USDT | "
            f"закрыто: {report_24h.get('closed_trades', 0)} | "
            f"win rate: {report_24h.get('win_rate_pct', 0)}%"
        )

        lines.extend(["", "<b>🔥 Сигналы с высокой уверенностью</b>"])
        if high_conf_signals:
            for s in high_conf_signals[:8]:
                lines.append(
                    f"• {s.get('symbol')} {s.get('side')} "
                    f"conf={float(s.get('confidence', 0)):.0%} "
                    f"({s.get('source', '?')})"
                )
        else:
            lines.append("• нет")

        lines.extend(["", "<b>📂 Открытые позиции на бирже</b>"])
        if positions:
            for p in positions[:10]:
                sym = p.get("symbol", "?")
                side = p.get("side", "?")
                size = p.get("size", 0)
                upnl = float(p.get("unrealisedPnl", 0) or 0)
                lines.append(f"• {sym} {side} size={size} uPnL={upnl:+.2f}")
        else:
            lines.append("• нет открытых")

        lines.extend(["", "<b>🛠 Изменения кода (с обоснованием)</b>"])
        if code_changes:
            for ch in code_changes[-5:]:
                lines.append(f"• [{ch.get('risk', '?')}] {ch.get('summary', '')}")
                if ch.get("justification"):
                    lines.append(f"  ↳ {ch['justification'][:200]}")
        else:
            lines.append("• за период изменений не было")

        if supervisor_summary:
            try:
                from prd_agent.supervisor.trade_supervisor import TradeSupervisor

                lines.extend(TradeSupervisor.format_report_section(supervisor_summary))
            except Exception:
                pass

        lines.append("")
        lines.append(
            "<i>⚠️ Прибыль не гарантируется. Бот управляет риском, не обещает только плюс.</i>"
        )
        return "\n".join(lines)

    async def publish_full_report(self, **kwargs) -> bool:
        text = self.format_report(**kwargs)
        return await self.send_message(text)
