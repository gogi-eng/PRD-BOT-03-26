"""После переноса SL в BE/BE+ — уже ужесточённая дистанция трейлинга (п.п. от цены)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from prd_agent.positions.breakeven_fees import breakeven_stop_price


@dataclass(frozen=True)
class TrailingAfterBeConfig:
    enabled: bool = False
    # Сколько процентных пунктов вычесть из trailing_distance_pct после BE.
    # Пример: base 3.5, reduce 0.5 → 3.0 (не ниже min_distance_pct).
    distance_reduce_pct: float = 0.5

    @classmethod
    def from_cfg(cls, positions_cfg: Mapping[str, Any]) -> TrailingAfterBeConfig:
        raw = positions_cfg.get("trailing_after_be")
        if not isinstance(raw, dict):
            return cls(enabled=False)
        try:
            reduce_pct = float(raw.get("distance_reduce_pct", 0.5) or 0.0)
        except (TypeError, ValueError):
            reduce_pct = 0.5
        # 0 = без эффекта; верх — защита от случайного «обнулить всю дистанцию»
        reduce_pct = max(0.0, min(5.0, reduce_pct))
        return cls(
            enabled=bool(raw.get("enabled", False)),
            distance_reduce_pct=reduce_pct,
        )


def sl_is_at_or_beyond_be(
    side: str,
    entry: float,
    stop_loss: float,
    be_buffer_pct: float,
    *,
    eps_pct: float = 0.02,
) -> bool:
    """
    True, если текущий SL уже на уровне безубытка (или лучше).
    be_buffer_pct — fee (+ lock), как в BE+.
    """
    if entry <= 0 or stop_loss <= 0:
        return False
    be = breakeven_stop_price(side, entry, be_buffer_pct)
    if be <= 0:
        return False
    tol = entry * max(0.0, eps_pct) / 100.0
    side_l = str(side or "").strip().lower()
    if side_l in {"buy", "long"}:
        return stop_loss + tol >= be
    if side_l in {"sell", "short"}:
        return stop_loss - tol <= be
    return False


def is_be_phase(phase: str) -> bool:
    """Фазы tp_progress после факта BE/BE+."""
    return str(phase or "").strip().lower() in {"breakeven", "sr_trail"}


def should_tighten_trailing_after_be(
    *,
    cfg: TrailingAfterBeConfig,
    tp_progress_phase: str = "",
    side: str = "",
    entry: float = 0.0,
    stop_loss: float = 0.0,
    be_buffer_pct: float = 0.0,
) -> bool:
    if not cfg.enabled or cfg.distance_reduce_pct <= 1e-12:
        return False
    if is_be_phase(tp_progress_phase):
        return True
    return sl_is_at_or_beyond_be(side, entry, stop_loss, be_buffer_pct)


def apply_trailing_after_be_distance(
    base_distance_pct: float,
    *,
    cfg: TrailingAfterBeConfig,
    min_distance_pct: float = 0.0,
    tp_progress_phase: str = "",
    side: str = "",
    entry: float = 0.0,
    stop_loss: float = 0.0,
    be_buffer_pct: float = 0.0,
) -> Tuple[float, Optional[str]]:
    """
    После BE: effective = max(min_floor, base_distance - reduce_pct).
    До BE — без изменений. Возвращает (дистанция_%, note_или_None).
    """
    base = max(0.0, float(base_distance_pct))
    if not should_tighten_trailing_after_be(
        cfg=cfg,
        tp_progress_phase=tp_progress_phase,
        side=side,
        entry=entry,
        stop_loss=stop_loss,
        be_buffer_pct=be_buffer_pct,
    ):
        return base, None
    reduced = base - float(cfg.distance_reduce_pct)
    floor = max(0.0, float(min_distance_pct or 0.0))
    if floor > 0:
        reduced = max(reduced, floor)
    else:
        reduced = max(0.0, reduced)
    note = (
        f"Trailing tighten after BE −{cfg.distance_reduce_pct:g}% "
        f"(dist {base:.2f}%→{reduced:.2f}%)"
    )
    return reduced, note
