"""
Стратегия Long Quality Gate — когда есть смысл открывать Buy (лонг).

По статистике AGENT-WORLD skipped_backtest (~6974 Buy) и реальных закрытий:
- часы UTC+3 {3,4,5,10,20}: WR закрытых ~22% — блокируем Buy;
- часы {6,9,12,13,14,16,17,18,19,21}: WR ~54% — предпочитаем;
- низкая волатильность (volatility=low) чаще у убыточных лонгов;
- слишком узкий SL (~0.5%) чаще у SL-исходов, чем у TP (медиана SL у TP ~2%).

Включается: trading.long_quality_gate.enabled (сначала песочница).
Маркер лога: Long quality gate
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


# Часы UTC+3: Buy WR низкий (лаборатория n>=140)
DEFAULT_BLOCK_HOURS: Tuple[int, ...] = (3, 4, 5, 10, 20)
# Часы UTC+3: Buy WR высокий (для soft / отчётов)
DEFAULT_PREFERRED_HOURS: Tuple[int, ...] = (6, 9, 12, 13, 14, 16, 17, 18, 19, 21)


@dataclass(frozen=True)
class LongQualityResult:
    allowed: bool
    reason: str
    profile: str = ""  # swing | scalp | skipped | disabled


def read_long_quality_cfg(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    trading = cfg.get("trading") if isinstance(cfg.get("trading"), dict) else {}
    raw = trading.get("long_quality_gate")
    if not isinstance(raw, dict):
        raw = cfg.get("long_quality_gate")
    return dict(raw) if isinstance(raw, dict) else {}


def long_quality_enabled(cfg: Mapping[str, Any]) -> bool:
    return bool(read_long_quality_cfg(cfg).get("enabled", False))


def read_long_swing_exit_cfg(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    positions = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
    raw = positions.get("long_swing_exit")
    if not isinstance(raw, dict):
        raw = cfg.get("long_swing_exit")
    return dict(raw) if isinstance(raw, dict) else {}


def long_swing_exit_enabled(cfg: Mapping[str, Any]) -> bool:
    return bool(read_long_swing_exit_cfg(cfg).get("enabled", False))


def _is_buy(side: object) -> bool:
    return str(side or "").strip().upper() in ("BUY", "LONG")


def _source_applies(source: str, gate: Mapping[str, Any]) -> bool:
    src = str(source or "").strip().lower()
    skip = {
        str(x).strip().lower()
        for x in (gate.get("skip_sources") or [])
        if str(x).strip()
    }
    if src in skip:
        return False
    apply = gate.get("apply_to_sources")
    if isinstance(apply, list) and apply:
        allowed = {str(x).strip().lower() for x in apply if str(x).strip()}
        return src in allowed or any(a in src for a in allowed)
    return True


def _tz_offset(cfg: Mapping[str, Any], gate: Mapping[str, Any]) -> int:
    try:
        if "timezone_offset" in gate:
            return int(gate.get("timezone_offset") or 3)
        return int(cfg.get("timezone_offset", 3) or 3)
    except (TypeError, ValueError):
        return 3


def _local_hour(
    *,
    cfg: Mapping[str, Any],
    gate: Mapping[str, Any],
    local_hour: Optional[int] = None,
    now_utc: Optional[datetime] = None,
) -> int:
    if local_hour is not None:
        try:
            return int(local_hour) % 24
        except (TypeError, ValueError):
            pass
    off = _tz_offset(cfg, gate)
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(timezone(timedelta(hours=off)))
    return int(local.hour) % 24


def _parse_hour_list(raw: object, default: Sequence[int]) -> List[int]:
    if not isinstance(raw, (list, tuple)):
        return [int(x) % 24 for x in default]
    out: List[int] = []
    for x in raw:
        try:
            out.append(int(x) % 24)
        except (TypeError, ValueError):
            continue
    return out


def _htf_bullish(htf: object) -> bool:
    s = str(htf or "").strip().lower()
    return s in ("1", "bullish", "up", "long", "buy")


def _htf_bearish(htf: object) -> bool:
    s = str(htf or "").strip().lower()
    return s in ("-1", "bearish", "down", "short", "sell")


def evaluate_long_quality_gate(
    *,
    side: str,
    cfg: Mapping[str, Any],
    source: str = "",
    volatility: str = "",
    atr_pct: float = 0.0,
    soft_score: Optional[float] = None,
    soft_label: str = "",
    htf_trend: object = None,
    local_hour: Optional[int] = None,
    now_utc: Optional[datetime] = None,
) -> LongQualityResult:
    """
    Жёсткий фильтр только для Buy. Sell всегда allowed (gate не трогает шорты).
    """
    gate = read_long_quality_cfg(cfg)
    if not bool(gate.get("enabled", False)):
        return LongQualityResult(allowed=True, reason="", profile="disabled")

    if not _is_buy(side):
        return LongQualityResult(allowed=True, reason="", profile="skipped")

    if not _source_applies(source, gate):
        return LongQualityResult(
            allowed=True,
            reason="long_quality: source skipped",
            profile="skipped",
        )

    hour = _local_hour(cfg=cfg, gate=gate, local_hour=local_hour, now_utc=now_utc)
    block_hours = set(
        _parse_hour_list(gate.get("block_local_hours"), DEFAULT_BLOCK_HOURS)
    )
    if hour in block_hours:
        return LongQualityResult(
            allowed=False,
            reason=(
                f"long_quality: Buy blocked hour {hour} UTC+3 "
                f"(статистика WR низкий)"
            ),
            profile="scalp",
        )

    block_vol = {
        str(x).strip().lower()
        for x in (gate.get("block_volatility") or ["low"])
        if str(x).strip()
    }
    vol = str(volatility or "").strip().lower()
    if vol and vol in block_vol:
        return LongQualityResult(
            allowed=False,
            reason=f"long_quality: Buy blocked volatility={vol}",
            profile="scalp",
        )

    min_atr = float(gate.get("min_atr_pct", 0.40) or 0.0)
    try:
        atr_v = float(atr_pct or 0.0)
    except (TypeError, ValueError):
        atr_v = 0.0
    # atr_pct в контексте обычно уже в % (0.5 = 0.5%), не доля
    if min_atr > 0 and atr_v > 0 and atr_v < min_atr:
        return LongQualityResult(
            allowed=False,
            reason=f"long_quality: atr_pct {atr_v:.3f} < min {min_atr:.3f}",
            profile="scalp",
        )

    min_soft = float(gate.get("min_soft_score", 0) or 0)
    if min_soft > 0 and soft_score is not None:
        try:
            sc = float(soft_score)
        except (TypeError, ValueError):
            sc = None
        if sc is not None and sc + 1e-9 < min_soft:
            return LongQualityResult(
                allowed=False,
                reason=f"long_quality: soft_score {sc:.1f} < {min_soft:.1f}",
                profile="scalp",
            )

    block_labels = {
        str(x).strip().lower()
        for x in (gate.get("block_soft_labels") or ["weak", "caution"])
        if str(x).strip()
    }
    label = str(soft_label or "").strip().lower()
    if label and label in block_labels:
        return LongQualityResult(
            allowed=False,
            reason=f"long_quality: soft_label={label} blocked",
            profile="scalp",
        )

    if bool(gate.get("require_htf_align", False)):
        if not _htf_bullish(htf_trend):
            return LongQualityResult(
                allowed=False,
                reason=(
                    f"long_quality: htf_trend={htf_trend!s} not bullish for Buy"
                ),
                profile="scalp",
            )

    preferred = set(
        _parse_hour_list(gate.get("preferred_local_hours"), DEFAULT_PREFERRED_HOURS)
    )
    profile = "swing" if hour in preferred else "scalp"
    return LongQualityResult(
        allowed=True,
        reason=f"long_quality: Buy ok hour={hour} profile={profile}",
        profile=profile,
    )


def widen_buy_sl_to_min_pct(
    *,
    side: str,
    entry: float,
    stop_loss: float,
    min_sl_pct: float,
) -> Tuple[float, bool]:
    """
    Для Buy: если SL ближе min_sl_pct % к входу — отодвинуть вниз.
    Возвращает (новый_sl, изменён_ли).
    """
    if not _is_buy(side):
        return float(stop_loss or 0), False
    try:
        entry_f = float(entry or 0)
        sl_f = float(stop_loss or 0)
        min_pct = float(min_sl_pct or 0)
    except (TypeError, ValueError):
        return float(stop_loss or 0), False
    if entry_f <= 0 or min_pct <= 0:
        return sl_f, False
    target_sl = entry_f * (1.0 - min_pct / 100.0)
    if sl_f <= 0 or sl_f > target_sl:
        return target_sl, True
    return sl_f, False


def buy_hour_sets_for_soft() -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """Зелёные / красные часы для soft-score именно на Buy."""
    return DEFAULT_PREFERRED_HOURS, DEFAULT_BLOCK_HOURS


def sell_hour_sets_for_soft() -> Tuple[Tuple[int, ...], Tuple[int, ...]]:
    """
    Для Sell: зеркально — часы, где Buy плох, часто хороши для шорта
    (лаборатория: 4,5,10,20), плюс утренние/ночные из старой статистики.
    """
    green = (4, 5, 7, 10, 14, 16, 17, 19, 20)
    red = (6, 9, 12, 13, 22)
    return green, red
