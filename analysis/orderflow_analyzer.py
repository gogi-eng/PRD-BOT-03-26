#!/usr/bin/env python3
"""Orderflow analysis for orderbook and recent trades."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("analysis.orderflow")


@dataclass
class OrderflowSnapshot:
    orderbook_ratio: float = 1.0
    trade_ratio: float = 1.0
    bullish_ratio: float = 1.0
    bearish_ratio: float = 1.0
    imbalance_score: float = 0.0
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    trade_delta: float = 0.0
    volume_spike: float = 1.0
    spread_pct: float = 0.0
    dominant_side: str = "neutral"
    normalized_imbalance: float = 0.0  # (Buy-Sell)/(Buy+Sell), range [-1, +1]


class OrderflowAnalyzer:
    """Builds a compact orderflow snapshot from Bybit orderbook and trades."""

    def __init__(self, depth_levels: int = 25):
        self.depth_levels = depth_levels

    def analyze(self, orderbook: Dict, trades: List[Dict]) -> OrderflowSnapshot:
        bids = orderbook.get("bids", [])[: self.depth_levels]
        asks = orderbook.get("asks", [])[: self.depth_levels]

        bid_volume = sum(float(level[1]) for level in bids if len(level) >= 2)
        ask_volume = sum(float(level[1]) for level in asks if len(level) >= 2)
        orderbook_ratio = bid_volume / ask_volume if ask_volume > 0 else 1.0

        buy_volume = 0.0
        sell_volume = 0.0
        sizes: List[float] = []
        # Track recent aggressive trades for absorption detection
        recent_buy_vol = 0.0
        recent_sell_vol = 0.0
        for i, trade in enumerate(trades):
            size = float(trade.get("size", 0.0))
            side = str(trade.get("side", "")).lower()
            sizes.append(size)
            if side == "buy":
                buy_volume += size
                if i < 30:
                    recent_buy_vol += size
            elif side == "sell":
                sell_volume += size
                if i < 30:
                    recent_sell_vol += size

        trade_ratio = buy_volume / sell_volume if sell_volume > 0 else (2.0 if buy_volume > 0 else 1.0)
        bearish_trade_ratio = sell_volume / buy_volume if buy_volume > 0 else (2.0 if sell_volume > 0 else 1.0)
        trade_delta = buy_volume - sell_volume

        # Normalized imbalance: (Buy - Sell) / (Buy + Sell)
        total_trade_volume = buy_volume + sell_volume
        normalized_imbalance = (buy_volume - sell_volume) / total_trade_volume if total_trade_volume > 0 else 0.0

        # Recent flow imbalance (last 30 trades) — detects absorption
        recent_total = recent_buy_vol + recent_sell_vol
        recent_imbalance = (recent_buy_vol - recent_sell_vol) / recent_total if recent_total > 0 else 0.0

        recent = sizes[:10] if sizes else []
        baseline = sizes[10:50] if len(sizes) > 10 else sizes
        recent_avg = sum(recent) / len(recent) if recent else 0.0
        baseline_avg = sum(baseline) / len(baseline) if baseline else recent_avg
        volume_spike = recent_avg / baseline_avg if baseline_avg > 0 else 1.0

        best_bid = float(bids[0][0]) if bids and len(bids[0]) >= 2 else 0.0
        best_ask = float(asks[0][0]) if asks and len(asks[0]) >= 2 else 0.0
        mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0
        spread_pct = ((best_ask - best_bid) / mid * 100) if mid > 0 else 0.0

        # Weighted imbalance: 60% recent + 40% total (recent trades matter more)
        weighted_imb = recent_imbalance * 0.6 + normalized_imbalance * 0.4

        # Combined imbalance: orderbook + trade flow
        orderbook_edge = orderbook_ratio - 1.0
        trade_edge = trade_ratio - 1.0
        imbalance_score = orderbook_edge * 0.40 + trade_edge * 0.35 + weighted_imb * 0.25

        dominant_side = "neutral"
        if weighted_imb >= 0.15:
            dominant_side = "bullish"
        elif weighted_imb <= -0.15:
            dominant_side = "bearish"

        return OrderflowSnapshot(
            orderbook_ratio=round(orderbook_ratio, 4),
            trade_ratio=round(trade_ratio, 4),
            bullish_ratio=round(max(orderbook_ratio, trade_ratio), 4),
            bearish_ratio=round(max((1 / orderbook_ratio) if orderbook_ratio > 0 else 0.0, bearish_trade_ratio), 4),
            imbalance_score=round(imbalance_score, 4),
            bid_volume=round(bid_volume, 4),
            ask_volume=round(ask_volume, 4),
            buy_volume=round(buy_volume, 4),
            sell_volume=round(sell_volume, 4),
            trade_delta=round(trade_delta, 4),
            volume_spike=round(volume_spike, 4),
            spread_pct=round(spread_pct, 5),
            dominant_side=dominant_side,
            normalized_imbalance=round(weighted_imb, 4),
        )


def _level_price_size(level: Any) -> tuple[float, float]:
    if not isinstance(level, (list, tuple)) or len(level) < 2:
        return 0.0, 0.0
    try:
        return float(level[0]), float(level[1])
    except (TypeError, ValueError):
        return 0.0, 0.0


def narrow_band_book(orderbook: Dict, *, band_bps: float = 20.0) -> Optional[Dict[str, float]]:
    """USD бидов и асков в узкой полосе от середины и спред в процентах."""
    if not isinstance(orderbook, dict):
        return None
    bids = list(orderbook.get("bids") or [])
    asks = list(orderbook.get("asks") or [])
    if not bids or not asks:
        return None
    best_bid, _bid_sz = _level_price_size(bids[0])
    best_ask, _ask_sz = _level_price_size(asks[0])
    if best_bid <= 0 or best_ask <= 0:
        return None
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return None
    spread_pct = (best_ask - best_bid) / mid * 100.0
    band = max(0.0, float(band_bps)) / 10000.0
    lo = mid * (1.0 - band)
    hi = mid * (1.0 + band)

    def _usd(levels: List) -> float:
        total = 0.0
        for level in levels:
            price, size = _level_price_size(level)
            if price <= 0 or size <= 0:
                continue
            if price < lo or price > hi:
                continue
            total += price * size
        return total

    bid_usd = _usd(bids)
    ask_usd = _usd(asks)
    denom = bid_usd + ask_usd
    imbalance = (bid_usd - ask_usd) / denom if denom > 0 else 0.0
    return {
        "bid_usd": bid_usd,
        "ask_usd": ask_usd,
        "imbalance": imbalance,
        "spread_pct": spread_pct,
        "mid": mid,
    }


def book_gate_settings(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        block = cfg.get("orderbook_imbalance_gate")
        if isinstance(block, dict):
            raw = block
    return {
        "enabled": bool(raw.get("enabled", True)),
        "band_bps": float(raw.get("band_bps", 20.0)),
        "skew_min": float(raw.get("skew_min", 0.55)),
        "max_spread_pct": float(raw.get("max_spread_pct", 0.35)),
    }


def book_entry_block_reason(
    orderbook: Optional[Dict],
    side: str,
    *,
    band_bps: float = 20.0,
    skew_min: float = 0.55,
    max_spread_pct: float = 0.35,
) -> str:
    """Пустая строка — вход по стакану не режем.

    Почти ровная книга проходит. Запрет только при явном перекосе против
    стороны сигнала или при широком спреде. Пустой стакан — не запрет.
    """
    if not orderbook:
        return ""
    snap = narrow_band_book(orderbook, band_bps=band_bps)
    if snap is None:
        return ""
    spread = float(snap["spread_pct"])
    if max_spread_pct > 0 and spread > max_spread_pct + 1e-9:
        return f"стакан: спред {spread:.3f}% шире {max_spread_pct:.3f}%"
    imb = float(snap["imbalance"])
    skew = abs(float(skew_min))
    is_buy = str(side or "").upper() in ("BUY", "LONG")
    if is_buy and imb <= -skew:
        return f"стакан против Buy: перекос {imb:.2f} (порог -{skew:.2f})"
    if (not is_buy) and imb >= skew:
        return f"стакан против Sell: перекос {imb:.2f} (порог {skew:.2f})"
    return ""


def ws_book_entry_block(exchange: Any, symbol: str, side: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """Запрет входа только по книге WebSocket. Без книги проверку пропускаем, REST не зовём."""
    gate = book_gate_settings(cfg)
    if not gate["enabled"]:
        return ""
    peek = getattr(exchange, "peek_orderbook", None)
    if not callable(peek):
        return ""
    try:
        book = peek(symbol)
    except Exception as exc:
        logger.warning("peek orderbook %s: %s", symbol, exc)
        return ""
    if not book:
        return ""
    return book_entry_block_reason(
        book,
        side,
        band_bps=float(gate["band_bps"]),
        skew_min=float(gate["skew_min"]),
        max_spread_pct=float(gate["max_spread_pct"]),
    )