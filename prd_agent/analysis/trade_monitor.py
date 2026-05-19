"""
Анализ сделок: сверка сигналов с закрытым PnL Bybit за период.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PeriodStats:
    hours: float
    closed_pnl_usdt: float = 0.0
    closed_count: int = 0
    wins: int = 0
    losses: int = 0
    signals_count: int = 0
    high_confidence_signals: int = 0

    def win_rate(self) -> float:
        total = self.wins + self.losses
        return (self.wins / total * 100) if total else 0.0


class TradeMonitor:
    def __init__(self, store_dir: Path):
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._trades_file = self.store_dir / "executed_trades.jsonl"

    def record_execution(
        self,
        symbol: str,
        side: str,
        qty: float,
        signal_source: str,
        order_id: str = "",
    ) -> None:
        row = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "signal_source": signal_source,
            "order_id": order_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with self._trades_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def fetch_closed_pnl(
        self, exchange, hours: float, symbol: Optional[str] = None
    ) -> List[Dict]:
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
        all_rows: List[Dict] = []
        cursor = ""
        for _ in range(20):
            rows, cursor = await exchange.get_closed_pnl_page(
                symbol=symbol,
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                cursor=cursor or None,
                limit=50,
            )
            all_rows.extend(rows)
            if not cursor:
                break
        return all_rows

    def summarize_pnl_rows(self, rows: List[Dict]) -> PeriodStats:
        stats = PeriodStats(hours=0)
        for r in rows:
            pnl = float(r.get("closedPnl", 0) or 0)
            stats.closed_pnl_usdt += pnl
            stats.closed_count += 1
            if pnl >= 0:
                stats.wins += 1
            else:
                stats.losses += 1
        return stats

    async def period_report(
        self,
        exchange,
        signals: List[Dict],
        hours: float,
        high_conf_threshold: float,
    ) -> Dict[str, Any]:
        closed = await self.fetch_closed_pnl(exchange, hours)
        stats = self.summarize_pnl_rows(closed)
        stats.hours = hours
        stats.signals_count = len(signals)
        stats.high_confidence_signals = sum(
            1 for s in signals if float(s.get("confidence", 0)) >= high_conf_threshold
        )
        open_trades = []
        if self._trades_file.exists():
            cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
            for line in self._trades_file.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                    if ts.timestamp() >= cutoff:
                        open_trades.append(row)
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
        return {
            "period_hours": hours,
            "pnl_usdt": round(stats.closed_pnl_usdt, 2),
            "closed_trades": stats.closed_count,
            "win_rate_pct": round(stats.win_rate(), 1),
            "signals_total": stats.signals_count,
            "high_confidence_signals": stats.high_confidence_signals,
            "executions_logged": len(open_trades),
        }
