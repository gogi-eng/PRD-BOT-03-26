"""После переноса SL в BE/BE+ — чуть шире trailing distance (больше «воздуха»)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from prd_agent.positions.breakeven_fees import breakeven_stop_price


@dataclass(frozen=True)
class TrailingAfterBeConfig:
    enabled: bool = False
    # Множитель дистанции трейлинга: 1.2 = на 20% шире после BE
    widen_mult: float = 1.2

    @classmethod
    def from_cfg(cls, positions_cfg: Mapping[str, Any]) -> TrailingAfterBeConfig:
        raw = positions_cfg.get("trailing_after_be")
        if not isinstance(raw, dict):
            return cls(enabled=False)
        try:
            mult = float(raw.get("widen_mult", 1.2) or 1.2)
        except (TypeError, ValueError):
            mult = 1.2
        # 1.0 = без эффекта; верх — защита от случайного «в 3 раза»
        mult = max(1.0, min(2.0, mult))
        return cls(
            enabled=bool(raw.get("enabled", False)),
            widen_mult=mult,
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


def should_widen_trailing_after_be(
    *,
    cfg: TrailingAfterBeConfig,
    tp_progress_phase: str = "",
    side: str = "",
    entry: float = 0.0,
    stop_loss: float = 0.0,
    be_buffer_pct: float = 0.0,
) -> bool:
    if not cfg.enabled or cfg.widen_mult <= 1.0 + 1e-12:
        return False
    if is_be_phase(tp_progress_phase):
        return True
    return sl_is_at_or_beyond_be(side, entry, stop_loss, be_buffer_pct)


def apply_trailing_after_be_widen(
    distance_factor: float,
    *,
    cfg: TrailingAfterBeConfig,
    tp_progress_phase: str = "",
    side: str = "",
    entry: float = 0.0,
    stop_loss: float = 0.0,
    be_buffer_pct: float = 0.0,
) -> Tuple[float, Optional[str]]:
    """
    Увеличивает distance_factor после BE (шире = SL дальше от цены).
    Возвращает (новый_фактор, note_или_None).
    """
    if not should_widen_trailing_after_be(
        cfg=cfg,
        tp_progress_phase=tp_progress_phase,
        side=side,
        entry=entry,
        stop_loss=stop_loss,
        be_buffer_pct=be_buffer_pct,
    ):
        return float(distance_factor), None
    base = max(0.05, float(distance_factor))
    widened = min(3.0, base * float(cfg.widen_mult))
    note = (
        f"Trailing after BE widen ×{cfg.widen_mult:g} "
        f"(dist_factor {base:.2f}→{widened:.2f})"
    )
    return widened, note
