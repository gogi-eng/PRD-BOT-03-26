"""
Единый журнал сделок: JSONL + строки в bot.log для analyze_bot_log.py.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("prd_agent.trades")


class TradeJournal:
    """Запись входов/выходов в data/trades/trade_history.jsonl."""

    def __init__(self, data_dir: Path):
        self.dir = data_dir / "trades"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trade_history.jsonl"
        self._pending: Dict[str, Dict[str, Any]] = {}

    def _append(self, row: Dict[str, Any]) -> None:
        row.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def log_entered(
        self,
        *,
        symbol: str,
        side: str,
        source: str,
        qty: float,
        entry: float,
        order_id: str = "",
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        confidence: float = 0.0,
        leverage: int = 0,
    ) -> None:
        sym = symbol.upper()
        grade = source or "unknown"
        logger.info("ENTERED %s: %s [%s]", sym, side.upper(), grade)
        row = {
            "event": "entered",
            "symbol": sym,
            "side": side,
            "source": source,
            "grade": grade,
            "qty": qty,
            "entry": entry,
            "order_id": order_id,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "confidence": confidence,
            "leverage": leverage,
        }
        self._append(row)
        if order_id:
            self._pending[order_id] = row
        key = f"{sym}:{side}"
        self._pending[key] = row

    def log_closed(
        self,
        *,
        symbol: str,
        pnl: float,
        reason: str,
        side: str = "",
        source: str = "",
        order_id: str = "",
        entry: float = 0.0,
        exit_price: float = 0.0,
        qty: float = 0.0,
        origin: str = "bot",
    ) -> None:
        sym = symbol.upper()
        logger.info("CLOSED %s: pnl=$%.2f reason=%s", sym, pnl, reason)
        if not source and order_id and order_id in self._pending:
            source = str(self._pending[order_id].get("source", ""))
        if not source:
            key = f"{sym}:{side}" if side else sym
            pending = self._pending.get(key) or self._pending.get(sym)
            if pending:
                source = str(pending.get("source", ""))
        self._append(
            {
                "event": "closed",
                "symbol": sym,
                "side": side,
                "pnl": round(pnl, 6),
                "reason": reason,
                "source": source,
                "order_id": order_id,
                "entry": entry,
                "exit": exit_price,
                "qty": qty,
                "origin": origin,
            }
        )
        if order_id and order_id in self._pending:
            del self._pending[order_id]

    def record_closed_from_exchange(self, row: Dict[str, Any], *, origin: str = "bot") -> None:
        """Строка closed-pnl Bybit API."""
        sym = str(row.get("symbol", "")).upper()
        pnl = float(row.get("closedPnl", 0) or 0)
        oid = str(row.get("orderId", "") or "")
        side_raw = str(row.get("side", "")).upper()
        side = "Buy" if side_raw in ("BUY", "LONG") else "Sell" if side_raw in ("SELL", "SHORT") else side_raw
        entry = float(row.get("avgEntryPrice", 0) or 0)
        exit_p = float(row.get("avgExitPrice", 0) or 0)
        qty = float(row.get("qty", 0) or 0)
        self.log_closed(
            symbol=sym,
            pnl=pnl,
            reason="exchange_closed",
            side=side,
            order_id=oid,
            entry=entry,
            exit_price=exit_p,
            qty=qty,
            origin=origin,
        )
