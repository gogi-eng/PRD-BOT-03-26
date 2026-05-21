"""
Сопровождение позиций с биржи (в т.ч. открытых вручную): трейлинг SL через Bybit API.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("prd_agent.positions")


@dataclass
class TrackedPosition:
    symbol: str
    side: str
    entry: float
    qty: float
    stop_loss: float = 0.0
    best_price: float = 0.0
    trailing_active: bool = False
    position_idx: int = 0
    origin: str = "manual"
    last_sl_sent: float = 0.0


class PositionSteward:
    def __init__(self, cfg: Dict[str, Any]):
        p = cfg.get("positions", {})
        self.enabled = bool(p.get("trailing_enabled", True))
        self.adopt_manual = bool(p.get("adopt_manual", True))
        self.activation_pct = float(p.get("trailing_activation_pct", 0.4))
        self.distance_pct = float(p.get("trailing_distance_pct", 0.35))
        self.breakeven_pct = float(p.get("breakeven_after_pct", 0.25))
        self.atr_period = int(p.get("atr_period", 14))
        self.notify_trailing = bool(p.get("notify_trailing_telegram", False))
        self._tracked: Dict[str, TrackedPosition] = {}
        self._bot_symbols: set[str] = set()

    def mark_bot_opened(self, symbol: str) -> None:
        self._bot_symbols.add(symbol.upper())

    @staticmethod
    def _atr_from_klines(klines: List[Dict], period: int = 14) -> float:
        if not klines or len(klines) < period + 2:
            return 0.0
        df = pd.DataFrame(klines)
        if "high" not in df.columns or "low" not in df.columns or "close" not in df.columns:
            return 0.0
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        prev = close.shift(1)
        tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if pd.notna(atr) else 0.0

    def _adopt_from_exchange(self, row: Dict) -> Optional[TrackedPosition]:
        sym = str(row.get("symbol", "")).upper()
        size = float(row.get("size", 0) or 0)
        if not sym or size <= 0:
            return None
        side_raw = str(row.get("side", "")).lower()
        side = "Buy" if side_raw == "buy" else "Sell"
        entry = float(row.get("avgPrice") or row.get("entryPrice") or row.get("markPrice") or 0)
        if entry <= 0:
            return None
        sl = float(row.get("stopLoss") or 0)
        origin = "bot" if sym in self._bot_symbols else "manual"
        if origin == "manual" and not self.adopt_manual:
            return None
        mark = float(row.get("markPrice") or entry)
        return TrackedPosition(
            symbol=sym,
            side=side,
            entry=entry,
            qty=size,
            stop_loss=sl,
            best_price=mark,
            position_idx=int(row.get("positionIdx", 0) or 0),
            origin=origin,
            last_sl_sent=sl,
        )

    def _calc_trailing_sl(self, pos: TrackedPosition, price: float, atr: float) -> Optional[float]:
        entry = pos.entry
        is_long = pos.side == "Buy"
        if is_long:
            profit_pct = (price - entry) / entry * 100
            pos.best_price = max(pos.best_price, price)
        else:
            profit_pct = (entry - price) / entry * 100
            pos.best_price = min(pos.best_price or entry, price)

        if profit_pct < self.breakeven_pct:
            return None
        if profit_pct < self.activation_pct:
            return None

        dist = max(entry * self.distance_pct / 100, atr * 0.8 if atr > 0 else 0)
        if dist <= 0:
            return None

        if is_long:
            new_sl = pos.best_price - dist
            if profit_pct >= self.breakeven_pct:
                new_sl = max(new_sl, entry * 1.001)
            if pos.stop_loss > 0:
                new_sl = max(new_sl, pos.stop_loss)
            if new_sl >= price:
                return None
            return new_sl
        new_sl = pos.best_price + dist
        if profit_pct >= self.breakeven_pct:
            new_sl = min(new_sl, entry * 0.999)
        if pos.stop_loss > 0:
            new_sl = min(new_sl, pos.stop_loss)
        if new_sl <= price:
            return None
        return new_sl

    async def manage(self, exchange, positions: List[Dict]) -> List[str]:
        """Обновляет трейлинг SL. Возвращает список сообщений для лога/Telegram."""
        if not self.enabled:
            return []
        notes: List[str] = []
        live_syms = set()
        for row in positions:
            sym = str(row.get("symbol", "")).upper()
            if sym:
                live_syms.add(sym)

        for sym in list(self._tracked.keys()):
            if sym not in live_syms:
                del self._tracked[sym]

        for row in positions:
            adopted = self._adopt_from_exchange(row)
            if not adopted:
                continue
            sym = adopted.symbol
            if sym not in self._tracked:
                self._tracked[sym] = adopted
                notes.append(f"📌 Подхвачена позиция {sym} {adopted.side} ({adopted.origin})")
                logger.info("Adopted position %s %s origin=%s", sym, adopted.side, adopted.origin)
            else:
                t = self._tracked[sym]
                t.qty = adopted.qty
                t.entry = adopted.entry
                if adopted.stop_loss > 0:
                    t.stop_loss = adopted.stop_loss

        for sym, pos in list(self._tracked.items()):
            row = next((p for p in positions if str(p.get("symbol", "")).upper() == sym), None)
            if not row:
                continue
            price = float(row.get("markPrice") or pos.entry)
            klines = await exchange.get_klines(sym, interval="15", limit=80)
            atr = self._atr_from_klines(klines, self.atr_period)
            new_sl = self._calc_trailing_sl(pos, price, atr)
            if new_sl is None:
                continue
            if pos.last_sl_sent > 0 and abs(new_sl - pos.last_sl_sent) / pos.last_sl_sent < 0.0003:
                continue
            client = exchange._client
            if not hasattr(client, "update_stop_loss"):
                continue
            res = await client.update_stop_loss(sym, new_sl, position_idx=pos.position_idx)
            if res.get("success"):
                pos.stop_loss = new_sl
                pos.last_sl_sent = new_sl
                pos.trailing_active = True
                msg = f"🔁 Трейлинг {sym} SL→{new_sl:.4f} ({pos.origin})"
                if self.notify_trailing:
                    notes.append(msg)
                logger.info(msg)
        return notes
