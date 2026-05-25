"""Снимок всех открытых позиций на Bybit (бот + ручные)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set


def _pos_size(p: Dict[str, Any]) -> float:
    for key in ("size", "qty", "positionQty"):
        v = float(p.get(key, 0) or 0)
        if v > 0:
            return v
    avg = float(p.get("avgPrice", 0) or p.get("entryPrice", 0) or 0)
    pval = float(p.get("positionValue", 0) or 0)
    if pval > 0 and avg > 0:
        return pval / avg
    return 0.0


class PositionTracker:
    def __init__(self, store_dir: Path):
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_path = self.store_dir / "exchange_positions.json"
        self.history_path = self.store_dir / "position_snapshots.jsonl"

    async def sync(
        self, exchange, bot_symbols: Set[str]
    ) -> Dict[str, Any]:
        positions = await exchange.get_positions()
        rows: List[Dict[str, Any]] = []
        for p in positions:
            sym = str(p.get("symbol", "")).upper()
            size = _pos_size(p)
            if not sym or size <= 0:
                continue
            origin = "bot" if sym in bot_symbols else "manual"
            rows.append(
                {
                    "symbol": sym,
                    "side": p.get("side", ""),
                    "size": size,
                    "entry": float(p.get("avgPrice") or p.get("entryPrice") or 0),
                    "mark": float(p.get("markPrice", 0) or 0),
                    "upnl": float(p.get("unrealisedPnl", 0) or 0),
                    "stop_loss": float(p.get("stopLoss", 0) or 0),
                    "take_profit": float(p.get("takeProfit", 0) or 0),
                    "origin": origin,
                }
            )
        snap = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "count": len(rows),
            "bot": sum(1 for r in rows if r["origin"] == "bot"),
            "manual": sum(1 for r in rows if r["origin"] == "manual"),
            "positions": rows,
        }
        self.snapshot_path.write_text(
            json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        return snap
