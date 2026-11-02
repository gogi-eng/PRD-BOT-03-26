"""Сигнал живёт сутки. Повтор того же отпечатка не плодит копии. Синтетика не в бой."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from prd_agent.signals.types import UnifiedSignal

SIGNAL_TTL_SECONDS = 24 * 3600
SIGNAL_TTL_HOURS = 24.0
_SYNTHETIC_MARKERS = (
    "synthetic",
    "synth",
    "missing_bar",
    "missing-bar",
    "missingbar",
    "stub",
    "upstreamstub",
    "fake",
)


def parse_signal_ts(value: Any, *, now: Optional[datetime] = None) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        ts = value
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e12:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def signal_created_at(signal: UnifiedSignal) -> Optional[datetime]:
    raw = signal.raw if isinstance(signal.raw, dict) else {}
    for key in (
        "created_at",
        "message_time_utc",
        "message_dt",
        "ts",
        "timestamp",
        "checked_at_utc",
        "checked_at",
    ):
        parsed = parse_signal_ts(raw.get(key))
        if parsed is not None:
            return parsed
    return parse_signal_ts(getattr(signal, "created_at", None))


def is_signal_fresh(
    signal: UnifiedSignal,
    *,
    now: Optional[datetime] = None,
    ttl_seconds: int = SIGNAL_TTL_SECONDS,
) -> bool:
    ts = signal_created_at(signal)
    if ts is None:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - ts) <= timedelta(seconds=max(1, int(ttl_seconds)))


def _looks_synthetic_text(value: Any) -> bool:
    text = str(value or "").strip().lower().replace(" ", "_")
    if not text:
        return False
    return any(marker in text for marker in _SYNTHETIC_MARKERS)


def is_synthetic_or_missing_bar(signal: UnifiedSignal) -> bool:
    """Живой вход не берёт синтетику и дырки в свечах."""
    raw = signal.raw if isinstance(signal.raw, dict) else {}
    if raw.get("live") is False and (
        raw.get("synthetic") or raw.get("missing_bar") or raw.get("upstreamStub")
    ):
        return True
    for key in (
        "synthetic",
        "missing_bar",
        "missingBar",
        "upstreamStub",
        "stub",
    ):
        val = raw.get(key)
        if val is True or str(val).lower() in {"1", "true", "yes"}:
            return True
    for key in (
        "data_source",
        "dataSource",
        "bar_source",
        "barSource",
        "kline_source",
        "source_kind",
    ):
        if _looks_synthetic_text(raw.get(key)):
            return True
    return _looks_synthetic_text(raw.get("note")) or _looks_synthetic_text(
        getattr(signal, "reason", "")
    )


def signal_fingerprint(signal: UnifiedSignal) -> str:
    raw = signal.raw if isinstance(signal.raw, dict) else {}
    explicit = str(raw.get("fingerprint") or "").strip()
    if explicit:
        return explicit
    payload = "|".join(
        [
            str(signal.symbol or "").upper(),
            str(signal.side or "").upper(),
            f"{float(signal.entry or 0):.10g}",
            f"{float(signal.stop_loss or 0):.10g}",
            f"{float(signal.take_profit or 0):.10g}",
            str(signal.source or "").strip().lower(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def fingerprint_from_row(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    explicit = str(row.get("fingerprint") or "").strip()
    if explicit:
        return explicit
    payload = "|".join(
        [
            str(row.get("symbol") or "").upper(),
            str(row.get("side") or "").upper(),
            f"{float(row.get('entry') or 0):.10g}",
            f"{float(row.get('stop_loss') or 0):.10g}",
            f"{float(row.get('take_profit') or 0):.10g}",
            str(row.get("source") or "").strip().lower(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def filter_live_signals(
    signals: Sequence[UnifiedSignal],
    *,
    now: Optional[datetime] = None,
    ttl_seconds: int = SIGNAL_TTL_SECONDS,
) -> list[UnifiedSignal]:
    """Протухшие, синтетика и повтор того же отпечатка в бой не идут."""
    current = now or datetime.now(timezone.utc)
    kept: list[UnifiedSignal] = []
    seen: set[str] = set()
    for signal in signals:
        if is_synthetic_or_missing_bar(signal):
            continue
        if not is_signal_fresh(signal, now=current, ttl_seconds=ttl_seconds):
            continue
        key = signal_fingerprint(signal)
        if key in seen:
            continue
        seen.add(key)
        kept.append(signal)
    return kept


def oldest_created_at(signals: Iterable[UnifiedSignal]) -> str:
    oldest: Optional[datetime] = None
    oldest_raw = ""
    for signal in signals:
        ts = signal_created_at(signal)
        if ts is None:
            continue
        if oldest is None or ts < oldest:
            oldest = ts
            oldest_raw = getattr(signal, "created_at", "") or ts.isoformat()
    return oldest_raw or datetime.now(timezone.utc).isoformat()
