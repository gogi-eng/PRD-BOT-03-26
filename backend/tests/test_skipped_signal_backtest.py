"""Бэктест пропущенных сигналов по свечам."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from prd_agent.supervisor.skipped_signal_backtest import (
    SkippedSignalBacktester,
    simulate_skipped_signal,
)


def _klines_long_tp() -> list:
    base_ms = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    return [
        {
            "startTime": base_ms,
            "open": 100,
            "high": 101,
            "low": 99.5,
            "close": 100.5,
        },
        {
            "startTime": base_ms + 900_000,
            "open": 100.5,
            "high": 102,
            "low": 100,
            "close": 101.5,
        },
    ]


def test_simulate_long_take_profit() -> None:
    entry_ts = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    sim = simulate_skipped_signal(
        side="Buy",
        entry=100.0,
        stop_loss=99.0,
        take_profit=101.5,
        klines=_klines_long_tp(),
        entry_ts_ms=entry_ts,
    )
    assert sim["outcome"] == "take_profit"
    assert sim["pnl_pct"] > 0


def test_simulate_long_stop_loss() -> None:
    entry_ts = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    klines = [
        {
            "startTime": entry_ts,
            "high": 100,
            "low": 98.5,
            "close": 99,
        },
    ]
    sim = simulate_skipped_signal(
        side="Buy",
        entry=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        klines=klines,
        entry_ts_ms=entry_ts,
    )
    assert sim["outcome"] == "stop_loss"
    assert sim["pnl_pct"] < 0


def test_stats_by_reason_groups_quality_gate_rr(tmp_path) -> None:
    bt = SkippedSignalBacktester(tmp_path / "sup", cfg={"skipped_signal_backtest": {}})
    bt.results_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "ledger_id": "x1",
            "skip_reason": "quality_gate: RR 1.8 < 2.0",
            "outcome": "take_profit",
            "pnl_pct": 1.2,
            "backtested_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "ledger_id": "x2",
            "skip_reason": "quality_gate: RR 1.7 < 2.0",
            "outcome": "take_profit",
            "pnl_pct": 0.8,
            "backtested_at": datetime.now(timezone.utc).isoformat(),
        },
    ]
    with bt.results_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    by_r = bt.stats_by_reason(24)
    assert "quality_gate_rr" in by_r
    assert by_r["quality_gate_rr"]["n"] == 2
    assert by_r["quality_gate_rr"]["win_rate_pct"] == 100.0


def test_backtester_picks_skipped_only(tmp_path) -> None:
    class FakeLedger:
        def recent(self, hours: float):
            return [
                {
                    "id": "a1",
                    "status": "skipped",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "entry": 100,
                    "stop_loss": 99,
                    "take_profit": 102,
                    "reason": "quality_gate: RR",
                    "source": "ta",
                    "created_at": "2020-01-01T00:00:00+00:00",
                },
                {
                    "id": "a2",
                    "status": "executed",
                    "symbol": "ETHUSDT",
                    "side": "Buy",
                    "created_at": "2020-01-01T00:00:00+00:00",
                },
            ]

    bt = SkippedSignalBacktester(tmp_path / "sup", cfg={"skipped_signal_backtest": {}})
    picked = bt._pick_candidates(FakeLedger().recent(72))
    assert len(picked) == 1
    assert picked[0]["id"] == "a1"
