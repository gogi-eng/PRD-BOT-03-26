"""
Обучение весов правил входа: если правило 2 недели подряд «работает» — усилить его вес.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from prd_agent.entry.entry_soft_rules import POSITIVE_RULE_IDS, detect_active_rules

logger = logging.getLogger("prd_agent.rule_weights")


def _load_journal_pairs_with_context(journal_path: Path) -> List[Dict[str, Any]]:
    """entered + closed с entry_context для обучения весов."""
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
        out.append({"entered": ent, "closed": row, "pnl_usdt": float(row.get("pnl") or 0)})
    return out


class RuleWeightTracker:
    """Статистика правил по закрытым сделкам → мультипликаторы весов."""

    def __init__(self, data_dir: Path, cfg: Mapping[str, Any]):
        self.data_dir = Path(data_dir)
        self.cfg = dict(cfg)
        block = cfg.get("rule_weight_learning", {})
        self.block = block if isinstance(block, dict) else {}
        self.enabled = bool(self.block.get("enabled", True))
        self.lookback_days = max(1.0, float(self.block.get("lookback_days", 14) or 14))
        self.min_samples = max(1, int(self.block.get("min_samples", 5) or 5))
        self.min_win_rate = float(self.block.get("min_win_rate", 0.52) or 0.52)
        self.min_total_pnl = float(self.block.get("min_total_pnl", 0.0) or 0.0)
        self.max_weight_mult = float(self.block.get("max_weight_mult", 1.35) or 1.35)
        self.refresh_interval_sec = max(
            60.0, float(self.block.get("refresh_interval_sec", 3600) or 3600)
        )
        self.tz_offset = int(cfg.get("timezone_offset", 3) or 3)

        learn_dir = self.data_dir / "learning"
        learn_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = learn_dir / "rule_weights_state.json"
        self._last_refresh_ts = 0.0
        self._weights: Dict[str, float] = {}
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._validated: List[str] = []
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._weights = {
                str(k): float(v) for k, v in (data.get("weights") or {}).items()
            }
            self._stats = dict(data.get("stats") or {})
            self._validated = list(data.get("validated_rules") or [])
            self._last_refresh_ts = float(data.get("last_refresh_ts") or 0)
        except Exception as exc:
            logger.warning("rule_weights load failed: %s", exc)

    def _save_state(self) -> None:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": self.lookback_days,
            "last_refresh_ts": self._last_refresh_ts,
            "validated_rules": self._validated,
            "weights": self._weights,
            "stats": self._stats,
        }
        try:
            self.state_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("rule_weights save failed: %s", exc)

    @staticmethod
    def _parse_ts(row: Mapping[str, Any]) -> Optional[datetime]:
        for key in ("ts", "time", "closed_at"):
            val = row.get(key)
            if not val:
                continue
            try:
                dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
        return None

    def refresh_from_journal(self, journal_path: Path, *, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.time()
        if not force and self._last_refresh_ts and now - self._last_refresh_ts < self.refresh_interval_sec:
            return
        if not journal_path.is_file():
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        pairs = _load_journal_pairs_with_context(journal_path)
        stats: Dict[str, Dict[str, Any]] = {
            rid: {"n": 0, "wins": 0, "pnl": 0.0} for rid in POSITIVE_RULE_IDS
        }

        for pair in pairs:
            closed = pair.get("closed") or {}
            entered = pair.get("entered") or {}
            ts = self._parse_ts(closed) or self._parse_ts(entered)
            if ts and ts < cutoff:
                continue
            pnl = float(closed.get("pnl") or pair.get("pnl_usdt") or 0)
            ctx = closed.get("entry_context") or entered.get("entry_context") or {}
            if not isinstance(ctx, dict):
                ctx = {}
            side = str(closed.get("side") or entered.get("side") or "")
            stored = ctx.get("active_rules")
            if isinstance(stored, list) and stored:
                rules = [str(r) for r in stored if r]
            else:
                rules = detect_active_rules(ctx, side=side, tz_offset=self.tz_offset)
            for rid in rules:
                if rid not in POSITIVE_RULE_IDS:
                    continue
                bucket = stats.setdefault(rid, {"n": 0, "wins": 0, "pnl": 0.0})
                bucket["n"] += 1
                bucket["pnl"] += pnl
                if pnl > 0:
                    bucket["wins"] += 1

        weights: Dict[str, float] = {}
        validated: List[str] = []
        for rid, bucket in stats.items():
            n = int(bucket.get("n") or 0)
            wins = int(bucket.get("wins") or 0)
            pnl_sum = float(bucket.get("pnl") or 0)
            mult = 1.0
            if n >= self.min_samples:
                wr = wins / n if n else 0.0
                if wr >= self.min_win_rate and pnl_sum > self.min_total_pnl:
                    edge = max(0.0, wr - 0.5)
                    boost = min(self.max_weight_mult - 1.0, 0.08 + edge * 0.6)
                    mult = round(1.0 + boost, 4)
                    validated.append(rid)
            weights[rid] = mult

        self._stats = stats
        self._weights = weights
        self._validated = sorted(validated)
        self._last_refresh_ts = now
        self._save_state()
        if validated:
            logger.info(
                "Rule weights refreshed: validated=%s (lookback=%.0fd, pairs=%d)",
                ",".join(validated),
                self.lookback_days,
                len(pairs),
            )

    def refresh_if_due(self, journal_path: Path, *, force: bool = False) -> None:
        try:
            self.refresh_from_journal(journal_path, force=force)
        except Exception as exc:
            logger.warning("rule_weights refresh: %s", exc)

    def get_weights(self) -> Dict[str, float]:
        return dict(self._weights)

    def get_validated_rules(self) -> List[str]:
        return list(self._validated)

    def summary(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "lookback_days": self.lookback_days,
            "validated_rules": self._validated,
            "weights": self._weights,
            "stats": self._stats,
        }
