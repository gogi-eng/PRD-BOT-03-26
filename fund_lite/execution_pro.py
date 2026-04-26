#!/usr/bin/env python3
"""
Execution PRO — контракт поверх ``ExecutionEngine``.

Сейчас в PRD-SCALP: PostOnly limit + market fallback (см. ``engine.execution_engine``).
TWAP / IOC — задел под расширение (пока не реализованы в клиенте).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from engine.execution_engine import ExecutionEngine


@dataclass
class ExecutionAIPro:
    aggression_market_threshold: float = 0.7
    engine: Optional[ExecutionEngine] = None

    async def execute(self, decision: Dict[str, Any], size: float) -> Dict[str, Any]:
        """Маршрутизация: высокая агрессия → market (если подключён engine с клиентом)."""
        agg = float(decision.get("aggression", 0.0))
        if agg > self.aggression_market_threshold:
            return await self.market_order(decision, size)
        return await self.limit_order(decision, size)

    async def market_order(self, decision: Dict[str, Any], size: float) -> Dict[str, Any]:
        if self.engine is None:
            return {"success": False, "error": "ExecutionAIPro: engine not wired"}
        # Реальный вызов зависит от symbol/side в decision — оставляем контракт
        return {"success": False, "error": "wire engine.execute_entry/close from bot context"}

    async def limit_order(self, decision: Dict[str, Any], size: float) -> Dict[str, Any]:
        if self.engine is None:
            return {"success": False, "error": "ExecutionAIPro: engine not wired"}
        return {"success": False, "error": "wire engine.execute_entry from bot context"}
