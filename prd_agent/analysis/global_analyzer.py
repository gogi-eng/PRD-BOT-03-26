"""Полный анализ работы бота за период."""
from __future__ import annotations

from typing import Any, Dict, List

from prd_agent.analysis.signal_ledger import SignalLedger
from prd_agent.analysis.trade_monitor import TradeMonitor
from prd_agent.signals.confidence_filter import load_min_analysis_confidence


class GlobalAnalyzer:
    def __init__(self, cfg: Dict[str, Any], ledger: SignalLedger, monitor: TradeMonitor):
        self.cfg = cfg
        self.ledger = ledger
        self.monitor = monitor

    async def build_report(self, exchange, signals_recent: List[Dict], hours: float = 24) -> str:
        ledger_sum = self.ledger.summary(hours)
        closed = await self.monitor.fetch_closed_pnl(exchange, hours)
        pnl_stats = self.monitor.summarize_pnl_rows(closed)
        min_conf = load_min_analysis_confidence(self.cfg)
        sig_report = await self.monitor.period_report(
            exchange, signals_recent, hours, min_conf
        )

        lines = [
            f"Период: {hours:.0f} ч",
            "",
            "<b>Сигналы (все, в т.ч. не открытые)</b>",
            f"• Всего: {ledger_sum.get('total', 0)}",
            f"• Не открыто: {ledger_sum.get('not_opened', 0)}",
            f"• По статусам: {ledger_sum.get('by_status', {})}",
            f"• По источникам: {ledger_sum.get('by_source', {})}",
            "",
            "<b>Исполнение на Bybit</b>",
            f"• Закрыто сделок: {pnl_stats.closed_count}",
            f"• PnL: {pnl_stats.closed_pnl_usdt:+.2f} USDT",
            f"• Win rate: {pnl_stats.win_rate():.1f}%",
            "",
            "<b>Качество сигналов</b>",
            f"• Сгенерировано: {sig_report.get('signals_total', 0)}",
            f"• Высокая уверенность: {sig_report.get('high_confidence_signals', 0)}",
            f"• Записано исполнений: {sig_report.get('executions_logged', 0)}",
        ]
        not_opened = ledger_sum.get("not_opened", 0)
        total = max(ledger_sum.get("total", 1), 1)
        if not_opened / total > 0.7:
            lines.append("")
            lines.append("⚠️ Много сигналов не доходит до ордера — проверьте риск-лимиты и min_signal_confidence.")
        if pnl_stats.closed_count >= 5 and pnl_stats.win_rate() < 40:
            lines.append("⚠️ Низкий win rate — self-improver может ужесточить фильтры.")
        return "\n".join(lines)

    def improvement_hints(self, ledger_sum: Dict, pnl_wr: float) -> List[Dict[str, Any]]:
        hints: List[Dict[str, Any]] = []
        if pnl_wr < 42 and ledger_sum.get("total", 0) > 10:
            hints.append(
                {
                    "risk": "low",
                    "path": ["trading", "min_signal_confidence"],
                    "delta": +0.03,
                    "summary": "Ужесточить порог после слабого win rate",
                    "justification": f"win_rate={pnl_wr:.1f}%",
                }
            )
        if ledger_sum.get("not_opened", 0) > ledger_sum.get("total", 0) * 0.8 and pnl_wr >= 45:
            hints.append(
                {
                    "risk": "low",
                    "path": ["trading", "min_signal_confidence"],
                    "delta": -0.02,
                    "summary": "Слегка снизить порог — много пропусков",
                    "justification": "not_opened ratio high",
                }
            )
        return hints
