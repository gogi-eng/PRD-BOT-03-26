#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from ..execution_pro import ExecutionAIPro


@dataclass
class ExecutionAgent:
    pro: ExecutionAIPro

    async def run(self, decision: Dict[str, Any], size: float) -> Dict[str, Any]:
        return await self.pro.execute(decision, size)
