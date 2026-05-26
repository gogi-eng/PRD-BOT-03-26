"""
Сбор честных меток для переобучения из trade_history.jsonl (реальные входы/выходы бота).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _parse_ts(val: Any) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _infer_exit_reason(row: Dict[str, Any], pnl: float) -> str:
    reason = str(row.get("reason", "") or "").lower()
    if reason in {"stop_loss", "take_profit", "time_stop", "late_retrace", "trailing"}:
        return reason
    if "time" in reason and "stop" in reason:
        return "time_stop"
    if pnl > 0:
        return "take_profit"
    if pnl < 0:
        return "stop_loss"
    return "exchange_closed"


def load_journal_pairs(journal_path: Path) -> List[Dict[str, Any]]:
    """Сопоставляет entered → closed в хронологическом порядке по символу."""
    if not journal_path.is_file():
        return []
    pending: Dict[str, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = str(row.get("event", "")).lower()
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue
        if ev == "entered":
            key = f"{sym}:{row.get('side', '')}"
            pending[key] = row
            continue
        if ev != "closed":
            continue
        side = str(row.get("side", "") or "")
        key = f"{sym}:{side}" if side else sym
        ent = pending.pop(key, None) or pending.pop(sym, None)
        if not ent:
            continue
        entry = float(ent.get("entry") or 0)
        exit_p = float(row.get("exit") or 0)
        if entry <= 0 and exit_p > 0:
            entry = exit_p
        qty = float(row.get("qty") or ent.get("qty") or 0)
        pnl_usdt = float(row.get("pnl") or 0)
        pnl_pct = 0.0
        if entry > 0 and exit_p > 0:
            if str(ent.get("side", row.get("side", ""))).lower() in ("buy", "long"):
                pnl_pct = (exit_p - entry) / entry * 100.0
            else:
                pnl_pct = (entry - exit_p) / entry * 100.0
        elif qty > 0 and entry > 0:
            pnl_pct = pnl_usdt / (entry * qty) * 100.0 if entry * qty else 0.0

        entry_dt = _parse_ts(ent.get("ts"))
        exit_dt = _parse_ts(row.get("ts"))
        hold_min = 0.0
        if entry_dt and exit_dt:
            hold_min = max(0.0, (exit_dt - entry_dt).total_seconds() / 60.0)

        sl = float(ent.get("stop_loss") or 0)
        tp = float(ent.get("take_profit") or 0)
        rr = 0.0
        if sl > 0 and tp > 0 and entry > 0:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0:
                rr = reward / risk

        out.append(
            {
                "symbol": sym,
                "side": str(ent.get("side") or row.get("side") or "Buy"),
                "entry_price": round(entry, 8),
                "stop_loss": round(sl, 8),
                "take_profit": round(tp, 8),
                "rr_ratio": round(rr, 4),
                "entry_time": ent.get("ts", ""),
                "exit_time": row.get("ts", ""),
                "exit_price": round(exit_p, 8),
                "pnl_pct": round(pnl_pct, 4),
                "result": "win" if pnl_pct > 0 else "loss",
                "exit_reason": _infer_exit_reason(row, pnl_pct),
                "source": "journal_feedback",
                "confidence": float(ent.get("confidence") or 0),
                "composite_score": 0.5,
                "trend_score": 0.5,
                "orderflow_score": 0.5,
                "ai_score": 0.5,
                "normalized_imbalance": 0.0,
                "htf_4h_trend": 0,
                "hold_minutes": round(hold_min, 2),
                "grade": str(ent.get("source") or ent.get("grade") or ""),
            }
        )
    return out


def filter_quality_rows(
    rows: List[Dict[str, Any]],
    *,
    min_abs_pnl_pct: float,
    min_hold_minutes: float,
    allowed_exit_reasons: Optional[Tuple[str, ...]] = None,
    include_soft: bool = True,
    soft_min_abs_pnl_pct: float = 0.15,
    soft_min_hold_minutes: float = 3.0,
) -> List[Dict[str, Any]]:
    allowed = allowed_exit_reasons or (
        "stop_loss",
        "take_profit",
        "time_stop",
        "late_retrace",
        "trailing",
        "exchange_closed",
    )
    out: List[Dict[str, Any]] = []
    for row in rows:
        ex = str(row.get("exit_reason", ""))
        if ex not in allowed:
            continue
        pnl_abs = abs(float(row.get("pnl_pct", 0) or 0))
        hold = float(row.get("hold_minutes", 0) or 0)
        hard_ok = pnl_abs >= min_abs_pnl_pct and hold >= min_hold_minutes
        soft_ok = include_soft and pnl_abs >= soft_min_abs_pnl_pct and hold >= soft_min_hold_minutes
        if hard_ok or soft_ok:
            tagged = dict(row)
            tagged["label_tier"] = "hard" if hard_ok else "soft"
            out.append(tagged)
    return out


def merge_journal_into_dataset(
    dataset_path: Path,
    journal_rows: List[Dict[str, Any]],
    *,
    dedupe_window_sec: float = 120.0,
) -> int:
    """Добавляет новые journal_feedback строки. Возвращает число добавленных."""
    existing: List[Dict[str, Any]] = []
    if dataset_path.is_file():
        try:
            loaded = json.loads(dataset_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = []

    seen: set[str] = set()
    for row in existing:
        key = f"{row.get('symbol')}|{row.get('entry_time')}|{row.get('exit_time')}"
        seen.add(key)

    added = 0
    for row in journal_rows:
        key = f"{row.get('symbol')}|{row.get('entry_time')}|{row.get('exit_time')}"
        if key in seen:
            continue
        seen.add(key)
        existing.append(row)
        added += 1

    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return added
