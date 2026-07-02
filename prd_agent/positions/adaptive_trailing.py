"""Адаптивный трейлинг: ужимать дистанцию SL при быстром движении в сторону профита."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class AdaptiveTrailingConfig:
    enabled: bool = False
    kline_interval: str = "15"
    lookback_bars: int = 3
    fast_move_pct: float = 1.0
    slow_move_pct: float = 0.3
    tight_distance_factor: float = 0.55
    normal_distance_factor: float = 1.0
    apply_to_manual: bool = True
    apply_to_pump_dump: bool = True
    apply_to_bot: bool = True

    @classmethod
    def from_cfg(cls, positions_cfg: Mapping[str, Any]) -> AdaptiveTrailingConfig:
        raw = positions_cfg.get("adaptive_trailing")
        if not isinstance(raw, dict):
            return cls(enabled=False)
        return cls(
            enabled=bool(raw.get("enabled", False)),
            kline_interval=str(raw.get("kline_interval", "15")),
            lookback_bars=max(1, int(raw.get("lookback_bars", 3) or 3)),
            fast_move_pct=float(raw.get("fast_move_pct", 1.0) or 1.0),
            slow_move_pct=float(raw.get("slow_move_pct", 0.3) or 0.3),
            tight_distance_factor=float(raw.get("tight_distance_factor", 0.55) or 0.55),
            normal_distance_factor=float(raw.get("normal_distance_factor", 1.0) or 1.0),
            apply_to_manual=bool(raw.get("apply_to_manual", True)),
            apply_to_pump_dump=bool(raw.get("apply_to_pump_dump", True)),
            apply_to_bot=bool(raw.get("apply_to_bot", True)),
        )


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def favorable_move_pct(side: str, klines: List[Dict[str, Any]], lookback_bars: int) -> float:
    """
  Движение цены за lookback свечей в сторону профита (%).
  Short: падение close → положительное значение.
  Long: рост close → положительное значение.
    """
    if not klines or lookback_bars <= 0:
        return 0.0
    n = min(lookback_bars, len(klines))
    window = klines[-n:]
    start = _sf(window[0].get("close"))
    end = _sf(window[-1].get("close"))
    if start <= 0 or end <= 0:
        return 0.0
    raw_move = (end - start) / start * 100.0
    side_u = str(side or "").strip().lower()
    if side_u in {"sell", "short"}:
        return -raw_move
    return raw_move


def compute_adaptive_distance_factor(
    *,
    side: str,
    klines: List[Dict[str, Any]],
    cfg: AdaptiveTrailingConfig,
) -> Tuple[float, str]:
    """
    Возвращает множитель дистанции трейлинга (меньше = ближе SL к цене).
    Медленная динамика → normal_distance_factor (обычно 1.0, без изменений).
    Быстрая динамика → tight_distance_factor (короче трейлинг).
    """
    if not cfg.enabled:
        return cfg.normal_distance_factor, "disabled"

    move = favorable_move_pct(side, klines, cfg.lookback_bars)
    fast = max(cfg.slow_move_pct + 1e-9, cfg.fast_move_pct)
    slow = min(cfg.slow_move_pct, fast)

    tight = max(0.1, min(1.0, cfg.tight_distance_factor))
    normal = max(tight, min(2.0, cfg.normal_distance_factor))

    if move >= fast:
        return tight, f"fast_move={move:+.2f}%>={fast:g}%"
    if move <= slow:
        return normal, f"slow_move={move:+.2f}%<={slow:g}%"

    t = (move - slow) / (fast - slow)
    factor = normal - t * (normal - tight)
    return factor, f"blend_move={move:+.2f}% factor={factor:.2f}"


def should_apply_adaptive_trailing(
    *,
    cfg: AdaptiveTrailingConfig,
    origin: str,
    pump_dump_mode: bool,
) -> bool:
    if not cfg.enabled:
        return False
    origin_l = str(origin or "").lower()
    if pump_dump_mode and cfg.apply_to_pump_dump:
        return True
    if origin_l == "manual" and cfg.apply_to_manual:
        return True
    if origin_l == "bot" and cfg.apply_to_bot:
        return True
    return False
