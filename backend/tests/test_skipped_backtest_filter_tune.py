"""Автоподстройка фильтров по бэктесту пропусков."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.supervisor.supervisor_v4 import SupervisorV4


def test_proposals_loosen_rr_when_skipped_were_profitable(tmp_path: Path) -> None:
    cfg = {
        "_root": str(tmp_path),
        "self_improvement": {"enabled": True, "auto_apply_low_risk": False},
        "supervisor_v4": {"skipped_signal_backtest": {"auto_tune_min_samples": 3}},
    }
    imp = SelfImprover(cfg, tmp_path)
    sup = SupervisorV4(cfg, tmp_path / "data", imp)
    bt = sup.skipped_bt
    bt.results_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "ledger_id": f"id{i}",
            "skip_reason": "quality_gate: RR 1.8 < 2.00",
            "outcome": "take_profit",
            "pnl_pct": 1.0,
            "backtested_at": now,
        }
        for i in range(5)
    ]
    bt.results_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    props = sup._proposals_from_skipped_backtest_by_reason()
    paths = [tuple(p["path"]) for p in props]
    assert ("quality_gate", "min_rr_ratio") in paths
    rr_prop = next(p for p in props if p["path"] == ["quality_gate", "min_rr_ratio"])
    assert float(rr_prop["delta"]) < 0
