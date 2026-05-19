"""Auto-block Telegram signal sources after sustained virtual losses (channel rating outcomes)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


STATE_OUTCOMES = "channel_signal_outcomes"
STATE_BLOCKS = "channel_auto_blocks"


@dataclass
class ChannelAutoBlockConfig:
    enabled: bool = False
    lookback_days: float = 5.0
    # Need at least this many resolved win+loss events in the window (neutrals excluded from ratio).
    min_resolved_signals: int = 5
    min_losses: int = 4
    max_wins: int = 1
    # If > 0, also require loss_ratio >= this (among win+loss only).
    min_loss_ratio: float = 0.0
    # 0 = block until manually removed from state JSON; else auto-unblock after N days.
    block_duration_days: float = 0.0
    skip_trusted_sources: bool = True


def _parse_dt(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def record_outcome(
    state: dict[str, Any],
    source_key: str,
    outcome: str,
    symbol: str,
    when: datetime,
) -> None:
    key = str(source_key or "").strip().lower()
    if not key or outcome not in {"win", "loss", "neutral"}:
        return
    node = state.setdefault(STATE_OUTCOMES, {})
    if not isinstance(node, dict):
        node = {}
        state[STATE_OUTCOMES] = node
    rows: list[dict[str, Any]] = node.setdefault(key, [])
    if not isinstance(rows, list):
        rows = []
        node[key] = rows
    rows.append(
        {
            "t": when.astimezone(timezone.utc).isoformat(),
            "outcome": outcome,
            "symbol": str(symbol or "").upper(),
        }
    )
    # cap storage
    del rows[:-500]


def _prune_outcomes(rows: list[dict[str, Any]], cutoff: datetime) -> list[dict[str, Any]]:
    keep: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        dt = _parse_dt(str(item.get("t", "")))
        if dt is None or dt >= cutoff:
            keep.append(item)
    return keep[-400:]


def outcomes_in_window(rows: list[dict[str, Any]], cutoff: datetime) -> tuple[int, int, int]:
    w = l = n = 0
    for item in rows:
        if not isinstance(item, dict):
            continue
        dt = _parse_dt(str(item.get("t", "")))
        if dt is None or dt < cutoff:
            continue
        o = str(item.get("outcome", "")).lower()
        if o == "win":
            w += 1
        elif o == "loss":
            l += 1
        elif o == "neutral":
            n += 1
    return w, l, n


def should_block_source(w: int, l: int, cfg: ChannelAutoBlockConfig) -> bool:
    resolved = w + l
    if resolved < max(1, cfg.min_resolved_signals):
        return False
    if l < cfg.min_losses:
        return False
    if w > cfg.max_wins:
        return False
    if cfg.min_loss_ratio > 0 and resolved > 0:
        if (l / resolved) + 1e-9 < cfg.min_loss_ratio:
            return False
    return True


def refresh_auto_blocks(
    state: dict[str, Any],
    cfg: ChannelAutoBlockConfig,
    *,
    trusted_keys: set[str],
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """Drop expired blocks, prune old outcomes, apply rules. Returns newly blocked (key, reason)."""
    if not cfg.enabled:
        return []
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(days=max(0.5, float(cfg.lookback_days or 1.0)))
    new_blocks: list[tuple[str, str]] = []

    outcomes_root = state.get(STATE_OUTCOMES, {})
    if isinstance(outcomes_root, dict):
        for key, rows in list(outcomes_root.items()):
            if not isinstance(rows, list):
                continue
            outcomes_root[key] = _prune_outcomes(rows, cutoff - timedelta(days=1))

    blocks = state.setdefault(STATE_BLOCKS, {})
    if not isinstance(blocks, dict):
        blocks = {}
        state[STATE_BLOCKS] = blocks

    # expire
    for key, meta in list(blocks.items()):
        if not isinstance(meta, dict):
            blocks.pop(key, None)
            continue
        until_raw = str(meta.get("until", "") or "")
        if not until_raw:
            continue
        until = _parse_dt(until_raw)
        if until is not None and now >= until:
            blocks.pop(key, None)

    # evaluate each source with outcomes
    if isinstance(outcomes_root, dict):
        for key, rows in outcomes_root.items():
            if not isinstance(rows, list) or not key:
                continue
            if cfg.skip_trusted_sources and str(key).lower() in trusted_keys:
                continue
            if key in blocks:
                continue
            rows_effective = _prune_outcomes(rows, cutoff)
            w, l, _n = outcomes_in_window(rows_effective, cutoff)
            if should_block_source(w, l, cfg):
                reason = (
                    f"auto_block: за {cfg.lookback_days}д исходы win={w} loss={l} "
                    f"(порог loss>={cfg.min_losses}, win<={cfg.max_wins}, min N={cfg.min_resolved_signals})"
                )
                until_iso = ""
                if float(cfg.block_duration_days or 0) > 0:
                    until_dt = now + timedelta(days=float(cfg.block_duration_days))
                    until_iso = until_dt.isoformat()
                blocks[key] = {
                    "blocked_at": now.isoformat(),
                    "reason": reason,
                    "until": until_iso,
                    "window_losses": l,
                    "window_wins": w,
                }
                new_blocks.append((key, reason))
    return new_blocks


def is_blocked(state: dict[str, Any], source_key: str, now: datetime | None = None) -> bool:
    key = str(source_key or "").strip().lower()
    if not key:
        return False
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    blocks = state.get(STATE_BLOCKS, {})
    if not isinstance(blocks, dict):
        return False
    meta = blocks.get(key)
    if not isinstance(meta, dict):
        # substring match for compact keys (same idea as ignored_chats)
        for bk, bmeta in list(blocks.items()):
            if isinstance(bmeta, dict) and len(str(bk)) >= 6 and str(bk) in key:
                meta = bmeta
                break
    if not isinstance(meta, dict):
        return False
    until_raw = str(meta.get("until", "") or "")
    if until_raw:
        until = _parse_dt(until_raw)
        if until is not None and now >= until:
            blocks.pop(key, None)
            return False
    return True


def from_agent_cfg(raw: dict[str, Any] | None) -> ChannelAutoBlockConfig:
    node = raw if isinstance(raw, dict) else {}
    return ChannelAutoBlockConfig(
        enabled=bool(node.get("enabled", False)),
        lookback_days=float(node.get("lookback_days", 5.0) or 5.0),
        min_resolved_signals=int(node.get("min_resolved_signals", 5) or 5),
        min_losses=int(node.get("min_losses", 4) or 4),
        max_wins=int(node.get("max_wins", 1) or 1),
        min_loss_ratio=float(node.get("min_loss_ratio", 0.0) or 0.0),
        block_duration_days=float(node.get("block_duration_days", 0.0) or 0.0),
        skip_trusted_sources=bool(node.get("skip_trusted_sources", True)),
    )
