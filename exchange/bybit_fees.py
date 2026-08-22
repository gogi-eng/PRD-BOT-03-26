"""Bybit USDT perpetual fee model for backtests and PnL (maker/taker)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


# Стандартные ставки Bybit linear (non-VIP, без промо)
DEFAULT_TAKER_RATE = 0.00055  # 0.055% за сторону
DEFAULT_MAKER_RATE = 0.0002   # 0.02% за сторону


def normalize_fee_rate(value: Any) -> Optional[float]:
    """Привести ставку к доле (0.00055); None если значение пустое/некорректное."""
    if value is None:
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    # legacy: иногда пишут 0.055 вместо 0.00055 (проценты)
    if rate > 0.01:
        rate = rate / 100.0
    return rate


def resolve_taker_fee_rate_from_mapping(cfg: Mapping[str, Any]) -> float:
    """exit.fee_rate → positions.fee_breakeven.taker_rate → DEFAULT_TAKER_RATE."""
    exit_cfg = cfg.get("exit") if isinstance(cfg.get("exit"), dict) else {}
    if isinstance(exit_cfg, dict):
        rate = normalize_fee_rate(exit_cfg.get("fee_rate"))
        if rate is not None:
            return rate
    positions = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
    fb = positions.get("fee_breakeven") if isinstance(positions.get("fee_breakeven"), dict) else {}
    if isinstance(fb, dict):
        rate = normalize_fee_rate(fb.get("taker_rate"))
        if rate is not None:
            return rate
    return DEFAULT_TAKER_RATE


def resolve_taker_fee_rate_from_config(cfg: Any) -> float:
    """BotConfig (.get / .raw) или plain dict."""
    if hasattr(cfg, "raw") and isinstance(getattr(cfg, "raw"), dict):
        return resolve_taker_fee_rate_from_mapping(cfg.raw)
    if hasattr(cfg, "get"):
        exit_rate = normalize_fee_rate(cfg.get("exit", "fee_rate", default=None))
        if exit_rate is not None:
            return exit_rate
        fb_rate = normalize_fee_rate(
            cfg.get("positions", "fee_breakeven", "taker_rate", default=None)
        )
        if fb_rate is not None:
            return fb_rate
    if isinstance(cfg, Mapping):
        return resolve_taker_fee_rate_from_mapping(cfg)
    return DEFAULT_TAKER_RATE


@dataclass(frozen=True)
class BybitFeeConfig:
    enabled: bool = True
    taker_rate: float = DEFAULT_TAKER_RATE
    maker_rate: float = DEFAULT_MAKER_RATE
    entry_as_maker: bool = False
    exit_as_maker: bool = False

    @classmethod
    def from_cfg(cls, cfg: Optional[Dict[str, Any]] = None) -> "BybitFeeConfig":
        raw = (cfg or {}).get("skipped_signal_backtest", {})
        if not isinstance(raw, dict):
            raw = {}
        fees = raw.get("fees", {})
        if not isinstance(fees, dict):
            fees = {}
        exit_cfg = (cfg or {}).get("exit", {}) or {}
        fallback_taker = float(exit_cfg.get("fee_rate", DEFAULT_TAKER_RATE) or DEFAULT_TAKER_RATE)
        if fallback_taker > 0.01:
            fallback_taker = fallback_taker / 100.0
        taker = float(fees.get("taker_rate", fallback_taker) or DEFAULT_TAKER_RATE)
        maker = float(fees.get("maker_rate", DEFAULT_MAKER_RATE) or DEFAULT_MAKER_RATE)
        return cls(
            enabled=bool(fees.get("enabled", True)),
            taker_rate=taker,
            maker_rate=maker,
            entry_as_maker=bool(fees.get("entry_as_maker", False)),
            exit_as_maker=bool(fees.get("exit_as_maker", False)),
        )

    def entry_rate(self) -> float:
        return self.maker_rate if self.entry_as_maker else self.taker_rate

    def exit_rate(self) -> float:
        return self.maker_rate if self.exit_as_maker else self.taker_rate

    def round_trip_fee_pct(self) -> float:
        """Комиссия вход+выход как % от номинала (≈ вычитается из price PnL%)."""
        return (self.entry_rate() + self.exit_rate()) * 100.0


def apply_fees_to_pnl_pct(
    gross_pnl_pct: float,
    fee_cfg: Optional[BybitFeeConfig] = None,
) -> Dict[str, float]:
    cfg = fee_cfg or BybitFeeConfig()
    if not cfg.enabled:
        fee_pct = 0.0
        return {
            "pnl_pct_gross": round(gross_pnl_pct, 4),
            "pnl_pct_net": round(gross_pnl_pct, 4),
            "fee_pct_round_trip": fee_pct,
        }
    fee_pct = cfg.round_trip_fee_pct()
    net = gross_pnl_pct - fee_pct
    return {
        "pnl_pct_gross": round(gross_pnl_pct, 4),
        "pnl_pct_net": round(net, 4),
        "fee_pct_round_trip": round(fee_pct, 4),
    }
