"""
Единый журнал сделок: JSONL + строки в bot.log для analyze_bot_log.py.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prd_agent.trades")


class TradeJournal:
    """Запись входов/выходов в data/trades/trade_history.jsonl."""

    def __init__(self, data_dir: Path, cfg: Optional[Dict[str, Any]] = None):
        self.dir = data_dir / "trades"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "trade_history.jsonl"
        self._pending: Dict[str, Dict[str, Any]] = {}
        j = (cfg or {}).get("trade_journal", {}) if isinstance((cfg or {}).get("trade_journal"), dict) else {}
        self._rotate_max_mb = float(j.get("rotate_max_mb", 8.0))
        self._rotate_keep_files = max(3, int(j.get("rotate_keep_files", 14)))
        self._store_entry_context = bool(j.get("store_entry_context", True))
        self._store_entry_candles = bool(j.get("store_entry_candles", True))

    def _maybe_rotate(self) -> None:
        if self._rotate_max_mb <= 0 or not self.path.exists():
            return
        limit_bytes = int(self._rotate_max_mb * 1024 * 1024)
        if self.path.stat().st_size < limit_bytes:
            return
        archive_dir = self.dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        target = archive_dir / f"trade_history_{stamp}.jsonl"
        shutil.move(str(self.path), str(target))
        self.path.touch()
        archives = sorted(archive_dir.glob("trade_history_*.jsonl"), key=lambda p: p.stat().st_mtime)
        while len(archives) > self._rotate_keep_files:
            old = archives.pop(0)
            old.unlink(missing_ok=True)
        logger.info("Trade journal rotated -> %s", target.name)

    def _append(self, row: Dict[str, Any]) -> None:
        self._maybe_rotate()
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
        origin: str = "bot",
        entry_context: Optional[Dict[str, Any]] = None,
        entry_candles: Optional[List[Dict[str, Any]]] = None,
        ledger_id: str = "",
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
            "origin": origin,
        }
        if ledger_id:
            row["ledger_id"] = ledger_id
        if self._store_entry_context and isinstance(entry_context, dict) and entry_context:
            row["entry_context"] = entry_context
        if self._store_entry_candles and entry_candles:
            row["entry_candles"] = entry_candles
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
        exit_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        sym = symbol.upper()
        logger.info("CLOSED %s: pnl=$%.2f reason=%s", sym, pnl, reason)
        if not source and order_id and order_id in self._pending:
            source = str(self._pending[order_id].get("source", ""))
        pending = None
        if order_id and order_id in self._pending:
            pending = self._pending[order_id]
        if pending is None:
            key = f"{sym}:{side}" if side else sym
            pending = self._pending.get(key) or self._pending.get(sym)
        if not source and pending:
            source = str(pending.get("source", ""))
        closed_row: Dict[str, Any] = {
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
        if pending:
            if pending.get("entry_context"):
                closed_row["entry_context"] = pending["entry_context"]
            if pending.get("entry_candles"):
                closed_row["entry_candles"] = pending["entry_candles"]
            if pending.get("confidence") is not None:
                closed_row["confidence"] = pending.get("confidence")
            if pending.get("stop_loss"):
                closed_row["stop_loss"] = pending.get("stop_loss")
            if pending.get("take_profit"):
                closed_row["take_profit"] = pending.get("take_profit")
            if pending.get("leverage"):
                closed_row["leverage"] = pending.get("leverage")
        if isinstance(exit_context, dict) and exit_context:
            closed_row["exit_context"] = exit_context
        self._append(closed_row)
        if order_id and order_id in self._pending:
            del self._pending[order_id]
        key = f"{sym}:{side}" if side else sym
        self._pending.pop(key, None)
        self._pending.pop(sym, None)

    def peek_pending(
        self,
        symbol: str,
        *,
        side: str = "",
        order_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        sym = symbol.upper()
        if order_id and order_id in self._pending:
            return dict(self._pending[order_id])
        key = f"{sym}:{side}" if side else sym
        row = self._pending.get(key) or self._pending.get(sym)
        return dict(row) if row else None

    def record_closed_from_exchange(
        self,
        row: Dict[str, Any],
        *,
        origin: str = "bot",
        exit_context: Optional[Dict[str, Any]] = None,
    ) -> None:
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
            exit_context=exit_context,
        )
