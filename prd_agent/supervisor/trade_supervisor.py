"""Обратная совместимость: TradeSupervisor(cfg, store_dir, improver)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.supervisor.supervisor_v4 import SupervisorV4


class TradeSupervisor(SupervisorV4):
    """Старый API: store_dir = data/supervisor или любая папка data."""

    def __init__(
        self,
        cfg: Dict[str, Any],
        store_dir: Path,
        improver: SelfImprover,
    ):
        p = Path(store_dir)
        data_dir = p.parent if p.name == "supervisor" else p
        super().__init__(cfg, data_dir, improver)


__all__ = ["TradeSupervisor"]
