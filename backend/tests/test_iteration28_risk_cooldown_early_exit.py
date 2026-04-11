#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from engine.risk_manager import RiskGuard


def test_early_exit_small_loss_does_not_set_cooldown_or_consecutive():
    guard = RiskGuard(
        cooldown_after_loss_sec=600,
        min_loss_usdt_for_cooldown=0.25,
        min_loss_usdt_for_consecutive=0.5,
        ignore_loss_cooldown_reasons=["early_exit"],
        ignore_consecutive_loss_reasons=["early_exit"],
    )
    guard.record_trade(-0.01, symbol="LYNUSDT", reason="early_exit")

    allowed, _reason = guard.can_trade("LYNUSDT")
    assert allowed is True
    assert guard._consecutive_losses == 0


def test_real_loss_sets_cooldown_and_consecutive():
    guard = RiskGuard(
        cooldown_after_loss_sec=600,
        min_loss_usdt_for_cooldown=0.25,
        min_loss_usdt_for_consecutive=0.5,
        ignore_loss_cooldown_reasons=["early_exit"],
        ignore_consecutive_loss_reasons=["early_exit"],
    )
    guard.record_trade(-1.2, symbol="LYNUSDT", reason="liquidation_stop")

    allowed, reason = guard.can_trade("LYNUSDT")
    assert allowed is False
    assert "Cooldown" in reason
    assert guard._consecutive_losses == 1
