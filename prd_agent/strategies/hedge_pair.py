"""Trend-Continuation Hedge Pair — pure strategy math (no network).

NOT a guaranteed-profit scheme. Edge exists only when price continues
in the bias direction AFTER the first stop-loss of the opposite leg,
with TP distance > SL distance enough to cover fees and losers.

Symmetric TP==SL collapses to approximately -fees (negative expectancy).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence

SideBias = Literal["long", "short"]
LegSide = Literal["long", "short"]


@dataclass(frozen=True)
class HedgePairConfig:
    """Parameters for one hedge pair (equal notional long+short)."""

    enabled: bool = False
    execute: bool = False
    symbols: tuple = ("BTCUSDT", "ETHUSDT")
    leverage: int = 5
    margin_pct_per_leg: float = 2.0
    sl_price_pct: float = 0.8
    tp_price_pct: float = 1.44
    tp_to_sl_ratio: float = 1.8
    be_after_profit_pct: float = 0.5
    trail_distance_pct: float = 0.4
    max_pair_minutes: float = 45.0
    max_pairs: int = 1
    trend_ema_period: int = 50
    trend_interval: str = "60"
    fee_pct_roundtrip_per_leg: float = 0.12
    require_trend_bias: bool = True

    @classmethod
    def from_cfg(cls, cfg: Dict[str, Any]) -> "HedgePairConfig":
        """Build from bot yaml root or a hedge_pair subsection."""
        raw = cfg.get("hedge_pair", cfg) if isinstance(cfg, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        sl = float(raw.get("sl_price_pct", 0.8))
        ratio = float(raw.get("tp_to_sl_ratio", 1.8))
        tp_default = sl * ratio
        tp = float(raw.get("tp_price_pct", tp_default))
        if tp < sl * ratio:
            tp = sl * ratio
        symbols = raw.get("symbols", ["BTCUSDT", "ETHUSDT"])
        if not isinstance(symbols, (list, tuple)):
            symbols = ["BTCUSDT", "ETHUSDT"]
        return cls(
            enabled=bool(raw.get("enabled", False)),
            execute=bool(raw.get("execute", False)),
            symbols=tuple(str(s) for s in symbols),
            leverage=int(raw.get("leverage", 5)),
            margin_pct_per_leg=float(raw.get("margin_pct_per_leg", 2.0)),
            sl_price_pct=sl,
            tp_price_pct=tp,
            tp_to_sl_ratio=ratio,
            be_after_profit_pct=float(raw.get("be_after_profit_pct", 0.5)),
            trail_distance_pct=float(raw.get("trail_distance_pct", 0.4)),
            max_pair_minutes=float(raw.get("max_pair_minutes", 45)),
            max_pairs=int(raw.get("max_pairs", 1)),
            trend_ema_period=int(raw.get("trend_ema_period", 50)),
            trend_interval=str(raw.get("trend_interval", "60")),
            fee_pct_roundtrip_per_leg=float(raw.get("fee_pct_roundtrip_per_leg", 0.12)),
            require_trend_bias=bool(raw.get("require_trend_bias", True)),
        )


@dataclass
class HedgeSimResult:
    """Result of a pure price-path simulation (PnL in % of one-leg notional)."""

    net_pct: float
    first_sl_leg: Optional[LegSide]
    runner_leg: Optional[LegSide]
    closed_reason: str
    long_pnl_pct: float = 0.0
    short_pnl_pct: float = 0.0
    fees_pct: float = 0.0
    notes: List[str] = field(default_factory=list)


def trend_allows_entry(side_bias: SideBias, ema: float, price: float) -> bool:
    """Allow hedge only when price is on the bias side of EMA."""
    if ema <= 0 or price <= 0:
        return False
    if side_bias == "long":
        return price > ema
    if side_bias == "short":
        return price < ema
    raise TypeError(f"unsupported side_bias: {side_bias!r}")


def expected_net_on_continuation(config: HedgePairConfig, fee_pct_per_leg: float) -> float:
    """Closed-form: loser hits SL, winner continues to TP. Units: % of one-leg notional.

    net = +tp - sl - 2 * fee_roundtrip_per_leg
    """
    return float(config.tp_price_pct - config.sl_price_pct - 2.0 * fee_pct_per_leg)


def expected_net_on_immediate_flatten_at_sl(
    config: HedgePairConfig, fee_pct: float
) -> float:
    """At the first SL moment, mark-to-market of both legs is ~0 before fees.

    Flattening both then yields approximately -2 * fee (symmetric move).
    ``config`` kept for API symmetry / future asymmetry hooks.
    """
    _ = config
    return float(-2.0 * fee_pct)


def _pct_move(entry: float, price: float) -> float:
    return (price / entry - 1.0) * 100.0


def _long_pnl(entry: float, exit_px: float) -> float:
    return _pct_move(entry, exit_px)


def _short_pnl(entry: float, exit_px: float) -> float:
    return -_pct_move(entry, exit_px)


def plan_levels(entry: float, config: HedgePairConfig) -> Dict[str, float]:
    """Absolute SL/TP prices for both legs from entry."""
    sl = config.sl_price_pct / 100.0
    tp = config.tp_price_pct / 100.0
    return {
        "long_sl": entry * (1.0 - sl),
        "long_tp": entry * (1.0 + tp),
        "short_sl": entry * (1.0 + sl),
        "short_tp": entry * (1.0 - tp),
    }


def simulate_pair_path(
    prices: Sequence[float],
    config: HedgePairConfig,
    fees_bps: float,
    *,
    bias: SideBias = "long",
    minutes_per_bar: float = 1.0,
) -> HedgeSimResult:
    """Simulate equal long+short from ``prices[0]`` along the path.

    ``fees_bps``: round-trip fee per leg in basis points (12 = 0.12%).
    PnL reported in percent of one-leg notional (not leveraged margin).
    """
    if not prices or len(prices) < 2:
        return HedgeSimResult(
            net_pct=0.0,
            first_sl_leg=None,
            runner_leg=None,
            closed_reason="empty_path",
            notes=["need at least 2 prices"],
        )

    entry = float(prices[0])
    if entry <= 0:
        return HedgeSimResult(
            net_pct=0.0,
            first_sl_leg=None,
            runner_leg=None,
            closed_reason="bad_entry",
        )

    fee_pct = float(fees_bps) / 100.0  # bps -> percent points (12 bps -> 0.12%)
    levels = plan_levels(entry, config)
    long_sl = levels["long_sl"]
    long_tp = levels["long_tp"]
    short_sl = levels["short_sl"]
    short_tp = levels["short_tp"]

    long_open = True
    short_open = True
    long_pnl = 0.0
    short_pnl = 0.0
    fees = 0.0
    first_sl: Optional[LegSide] = None
    runner: Optional[LegSide] = None
    notes: List[str] = []
    elapsed = 0.0

    # Runner management state
    runner_sl: Optional[float] = None
    runner_be_done = False
    runner_peak: Optional[float] = None  # best price for trail (high for long, low for short)

    def _close_long(px: float, reason: str) -> None:
        nonlocal long_open, long_pnl, fees
        if not long_open:
            return
        long_pnl = _long_pnl(entry, px)
        fees += fee_pct
        long_open = False
        notes.append(f"close_long@{px:.6g}:{reason}")

    def _close_short(px: float, reason: str) -> None:
        nonlocal short_open, short_pnl, fees
        if not short_open:
            return
        short_pnl = _short_pnl(entry, px)
        fees += fee_pct
        short_open = False
        notes.append(f"close_short@{px:.6g}:{reason}")

    def _activate_runner(leg: LegSide, px: float) -> None:
        nonlocal runner, runner_sl, runner_peak, runner_be_done
        runner = leg
        runner_be_done = False
        if leg == "long":
            runner_sl = long_sl
            runner_peak = px
        else:
            runner_sl = short_sl
            runner_peak = px
        notes.append(f"runner={leg}")

    for i, raw in enumerate(prices[1:], start=1):
        px = float(raw)
        if px <= 0:
            continue
        elapsed = i * minutes_per_bar

        if elapsed >= config.max_pair_minutes and (long_open or short_open):
            if long_open:
                _close_long(px, "timeout")
            if short_open:
                _close_short(px, "timeout")
            net = long_pnl + short_pnl - fees
            return HedgeSimResult(
                net_pct=net,
                first_sl_leg=first_sl,
                runner_leg=runner,
                closed_reason="timeout",
                long_pnl_pct=long_pnl,
                short_pnl_pct=short_pnl,
                fees_pct=fees,
                notes=notes,
            )

        # --- both legs still open: first SL / TP checks ---
        if long_open and short_open:
            hit_long_sl = px <= long_sl
            hit_short_sl = px >= short_sl
            hit_long_tp = px >= long_tp
            hit_short_tp = px <= short_tp

            # Prefer SL of the non-bias leg when both would be ambiguous (gap)
            if hit_long_sl and hit_short_sl:
                # Gap through both SLs — catastrophic; close both at px
                _close_long(px, "gap_sl")
                _close_short(px, "gap_sl")
                net = long_pnl + short_pnl - fees
                return HedgeSimResult(
                    net_pct=net,
                    first_sl_leg="long",
                    runner_leg=None,
                    closed_reason="gap_both_sl",
                    long_pnl_pct=long_pnl,
                    short_pnl_pct=short_pnl,
                    fees_pct=fees,
                    notes=notes,
                )

            if hit_long_sl:
                _close_long(long_sl, "sl")
                first_sl = "long"
                _activate_runner("short", px)
                if short_open and px <= short_tp:
                    _close_short(short_tp, "tp")
                    break
            elif hit_short_sl:
                _close_short(short_sl, "sl")
                first_sl = "short"
                _activate_runner("long", px)
                if long_open and px >= long_tp:
                    _close_long(long_tp, "tp")
                    break
            elif hit_long_tp and bias == "long":
                # Rare early TP while hedge still on — close long TP, keep short? Strategy: flatten pair on unexpected TP of bias leg with other open is messy.
                # Spec: pair opens for continuation after first SL. If TP hits first on one leg, close that leg at TP and treat other as runner with original SL.
                _close_long(long_tp, "tp")
                first_sl = None
                _activate_runner("short", px)
            elif hit_short_tp and bias == "short":
                _close_short(short_tp, "tp")
                first_sl = None
                _activate_runner("long", px)
            elif hit_long_tp:
                _close_long(long_tp, "tp")
                _activate_runner("short", px)
            elif hit_short_tp:
                _close_short(short_tp, "tp")
                _activate_runner("long", px)
            continue

        # --- runner phase ---
        if runner == "long" and long_open:
            runner_peak = max(runner_peak or px, px)
            upnl = _long_pnl(entry, px)
            if (not runner_be_done) and upnl >= config.be_after_profit_pct:
                # breakeven + fee buffer (fee already counted on close; buffer ~ half roundtrip)
                runner_sl = entry * (1.0 + fee_pct / 200.0)
                runner_be_done = True
                notes.append("long_be")
            if runner_be_done and config.trail_distance_pct > 0:
                trail_sl = runner_peak * (1.0 - config.trail_distance_pct / 100.0)
                if runner_sl is None or trail_sl > runner_sl:
                    runner_sl = trail_sl
            if px <= (runner_sl or long_sl):
                _close_long(runner_sl or long_sl, "runner_sl")
                break
            if px >= long_tp:
                _close_long(long_tp, "tp")
                break
        elif runner == "short" and short_open:
            runner_peak = min(runner_peak or px, px)
            upnl = _short_pnl(entry, px)
            if (not runner_be_done) and upnl >= config.be_after_profit_pct:
                runner_sl = entry * (1.0 - fee_pct / 200.0)
                runner_be_done = True
                notes.append("short_be")
            if runner_be_done and config.trail_distance_pct > 0:
                trail_sl = runner_peak * (1.0 + config.trail_distance_pct / 100.0)
                if runner_sl is None or trail_sl < runner_sl:
                    runner_sl = trail_sl
            if px >= (runner_sl or short_sl):
                _close_short(runner_sl or short_sl, "runner_sl")
                break
            if px <= short_tp:
                _close_short(short_tp, "tp")
                break

    # End of path: flatten leftovers at last price
    last = float(prices[-1])
    if long_open:
        _close_long(last, "path_end")
    if short_open:
        _close_short(last, "path_end")

    reason = "path_end"
    if any("tp" in n for n in notes) and first_sl:
        reason = "continuation_tp"
    elif first_sl and any("runner_sl" in n for n in notes):
        reason = "reversal_runner_sl"
    elif first_sl:
        reason = "after_first_sl"

    net = long_pnl + short_pnl - fees
    return HedgeSimResult(
        net_pct=net,
        first_sl_leg=first_sl,
        runner_leg=runner,
        closed_reason=reason,
        long_pnl_pct=long_pnl,
        short_pnl_pct=short_pnl,
        fees_pct=fees,
        notes=notes,
    )
