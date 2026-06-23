"""Тесты анализа входов и влияния фильтров (Hermes learning)."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.learning.winning_entry_rules import (
    WinningEntryRulesAnalyzer,
    build_markdown_report,
    classify_outcome_quality,
    load_skipped_tp_winners,
)


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_classify_outcome_quality():
    assert classify_outcome_quality(pnl_usdt=2.0, pnl_pct=0.5) == "profit"
    assert classify_outcome_quality(pnl_usdt=-1.0, pnl_pct=-0.2) == "loss"
    assert classify_outcome_quality(pnl_usdt=0.1, pnl_pct=0.01) == "neutral"
    assert classify_outcome_quality(simulated_outcome="take_profit") == "profit"


def test_mines_rules_and_filter_impacts(tmp_path: Path):
    data = tmp_path / "data"
    skipped = data / "supervisor" / "skipped_backtest" / "results.jsonl"
    ledger = data / "ledger" / "signal_ledger.jsonl"
    journal = data / "trades" / "trade_history.jsonl"
    _write_jsonl(
        skipped,
        [
            {
                "ledger_id": "a1",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "skip_reason": "supervisor_v4: panic",
                "outcome": "take_profit",
                "pnl_pct_net": 1.2,
                "backtested_at": "2026-06-20T12:00:00+00:00",
            },
            {
                "ledger_id": "a2",
                "symbol": "ETHUSDT",
                "side": "Buy",
                "skip_reason": "orderflow_direction_mismatch",
                "outcome": "take_profit",
                "pnl_pct_net": 0.9,
                "backtested_at": "2026-06-20T13:00:00+00:00",
            },
            {
                "ledger_id": "a3",
                "symbol": "SOLUSDT",
                "side": "Buy",
                "skip_reason": "orderflow_direction_mismatch",
                "outcome": "take_profit",
                "pnl_pct_net": 1.1,
                "backtested_at": "2026-06-20T14:00:00+00:00",
            },
            {
                "ledger_id": "b1",
                "symbol": "XRPUSDT",
                "side": "Buy",
                "skip_reason": "quality_gate",
                "outcome": "stop_loss",
                "pnl_pct_net": -0.8,
                "backtested_at": "2026-06-20T15:00:00+00:00",
            },
        ],
    )
    _write_jsonl(
        ledger,
        [
            {
                "id": "a1",
                "entry_context": {
                    "regime": "trend",
                    "htf_trend": "bullish",
                    "adx": 28,
                    "side": "BUY",
                    "active_rules": ["regime_trend", "htf_aligned", "adx_ok"],
                },
            },
            {
                "id": "a2",
                "entry_context": {
                    "regime": "trend",
                    "htf_trend": "bullish",
                    "adx": 26,
                    "side": "BUY",
                    "active_rules": ["regime_trend", "htf_aligned"],
                },
            },
            {
                "id": "a3",
                "entry_context": {
                    "regime": "trend",
                    "htf_trend": "bullish",
                    "side": "BUY",
                    "active_rules": ["regime_trend", "htf_aligned"],
                },
            },
        ],
    )
    _write_jsonl(
        journal,
        [
            {
                "event": "entered",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "entry": 100,
                "entry_context": {
                    "regime": "trend",
                    "htf_trend": "bullish",
                    "adx": 30,
                    "side": "BUY",
                    "active_rules": ["regime_trend", "htf_aligned", "adx_strong"],
                },
            },
            {
                "event": "closed",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "pnl": 1.5,
                "reason": "take_profit",
                "ts": "2026-06-21T10:00:00+00:00",
            },
            {
                "event": "entered",
                "symbol": "ETHUSDT",
                "side": "Buy",
                "entry": 50,
                "entry_context": {
                    "regime": "chop",
                    "htf_trend": "bearish",
                    "side": "BUY",
                    "active_rules": ["htf_misaligned", "regime_chop"],
                },
            },
            {
                "event": "closed",
                "symbol": "ETHUSDT",
                "side": "Buy",
                "pnl": -0.5,
                "reason": "stop_loss",
                "ts": "2026-06-21T11:00:00+00:00",
            },
            {
                "event": "entered",
                "symbol": "SOLUSDT",
                "side": "Buy",
                "entry": 20,
            },
            {
                "event": "closed",
                "symbol": "SOLUSDT",
                "side": "Buy",
                "pnl": 0.005,
                "reason": "early_exit",
                "ts": "2026-06-21T12:00:00+00:00",
            },
        ],
    )

    analyzer = WinningEntryRulesAnalyzer(data)
    report = analyzer.analyze(hours=9999)
    assert report.outcome_counts.get("profit", 0) >= 4
    assert report.outcome_counts.get("loss", 0) >= 1
    assert report.outcome_counts.get("neutral", 0) >= 1
    md = build_markdown_report(report)
    assert "профит" in md.lower() or "Профит" in md
    assert report.filter_impacts or report.weight_recommendations or report.rules


def test_load_skipped_tp_joins_ledger(tmp_path: Path):
    skipped = tmp_path / "results.jsonl"
    _write_jsonl(
        skipped,
        [
            {
                "ledger_id": "x1",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "outcome": "take_profit",
                "pnl_pct_net": 1.0,
                "backtested_at": "2026-06-21T10:00:00+00:00",
            }
        ],
    )
    idx = {
        "x1": {
            "id": "x1",
            "entry_context": {"rsi": 45, "regime": "trend"},
        }
    }
    rows = load_skipped_tp_winners(skipped_path=skipped, ledger_index=idx, hours=9999)
    assert len(rows) == 1
    assert rows[0].features.get("rsi") == 45
    assert rows[0].outcome_quality == "profit"
