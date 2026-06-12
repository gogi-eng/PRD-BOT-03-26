"""
Отчёт каждые 2 часа в Telegram-канал.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from prd_agent.analysis.trade_analytics import _bucket_stats, load_closed_trades


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
        exchange_pnl_today_usdt: Optional[float] = None,
        exchange_pnl_today_pct: Optional[float] = None,
        supervisor_summary: Optional[Dict[str, Any]] = None,
        trade_journal_path: Optional[Path] = None,
        api_stats: Optional[Dict[str, Any]] = None,
        skip_baseline: Optional[Dict[str, Any]] = None,
        active_strategy: Optional[str] = None,
    ) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"<b>📊 PRD Agent — отчёт за 2ч</b> ({now})",
            "",
            f"<b>Баланс USDT:</b> {balance:.2f}",
        ]
        ex_usdt = (
            exchange_pnl_today_usdt
            if exchange_pnl_today_usdt is not None
            else risk_snapshot.get("pnl_today_usdt", 0)
        )
        ex_pct = (
            exchange_pnl_today_pct
            if exchange_pnl_today_pct is not None
            else risk_snapshot.get("pnl_today_pct", 0)
        )
        lines.append(
            f"<b>PnL сегодня (биржа, UTC):</b> {float(ex_usdt):+.2f} USDT "
            f"({float(ex_pct):+.2f}%)"
        )
        lines.append(
            f"<b>Риск-статус:</b> {risk_snapshot.get('status', '?')} | "
            f"сделок закрыто: {risk_snapshot.get('trades_today', 0)}"
        )
        if risk_snapshot.get("blocked"):
            reason = risk_snapshot.get("block_reason", "")
            if float(ex_usdt) >= 0 and "дневн" in str(reason).lower():
                lines.append(
                    "⚠️ <b>Внимание:</b> день на бирже в плюсе, но риск ещё показывает блок. "
                    "Нажмите в боте «💰 Сбросить убыток» или дождитесь следующего цикла."
                )
            else:
                lines.append(f"⚠️ <b>Торговля заблокирована:</b> {reason}")

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

        if trade_journal_path is not None:
            closed_2h = load_closed_trades(trade_journal_path, hours=2)
            by_source = _bucket_stats(closed_2h, "source", limit=8)
            if by_source:
                lines.extend(["", "<b>📡 Источники сигналов (2ч)</b>"])
                for r in sorted(by_source, key=lambda x: -x["pnl"]):
                    lines.append(
                        f"• {r['name']}: n={r['n']}, PnL={r['pnl']:+.2f}, WR={r['winrate']:.0f}%"
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

        if api_stats:
            lines.extend(["", "<b>📡 API-нагрузка</b>"])
            lines.append(
                f"• Последний цикл: {api_stats.get('last_cycle_calls', 0)} REST | "
                f"всего с запуска: {api_stats.get('total_since_boot', 0)}"
            )

        if skip_baseline and skip_baseline.get("skipped", 0) > 0:
            lines.extend(["", "<b>⏭ SKIP baseline (ledger)</b>"])
            lines.append(
                f"• {skip_baseline.get('skipped', 0)}/{skip_baseline.get('total_signals', 0)} "
                f"({skip_baseline.get('pct_skipped_of_total', 0)}%)"
            )
            for bucket, cnt in skip_baseline.get("top_buckets", [])[:6]:
                pct = skip_baseline.get("pct_of_skips_by_bucket", {}).get(bucket, 0)
                lines.append(f"  — {bucket}: {cnt} ({pct}%)")

        if active_strategy:
            lines.extend(["", f"<b>🎯 Стратегия:</b> {active_strategy}"])

        lines.append("")
        lines.append(
            "<i>⚠️ Прибыль не гарантируется. Бот управляет риском, не обещает только плюс.</i>"
        )
        return "\n".join(lines)

    async def publish_full_report(self, **kwargs) -> bool:
        text = self.format_report(**kwargs)
        return await self.send_message(text)
