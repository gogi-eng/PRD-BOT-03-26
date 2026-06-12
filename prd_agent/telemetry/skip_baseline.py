"""
Baseline-метрики: % SKIP по причинам за N дней (ledger + supervisor buckets).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from prd_agent.analysis.signal_ledger import SignalLedger


def bucket_skip_reason(reason: str) -> str:
    """Категория причины пропуска (совместима с supervisor skipped backtest)."""
    r = str(reason or "").strip().lower()
    if not r:
        return "unknown"
    if "quality_gate" in r:
        if "rr" in r:
            return "quality_gate_rr"
        if "confidence" in r:
            return "quality_gate_conf"
        return "quality_gate"
    if "entry_guard" in r:
        return "entry_guard"
    if "pullback" in r:
        return "pullback_entry"
    if "impulse_retest" in r or "retest_watch" in r:
        return "retest"
    if "zone_entry" in r:
        return "zone_entry"
    if "entry_pipeline" in r:
        return "entry_pipeline"
    if "supervisor" in r or "meta_v3" in r or "пауза" in r:
        return "supervisor_block"
    if "circuit" in r or "rate" in r:
        return "api_circuit"
    if "на бирже уже" in r or "position" in r:
        return "position_open"
    if ":" in r:
        return r.split(":", 1)[0].strip()[:48]
    return r[:48]


def skip_baseline_from_rows(rows: List[Dict[str, Any]], hours: float = 168) -> Dict[str, Any]:
    """Агрегация SKIP по bucket из списка записей ledger."""
    skipped = [r for r in rows if str(r.get("status", "")).lower() == "skipped"]
    total = len(rows)
    by_bucket: Dict[str, int] = {}
    by_reason_raw: Dict[str, int] = {}
    for r in skipped:
        reason = str(r.get("reason", "") or "")
        bucket = bucket_skip_reason(reason)
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1
        short = reason[:80] if reason else "empty"
        by_reason_raw[short] = by_reason_raw.get(short, 0) + 1

    n_skip = len(skipped)
    pct_skip = round(n_skip / total * 100, 1) if total else 0.0
    pct_by_bucket: Dict[str, float] = {}
    if n_skip:
        for k, v in by_bucket.items():
            pct_by_bucket[k] = round(v / n_skip * 100, 1)

    top_buckets = sorted(by_bucket.items(), key=lambda x: -x[1])[:12]
    top_raw = sorted(by_reason_raw.items(), key=lambda x: -x[1])[:8]

    return {
        "period_hours": hours,
        "total_signals": total,
        "skipped": n_skip,
        "pct_skipped_of_total": pct_skip,
        "by_bucket": dict(by_bucket),
        "pct_of_skips_by_bucket": pct_by_bucket,
        "top_buckets": top_buckets,
        "top_raw_reasons": top_raw,
    }


def skip_baseline_report(ledger: SignalLedger, hours: float = 168) -> Dict[str, Any]:
    """Полный baseline из SignalLedger за N часов (по умолчанию 7 дней)."""
    rows = ledger.recent(hours)
    return skip_baseline_from_rows(rows, hours=hours)


def format_skip_baseline_text(report: Dict[str, Any]) -> str:
    """Текст для лога / bi-hourly / CLI."""
    lines = [
        f"SKIP baseline ({report.get('period_hours', 0):.0f}h): "
        f"{report.get('skipped', 0)}/{report.get('total_signals', 0)} "
        f"({report.get('pct_skipped_of_total', 0)}%)",
    ]
    for bucket, cnt in report.get("top_buckets", []):
        pct = report.get("pct_of_skips_by_bucket", {}).get(bucket, 0)
        lines.append(f"  • {bucket}: {cnt} ({pct}% of skips)")
    return "\n".join(lines)
