"""
SPIKE: вход на откате (FVG / небольшой retrace) или сразу, если отката не ждут.

Чистые функции без сети — стакан и свечи передаёт вызывающий код.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Mapping, Optional, Sequence, Tuple


class SpikePullbackAction(str, Enum):
    ENTER_NOW = "ENTER_NOW"
    WAIT_PULLBACK = "WAIT_PULLBACK"
    SKIP = "SKIP"
    ENTER_AFTER_RETEST = "ENTER_AFTER_RETEST"


@dataclass(frozen=True)
class SpikePullbackConfig:
    enabled: bool = False
    wait_timeout_sec: float = 300.0
    min_retrace_pct: float = 0.15
    require_fvg: bool = False
    enter_immediate_if_no_fvg: bool = True
    enter_immediate_if_book_confirms: bool = True
    multi_tf_intervals: Tuple[str, ...] = ("1", "5", "15")
    orderbook_depth: int = 50
    direction_guard_ratio: float = 1.3
    min_fvg_pct: float = 0.12
    impulse_extend_pct: float = 0.25
    absorption_ratio: float = 1.2
    synthetic_retrace_pct: float = 0.20
    watchlist_max: int = 12


@dataclass(frozen=True)
class SpikePullbackDecision:
    action: SpikePullbackAction
    reason: str
    zone_low: float = 0.0
    zone_high: float = 0.0
    book_ratio: float = 1.0
    has_fvg: bool = False


def read_spike_pullback_cfg(cfg: Mapping[str, Any]) -> SpikePullbackConfig:
    """Читает market_scanner.spike_scalp.pullback_entry (+ fallback telegram_signal_agent)."""
    mc = cfg.get("market_scanner") if isinstance(cfg.get("market_scanner"), dict) else {}
    agent = cfg.get("telegram_signal_agent") if isinstance(cfg.get("telegram_signal_agent"), dict) else {}
    spike = mc.get("spike_scalp") if isinstance(mc.get("spike_scalp"), dict) else {}
    if not spike and isinstance(agent.get("spike_scalp"), dict):
        spike = agent["spike_scalp"]
    raw = spike.get("pullback_entry") if isinstance(spike, dict) else None
    if not isinstance(raw, dict):
        raw = {}

    ob = cfg.get("orderbook_entry") if isinstance(cfg.get("orderbook_entry"), dict) else {}
    intervals_raw = raw.get("multi_tf_intervals", ["1", "5", "15"])
    intervals: Tuple[str, ...]
    if isinstance(intervals_raw, (list, tuple)) and intervals_raw:
        intervals = tuple(str(x) for x in intervals_raw)
    else:
        intervals = ("1", "5", "15")

    depth_default = int(ob.get("orderflow_depth", ob.get("depth_levels", 50)) or 50)
    ratio_default = float(ob.get("direction_guard_ratio", 1.3) or 1.3)

    return SpikePullbackConfig(
        enabled=bool(raw.get("enabled", False)),
        wait_timeout_sec=float(raw.get("wait_timeout_sec", 300) or 300),
        min_retrace_pct=float(raw.get("min_retrace_pct", 0.15) or 0.15),
        require_fvg=bool(raw.get("require_fvg", False)),
        enter_immediate_if_no_fvg=bool(raw.get("enter_immediate_if_no_fvg", True)),
        enter_immediate_if_book_confirms=bool(raw.get("enter_immediate_if_book_confirms", True)),
        multi_tf_intervals=intervals,
        orderbook_depth=max(5, int(raw.get("orderbook_depth", depth_default) or depth_default)),
        direction_guard_ratio=float(raw.get("direction_guard_ratio", ratio_default) or ratio_default),
        min_fvg_pct=float(raw.get("min_fvg_pct", 0.12) or 0.12),
        impulse_extend_pct=float(raw.get("impulse_extend_pct", 0.25) or 0.25),
        absorption_ratio=float(raw.get("absorption_ratio", 1.2) or 1.2),
        synthetic_retrace_pct=float(raw.get("synthetic_retrace_pct", 0.20) or 0.20),
        watchlist_max=max(1, int(raw.get("watchlist_max", 12) or 12)),
    )


def spike_pullback_enabled(cfg: Mapping[str, Any]) -> bool:
    return bool(read_spike_pullback_cfg(cfg).enabled)


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_side(side: str) -> str:
    s = str(side or "").strip().upper()
    if s in ("BUY", "LONG", "PUMP"):
        return "BUY"
    if s in ("SELL", "SHORT", "DUMP"):
        return "SELL"
    return s


def orderbook_volumes(
    orderbook: Optional[Mapping[str, Any]],
    depth: int = 50,
) -> Tuple[float, float, float]:
    """Возвращает (bid_vol, ask_vol, bid/ask ratio)."""
    if not isinstance(orderbook, Mapping):
        return 0.0, 0.0, 1.0
    bids = list(orderbook.get("bids") or [])[: max(1, depth)]
    asks = list(orderbook.get("asks") or [])[: max(1, depth)]
    bid_vol = 0.0
    ask_vol = 0.0
    for row in bids:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            bid_vol += _sf(row[1])
        elif isinstance(row, Mapping):
            bid_vol += _sf(row.get("size") or row.get("qty"))
    for row in asks:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            ask_vol += _sf(row[1])
        elif isinstance(row, Mapping):
            ask_vol += _sf(row.get("size") or row.get("qty"))
    ratio = bid_vol / ask_vol if ask_vol > 0 else (2.0 if bid_vol > 0 else 1.0)
    return bid_vol, ask_vol, ratio


def find_latest_fvg(
    klines: Sequence[Mapping[str, Any]],
    side: str,
    min_fvg_pct: float = 0.12,
) -> Tuple[float, float, str]:
    """
    FVG в направлении импульса (зона отката для заполнения):
    BUY/PUMP → bullish gap ниже цены; SELL/DUMP → bearish gap выше.
    """
    side_u = _normalize_side(side)
    lookback = list(klines[-40:]) if klines else []
    if len(lookback) < 3:
        return 0.0, 0.0, ""
    for i in range(len(lookback) - 1, 1, -1):
        prev2 = lookback[i - 2]
        cur = lookback[i]
        if side_u == "BUY":
            low = _sf(prev2.get("high"))
            high = _sf(cur.get("low"))
            if high > low > 0:
                gap_pct = (high - low) / max(low, 1e-12) * 100.0
                if gap_pct >= min_fvg_pct:
                    return low, high, f"bullish FVG {low:.8g}-{high:.8g}"
        elif side_u == "SELL":
            low = _sf(cur.get("high"))
            high = _sf(prev2.get("low"))
            if high > low > 0:
                gap_pct = (high - low) / max(high, 1e-12) * 100.0
                if gap_pct >= min_fvg_pct:
                    return low, high, f"bearish FVG {low:.8g}-{high:.8g}"
    return 0.0, 0.0, ""


def _closes(klines: Sequence[Mapping[str, Any]]) -> List[float]:
    out: List[float] = []
    for row in klines:
        c = _sf(row.get("close"))
        if c > 0:
            out.append(c)
    return out


def impulse_still_extending(
    side: str,
    klines_1m: Sequence[Mapping[str, Any]],
    extend_pct: float,
) -> bool:
    """True если последняя 1m свеча всё ещё сильно в сторону импульса."""
    side_u = _normalize_side(side)
    closes = _closes(klines_1m)
    if len(closes) < 3:
        return False
    base = closes[-3]
    last = closes[-1]
    if base <= 0 or last <= 0:
        return False
    move = (last / base - 1.0) * 100.0
    if side_u == "BUY":
        return move >= extend_pct
    if side_u == "SELL":
        return move <= -extend_pct
    return False


def retrace_pct_from_extreme(
    side: str,
    klines: Sequence[Mapping[str, Any]],
    lookback: int = 8,
) -> float:
    closes = _closes(klines)
    if len(closes) < 2:
        return 0.0
    window = closes[-lookback:] if lookback > 0 else closes
    last = closes[-1]
    side_u = _normalize_side(side)
    if side_u == "BUY":
        hi = max(window)
        if hi <= 0:
            return 0.0
        return (hi - last) / hi * 100.0
    lo = min(window)
    if lo <= 0:
        return 0.0
    return (last - lo) / lo * 100.0


def price_in_pullback_zone(
    price: float,
    zone_low: float,
    zone_high: float,
    *,
    tol_pct: float = 0.02,
) -> bool:
    p = float(price or 0)
    lo = float(zone_low or 0)
    hi = float(zone_high or 0)
    if p <= 0 or lo <= 0 or hi <= 0 or hi < lo:
        return False
    mid = (lo + hi) / 2.0
    tol = mid * max(0.0, tol_pct) / 100.0
    return (lo - tol) <= p <= (hi + tol)


def synthetic_pullback_zone(
    side: str,
    price: float,
    retrace_pct: float,
) -> Tuple[float, float]:
    """Зона отката без FVG: небольшой % против импульса от текущей цены."""
    p = float(price or 0)
    pct = max(0.05, float(retrace_pct or 0.2)) / 100.0
    if p <= 0:
        return 0.0, 0.0
    side_u = _normalize_side(side)
    if side_u == "BUY":
        hi = p
        lo = p * (1.0 - pct)
        return lo, hi
    lo = p
    hi = p * (1.0 + pct)
    return lo, hi


def book_confirms_side(
    side: str,
    bid_vol: float,
    ask_vol: float,
    ratio_threshold: float,
) -> bool:
    side_u = _normalize_side(side)
    if bid_vol <= 0 or ask_vol <= 0:
        return False
    thr = max(1.01, float(ratio_threshold or 1.3))
    if side_u == "BUY":
        return bid_vol >= ask_vol * thr
    if side_u == "SELL":
        return ask_vol >= bid_vol * thr
    return False


def book_opposes_side(
    side: str,
    bid_vol: float,
    ask_vol: float,
    ratio_threshold: float,
) -> bool:
    side_u = _normalize_side(side)
    if bid_vol <= 0 or ask_vol <= 0:
        return False
    thr = max(1.01, float(ratio_threshold or 1.3))
    if side_u == "BUY":
        return ask_vol >= bid_vol * thr
    if side_u == "SELL":
        return bid_vol >= ask_vol * thr
    return False


def book_shows_absorption(
    side: str,
    bid_vol: float,
    ask_vol: float,
    absorption_ratio: float,
) -> bool:
    """Стенка против входа (поглощение) — ждём откат, не SKIP."""
    return book_opposes_side(side, bid_vol, ask_vol, absorption_ratio)


def decide_spike_pullback(
    *,
    side: str,
    price: float,
    orderbook: Optional[Mapping[str, Any]] = None,
    klines_1m: Optional[Sequence[Mapping[str, Any]]] = None,
    klines_5m: Optional[Sequence[Mapping[str, Any]]] = None,
    klines_15m: Optional[Sequence[Mapping[str, Any]]] = None,
    fvg_low: float = 0.0,
    fvg_high: float = 0.0,
    cfg: Optional[SpikePullbackConfig] = None,
    already_waiting: bool = False,
) -> SpikePullbackDecision:
    """
    Решает: входить сейчас, ждать откат в зону, или пропустить.
    """
    pe = cfg or SpikePullbackConfig()
    if not pe.enabled:
        return SpikePullbackDecision(
            action=SpikePullbackAction.ENTER_NOW,
            reason="pullback_entry disabled",
        )

    side_u = _normalize_side(side)
    if side_u not in ("BUY", "SELL"):
        return SpikePullbackDecision(
            action=SpikePullbackAction.SKIP,
            reason=f"unknown side={side!r}",
        )

    px = float(price or 0)
    if px <= 0:
        return SpikePullbackDecision(
            action=SpikePullbackAction.SKIP,
            reason="invalid price",
        )

    bid_vol, ask_vol, book_ratio = orderbook_volumes(orderbook, pe.orderbook_depth)
    confirms = book_confirms_side(side_u, bid_vol, ask_vol, pe.direction_guard_ratio)
    opposes = book_opposes_side(side_u, bid_vol, ask_vol, pe.direction_guard_ratio)
    absorbs = book_shows_absorption(side_u, bid_vol, ask_vol, pe.absorption_ratio)

    # Сильный противоположный стакан → SKIP (не входим против книги).
    if opposes:
        return SpikePullbackDecision(
            action=SpikePullbackAction.SKIP,
            reason=(
                f"orderbook opposite bid={bid_vol:.0f} ask={ask_vol:.0f} "
                f"ratio={book_ratio:.2f}"
            ),
            book_ratio=book_ratio,
        )

    zl = float(fvg_low or 0)
    zh = float(fvg_high or 0)
    fvg_reason = ""
    if zl > 0 and zh > zl:
        fvg_reason = f"provided FVG {zl:.8g}-{zh:.8g}"
    else:
        for kl in (klines_1m, klines_5m, klines_15m):
            if not kl:
                continue
            zl, zh, fvg_reason = find_latest_fvg(kl, side_u, pe.min_fvg_pct)
            if zl > 0 and zh > zl:
                break

    has_fvg = bool(zl > 0 and zh > zl and fvg_reason)
    # FVG должен быть зоной отката (ниже для BUY, выше для SELL).
    if has_fvg:
        if side_u == "BUY" and zh >= px:
            # gap уже «выше/на цене» — не ждём заполнения вниз
            if zl >= px:
                has_fvg = False
                zl, zh, fvg_reason = 0.0, 0.0, ""
        elif side_u == "SELL" and zl <= px:
            if zh <= px:
                has_fvg = False
                zl, zh, fvg_reason = 0.0, 0.0, ""

    if has_fvg and price_in_pullback_zone(px, zl, zh):
        action = (
            SpikePullbackAction.ENTER_AFTER_RETEST
            if already_waiting
            else SpikePullbackAction.ENTER_NOW
        )
        return SpikePullbackDecision(
            action=action,
            reason=f"price in FVG zone ({fvg_reason})",
            zone_low=zl,
            zone_high=zh,
            book_ratio=book_ratio,
            has_fvg=True,
        )

    kl_retrace = klines_1m or klines_5m or klines_15m or []
    retrace = retrace_pct_from_extreme(side_u, kl_retrace, lookback=8)
    extending = impulse_still_extending(side_u, klines_1m or [], pe.impulse_extend_pct)

    # Уже достаточно откатились без явного FVG.
    if not has_fvg and retrace >= pe.min_retrace_pct:
        action = (
            SpikePullbackAction.ENTER_AFTER_RETEST
            if already_waiting
            else SpikePullbackAction.ENTER_NOW
        )
        return SpikePullbackDecision(
            action=action,
            reason=f"retrace {retrace:.2f}% >= {pe.min_retrace_pct:.2f}%",
            zone_low=0.0,
            zone_high=0.0,
            book_ratio=book_ratio,
            has_fvg=False,
        )

    # Есть FVG ниже/выше — ждём заполнение (если не continuation + book).
    if has_fvg:
        if (
            pe.enter_immediate_if_book_confirms
            and confirms
            and extending
        ):
            return SpikePullbackDecision(
                action=SpikePullbackAction.ENTER_NOW,
                reason=(
                    f"continuation: book confirms + 1m extending; "
                    f"skip wait FVG ({fvg_reason})"
                ),
                zone_low=zl,
                zone_high=zh,
                book_ratio=book_ratio,
                has_fvg=True,
            )
        return SpikePullbackDecision(
            action=SpikePullbackAction.WAIT_PULLBACK,
            reason=f"wait FVG fill ({fvg_reason}) bid={bid_vol:.0f} ask={ask_vol:.0f}",
            zone_low=zl,
            zone_high=zh,
            book_ratio=book_ratio,
            has_fvg=True,
        )

    # Нет FVG
    if pe.require_fvg:
        return SpikePullbackDecision(
            action=SpikePullbackAction.SKIP,
            reason="require_fvg=true and no FVG",
            book_ratio=book_ratio,
        )

    if absorbs and not confirms:
        syn_lo, syn_hi = synthetic_pullback_zone(side_u, px, pe.synthetic_retrace_pct)
        return SpikePullbackDecision(
            action=SpikePullbackAction.WAIT_PULLBACK,
            reason=(
                f"absorption vs entry bid={bid_vol:.0f} ask={ask_vol:.0f}; "
                f"wait synthetic zone"
            ),
            zone_low=syn_lo,
            zone_high=syn_hi,
            book_ratio=book_ratio,
            has_fvg=False,
        )

    if pe.enter_immediate_if_no_fvg:
        if pe.enter_immediate_if_book_confirms and not confirms and extending:
            # Импульс без подтверждения стакана — лучше короткий wait
            syn_lo, syn_hi = synthetic_pullback_zone(side_u, px, pe.synthetic_retrace_pct)
            return SpikePullbackDecision(
                action=SpikePullbackAction.WAIT_PULLBACK,
                reason="no FVG, impulse extending without book confirm; wait small pullback",
                zone_low=syn_lo,
                zone_high=syn_hi,
                book_ratio=book_ratio,
            )
        return SpikePullbackDecision(
            action=SpikePullbackAction.ENTER_NOW,
            reason=(
                f"no FVG; enter immediate "
                f"(book_confirms={confirms} extending={extending})"
            ),
            book_ratio=book_ratio,
        )

    if confirms:
        return SpikePullbackDecision(
            action=SpikePullbackAction.ENTER_NOW,
            reason="book confirms direction, no FVG wait required",
            book_ratio=book_ratio,
        )

    syn_lo, syn_hi = synthetic_pullback_zone(side_u, px, pe.synthetic_retrace_pct)
    return SpikePullbackDecision(
        action=SpikePullbackAction.WAIT_PULLBACK,
        reason="no FVG; wait synthetic pullback",
        zone_low=syn_lo,
        zone_high=syn_hi,
        book_ratio=book_ratio,
    )


def evaluate_pending_pullback(
    *,
    side: str,
    price: float,
    zone_low: float,
    zone_high: float,
    orderbook: Optional[Mapping[str, Any]] = None,
    cfg: Optional[SpikePullbackConfig] = None,
    timed_out: bool = False,
) -> SpikePullbackDecision:
    """Повторная проверка отложенного SPIKE-сетапа."""
    pe = cfg or SpikePullbackConfig(enabled=True)
    if timed_out:
        return SpikePullbackDecision(
            action=SpikePullbackAction.SKIP,
            reason="timeout",
            zone_low=float(zone_low or 0),
            zone_high=float(zone_high or 0),
        )

    bid_vol, ask_vol, book_ratio = orderbook_volumes(orderbook, pe.orderbook_depth)
    if book_opposes_side(side, bid_vol, ask_vol, pe.direction_guard_ratio):
        return SpikePullbackDecision(
            action=SpikePullbackAction.SKIP,
            reason=(
                f"orderbook flipped against entry bid={bid_vol:.0f} ask={ask_vol:.0f}"
            ),
            zone_low=float(zone_low or 0),
            zone_high=float(zone_high or 0),
            book_ratio=book_ratio,
        )

    zl = float(zone_low or 0)
    zh = float(zone_high or 0)
    if zl > 0 and zh >= zl and price_in_pullback_zone(price, zl, zh):
        return SpikePullbackDecision(
            action=SpikePullbackAction.ENTER_AFTER_RETEST,
            reason=f"price in pullback zone {zl:.8g}-{zh:.8g}",
            zone_low=zl,
            zone_high=zh,
            book_ratio=book_ratio,
            has_fvg=True,
        )

    return SpikePullbackDecision(
        action=SpikePullbackAction.WAIT_PULLBACK,
        reason=(
            f"still waiting zone {zl:.8g}-{zh:.8g} price={float(price):.8g}"
            if zl > 0 and zh >= zl
            else f"still waiting pullback price={float(price):.8g}"
        ),
        zone_low=zl,
        zone_high=zh,
        book_ratio=book_ratio,
    )
