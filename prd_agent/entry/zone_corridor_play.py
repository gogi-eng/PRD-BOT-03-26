"""
Стратегия «коридор поддержка↔сопротивление»:
- bounce — отскок от зоны к противоположной;
- breakout — пробой зоны с подтверждением (close за уровнем / BOS);
- mid_range — середина коридора → не входить.

Включается через trading.zone_corridor_play.enabled (сначала песочница).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from analysis.structure_zones import StructureZoneAnalyzer, ZoneContext


@dataclass(frozen=True)
class ZoneCorridorResult:
    play: str  # bounce | breakout | mid_range | no_corridor | disabled
    allowed: bool
    reason: str
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0
    score_bonus: float = 0.0


def read_zone_corridor_cfg(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    trading = cfg.get("trading") if isinstance(cfg.get("trading"), dict) else {}
    raw = trading.get("zone_corridor_play")
    if not isinstance(raw, dict):
        raw = cfg.get("zone_corridor_play")
    return dict(raw) if isinstance(raw, dict) else {}


def zone_corridor_enabled(cfg: Mapping[str, Any]) -> bool:
    return bool(read_zone_corridor_cfg(cfg).get("enabled", False))


def _is_spike_source(source: str) -> bool:
    src = str(source or "").strip().lower()
    if not src:
        return False
    needles = ("spike", "spike_scalp", "spike_scanner", "pump_dump")
    return any(n in src for n in needles)


def apply_spike_bypass_no_corridor(
    result: ZoneCorridorResult,
    *,
    cfg: Mapping[str, Any],
    source: str = "",
    score: float = 0.0,
    move_pct: float = 0.0,
) -> ZoneCorridorResult:
    """P0: strong SPIKE with no_corridor may pass when enabled in config."""
    if result.allowed or result.play != "no_corridor":
        return result
    zc = read_zone_corridor_cfg(cfg)
    if not bool(zc.get("spike_bypass_no_corridor", False)):
        return result
    if not _is_spike_source(source):
        return result
    try:
        score_v = float(score or 0)
    except (TypeError, ValueError):
        score_v = 0.0
    try:
        move_v = abs(float(move_pct or 0))
    except (TypeError, ValueError):
        move_v = 0.0
    min_score = float(zc.get("spike_bypass_min_score", 88) or 88)
    min_move = float(zc.get("spike_bypass_min_move_pct", 6.0) or 6.0)
    score_ok = score_v >= min_score
    move_ok = move_v >= min_move
    if not (score_ok or move_ok):
        return result
    reason = (
        f"SPIKE bypass no_corridor (score={score_v:.0f} move={move_v:.2f}% "
        f"min_score={min_score:.0f} min_move={min_move:.2f}%; {result.reason})"
    )
    return ZoneCorridorResult(
        play=result.play,
        allowed=True,
        reason=reason,
        nearest_support=result.nearest_support,
        nearest_resistance=result.nearest_resistance,
        score_bonus=result.score_bonus,
    )


def _source_applies(source: str, zc: Mapping[str, Any]) -> bool:
    src = str(source or "").strip().lower()
    skip = {
        str(x).strip().lower()
        for x in (zc.get("skip_sources") or [])
        if str(x).strip()
    }
    if src in skip:
        return False
    if bool(zc.get("skip_fast_sources", True)):
        for needle in ("spike", "pump_dump", "agent_world", "world_feed"):
            if needle in src:
                return False
    apply = zc.get("apply_to_sources")
    if isinstance(apply, list) and apply:
        allowed = {str(x).strip().lower() for x in apply if str(x).strip()}
        return src in allowed or any(a in src for a in allowed)
    return True


def _bars_hlc(klines: Sequence[Mapping[str, Any]]) -> List[Tuple[float, float, float, float]]:
    out: List[Tuple[float, float, float, float]] = []
    for row in klines:
        try:
            o = float(row.get("open", 0) or 0)
            h = float(row.get("high", 0) or 0)
            lo = float(row.get("low", 0) or 0)
            c = float(row.get("close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if h > 0 and lo > 0 and c > 0:
            out.append((o, h, lo, c))
    return out


def _all_supports(zone_ctx: ZoneContext) -> List[float]:
    levels = {float(s) for s in zone_ctx.support_levels if float(s) > 0}
    for z in zone_ctx.all_bullish_zones:
        if not z.mitigated and z.low > 0:
            levels.add(float(z.low))
    return sorted(levels)


def _all_resistances(zone_ctx: ZoneContext) -> List[float]:
    levels = {float(r) for r in zone_ctx.resistance_levels if float(r) > 0}
    for z in zone_ctx.all_bearish_zones:
        if not z.mitigated and z.high > 0:
            levels.add(float(z.high))
    return sorted(levels)


def _nearest_corridor(
    zone_ctx: ZoneContext, price: float
) -> Tuple[Optional[float], Optional[float]]:
    """Пара S/R: внутри коридора или только что пробитого края."""
    supports = _all_supports(zone_ctx)
    resistances = _all_resistances(zone_ctx)
    s_below = [s for s in supports if s < price]
    r_above = [r for r in resistances if r > price]
    if s_below and r_above:
        return max(s_below), min(r_above)

    # Пробой вверх: сопротивление уже ниже цены
    r_below = [r for r in resistances if r <= price]
    if s_below and r_below:
        broken_r = max(r_below)
        s = max(s for s in s_below if s < broken_r) if any(s < broken_r for s in s_below) else max(s_below)
        if broken_r > s:
            return s, broken_r

    # Пробой вниз: поддержка уже выше цены
    s_above = [s for s in supports if s >= price]
    if s_above and r_above:
        broken_s = min(s_above)
        r = min(r for r in r_above if r > broken_s) if any(r > broken_s for r in r_above) else min(r_above)
        if r > broken_s:
            return broken_s, r

    return (
        max(s_below) if s_below else None,
        min(r_above) if r_above else None,
    )


def _edge_position(price: float, support: float, resistance: float) -> float:
    """0 = у поддержки, 1 = у сопротивления."""
    width = resistance - support
    if width <= 0:
        return 0.5
    return max(0.0, min(1.0, (price - support) / width))


def _bounce_rejection(
    *,
    is_buy: bool,
    support: float,
    resistance: float,
    bars: Sequence[Tuple[float, float, float, float]],
    atr: float,
    lookback: int,
    wick_atr_mult: float,
) -> bool:
    if len(bars) < 2:
        return False
    look = bars[-max(1, lookback) :]
    pad = max(atr * wick_atr_mult, abs(support) * 1e-6)
    if is_buy:
        zone_hi = support + pad
        for o, h, lo, c in look:
            tagged = lo <= support + pad
            rejected = c > o and c >= support and lo < zone_hi
            if tagged and rejected:
                return True
        # последняя свеча закрылась выше поддержки после касания
        o, h, lo, c = look[-1]
        return lo <= support + pad and c >= support
    zone_lo = resistance - pad
    for o, h, lo, c in look:
        tagged = h >= resistance - pad
        rejected = c < o and c <= resistance and h > zone_lo
        if tagged and rejected:
            return True
    o, h, lo, c = look[-1]
    return h >= resistance - pad and c <= resistance


def _breakout_confirmed(
    *,
    is_buy: bool,
    support: float,
    resistance: float,
    bars: Sequence[Tuple[float, float, float, float]],
    has_bos: bool,
    confirm_bars: int,
) -> bool:
    if has_bos:
        return True
    if len(bars) < max(2, confirm_bars):
        return False
    recent = bars[-max(2, confirm_bars) :]
    if is_buy:
        # минимум одно закрытие выше сопротивления + последнее всё ещё выше или ретест сверху
        closes_above = sum(1 for *_, c in recent if c > resistance)
        last_c = recent[-1][3]
        last_lo = recent[-1][2]
        retest = last_lo <= resistance * 1.002 and last_c >= resistance
        return closes_above >= 1 and (last_c > resistance or retest)
    closes_below = sum(1 for *_, c in recent if c < support)
    last_c = recent[-1][3]
    last_hi = recent[-1][1]
    retest = last_hi >= support * 0.998 and last_c <= support
    return closes_below >= 1 and (last_c < support or retest)


def evaluate_zone_corridor_play(
    *,
    side: str,
    price: float,
    klines: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    source: str = "",
    has_bos: bool = False,
    atr: float = 0.0,
    zone_ctx: Optional[ZoneContext] = None,
    score: float = 0.0,
    move_pct: float = 0.0,
) -> ZoneCorridorResult:
    zc = read_zone_corridor_cfg(cfg)
    if not bool(zc.get("enabled", False)):
        return ZoneCorridorResult(
            play="disabled",
            allowed=True,
            reason="",
            score_bonus=0.0,
        )
    if not _source_applies(source, zc):
        return ZoneCorridorResult(
            play="disabled",
            allowed=True,
            reason="zone_corridor: source skipped",
            score_bonus=0.0,
        )

    price = float(price or 0)
    if price <= 0:
        return apply_spike_bypass_no_corridor(
            ZoneCorridorResult(
                play="no_corridor",
                allowed=not bool(zc.get("require_play", True)),
                reason="zone_corridor: нет цены",
            ),
            cfg=cfg,
            source=source,
            score=score,
            move_pct=move_pct,
        )

    if zone_ctx is None:
        if not klines or len(klines) < 6:
            return apply_spike_bypass_no_corridor(
                ZoneCorridorResult(
                    play="no_corridor",
                    allowed=not bool(zc.get("require_play", True)),
                    reason="zone_corridor: мало свечей для зон",
                ),
                cfg=cfg,
                source=source,
                score=score,
                move_pct=move_pct,
            )
        zone_ctx = StructureZoneAnalyzer().analyze(list(klines), price)

    support, resistance = _nearest_corridor(zone_ctx, price)
    if support is None or resistance is None or resistance <= support:
        allow = not bool(zc.get("require_play", True))
        return apply_spike_bypass_no_corridor(
            ZoneCorridorResult(
                play="no_corridor",
                allowed=allow,
                reason="zone_corridor: нет пары S/R (коридор)",
                nearest_support=float(support or 0),
                nearest_resistance=float(resistance or 0),
            ),
            cfg=cfg,
            source=source,
            score=score,
            move_pct=move_pct,
        )

    edge_frac = float(zc.get("edge_fraction", 0.28) or 0.28)
    edge_frac = max(0.1, min(0.45, edge_frac))
    pos = _edge_position(price, support, resistance)
    near_support = pos <= edge_frac
    near_resistance = pos >= (1.0 - edge_frac)
    mid_skip = bool(zc.get("mid_range_skip", True))

    if mid_skip and not near_support and not near_resistance:
        return ZoneCorridorResult(
            play="mid_range",
            allowed=False,
            reason=(
                f"zone_corridor: середина коридора "
                f"(S={support:.6g} R={resistance:.6g} pos={pos:.2f})"
            ),
            nearest_support=support,
            nearest_resistance=resistance,
        )

    is_buy = str(side or "").lower() in ("buy", "long")
    bars = _bars_hlc(klines)
    atr_v = float(atr or 0)
    if atr_v <= 0 and bars:
        ranges = [h - lo for _, h, lo, _ in bars[-20:]]
        atr_v = sum(ranges) / max(1, len(ranges))

    allow_bounce = bool(zc.get("allow_bounce", True))
    allow_breakout = bool(zc.get("allow_breakout", True))
    lookback = max(1, int(zc.get("bounce_lookback_bars", 3) or 3))
    wick_mult = float(zc.get("bounce_wick_atr_mult", 0.35) or 0.35)
    confirm_bars = max(1, int(zc.get("breakout_confirm_bars", 2) or 2))

    # Breakout имеет приоритет, если цена уже за краем коридора
    broke_up = price > resistance or (bars and bars[-1][3] > resistance)
    broke_down = price < support or (bars and bars[-1][3] < support)

    if allow_breakout and (
        (is_buy and (broke_up or has_bos))
        or ((not is_buy) and (broke_down or has_bos))
    ):
        ok = _breakout_confirmed(
            is_buy=is_buy,
            support=support,
            resistance=resistance,
            bars=bars,
            has_bos=has_bos,
            confirm_bars=confirm_bars,
        )
        if ok and ((is_buy and (broke_up or has_bos)) or ((not is_buy) and (broke_down or has_bos))):
            return ZoneCorridorResult(
                play="breakout",
                allowed=True,
                reason=(
                    f"zone_corridor: breakout "
                    f"{'up' if is_buy else 'down'} "
                    f"(S={support:.6g} R={resistance:.6g})"
                ),
                nearest_support=support,
                nearest_resistance=resistance,
                score_bonus=1.0,
            )
        if bool(zc.get("require_play", True)):
            return ZoneCorridorResult(
                play="breakout",
                allowed=False,
                reason="zone_corridor: пробой без подтверждения (нужен ретест/BOS)",
                nearest_support=support,
                nearest_resistance=resistance,
            )

    if allow_bounce:
        want_near = near_support if is_buy else near_resistance
        if want_near and _bounce_rejection(
            is_buy=is_buy,
            support=support,
            resistance=resistance,
            bars=bars,
            atr=atr_v,
            lookback=lookback,
            wick_atr_mult=wick_mult,
        ):
            return ZoneCorridorResult(
                play="bounce",
                allowed=True,
                reason=(
                    f"zone_corridor: bounce "
                    f"{'от поддержки' if is_buy else 'от сопротивления'} "
                    f"→ к {'R' if is_buy else 'S'} "
                    f"(S={support:.6g} R={resistance:.6g})"
                ),
                nearest_support=support,
                nearest_resistance=resistance,
                score_bonus=0.75,
            )

    if bool(zc.get("require_play", True)):
        where = "у поддержки" if near_support else ("у сопротивления" if near_resistance else "в коридоре")
        return ZoneCorridorResult(
            play="none",
            allowed=False,
            reason=(
                f"zone_corridor: нет отскока/пробоя ({where}, "
                f"side={'BUY' if is_buy else 'SELL'})"
            ),
            nearest_support=support,
            nearest_resistance=resistance,
        )

    return ZoneCorridorResult(
        play="none",
        allowed=True,
        reason="zone_corridor: advisory, вход не блокирован",
        nearest_support=support,
        nearest_resistance=resistance,
    )
