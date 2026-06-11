"""Сброс протокола восстановления Supervisor V4."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prd_agent.supervisor.supervisor_v4 import SupervisorMode, SupervisorV4


def test_clear_recovery_protocol_clears_panic(tmp_path):
    sup_cfg = {"supervisor_v4": {"enabled": True, "mode_cooldown_minutes": 0}}
    sup = SupervisorV4(sup_cfg, tmp_path / "data", improver=object())  # type: ignore[arg-type]
    sup._meta.panic_until = datetime.now(timezone.utc) + timedelta(minutes=30)
    sup._meta.mode = SupervisorMode.DEFENSIVE

    msg = sup.clear_recovery_protocol()

    assert sup._meta.panic_until is None
    assert sup._meta.mode == SupervisorMode.NORMAL
    assert "снят" in msg.lower()
