"""
Снимок рынка и фильтров в момент входа в сделку (для trade_journal / обучения).
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from prd_agent.risk.rr_enforce import rr_ratio
from prd_agent.signals.types import UnifiedSignal

logger = logging.getLogger("prd_agent.entry_snapshot")

_ANALYSIS_ROOT: Optional[Path] = None


def _ensure_analysis_imports(root: Path) -> None:
    global _ANALYSIS_ROOT
    root = root.resolve()
    if _ANALYSIS_ROOT == root:
        return
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    _ANALYSIS_ROOT = root


def _enum_label(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value).lower()
    if hasattr(value, "name"):
        return str(value.name).lower()
    return str(value).lower()


def _kline_ts_ms(k: Dict[str, Any]) -> int:
    raw = k.get("timestamp") or k.get("startTime") or k.get("open_time") or 0
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        return 0
    if ts > 0 and ts < 1_000_000_000_000:
        ts *= 1000
    return ts


def compact_candles(klines: List[Dict[str, Any]], *, max_count: int) -> List[Dict[str, Any]]:
    """Последние N свечей в компактном OHLCV для jsonl."""
    if not klines or max_count <= 0:
        return []
    tail = list(klines[-max_count:])
    out: List[Dict[str, Any]] = []
    for k in tail:
        ts = _kline_ts_ms(k)
        try:
            out.append(
                {
                    "t": ts,
                    "o": round(float(k.get("open", 0) or 0), 8),
                    "h": round(float(k.get("high", 0) or 0), 8),
                    "l": round(float(k.get("low", 0) or 0), 8),
                    "c": round(float(k.get("close", 0) or 0), 8),
                    "v": round(float(k.get("volume", 0) or 0), 4),
                }
            )
        except (TypeError, ValueError):
            continue
    return out


async def _fetch_orderflow(exchange: Any, symbol: str) -> Any:
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
        logger.warning("entry_snapshot orderflow %s: %s", symbol, exc)
        return None


async def _volume_24h(exchange: Any, symbol: str) -> float:
    if not hasattr(exchange, "get_tickers"):
        return 0.0
    sym = symbol.upper()
    try:
        for t in await exchange.get_tickers():
            if str(t.get("symbol", "")).upper() == sym:
                return float(t.get("turnover24h", 0) or 0)
    except Exception as exc:
        logger.warning("entry_snapshot volume %s: %s", sym, exc)
    return 0.0


async def build_entry_snapshot(
    *,
    exchange: Any,
    cfg: Dict[str, Any],
    symbol: str,
    side: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    sig: UnifiedSignal,
    klines: Optional[List[Dict[str, Any]]] = None,
    extra_filters: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Контекст входа + компактные свечи. Не бросает исключений — при ошибке возвращает минимум.
    """
    root = Path(cfg.get("_root", "."))
    _ensure_analysis_imports(root)

    j = cfg.get("trade_journal", {}) if isinstance(cfg.get("trade_journal"), dict) else {}
    candle_count = max(0, int(j.get("entry_candles_count", 60) or 60))
    candle_interval = str(j.get("entry_candles_interval", j.get("kline_interval", "15")) or "15")
    htf_interval = str(j.get("entry_htf_interval", "240") or "240")
    store_candles = bool(j.get("store_entry_candles", True))

    sym = symbol.upper()
    kline_limit = max(candle_count + 60, 120)
    bars = list(klines or [])
    if not bars:
        try:
            bars = list(await exchange.get_klines(sym, interval=candle_interval, limit=kline_limit))
        except Exception as exc:
            logger.warning("entry_snapshot klines %s: %s", sym, exc)
            bars = []

    htf_bars: List[Dict[str, Any]] = []
    try:
        htf_bars = list(await exchange.get_klines(sym, interval=htf_interval, limit=120))
    except Exception as exc:
        logger.warning("entry_snapshot htf %s: %s", sym, exc)

    ctx: Dict[str, Any] = {
        "symbol": sym,
        "side": str(side or "").upper(),
        "entry": float(entry or 0),
        "stop_loss": float(stop_loss or 0),
        "take_profit": float(take_profit or 0),
        "confidence": float(sig.confidence or 0),
        "source": str(sig.source or ""),
        "signal_reason": str(sig.reason or "")[:400],
    }

    try:
        from analysis.market_analyzer import MarketAnalyzer
        from analysis.structure_zones import StructureZoneAnalyzer

        ma = MarketAnalyzer()
        market = ma.analyze(bars, htf_bars if htf_bars else None)
        zone_context = StructureZoneAnalyzer().analyze(bars, float(entry or 0) or float(bars[-1]["close"]))
        ctx.update(
            {
                "atr_pct": float(getattr(market, "atr_pct", 0.0) or 0.0),
                "adx": float(getattr(market, "adx", 0.0) or 0.0),
                "rsi": float(getattr(market, "rsi", 0.0) or 0.0),
                "regime": _enum_label(getattr(market, "regime", "")),
                "trend": _enum_label(getattr(market, "trend", "")),
                "htf_trend": _enum_label(getattr(market, "htf_trend", "")),
                "volatility": _enum_label(getattr(market, "volatility", "")),
                "entry_zone": str(
                    getattr(zone_context.active_zone, "kind", "no_zone")
                    if getattr(zone_context, "active_zone", None)
                    else "no_zone"
                ),
            }
        )
    except Exception as exc:
        logger.warning("entry_snapshot market %s: %s", sym, exc)

    orderflow = await _fetch_orderflow(exchange, sym)
    if orderflow is not None:
        ctx["normalized_imbalance"] = float(getattr(orderflow, "normalized_imbalance", 0.0) or 0.0)
        ctx["spread_pct"] = float(getattr(orderflow, "spread_pct", 0.0) or 0.0)

    vol24 = await _volume_24h(exchange, sym)
    if vol24 > 0:
        ctx["volume_24h_usdt"] = round(vol24, 2)

    tz_off = int(cfg.get("timezone_offset", 3) or 3)
    ctx["local_hour"] = (datetime.now(timezone.utc).hour + tz_off) % 24
    ctx["side"] = str(side or "").upper()

    q = cfg.get("quality_gate", {}) if isinstance(cfg.get("quality_gate"), dict) else {}
    filters: Dict[str, Any] = {
        "min_confidence_gate": float(q.get("min_confidence", 0.85) or 0.85),
        "min_rr_gate": float(q.get("min_rr_ratio", 2.0) or 2.0),
        "min_volume_gate": float(q.get("min_24h_volume_usdt", 10_000_000) or 10_000_000),
        "rr_at_entry": round(rr_ratio(float(entry or 0), float(stop_loss or 0), float(take_profit or 0), side), 4),
    }
    if isinstance(sig.raw, dict) and sig.raw:
        filters["signal_raw"] = sig.raw
    if extra_filters:
        filters.update(extra_filters)
    ctx["filters"] = filters

    candles: List[Dict[str, Any]] = []
    if store_candles and bars:
        candles = compact_candles(bars, max_count=candle_count)
        if candles:
            ctx["candles_interval"] = candle_interval
            ctx["candles_count"] = len(candles)

    return ctx, candles


async def build_light_signal_snapshot(
    *,
    exchange: Any,
    cfg: Dict[str, Any],
    symbol: str,
    side: str = "",
    entry: float = 0.0,
    sig_raw: Optional[Dict[str, Any]] = None,
    klines: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Лёгкий снимок для signal_ledger: ATR, RSI, стакан.
    Без HTF и без сохранения свечей — быстрый вызов на каждый сигнал.
    """
    root = Path(cfg.get("_root", "."))
    _ensure_analysis_imports(root)

    sym = str(symbol or "").upper()
    j = cfg.get("trade_journal", {}) if isinstance(cfg.get("trade_journal"), dict) else {}
    candle_interval = str(j.get("entry_candles_interval", j.get("kline_interval", "15")) or "15")

    raw = sig_raw if isinstance(sig_raw, dict) else {}
    snap: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": sym,
        "side": str(side or "").upper(),
    }
    if entry > 0:
        snap["entry"] = float(entry)

    for key in ("atr_pct", "rsi", "adx", "normalized_imbalance", "spread_pct", "regime", "htf_trend"):
        if key in raw and raw[key] is not None:
            snap[key] = raw[key]

    bars = list(klines or [])
    if not bars:
        try:
            bars = list(await exchange.get_klines(sym, interval=candle_interval, limit=60))
        except Exception as exc:
            logger.warning("light_snapshot klines %s: %s", sym, exc)
            bars = []

    if bars:
        try:
            from analysis.market_analyzer import MarketAnalyzer

            market = MarketAnalyzer().analyze(bars, None)
            snap["atr_pct"] = round(float(getattr(market, "atr_pct", 0.0) or 0.0), 4)
            snap["rsi"] = round(float(getattr(market, "rsi", 0.0) or 0.0), 2)
            snap["adx"] = round(float(getattr(market, "adx", 0.0) or 0.0), 2)
            snap["regime"] = _enum_label(getattr(market, "regime", ""))
            snap["trend"] = _enum_label(getattr(market, "trend", ""))
            snap["mark_price"] = round(float(bars[-1].get("close", 0) or 0), 8)
        except Exception as exc:
            logger.warning("light_snapshot market %s: %s", sym, exc)

    orderflow = await _fetch_orderflow(exchange, sym)
    if orderflow is not None:
        snap["normalized_imbalance"] = round(
            float(getattr(orderflow, "normalized_imbalance", 0.0) or 0.0), 4
        )
        snap["spread_pct"] = round(float(getattr(orderflow, "spread_pct", 0.0) or 0.0), 4)

    tz_off = int(cfg.get("timezone_offset", 3) or 3)
    snap["local_hour"] = (datetime.now(timezone.utc).hour + tz_off) % 24
    return snap
