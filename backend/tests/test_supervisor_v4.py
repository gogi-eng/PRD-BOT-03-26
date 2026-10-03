"""Supervisor V4: объединённый надсмотрщик."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.supervisor.supervisor_v4 import SupervisorMode, SupervisorV4


def _cfg(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "trading": {
            "symbol_blacklist": ["SOLUSDT"],
            "block_entry_utc_hours": [3, 11],
            "risk_pct_per_trade": 0.35,
        },
        "supervisor_v4": {
            "enabled": True,
            "mode_cooldown_minutes": 120,
            "seed_blocked_symbols": ["CLOUSDT"],
            "seed_blocked_utc_hours": [2, 5],
            "preferred_utc_hours": [4],
            "min_trades_for_block": 3,
            "max_symbol_loss_usdt": -5,
            "max_symbol_wr_pct": 40,
        },
    }


def _make(tmp_path: Path) -> SupervisorV4:
    cfg = _cfg(tmp_path)
    imp = SelfImprover(cfg, tmp_path)
    return SupervisorV4(cfg, tmp_path / "data", imp)


def test_blocks_seed_symbol_and_hour(tmp_path: Path) -> None:
    sup = _make(tmp_path)
    ok, reason = sup.can_enter("SOLUSDT", utc_hour=10)
    assert not ok
    assert "SOLUSDT" in reason
    ok, reason = sup.can_enter("BTCUSDT", utc_hour=2)
    assert not ok
    assert "2" in reason


def test_blocks_local_hours_with_timezone_offset(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg["timezone_offset"] = 3
    cfg["supervisor_v4"]["seed_blocked_utc_hours"] = [18]
    imp = SelfImprover(cfg, tmp_path)
    sup = SupervisorV4(cfg, tmp_path / "data", imp)
    # UTC 15:00 = местный 18:00 — блок
    ok, reason = sup.can_enter("BTCUSDT", utc_hour=15)
    assert not ok
    assert "18" in reason
    # UTC 18:00 = местный 21:00 — не блок (18 местный не совпадает)
    ok, reason = sup.can_enter("BTCUSDT", utc_hour=18)
    assert ok, reason


def test_defensive_only_preferred_hours(tmp_path: Path) -> None:
    sup = _make(tmp_path)
    sup._meta.mode = SupervisorMode.DEFENSIVE
    sup._meta.mode_changed_at = datetime.now(timezone.utc) - timedelta(hours=3)
    ok, _ = sup.can_enter("BTCUSDT", utc_hour=4)
    assert ok
    ok, reason = sup.can_enter("BTCUSDT", utc_hour=10)
    assert not ok
    assert "DEFENSIVE" in reason


def test_preferred_hour_stays_open_when_learned(tmp_path: Path) -> None:
    """Час из preferred_utc_hours обучение не закрывает. Список yaml и чужой час — закрывает."""
    cfg = _cfg(tmp_path)
    cfg["timezone_offset"] = 3
    cfg["trading"]["block_entry_utc_hours"] = []
    cfg["supervisor_v4"]["seed_blocked_utc_hours"] = [3]
    cfg["supervisor_v4"]["preferred_utc_hours"] = [12]
    cfg["supervisor_v4"]["max_hour_loss_usdt"] = -12
    cfg["supervisor_v4"]["max_hour_wr_pct"] = 35
    cfg["supervisor_v4"]["max_symbol_loss_usdt"] = -100
    data_dir = tmp_path / "data"
    journal = data_dir / "trades" / "trade_history.jsonl"
    journal.parent.mkdir(parents=True)
    rows = []
    for hour, symbol in ((9, "ADAUSDT"), (12, "DOTUSDT")):
        ts = datetime(2026, 10, 2, hour, 15, tzinfo=timezone.utc).isoformat()
        for _ in range(4):
            rows.append(
                json.dumps(
                    {
                        "event": "closed",
                        "ts": ts,
                        "symbol": symbol,
                        "pnl": -4.0,
                    }
                )
            )
    journal.write_text("\n".join(rows) + "\n", encoding="utf-8")
    imp = SelfImprover(cfg, tmp_path)
    sup = SupervisorV4(cfg, data_dir, imp)
    sup.tick_meta()
    # UTC 9 + 3 = местный 12, этот час в preferred
    ok, reason = sup.can_enter("ADAUSDT", utc_hour=9)
    assert ok, reason
    assert 12 not in sup.blocked_hours()
    # UTC 12 + 3 = местный 15, его в preferred нет
    ok, reason = sup.can_enter("DOTUSDT", utc_hour=12)
    assert not ok
    assert "15" in reason
    # Список из yaml (местный 3) остаётся закрытым, даже если он когда-то был в preferred
    ok, reason = sup.can_enter("ADAUSDT", utc_hour=0)
    assert not ok
    assert "3" in reason


def test_learns_bad_symbol_from_journal(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    journal = data_dir / "trades" / "trade_history.jsonl"
    journal.parent.mkdir(parents=True)
    rows = []
    for _ in range(4):
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
    sup = _make(tmp_path)
    sup.tick_meta()
    assert "ZECUSDT" in sup.blocked_symbols()


def test_virtual_signal_and_leverage(tmp_path: Path) -> None:
    from prd_agent.signals.types import UnifiedSignal

    sup = _make(tmp_path)
    sup.register_virtual_signal(
        symbol="BTCUSDT",
        side="Buy",
        entry=100,
        stop_loss=99,
        take_profit=103,
        source="ta",
        confidence=0.9,
    )
    sig = UnifiedSignal("BTCUSDT", "Buy", 0.9, "ta", entry=100, stop_loss=99, take_profit=103)
    advice = sup.recommend_leverage(sig, entry=100, stop_loss=99, take_profit=103)
    assert 20 <= advice.leverage <= 50


def test_format_report_includes_meta(tmp_path: Path) -> None:
    sup = _make(tmp_path)
    lines = SupervisorV4.format_report_section({"meta": sup.meta_snapshot(), "virtual_2h": {}})
    text = "\n".join(lines)
    assert "Supervisor V4" in text
