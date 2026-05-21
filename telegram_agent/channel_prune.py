"""
Отключение Telegram-каналов без сигналов дольше заданного порога (добавление в ignored_chats).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def normalize_chat_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lstrip("@").lower())


@dataclass
class ChannelPruneConfig:
    enabled: bool = True
    inactive_hours: float = 24.0
    min_track_hours: float = 48.0
    never_prune: Tuple[str, ...] = ()
    auto_update_config: bool = True
    notify_telegram: bool = True


def prune_config_from_agent(agent_cfg: Dict[str, Any]) -> ChannelPruneConfig:
    raw = agent_cfg.get("channel_prune", {})
    if not isinstance(raw, dict):
        raw = {}
    never = tuple(str(x) for x in (raw.get("never_prune", []) or []))
    return ChannelPruneConfig(
        enabled=bool(raw.get("enabled", True)),
        inactive_hours=float(raw.get("inactive_hours", 24)),
        min_track_hours=float(raw.get("min_track_hours", 48)),
        never_prune=never,
        auto_update_config=bool(raw.get("auto_update_config", True)),
        notify_telegram=bool(raw.get("notify_telegram", True)),
    )


def _parse_dt(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def load_channel_activity(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    node = state.get("channel_activity", {})
    if not isinstance(node, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, row in node.items():
        if isinstance(row, dict):
            out[str(key)] = dict(row)
    return out


def merge_ratings_into_activity(state: Dict[str, Any], activity: Dict[str, Dict[str, Any]]) -> None:
    ratings = state.get("channel_ratings", {})
    if not isinstance(ratings, dict):
        return
    for key, row in ratings.items():
        if not isinstance(row, dict):
            continue
        src = str(row.get("source") or key)
        k = normalize_chat_name(src)
        slot = activity.setdefault(k, {"source": src})
        ls = _parse_dt(str(row.get("last_signal_at", "")))
        if ls:
            prev = _parse_dt(str(slot.get("last_signal_at", "")))
            if prev is None or ls > prev:
                slot["last_signal_at"] = ls.isoformat()


def scan_jsonl_last_signals(
    paths: List[Path],
    activity: Dict[str, Dict[str, Any]],
) -> None:
    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines[-5000:]:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = ""
            ts = ""
            nested = row.get("signal") if isinstance(row.get("signal"), dict) else {}
            if nested:
                src = str(nested.get("source", ""))
                ts = str(
                    nested.get("received_at_utc")
                    or nested.get("message_time_utc")
                    or ""
                )
                review = row.get("review") if isinstance(row.get("review"), dict) else {}
                if not bool(review.get("approve")):
                    continue
            else:
                src = str(row.get("channel") or row.get("source") or "")
                ts = str(row.get("received_at_utc") or row.get("created_at") or "")
            if not src:
                continue
            dt = _parse_dt(ts)
            if dt is None:
                continue
            k = normalize_chat_name(src)
            slot = activity.setdefault(k, {"source": src})
            prev = _parse_dt(str(slot.get("last_signal_at", "")))
            if prev is None or dt > prev:
                slot["last_signal_at"] = dt.isoformat()


def find_inactive_channels(
    activity: Dict[str, Dict[str, Any]],
    cfg: ChannelPruneConfig,
    *,
    now: Optional[datetime] = None,
    trusted: Optional[Set[str]] = None,
    already_ignored: Optional[Set[str]] = None,
) -> List[Tuple[str, str, str]]:
    """
    Возвращает список (source_display, key, reason) для отключения.
    """
    if not cfg.enabled:
        return []
    now = now or datetime.now(timezone.utc)
    trusted = trusted or set()
    already_ignored = already_ignored or set()
    inactive_delta = timedelta(hours=cfg.inactive_hours)
    min_track_delta = timedelta(hours=cfg.min_track_hours)
    never = {normalize_chat_name(x) for x in cfg.never_prune}

    out: List[Tuple[str, str, str]] = []
    for key, row in activity.items():
        src = str(row.get("source") or key)
        nk = normalize_chat_name(src)
        if nk in trusted or nk in already_ignored or nk in never:
            continue
        if row.get("pruned_at"):
            continue
        first = _parse_dt(str(row.get("first_seen_at", "")))
        if first is None:
            continue
        if now - first < min_track_delta:
            continue
        last_sig = _parse_dt(str(row.get("last_signal_at", "")))
        if last_sig is None:
            out.append((src, key, f"нет сигналов за {cfg.min_track_hours:.0f}ч+ наблюдения"))
            continue
        if now - last_sig >= inactive_delta:
            hours = (now - last_sig).total_seconds() / 3600.0
            out.append((src, key, f"последний сигнал {hours:.1f}ч назад"))
    return out


def apply_prune_to_config(
    config_data: Dict[str, Any],
    to_prune: List[Tuple[str, str, str]],
) -> List[str]:
    """Добавляет каналы в telegram_signal_agent.ignored_chats. Возвращает добавленные имена."""
    if not to_prune:
        return []
    tsa = config_data.setdefault("telegram_signal_agent", {})
    if not isinstance(tsa, dict):
        tsa = {}
        config_data["telegram_signal_agent"] = tsa
    ignored = list(tsa.get("ignored_chats", []) or [])
    existing = {normalize_chat_name(x) for x in ignored}
    added: List[str] = []
    for src, _key, _reason in to_prune:
        nk = normalize_chat_name(src)
        if nk in existing:
            continue
        ignored.append(src)
        existing.add(nk)
        added.append(src)
    tsa["ignored_chats"] = ignored
    return added
