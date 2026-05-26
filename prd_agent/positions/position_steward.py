"""
Сопровождение позиций с биржи (в т.ч. открытых вручную): трейлинг SL, time-stop, breakeven по ATR.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from prd_agent.positions.exit_management import (
    ExitManagementConfig,
    age_minutes,
    effective_breakeven_pct,
    evaluate_exit_actions,
    late_retrace_active,
    profit_pct,
    progress_in_atr,
)

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
    opened_at_utc: str = ""
    peak_profit_pct: float = 0.0


class PositionSteward:
    def __init__(self, cfg: Dict[str, Any]):
        self.apply_config(cfg)

    def apply_config(self, cfg: Dict[str, Any]) -> None:
        p = cfg.get("positions", {}) if isinstance(cfg.get("positions"), dict) else {}
        self.enabled = bool(p.get("trailing_enabled", True))
        self.adopt_manual = bool(p.get("adopt_manual", True))
        self.activation_pct = float(p.get("trailing_activation_pct", 0.4))
        self.distance_pct = float(p.get("trailing_distance_pct", 0.35))
        self.distance_atr_mult = float(p.get("trailing_distance_atr_mult", 1.4))
        self.breakeven_pct = float(p.get("breakeven_after_pct", 0.25))
        self.atr_period = int(p.get("atr_period", 14))
        self.notify_trailing = bool(p.get("notify_trailing_telegram", False))
        self.exit_cfg = ExitManagementConfig.from_cfg(p)
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
        now_iso = datetime.now(timezone.utc).isoformat()
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
            opened_at_utc=now_iso,
        )

    def _calc_trailing_sl(
        self,
        pos: TrackedPosition,
        price: float,
        atr: float,
        *,
        be_pct_override: Optional[float] = None,
        distance_factor: float = 1.0,
    ) -> Optional[float]:
        entry = pos.entry
        is_long = pos.side == "Buy"
        p_pct = profit_pct(pos.side, entry, price)
        if is_long:
            pos.best_price = max(pos.best_price, price)
        else:
            pos.best_price = min(pos.best_price or entry, price)

        be_pct = be_pct_override if be_pct_override is not None else self.breakeven_pct
        if p_pct < be_pct:
            return None
        if p_pct < self.activation_pct:
            return None

        ref = pos.best_price if pos.best_price > 0 else price
        dist_pct = ref * self.distance_pct / 100 * distance_factor
        dist_atr = atr * self.distance_atr_mult * distance_factor if atr > 0 else 0.0
        dist = max(dist_pct, dist_atr)
        if dist <= 0:
            return None

        if is_long:
            new_sl = pos.best_price - dist
            if p_pct >= be_pct:
                new_sl = max(new_sl, entry * 1.001)
            if pos.stop_loss > 0:
                new_sl = max(new_sl, pos.stop_loss)
            if new_sl >= price:
                return None
            return new_sl
        new_sl = pos.best_price + dist
        if p_pct >= be_pct:
            new_sl = min(new_sl, entry * 0.999)
        if pos.stop_loss > 0:
            new_sl = min(new_sl, pos.stop_loss)
        if new_sl <= price:
            return None
        return new_sl

    def _log_note_close(self, pos: TrackedPosition, action: str, reason: str) -> None:
        logger.info(
            "EXIT %s %s action=%s reason=%s peak=%.2f%% age=%.0fm",
            pos.symbol,
            pos.side,
            action,
            reason,
            pos.peak_profit_pct,
            age_minutes(pos.opened_at_utc),
        )

    async def _try_close_position(self, exchange, pos: TrackedPosition, reason: str) -> Optional[str]:
        if not hasattr(exchange, "close_position"):
            return None
        res = await exchange.close_position(
            pos.symbol,
            pos.side,
            qty=pos.qty,
            position_idx=pos.position_idx,
        )
        if res.get("success") or res.get("orderId"):
            msg = f"⏹ Выход {pos.symbol} ({reason})"
            logger.info("%s origin=%s", msg, pos.origin)
            return msg
        err = str(res.get("error", ""))[:120]
        logger.warning("Close failed %s: %s", pos.symbol, err)
        return None

    async def manage(self, exchange, positions: List[Dict]) -> List[str]:
        """Трейлинг SL, time-stop, breakeven. Возвращает сообщения для лога/Telegram."""
        if not self.enabled and not self.exit_cfg.enabled:
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
            p_pct = profit_pct(pos.side, pos.entry, price)
            pos.peak_profit_pct = max(pos.peak_profit_pct, p_pct)
            prog_atr = progress_in_atr(pos.side, pos.entry, price, atr)

            action, action_reason = evaluate_exit_actions(
                cfg=self.exit_cfg,
                side=pos.side,
                entry=pos.entry,
                price=price,
                atr=atr,
                opened_at_iso=pos.opened_at_utc,
                peak_profit_pct=pos.peak_profit_pct,
            )
            if action and action.startswith("close_"):
                closed_msg = await self._try_close_position(exchange, pos, action_reason)
                if closed_msg:
                    if self.notify_trailing or action == "close_time_stop":
                        notes.append(closed_msg)
                    self._log_note_close(pos, action, action_reason)
                    del self._tracked[sym]
                continue

            be_override = effective_breakeven_pct(
                self.breakeven_pct,
                cfg=self.exit_cfg,
                progress_atr=prog_atr,
            )
            dist_factor = 1.0
            if late_retrace_active(
                cfg=self.exit_cfg,
                peak_profit_pct=pos.peak_profit_pct,
                current_profit_pct=p_pct,
            ):
                dist_factor = self.exit_cfg.late_tighten_distance_factor

            if not self.enabled:
                continue

            new_sl = self._calc_trailing_sl(
                pos,
                price,
                atr,
                be_pct_override=be_override,
                distance_factor=dist_factor,
            )
            if new_sl is None:
                continue
            if pos.last_sl_sent > 0 and abs(new_sl - pos.last_sl_sent) / max(pos.last_sl_sent, 1e-9) < 0.0003:
                continue
            client = exchange._client
            if not hasattr(client, "update_stop_loss"):
                continue
            res = await client.update_stop_loss(sym, new_sl, position_idx=pos.position_idx)
            if res.get("success"):
                pos.stop_loss = new_sl
                pos.last_sl_sent = new_sl
                pos.trailing_active = True
                age = age_minutes(pos.opened_at_utc)
                msg = f"🔁 Трейлинг {sym} SL→{new_sl:.4f} ({pos.origin}, {age:.0f}m)"
                if self.notify_trailing:
                    notes.append(msg)
                logger.info(msg)
        return notes
