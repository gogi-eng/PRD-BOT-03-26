"""Meta-Supervisor V3: блок символов/часов, режимы, hysteresis."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prd_agent.supervisor.meta_supervisor_v3 import MetaSupervisorV3, SupervisorMode


def _cfg(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "trading": {
            "symbol_blacklist": ["SOLUSDT"],
            "block_entry_utc_hours": [3, 11],
            "risk_pct_per_trade": 0.35,
        },
        "meta_supervisor_v3": {
            "enabled": True,
            "mode_cooldown_minutes": 120,
            "seed_blocked_symbols": ["CLOUSDT"],
            "seed_blocked_utc_hours": [2, 5],
            "preferred_utc_hours": [4, 18],
            "min_trades_for_block": 3,
            "max_symbol_loss_usdt": -5,
            "max_symbol_wr_pct": 40,
        },
    }


def test_blocks_seed_symbol_and_hour(tmp_path: Path) -> None:
    sup = MetaSupervisorV3(_cfg(tmp_path), tmp_path / "data")
    ok, reason = sup.can_enter("SOLUSDT", utc_hour=10)
    assert not ok
    assert "SOLUSDT" in reason
    ok, reason = sup.can_enter("BTCUSDT", utc_hour=2)
    assert not ok
    assert "2" in reason


def test_defensive_only_preferred_hours(tmp_path: Path) -> None:
    sup = MetaSupervisorV3(_cfg(tmp_path), tmp_path / "data")
    sup.state.mode = SupervisorMode.DEFENSIVE
    sup.state.mode_changed_at = datetime.now(timezone.utc) - timedelta(hours=3)
    ok, _ = sup.can_enter("BTCUSDT", utc_hour=4)
    assert ok
    ok, reason = sup.can_enter("BTCUSDT", utc_hour=10)
    assert not ok
    assert "DEFENSIVE" in reason


def test_mode_cooldown_blocks_upgrade(tmp_path: Path) -> None:
    sup = MetaSupervisorV3(_cfg(tmp_path), tmp_path / "data")
    sup.state.mode = SupervisorMode.DEFENSIVE
    sup.state.mode_changed_at = datetime.now(timezone.utc)
    sup.tick(day_pnl_usdt=50, consecutive_losses=0, recent_wr_pct=70, recent_trades=10)
    assert sup.state.mode == SupervisorMode.DEFENSIVE


def test_learns_bad_symbol_from_journal(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    journal = data_dir / "trades" / "trade_history.jsonl"
    journal.parent.mkdir(parents=True)
    rows = []
    for i in range(4):
        rows.append(
            json.dumps(
                {
                    "event": "closed",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "symbol": "ZECUSDT",
                    "pnl": -3.0,
                }
            )
        )
    journal.write_text("\n".join(rows), encoding="utf-8")
    sup = MetaSupervisorV3(_cfg(tmp_path), data_dir)
    sup.tick()
    assert "ZECUSDT" in sup.blocked_symbols()


def test_effective_risk_never_exceeds_base(tmp_path: Path) -> None:
    sup = MetaSupervisorV3(_cfg(tmp_path), tmp_path / "data")
    base = 0.35
    assert sup.effective_risk_pct(base) <= base
    sup.state.mode = SupervisorMode.DEFENSIVE
    assert sup.effective_risk_pct(base) < base
