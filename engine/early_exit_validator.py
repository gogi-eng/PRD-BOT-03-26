#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass
class EarlyExitValidationResult:
    is_valid: bool
    code: str
    detail: str


class EarlyExitValidator:
    """
    Defensive validator for early-exit decisions.

    Purpose:
    - prevent technically invalid closes (NaN/inf/negative thresholds),
    - prevent contradictory closes when progress is already sufficient.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)

    @staticmethod
    def _is_finite(value: float) -> bool:
        return isinstance(value, (int, float)) and math.isfinite(float(value))

    def validate(
        self,
        *,
        bars_since_entry: int,
        early_exit_bars: int,
        trailing_active: bool,
        hold_ok: bool,
        profit: float,
        favorable_profit: float,
        effective_profit: float,
        min_profit: float,
    ) -> EarlyExitValidationResult:
        if not self.enabled:
            return EarlyExitValidationResult(True, "validator_disabled", "Validator disabled")

        if early_exit_bars <= 0:
            return EarlyExitValidationResult(False, "early_exit_disabled", "early_exit_bars <= 0")

        if bars_since_entry < early_exit_bars:
            return EarlyExitValidationResult(
                False,
                "bars_not_reached",
                f"bars_since_entry={bars_since_entry} < early_exit_bars={early_exit_bars}",
            )

        if trailing_active:
            return EarlyExitValidationResult(
                False,
                "trailing_active",
                "Trailing is active; early-exit must not trigger",
            )

        if not hold_ok:
            return EarlyExitValidationResult(False, "min_hold_not_reached", "Minimum hold time not reached")

        numeric_fields = {
            "profit": profit,
            "favorable_profit": favorable_profit,
            "effective_profit": effective_profit,
            "min_profit": min_profit,
        }
        for key, value in numeric_fields.items():
            if not self._is_finite(value):
                return EarlyExitValidationResult(False, "non_finite_metric", f"{key} is non-finite: {value}")

        if min_profit < 0:
            return EarlyExitValidationResult(False, "negative_min_profit", f"min_profit={min_profit:.8f} < 0")

        if effective_profit + 1e-12 >= min_profit:
            return EarlyExitValidationResult(
                False,
                "progress_sufficient",
                f"effective_profit={effective_profit:.8f} >= min_profit={min_profit:.8f}",
            )

        return EarlyExitValidationResult(True, "ok_to_close", "Low-progress early-exit is valid")
