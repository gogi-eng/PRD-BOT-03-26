"""Тесты анализа удачных TP (Hermes learning)."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.learning.winning_entry_rules import (
    WinningEntryRulesAnalyzer,
    build_markdown_report,
    mine_rules,
    load_skipped_tp_winners,
    WinningSignalRecord,
)


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_mines_rules_from_synthetic_tp(tmp_path: Path):
    data = tmp_path / "data"
    skipped = data / "supervisor" / "skipped_backtest" / "results.jsonl"
    ledger = data / "ledger" / "signal_ledger.jsonl"
    _write_jsonl(
        skipped,
        [
            {
                "ledger_id": "a1",
                "symbol": "BTCUSDT",
                "side": "Buy",
                "source": "own_multi_agent",
                "skip_reason": "supervisor_v4: panic",
                "outcome": "take_profit",
                "pnl_pct_net": 1.2,
                "signal_at": "2026-06-20T10:00:00+00:00",
                "backtested_at": "2026-06-20T12:00:00+00:00",
            },
            {
                "ledger_id": "a2",
                "symbol": "ETHUSDT",
                "side": "Buy",
                "source": "own_multi_agent",
                "skip_reason": "orderflow_direction_mismatch",
                "outcome": "take_profit",
                "pnl_pct_net": 0.9,
                "signal_at": "2026-06-20T11:00:00+00:00",
                "backtested_at": "2026-06-20T13:00:00+00:00",
            },
            {
                "ledger_id": "a3",
                "symbol": "SOLUSDT",
                "side": "Buy",
                "source": "own_multi_agent",
                "skip_reason": "quality_gate: rr",
                "outcome": "take_profit",
                "pnl_pct_net": 1.5,
                "signal_at": "2026-06-20T12:00:00+00:00",
                "backtested_at": "2026-06-20T14:00:00+00:00",
            },
            {
                "ledger_id": "b1",
                "symbol": "XRPUSDT",
                "side": "Buy",
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
                "confidence": 0.92,
                "reason": "supervisor_v4: panic",
                "raw": {"regime": "trend", "rsi": 55},
            },
            {
                "id": "a2",
                "confidence": 0.89,
                "reason": "orderflow",
                "entry_context": {
                    "confidence": 0.89,
                    "rsi": 48,
                    "atr_pct": 0.004,
                    "regime": "trend",
                    "normalized_imbalance": 0.25,
                    "filters": {"rr_at_entry": 2.5},
                },
            },
            {
                "id": "a3",
                "confidence": 0.91,
                "entry_context": {
                    "confidence": 0.91,
                    "rsi": 50,
                    "atr_pct": 0.0035,
                    "regime": "trend",
                    "normalized_imbalance": 0.3,
                    "filters": {"rr_at_entry": 2.8},
                },
            },
        ],
    )

    analyzer = WinningEntryRulesAnalyzer(data)
    report = analyzer.analyze(hours=9999)
    assert report.tp_winners == 3
    assert report.tp_skipped_virtual == 3
    md = build_markdown_report(report)
    assert "Правила удачных входов" in md
    assert report.rules or report.winner_feature_medians


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
            "confidence": 0.9,
            "entry_context": {"rsi": 45, "regime": "trend"},
        }
    }
    rows = load_skipped_tp_winners(skipped_path=skipped, ledger_index=idx, hours=9999)
    assert len(rows) == 1
    assert rows[0].features.get("rsi") == 45
