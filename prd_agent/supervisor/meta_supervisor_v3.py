"""Обратная совместимость: MetaSupervisorV3 — делегирует SupervisorV4."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.supervisor.supervisor_v4 import SupervisorMode, SupervisorV4


class MetaSupervisorV3:
    """Shim для старых вызовов MetaSupervisorV3(cfg, data_dir)."""

    def __init__(self, cfg: Dict[str, Any], data_dir: Path):
        root = Path(str(cfg.get("_root", Path(data_dir).parent)))
        improver = SelfImprover(cfg, root)
        self._v4 = SupervisorV4(cfg, Path(data_dir), improver)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._v4, name)

    def tick(self, **kwargs: Any) -> Dict[str, Any]:
        return self._v4.tick_meta(**kwargs)

    def snapshot(self) -> Dict[str, Any]:
        return self._v4.meta_snapshot()

    @staticmethod
    def format_status_line(snap: Dict[str, Any]) -> str:
        return SupervisorV4.format_meta_status_line(snap)


__all__ = ["MetaSupervisorV3", "SupervisorMode"]
