"""Перед неторговым окном: закрыть убыточные, прибыльные с трендом оставить."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class PreBlockCloseConfig:
    enabled: bool = True
    losers_only: bool = True
    min_profit_pct_to_keep: float = 0.0
    fee_buffer_pct: float = 0.12
    trend_lookback_bars: int = 3
    trend_kline_interval: str = "15"
    require_favorable_trend_to_keep: bool = False

    @classmethod
    def from_cfg(cls, cfg: Mapping[str, Any]) -> "PreBlockCloseConfig":
        trading = cfg.get("trading") if isinstance(cfg.get("trading"), dict) else {}
        sched = trading.get("non_trading_systemd") if isinstance(trading.get("non_trading_systemd"), dict) else {}
        raw = sched.get("pre_block_close")
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            losers_only=bool(raw.get("losers_only", True)),
            min_profit_pct_to_keep=float(raw.get("min_profit_pct_to_keep", 0.0) or 0.0),
            fee_buffer_pct=float(raw.get("fee_buffer_pct", 0.12) or 0.12),
            trend_lookback_bars=max(2, int(raw.get("trend_lookback_bars", 3) or 3)),
            trend_kline_interval=str(raw.get("trend_kline_interval", "15") or "15"),
            require_favorable_trend_to_keep=bool(
                raw.get("require_favorable_trend_to_keep", False)
            ),
        )


def read_trading_hours_ctl_flags(cfg: Mapping[str, Any]) -> Dict[str, bool]:
    trading = cfg.get("trading") if isinstance(cfg.get("trading"), dict) else {}
    sched = trading.get("non_trading_systemd") if isinstance(trading.get("non_trading_systemd"), dict) else {}
    pre = PreBlockCloseConfig.from_cfg(cfg)
    return {
        "sched_enabled": bool(sched.get("enabled", True)),
        "stop_systemd": bool(sched.get("stop_systemd", False)),
        "pre_block_close": pre.enabled,
    }


def _normalize_side(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in ("buy", "long"):
        return "Buy"
    return "Sell"


def position_size(row: Mapping[str, Any]) -> float:
    for key in ("size", "qty", "positionQty"):
        val = float(row.get(key, 0) or 0)
        if val > 0:
            return val
    avg = float(row.get("avgPrice", 0) or row.get("entryPrice", 0) or 0)
    pval = float(row.get("positionValue", 0) or 0)
    if pval > 0 and avg > 0:
        return pval / avg
    return 0.0


def profit_pct_from_row(row: Mapping[str, Any]) -> float:
    side = _normalize_side(row.get("side"))
    entry = float(row.get("avgPrice") or row.get("entryPrice") or 0)
    mark = float(row.get("markPrice") or entry or 0)
    if entry <= 0 or mark <= 0:
        upnl = float(row.get("unrealisedPnl", 0) or row.get("unrealizedPnl", 0) or 0)
        pval = float(row.get("positionValue", 0) or 0)
        if pval > 0:
            return (upnl / pval) * 100.0
        return 0.0
    if side == "Buy":
        return (mark - entry) / entry * 100.0
    return (entry - mark) / entry * 100.0


def closes_from_klines(klines: Sequence[Sequence[Any]]) -> List[float]:
    out: List[float] = []
    for row in klines:
        if not row or len(row) < 5:
            continue
        try:
            out.append(float(row[4]))
        except (TypeError, ValueError):
            continue
    return out


def favorable_trend(side: str, closes: Sequence[float], lookback: int) -> bool:
    """Тренд в сторону позиции по последним закрытиям свечей."""
    if len(closes) < lookback + 1:
        return True
    recent = list(closes[-(lookback + 1) :])
    moves = [recent[i + 1] - recent[i] for i in range(len(recent) - 1)]
    if not moves:
        return True
    if side == "Buy":
        up = sum(1 for m in moves if m > 0)
        return up >= max(1, len(moves) // 2 + len(moves) % 2)
    down = sum(1 for m in moves if m < 0)
    return down >= max(1, len(moves) // 2 + len(moves) % 2)


def should_close_before_block(
    row: Mapping[str, Any],
    *,
    cfg: PreBlockCloseConfig,
    closes: Optional[Sequence[float]] = None,
) -> Tuple[bool, str]:
    """True = закрыть перед блоком часов."""
    if not cfg.enabled or not cfg.losers_only:
        return True, "pre_block_disabled_or_close_all"

    profit_pct = profit_pct_from_row(row)
    upnl = float(row.get("unrealisedPnl", 0) or row.get("unrealizedPnl", 0) or 0)
    loser_threshold = -abs(cfg.fee_buffer_pct)

    if profit_pct < loser_threshold or upnl < 0:
        return True, f"loser profit_pct={profit_pct:.3f}% upnl={upnl:.4f}"

    if profit_pct < cfg.min_profit_pct_to_keep and upnl <= 0:
        return True, f"flat_or_small profit_pct={profit_pct:.3f}% upnl={upnl:.4f}"

    side = _normalize_side(row.get("side"))
    trend_note = "trend_unknown"
    if closes:
        trend_ok = favorable_trend(side, closes, cfg.trend_lookback_bars)
        trend_note = "trend_ok" if trend_ok else "trend_against"
        if cfg.require_favorable_trend_to_keep and not trend_ok:
            return True, f"profit_but_trend_against profit_pct={profit_pct:.3f}%"

    return False, f"keep profit_pct={profit_pct:.3f}% {trend_note}"
