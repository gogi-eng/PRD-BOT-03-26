"""
Разворот MARKET SCANNER / SPIKE против открытой позиции:
срочное закрытие (по умолчанию) или подтяжка SL + Telegram-алерт.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional

from prd_agent.positions.breakeven_fees import (
    breakeven_stop_price,
    clamp_sl_for_profit_lock,
    fee_buffer_pct_from_bot_cfg,
    is_stop_in_loss_after_fees,
)
from prd_agent.signals.side_utils import normalize_trade_side

logger = logging.getLogger("prd_agent.scanner_reversal")


def _setup_field(setup: Any, key: str, default: Any = None) -> Any:
    """MarketSetup (dataclass) или dict — без setup.get() при False/0."""
    if isinstance(setup, Mapping):
        return setup.get(key, default)
    return getattr(setup, key, default)


def load_reversal_cfg(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    agent = cfg.get("telegram_signal_agent") if isinstance(cfg.get("telegram_signal_agent"), dict) else {}
    mc = cfg.get("market_scanner") if isinstance(cfg.get("market_scanner"), dict) else {}
    block = {}
    if isinstance(mc.get("scanner_reversal_exit"), dict):
        block.update(mc["scanner_reversal_exit"])
    if isinstance(agent.get("scanner_reversal_exit"), dict):
        block.update(agent["scanner_reversal_exit"])
    return {
        "enabled": bool(block.get("enabled", True)),
        "alert_telegram": bool(block.get("alert_telegram", True)),
        "close_on_reversal": bool(block.get("close_on_reversal", True)),
        "tighten_sl": bool(block.get("tighten_sl", False)),
        "min_score": int(block.get("min_score", 72) or 72),
        "require_confirmed_bos": bool(block.get("require_confirmed_bos", False)),
        "min_position_age_min": float(block.get("min_position_age_min", 0) or 0),
        "tighten_from_mark_pct": float(block.get("tighten_from_mark_pct", 0.35) or 0.35),
        "entry_buffer_pct": float(block.get("entry_buffer_pct", 0.05) or 0.05),
        "min_sl_improve_pct": float(block.get("min_sl_improve_pct", 0.03) or 0.03),
        "symbol_cooldown_sec": int(block.get("symbol_cooldown_sec", 1800) or 1800),
    }


def scenario_to_side(scenario: str) -> str:
    scen = str(scenario or "").upper()
    if scen == "PUMP":
        return "BUY"
    if scen == "DUMP":
        return "SELL"
    return ""


def normalize_pos_side(side: str) -> str:
    return normalize_trade_side(side)


def setup_opposes_position(scenario: str, position_side: str) -> bool:
    setup_side = scenario_to_side(scenario)
    pos = normalize_pos_side(position_side)
    if not setup_side or not pos:
        return False
    return setup_side != pos


def _position_mark(row: Mapping[str, Any]) -> float:
    for key in ("markPrice", "mark_price", "lastPrice"):
        val = float(row.get(key, 0) or 0)
        if val > 0:
            return val
    return float(row.get("avgPrice") or row.get("entryPrice") or 0)


def _position_entry(row: Mapping[str, Any]) -> float:
    return float(row.get("avgPrice") or row.get("entryPrice") or row.get("markPrice") or 0)


def _position_sl(row: Mapping[str, Any]) -> float:
    return float(row.get("stopLoss") or row.get("stop_loss") or 0)


def _position_idx(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("positionIdx") or row.get("position_idx") or 0)
    except (TypeError, ValueError):
        return 0


def _position_qty(row: Mapping[str, Any]) -> float:
    for key in ("size", "qty", "positionQty"):
        val = float(row.get(key, 0) or 0)
        if val > 0:
            return val
    return 0.0


def position_age_minutes(row: Mapping[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    now = now or datetime.now(timezone.utc)
    raw = row.get("createdTime") or row.get("updatedTime") or row.get("created_at")
    if raw is None:
        return None
    try:
        ts_ms = int(raw)
        if ts_ms > 1_000_000_000_000:
            opened = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        else:
            opened = datetime.fromtimestamp(ts_ms, tz=timezone.utc)
        return max(0.0, (now - opened).total_seconds() / 60.0)
    except (TypeError, ValueError, OSError):
        pass
    try:
        opened = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return max(0.0, (now.astimezone(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() / 60.0)
    except (TypeError, ValueError):
        return None


def on_reversal_cooldown(
    symbol: str,
    cooldown_state: Mapping[str, Any],
    cooldown_sec: int,
    now: Optional[datetime] = None,
) -> bool:
    if cooldown_sec <= 0:
        return False
    key = str(symbol or "").upper()
    raw = cooldown_state.get(key)
    if not raw:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        last = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age = (now.astimezone(timezone.utc) - last.astimezone(timezone.utc)).total_seconds()
        return age < cooldown_sec
    except (TypeError, ValueError):
        return False


def mark_reversal_handled(cooldown_state: Dict[str, Any], symbol: str, now: Optional[datetime] = None) -> None:
    now = now or datetime.now(timezone.utc)
    cooldown_state[str(symbol or "").upper()] = now.isoformat()


def passes_reversal_filters(
    setup: Any,
    position: Mapping[str, Any],
    cfg: Mapping[str, Any],
    *,
    cooldown_state: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    if not bool(cfg.get("enabled", True)):
        return False, "disabled"
    sym = str(_setup_field(setup, "symbol", "") or "").upper()
    scenario = str(_setup_field(setup, "scenario", "") or "")
    score = int(_setup_field(setup, "score", 0) or 0)
    confirmed = bool(_setup_field(setup, "confirmed_bos", False))
    pos_side = str(position.get("side", "") or "")

    if not sym:
        return False, "no_symbol"
    if not setup_opposes_position(scenario, pos_side):
        return False, "same_direction"
    if score < int(cfg.get("min_score", 72)):
        return False, f"score<{cfg.get('min_score')}"
    min_age = float(cfg.get("min_position_age_min", 0) or 0)
    if min_age > 0:
        age = position_age_minutes(position, now=now)
        if age is None:
            return False, "position_age_unknown"
        if age < min_age:
            return False, f"age<{min_age}min"
    # При срочном закрытии BOS не обязателен (иначе слабый DUMP не закроет LONG).
    require_bos = bool(cfg.get("require_confirmed_bos", False))
    if bool(cfg.get("close_on_reversal", True)):
        require_bos = False
    if require_bos and not confirmed:
        return False, "no_bos"
    cd = int(cfg.get("symbol_cooldown_sec", 1800) or 0)
    if cooldown_state is not None and on_reversal_cooldown(sym, cooldown_state, cd, now=now):
        return False, "cooldown"
    return True, "ok"


def compute_tightened_sl(
    *,
    position_side: str,
    entry: float,
    mark: float,
    current_sl: float,
    invalidation: float,
    cfg: Mapping[str, Any],
    bot_cfg: Optional[Mapping[str, Any]] = None,
    hold_hours: Optional[float] = None,
) -> Optional[float]:
    is_long = normalize_pos_side(position_side) == "BUY"
    if mark <= 0 and entry <= 0:
        return None
    ref = mark if mark > 0 else entry
    from_mark = float(cfg.get("tighten_from_mark_pct", 0.35) or 0.35)
    min_improve = float(cfg.get("min_sl_improve_pct", 0.03) or 0.03) / 100.0
    inv = float(invalidation or 0)

    full_cfg = bot_cfg if isinstance(bot_cfg, dict) else {}
    be_fee_pct = (
        fee_buffer_pct_from_bot_cfg(full_cfg, hold_hours=hold_hours)
        if full_cfg
        else 0.15
    )
    be_sl = breakeven_stop_price(position_side, entry, be_fee_pct) if entry > 0 else 0.0

    candidates: list[float] = []
    if is_long:
        if be_sl > 0:
            candidates.append(be_sl)
        if ref > 0:
            candidates.append(ref * (1.0 - from_mark / 100.0))
        if inv > 0 and inv < ref:
            candidates.append(inv)
        if not candidates:
            return None
        new_sl = max(candidates)
        if new_sl >= ref:
            new_sl = ref * (1.0 - max(from_mark, 0.15) / 100.0)
        if current_sl > 0:
            if new_sl <= current_sl * (1.0 + min_improve):
                return None
    else:
        if be_sl > 0:
            candidates.append(be_sl)
        if ref > 0:
            candidates.append(ref * (1.0 + from_mark / 100.0))
        if inv > 0 and inv > ref:
            candidates.append(inv)
        if not candidates:
            return None
        new_sl = min(candidates)
        if new_sl <= ref:
            new_sl = ref * (1.0 + max(from_mark, 0.15) / 100.0)
        if current_sl > 0:
            if new_sl >= current_sl * (1.0 - min_improve):
                return None
    if new_sl <= 0:
        return None

    in_profit = (is_long and ref > entry) or ((not is_long) and ref < entry)
    if in_profit:
        new_sl = clamp_sl_for_profit_lock(
            position_side, entry, new_sl, be_fee_pct, in_profit=True
        )
        if is_stop_in_loss_after_fees(position_side, entry, new_sl, be_fee_pct):
            new_sl = be_sl
    return round(new_sl, 8)


@dataclass
class ReversalHandleResult:
    handled: bool
    alerted: bool
    sl_updated: bool
    closed: bool = False
    new_sl: float = 0.0
    reason: str = ""


def build_reversal_alert_text(
    setup: Any,
    position: Mapping[str, Any],
    *,
    new_sl: float = 0.0,
    closed: bool = False,
) -> str:
    sym = str(_setup_field(setup, "symbol", "") or "").upper()
    scenario = str(_setup_field(setup, "scenario", "") or "")
    score = int(_setup_field(setup, "score", 0) or 0)
    pos_side = str(position.get("side", "") or "")
    entry = _position_entry(position)
    mark = _position_mark(position)
    cur_sl = _position_sl(position)
    arrow = "⬆ PUMP" if scenario.upper() == "PUMP" else "⬇ DUMP"
    lines = [
        "⚠️ SCANNER vs ПОЗИЦИЯ",
        f"Монета: {sym}",
        f"Открыто: {pos_side} | вход≈{entry:.8g} | mark≈{mark:.8g}",
        f"Сканер: {arrow} (score {score}/100)",
    ]
    if closed:
        lines.append("Действие: позиция закрыта по обратному сигналу сканера.")
    else:
        lines.append("Действие: обратный сигнал — закрытие или подтяжка SL.")
    if cur_sl > 0:
        lines.append(f"Текущий SL: {cur_sl:.8g}")
    if new_sl > 0:
        lines.append(f"Новый SL (подтянут): {new_sl:.8g}")
    elif cur_sl <= 0 and not closed:
        lines.append("SL на бирже не задан — подтянуть автоматически нельзя.")
    return "\n".join(lines)


async def handle_scanner_reversal(
    *,
    setup: Any,
    position: Mapping[str, Any],
    client: Any,
    cfg: Mapping[str, Any],
    cooldown_state: Dict[str, Any],
    notify: Optional[Callable[[str], bool]] = None,
    now: Optional[datetime] = None,
    bot_cfg: Optional[Mapping[str, Any]] = None,
) -> ReversalHandleResult:
    ok, why = passes_reversal_filters(
        setup, position, cfg, cooldown_state=cooldown_state, now=now
    )
    if not ok:
        return ReversalHandleResult(
            handled=False, alerted=False, sl_updated=False, closed=False, reason=why
        )

    sym = str(_setup_field(setup, "symbol", "") or "").upper()
    pos_side = str(position.get("side", "") or "")
    entry = _position_entry(position)
    mark = _position_mark(position)
    cur_sl = _position_sl(position)
    inv = float(_setup_field(setup, "invalidation", 0) or 0)

    closed = False
    if bool(cfg.get("close_on_reversal", True)) and hasattr(client, "close_position"):
        qty = _position_qty(position)
        pidx = _position_idx(position)
        try:
            res = await client.close_position(
                sym,
                pos_side,
                qty=qty if qty > 0 else None,
                position_idx=pidx,
            )
        except Exception as exc:
            logger.error(
                "Opposite signal EXIT %s close failed: %s", sym, exc, exc_info=True
            )
            res = {"success": False, "error": str(exc)}
        if isinstance(res, dict) and (res.get("success") or res.get("orderId")):
            closed = True
            logger.warning(
                "Opposite signal EXIT %s open=%s (scanner/SPIKE reverse close)",
                sym,
                pos_side,
            )
        else:
            err = str(res.get("error", ""))[:120] if isinstance(res, dict) else "close failed"
            logger.warning("Opposite signal EXIT failed %s: %s", sym, err)

    new_sl = 0.0
    sl_updated = False
    can_tighten = bool(cfg.get("tighten_sl", False)) and not closed
    min_age = float(cfg.get("min_position_age_min", 0) or 0)
    age = position_age_minutes(position, now=now)
    hold_h = (age / 60.0) if age is not None and age > 0 else None
    if can_tighten and min_age > 0:
        if age is None:
            can_tighten = False
        elif age < min_age:
            can_tighten = False

    if can_tighten and hasattr(client, "update_stop_loss"):
        candidate = compute_tightened_sl(
            position_side=pos_side,
            entry=entry,
            mark=mark,
            current_sl=cur_sl,
            invalidation=inv,
            cfg=cfg,
            bot_cfg=bot_cfg,
            hold_hours=hold_h,
        )
        if candidate is not None:
            res = await client.update_stop_loss(sym, candidate, position_idx=_position_idx(position))
            if isinstance(res, dict) and res.get("success"):
                new_sl = candidate
                sl_updated = True
                logger.info("Scanner reversal SL tightened %s %s -> %.8g", sym, pos_side, new_sl)
            else:
                err = str(res.get("error", ""))[:120] if isinstance(res, dict) else "update failed"
                logger.warning("Scanner reversal SL failed %s: %s", sym, err)

    alerted = False
    if bool(cfg.get("alert_telegram", True)) and notify is not None:
        text = build_reversal_alert_text(setup, position, new_sl=new_sl, closed=closed)
        try:
            alerted = bool(notify(text))
        except Exception as exc:
            logger.warning("Scanner reversal alert failed %s: %s", sym, exc)

    if alerted or sl_updated or closed:
        mark_reversal_handled(cooldown_state, sym, now=now)

    return ReversalHandleResult(
        handled=alerted or sl_updated or closed,
        alerted=alerted,
        sl_updated=sl_updated,
        closed=closed,
        new_sl=new_sl,
        reason="ok",
    )
