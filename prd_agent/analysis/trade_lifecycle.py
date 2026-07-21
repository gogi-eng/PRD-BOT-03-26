"""
Жизненный цикл сделки: MFE/MAE, периодические снимки стакана, exit_context в journal.

Дополняет trade_journal — не заменяет entry_snapshot на входе.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, TYPE_CHECKING

from prd_agent.positions.exit_management import age_minutes, profit_pct

if TYPE_CHECKING:
    from prd_agent.positions.position_steward import TrackedPosition

logger = logging.getLogger("prd_agent.trade_lifecycle")


def _side_norm(side: str) -> str:
    s = str(side or "").strip().upper()
    if s in ("LONG", "BUY"):
        return "Buy"
    if s in ("SHORT", "SELL"):
        return "Sell"
    return str(side or "").strip()


def _state_key(symbol: str, side: str) -> str:
    return f"{str(symbol).upper()}:{_side_norm(side)}"


def _zone_to_dict(zone: Any) -> Dict[str, Any]:
    if zone is None:
        return {}
    return {
        "kind": str(getattr(zone, "kind", "") or ""),
        "bias": str(getattr(zone, "bias", "") or ""),
        "low": round(float(getattr(zone, "low", 0) or 0), 8),
        "high": round(float(getattr(zone, "high", 0) or 0), 8),
        "strength": round(float(getattr(zone, "strength", 0) or 0), 4),
        "mitigated": bool(getattr(zone, "mitigated", False)),
    }


def serialize_zone_context(zone_context: Any, price: float, side: str) -> Dict[str, Any]:
    """Компактный SMC-снимок для entry_context / анализа."""
    if zone_context is None or price <= 0:
        return {"entry_zone": "no_zone"}
    side_u = _side_norm(side)
    active = None
    if side_u == "Buy":
        active = zone_context.price_in_bullish_zone(price)
        if active is None and hasattr(zone_context, "price_near_bullish_zone"):
            active = zone_context.price_near_bullish_zone(price)
    else:
        active = zone_context.price_in_bearish_zone(price)
        if active is None and hasattr(zone_context, "price_near_bearish_zone"):
            active = zone_context.price_near_bearish_zone(price)

    supports = sorted(
        [float(s) for s in (getattr(zone_context, "support_levels", None) or []) if float(s) < price],
        reverse=True,
    )[:2]
    resistances = sorted(
        [float(r) for r in (getattr(zone_context, "resistance_levels", None) or []) if float(r) > price],
    )[:2]

    out: Dict[str, Any] = {
        "entry_zone": (
            f"{active.kind}_{active.bias}" if active else "no_zone"
        ),
        "active_zone": _zone_to_dict(active) if active else None,
        "support_near": [round(x, 8) for x in supports],
        "resistance_near": [round(x, 8) for x in resistances],
    }
    for attr in ("bullish_ob", "bearish_ob", "bullish_fvg", "bearish_fvg"):
        z = getattr(zone_context, attr, None)
        if z is not None and not bool(getattr(z, "mitigated", False)):
            out[attr] = _zone_to_dict(z)
    return out


def serialize_orderflow(orderflow: Any) -> Dict[str, Any]:
    if orderflow is None:
        return {}
    return {
        "normalized_imbalance": round(float(getattr(orderflow, "normalized_imbalance", 0) or 0), 4),
        "spread_pct": round(float(getattr(orderflow, "spread_pct", 0) or 0), 4),
        "orderbook_ratio": round(float(getattr(orderflow, "orderbook_ratio", 0) or 0), 4),
        "trade_delta": round(float(getattr(orderflow, "trade_delta", 0) or 0), 4),
        "volume_spike": round(float(getattr(orderflow, "volume_spike", 0) or 0), 4),
        "dominant_side": str(getattr(orderflow, "dominant_side", "") or ""),
        "bid_volume": round(float(getattr(orderflow, "bid_volume", 0) or 0), 4),
        "ask_volume": round(float(getattr(orderflow, "ask_volume", 0) or 0), 4),
        "buy_volume": round(float(getattr(orderflow, "buy_volume", 0) or 0), 4),
        "sell_volume": round(float(getattr(orderflow, "sell_volume", 0) or 0), 4),
    }


def volume_gate_ratio(volume_24h_usdt: float, min_gate: float) -> float:
    if min_gate <= 0 or volume_24h_usdt <= 0:
        return 0.0
    return round(volume_24h_usdt / min_gate, 3)


async def _fetch_orderflow_for_sample(exchange: Any, symbol: str) -> Any:
    client = getattr(exchange, "_client", None)
    if client is None:
        return None
    if not hasattr(client, "get_orderbook") or not hasattr(client, "get_recent_trades"):
        return None
    try:
        from analysis.orderflow_analyzer import OrderflowAnalyzer

        orderbook = await client.get_orderbook(symbol, limit=25)
        trades = await client.get_recent_trades(symbol, limit=80)
        return OrderflowAnalyzer().analyze(orderbook, trades)
    except Exception as exc:
        logger.warning("lifecycle orderflow %s: %s", symbol, exc)
        return None


async def _volume_24h_for_sample(exchange: Any, symbol: str) -> float:
    if not hasattr(exchange, "get_tickers"):
        return 0.0
    sym = symbol.upper()
    try:
        for t in await exchange.get_tickers():
            if str(t.get("symbol", "")).upper() == sym:
                return float(t.get("turnover24h", 0) or 0)
    except Exception as exc:
        logger.warning("lifecycle volume %s: %s", sym, exc)
    return 0.0


def build_exit_context(
    *,
    side: str,
    entry: float,
    exit_price: float,
    pnl_usdt: float,
    reason: str,
    mfe_pct: float,
    mae_pct: float,
    peak_profit_pct: float,
    hold_minutes: float,
    leverage: int = 0,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
    sample_count: int = 0,
) -> Dict[str, Any]:
    side_n = _side_norm(side)
    pnl_pct = profit_pct(side_n, entry, exit_price) if entry > 0 and exit_price > 0 else 0.0
    rr_realized = 0.0
    if entry > 0 and stop_loss > 0 and exit_price > 0:
        risk = abs(entry - stop_loss)
        if risk > 0:
            reward = (exit_price - entry) if side_n == "Buy" else (entry - exit_price)
            rr_realized = round(reward / risk, 4)
    return {
        "exit_mark": round(float(exit_price or 0), 8),
        "pnl_usdt": round(float(pnl_usdt or 0), 6),
        "pnl_pct": round(pnl_pct, 4),
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "peak_profit_pct": round(peak_profit_pct, 4),
        "hold_minutes": round(hold_minutes, 2),
        "leverage": int(leverage or 0),
        "rr_realized": rr_realized,
        "stop_loss": round(float(stop_loss or 0), 8),
        "take_profit": round(float(take_profit or 0), 8),
        "close_reason": str(reason or "")[:200],
        "lifecycle_samples": int(sample_count),
    }


@dataclass
class LifecycleState:
    symbol: str
    side: str
    entry: float
    leverage: int = 0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    peak_profit_pct: float = 0.0
    min_profit_pct: float = 0.0
    opened_at_utc: str = ""
    last_sample_at: float = 0.0
    sample_count: int = 0
    stop_loss: float = 0.0
    take_profit: float = 0.0


@dataclass
class TradeLifecycleConfig:
    enabled: bool = True
    sample_interval_sec: float = 300.0
    store_periodic_samples: bool = True
    bot_positions_only: bool = True

    @classmethod
    def from_cfg(cls, cfg: Mapping[str, Any]) -> TradeLifecycleConfig:
        raw = cfg.get("trade_lifecycle")
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            sample_interval_sec=float(raw.get("sample_interval_sec", 300) or 300),
            store_periodic_samples=bool(raw.get("store_periodic_samples", True)),
            bot_positions_only=bool(raw.get("bot_positions_only", True)),
        )


class TradeLifecycleTracker:
    """Накопление статистики по открытым позициям + exit_context при закрытии."""

    def __init__(self, data_dir: Path, cfg: Optional[Dict[str, Any]] = None):
        self._data_dir = data_dir
        self._states: Dict[str, LifecycleState] = {}
        self.apply_config(cfg or {})

    def apply_config(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self._cfg = TradeLifecycleConfig.from_cfg(cfg)
        trades_dir = self._data_dir / "trades"
        trades_dir.mkdir(parents=True, exist_ok=True)
        self._samples_path = trades_dir / "trade_lifecycle.jsonl"

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    def _ensure_state(self, pos: "TrackedPosition", *, leverage: int = 0) -> LifecycleState:
        key = _state_key(pos.symbol, pos.side)
        st = self._states.get(key)
        if st is None:
            st = LifecycleState(
                symbol=pos.symbol.upper(),
                side=_side_norm(pos.side),
                entry=float(pos.entry or 0),
                leverage=int(leverage or 0),
                opened_at_utc=str(pos.opened_at_utc or ""),
                stop_loss=float(pos.stop_loss or 0),
                take_profit=float(pos.take_profit or 0),
                peak_profit_pct=float(pos.peak_profit_pct or 0),
            )
            self._states[key] = st
        else:
            if pos.stop_loss > 0:
                st.stop_loss = float(pos.stop_loss)
            if pos.take_profit > 0:
                st.take_profit = float(pos.take_profit)
            if leverage > 0:
                st.leverage = leverage
        return st

    def update_mark_prices(
        self,
        positions: List[Dict[str, Any]],
        tracked: Dict[str, "TrackedPosition"],
        *,
        bot_symbols: Optional[set[str]] = None,
        pending_leverage: Optional[Dict[str, int]] = None,
    ) -> None:
        if not self.enabled:
            return
        bot_symbols = bot_symbols or set()
        pending_leverage = pending_leverage or {}
        live: Dict[str, Dict[str, Any]] = {}
        for row in positions:
            sym = str(row.get("symbol", "")).upper()
            if sym:
                live[sym] = row

        for sym, pos in tracked.items():
            if self._cfg.bot_positions_only and sym not in bot_symbols:
                continue
            row = live.get(sym)
            if not row:
                continue
            price = float(row.get("markPrice") or pos.entry or 0)
            if price <= 0:
                continue
            lev = int(pending_leverage.get(sym, 0) or 0)
            st = self._ensure_state(pos, leverage=lev)
            p_pct = profit_pct(st.side, st.entry, price)
            st.mfe_pct = max(st.mfe_pct, p_pct)
            st.mae_pct = min(st.mae_pct, p_pct)
            st.peak_profit_pct = max(st.peak_profit_pct, p_pct)
            st.min_profit_pct = min(st.min_profit_pct, p_pct)

        stale = [k for k in self._states if k.split(":", 1)[0] not in live]
        for key in stale:
            if key.split(":", 1)[0] not in tracked:
                pass

    def pop_exit_context(
        self,
        symbol: str,
        side: str,
        *,
        exit_price: float,
        pnl_usdt: float,
        reason: str,
        pending: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        key = _state_key(symbol, side)
        st = self._states.pop(key, None)
        if st is None:
            entry = float((pending or {}).get("entry", 0) or 0)
            lev = int((pending or {}).get("leverage", 0) or 0)
            sl = float((pending or {}).get("stop_loss", 0) or 0)
            tp = float((pending or {}).get("take_profit", 0) or 0)
            opened = str((pending or {}).get("opened_at_utc", "") or "")
            if entry <= 0 and pending:
                entry = float(pending.get("entry", 0) or 0)
            hold = age_minutes(opened) if opened else 0.0
            pnl_pct = profit_pct(_side_norm(side), entry, exit_price) if entry > 0 else 0.0
            return build_exit_context(
                side=side,
                entry=entry,
                exit_price=exit_price,
                pnl_usdt=pnl_usdt,
                reason=reason,
                mfe_pct=max(0.0, pnl_pct),
                mae_pct=min(0.0, pnl_pct),
                peak_profit_pct=max(0.0, pnl_pct),
                hold_minutes=hold,
                leverage=lev,
                stop_loss=sl,
                take_profit=tp,
                sample_count=0,
            )

        hold = age_minutes(st.opened_at_utc) if st.opened_at_utc else 0.0
        ctx = build_exit_context(
            side=st.side,
            entry=st.entry,
            exit_price=exit_price,
            pnl_usdt=pnl_usdt,
            reason=reason,
            mfe_pct=st.mfe_pct,
            mae_pct=st.mae_pct,
            peak_profit_pct=st.peak_profit_pct,
            hold_minutes=hold,
            leverage=st.leverage,
            stop_loss=st.stop_loss,
            take_profit=st.take_profit,
            sample_count=st.sample_count,
        )
        return ctx

    async def maybe_sample(
        self,
        exchange: Any,
        positions: List[Dict[str, Any]],
        tracked: Dict[str, "TrackedPosition"],
        *,
        bot_symbols: Optional[set[str]] = None,
    ) -> None:
        if not self.enabled or not self._cfg.store_periodic_samples:
            return
        bot_symbols = bot_symbols or set()
        now = time.time()
        interval = max(60.0, self._cfg.sample_interval_sec)

        for sym, pos in tracked.items():
            if self._cfg.bot_positions_only and sym not in bot_symbols:
                continue
            row = next((p for p in positions if str(p.get("symbol", "")).upper() == sym), None)
            if not row:
                continue
            key = _state_key(sym, pos.side)
            st = self._states.get(key)
            if st is None:
                st = self._ensure_state(pos)
            if now - st.last_sample_at < interval:
                continue
            mark = float(row.get("markPrice") or pos.entry or 0)
            sample: Dict[str, Any] = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "lifecycle_sample",
                "symbol": sym,
                "side": st.side,
                "mark": round(mark, 8),
                "mfe_pct": round(st.mfe_pct, 4),
                "mae_pct": round(st.mae_pct, 4),
                "peak_profit_pct": round(st.peak_profit_pct, 4),
            }
            try:
                of = await _fetch_orderflow_for_sample(exchange, sym)
                if of is not None:
                    sample["orderflow"] = serialize_orderflow(of)
                vol = await _volume_24h_for_sample(exchange, sym)
                if vol > 0:
                    sample["volume_24h_usdt"] = round(vol, 2)
            except Exception as exc:
                logger.warning("lifecycle sample %s: %s", sym, exc)

            st.last_sample_at = now
            st.sample_count += 1
            try:
                with self._samples_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(sample, ensure_ascii=False) + "\n")
            except OSError as exc:
                logger.warning("lifecycle sample write: %s", exc)
