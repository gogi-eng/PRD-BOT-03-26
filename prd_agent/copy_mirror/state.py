"""Персистентное состояние зеркала (отдельно от trading_bot)."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass
class WatchedPosition:
    symbol: str
    side: str
    first_seen_ts: float
    entry: float = 0.0
    status: str = "watching"  # watching | mirrored | skipped
    skip_reason: str = ""
    mirrored_qty: float = 0.0
    peak_profit_pct: float = 0.0


class MirrorStateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._items: Dict[str, WatchedPosition] = {}
        self.load()

    @staticmethod
    def _key(symbol: str, side: str) -> str:
        return f"{symbol.upper()}:{side}"

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in (raw.get("positions") or {}).items():
                self._items[k] = WatchedPosition(**v)
        except (json.JSONDecodeError, TypeError, ValueError):
            self._items = {}

    def save(self) -> None:
        payload = {
            "updated_ts": time.time(),
            "positions": {k: asdict(v) for k, v in self._items.items()},
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get(self, symbol: str, side: str) -> Optional[WatchedPosition]:
        return self._items.get(self._key(symbol, side))

    def upsert_watch(self, symbol: str, side: str, entry: float) -> WatchedPosition:
        k = self._key(symbol, side)
        cur = self._items.get(k)
        if cur and cur.status in ("mirrored", "skipped"):
            return cur
        if not cur:
            cur = WatchedPosition(
                symbol=symbol.upper(),
                side=side,
                first_seen_ts=time.time(),
                entry=entry,
                status="watching",
            )
            self._items[k] = cur
        return cur

    def mark_mirrored(self, symbol: str, side: str, qty: float) -> None:
        k = self._key(symbol, side)
        w = self._items.get(k)
        if not w:
            return
        w.status = "mirrored"
        w.mirrored_qty = qty
        w.skip_reason = ""

    def mark_skipped(self, symbol: str, side: str, reason: str) -> None:
        k = self._key(symbol, side)
        w = self._items.get(k)
        if not w:
            w = WatchedPosition(
                symbol=symbol.upper(),
                side=side,
                first_seen_ts=time.time(),
                status="skipped",
            )
            self._items[k] = w
        w.status = "skipped"
        w.skip_reason = reason[:200]

    def remove(self, symbol: str, side: str) -> None:
        self._items.pop(self._key(symbol, side), None)

    def update_peak(self, symbol: str, side: str, profit_pct: float) -> None:
        w = self._items.get(self._key(symbol, side))
        if w:
            w.peak_profit_pct = max(w.peak_profit_pct, profit_pct)

    def iter_tracked(self):
        return list(self._items.values())

    def prune_missing_source(self, live_keys: set[str]) -> None:
        for key in list(self._items.keys()):
            if key not in live_keys:
                self._items.pop(key, None)
