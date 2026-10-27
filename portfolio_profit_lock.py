#!/usr/bin/env python3
"""Обратная совместимость: основной модуль перенесён в prd_agent.positions.portfolio_profit_lock."""
from __future__ import annotations

from prd_agent.positions.portfolio_profit_lock import (
    LockState,
    LockStatus,
    PortfolioProfitLock,
)

__all__ = ["LockState", "LockStatus", "PortfolioProfitLock"]
