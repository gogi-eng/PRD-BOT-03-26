#!/usr/bin/env python3
"""Learn lightweight entry preferences from profitable manual trades.

This module intentionally does not create trades by itself and does not bypass
risk checks. It adds a small confidence/ranking boost when a bot-generated
signal looks similar to historically profitable manual trades.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger("BOT")


@dataclass
class ManualWinnerMatch:
    score: float
    confidence_boost: float
    capital_score_mult: float
    reason: str
    profile: dict[str, Any]


class ManualTradeLearner:
    """Profiles profitable manual trades and scores bot signals against them."""

    def __init__(self, bot_dir: Path, cfg):
        self.bot_dir = Path(bot_dir)
        self.enabled = bool(cfg.get("manual_trade_learning", "enabled", default=True))
        self.trade_history_path = self._resolve_path(
            cfg.get("manual_trade_learning", "trade_history_path", default="trade_history.json")
        )
        self.state_path = self._resolve_path(
            cfg.get("manual_trade_learning", "state_path", default="manual_trade_learning_state.json")
        )
        self.lookback_days = max(1.0, float(cfg.get("manual_trade_learning", "lookback_days", default=30.0) or 30.0))
        self.min_manual_winners = max(1, int(cfg.get("manual_trade_learning", "min_manual_winners", default=2) or 2))
        self.min_pnl_usdt = float(cfg.get("manual_trade_learning", "min_pnl_usdt", default=0.5) or 0.5)
        self.min_pnl_pct = float(cfg.get("manual_trade_learning", "min_pnl_pct", default=0.4) or 0.4)
        self.symbol_weight = float(cfg.get("manual_trade_learning", "symbol_weight", default=0.45) or 0.45)
        self.side_weight = float(cfg.get("manual_trade_learning", "side_weight", default=0.25) or 0.25)
        self.hour_weight = float(cfg.get("manual_trade_learning", "hour_weight", default=0.20) or 0.20)
        self.profit_weight = float(cfg.get("manual_trade_learning", "profit_weight", default=0.10) or 0.10)
        self.min_match_score = float(cfg.get("manual_trade_learning", "min_match_score", default=0.55) or 0.55)
        self.max_confidence_boost = float(
            cfg.get("manual_trade_learning", "max_confidence_boost", default=0.05) or 0.05
        )
        self.max_capital_score_mult = float(
            cfg.get("manual_trade_learning", "max_capital_score_mult", default=1.10) or 1.10
        )
        self.cache_ttl_sec = max(30.0, float(cfg.get("manual_trade_learning", "cache_ttl_sec", default=300) or 300))
        self.timezone_offset = int(cfg.get("timezone_offset", default=3) or 3)
        self._last_load_ts = 0.0
        self._profiles: dict[str, Any] = {}

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.bot_dir / path

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        try:
            dt = datetime.fromisoformat(str(value))
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _load_trades(self) -> list[dict[str, Any]]:
        try:
            if self.trade_history_path.exists():
                data = json.loads(self.trade_history_path.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning(f"[MANUAL LEARN] failed to read trade history: {exc}")
        return []

    def _save_state(self) -> None:
        try:
            self.state_path.write_text(json.dumps(self._profiles, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[MANUAL LEARN] failed to save state: {exc}")

    def refresh_if_needed(self, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.time()
        if not force and self._profiles and now - self._last_load_ts < self.cache_ttl_sec:
            return
        self._last_load_ts = now
        rows = self._load_trades()
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        winners: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("origin", "")).lower() != "manual":
                continue
            pnl = self._safe_float(row.get("pnl"))
            pnl_pct = self._safe_float(row.get("pnl_pct"))
            if pnl < self.min_pnl_usdt and pnl_pct < self.min_pnl_pct:
                continue
            dt = self._parse_time(row.get("time"))
            if dt is None or dt < cutoff:
                continue
            winners.append(row)

        by_symbol_side: dict[str, dict[str, Any]] = {}
        by_side: dict[str, dict[str, Any]] = {}
        by_hour: dict[str, dict[str, Any]] = {}
        by_context: dict[str, dict[str, Any]] = {}

        def add(bucket: dict[str, dict[str, Any]], key: str, row: dict[str, Any], dt: datetime) -> None:
            item = bucket.setdefault(key, {"n": 0, "pnl": 0.0, "pnl_pct": 0.0, "hours": {}, "symbols": {}})
            item["n"] += 1
            item["pnl"] += self._safe_float(row.get("pnl"))
            item["pnl_pct"] += self._safe_float(row.get("pnl_pct"))
            local_hour = (dt.hour + self.timezone_offset) % 24
            item["hours"][str(local_hour)] = int(item["hours"].get(str(local_hour), 0)) + 1
            symbol = str(row.get("symbol", "")).upper()
            if symbol:
                item["symbols"][symbol] = int(item["symbols"].get(symbol, 0)) + 1

        for row in winners:
            dt = self._parse_time(row.get("entry_time")) or self._parse_time(row.get("time"))
            if dt is None:
                continue
            symbol = str(row.get("symbol", "")).upper()
            side = str(row.get("side", "")).upper()
            if not symbol or side not in {"BUY", "SELL"}:
                continue
            local_hour = (dt.hour + self.timezone_offset) % 24
            add(by_symbol_side, f"{symbol}:{side}", row, dt)
            add(by_side, side, row, dt)
            add(by_hour, str(local_hour), row, dt)
            context = row.get("entry_context") if isinstance(row.get("entry_context"), dict) else {}
            for ctx_key in ("regime", "trend", "htf_trend", "entry_zone"):
                ctx_val = str(context.get(ctx_key, "") or "").lower()
                if ctx_val and ctx_val not in {"none", "unknown"}:
                    add(by_context, f"{ctx_key}:{ctx_val}", row, dt)

        profiles = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": self.lookback_days,
            "manual_winners": len(winners),
            "by_symbol_side": by_symbol_side,
            "by_side": by_side,
            "by_hour": by_hour,
            "by_context": by_context,
        }
        self._profiles = profiles
        self._save_state()
        logger.info(
            "[MANUAL LEARN] winners=%s symbol_side_profiles=%s side_profiles=%s hour_profiles=%s context_profiles=%s",
            len(winners),
            len(by_symbol_side),
            len(by_side),
            len(by_hour),
            len(by_context),
        )

    def score_signal(self, symbol: str, side: str, signal) -> ManualWinnerMatch | None:
        if not self.enabled:
            return None
        self.refresh_if_needed()
        profiles = self._profiles
        if int(profiles.get("manual_winners", 0) or 0) < self.min_manual_winners:
            return None
        symbol = str(symbol or "").upper()
        side = str(side or "").upper()
        if not symbol or side not in {"BUY", "SELL"}:
            return None

        local_hour = (datetime.now(timezone.utc).hour + self.timezone_offset) % 24
        ss = (profiles.get("by_symbol_side") or {}).get(f"{symbol}:{side}", {})
        side_profile = (profiles.get("by_side") or {}).get(side, {})
        hour_profile = (profiles.get("by_hour") or {}).get(str(local_hour), {})
        context_profiles = profiles.get("by_context") or {}

        ss_n = int(ss.get("n", 0) or 0)
        side_n = int(side_profile.get("n", 0) or 0)
        hour_n = int(hour_profile.get("n", 0) or 0)

        score = 0.0
        reasons = []
        if ss_n > 0:
            score += self.symbol_weight * min(1.0, ss_n / max(1, self.min_manual_winners))
            reasons.append(f"{symbol}:{side} manual_winners={ss_n}")
        if side_n > 0:
            score += self.side_weight * min(1.0, side_n / max(1, self.min_manual_winners * 2))
            reasons.append(f"side_{side}_manual_winners={side_n}")
        if hour_n > 0:
            score += self.hour_weight * min(1.0, hour_n / max(1, self.min_manual_winners))
            reasons.append(f"local_hour_{local_hour}_manual_winners={hour_n}")

        context_hits: dict[str, Any] = {}
        meta = getattr(signal, "metadata", {}) if signal is not None else {}
        context_budget = 0.10
        context_keys = {
            "regime": str(meta.get("regime", "") or "").lower(),
            "trend": str(meta.get("trend", "") or "").lower(),
            "htf_trend": str(meta.get("htf_trend", "") or "").lower(),
            "entry_zone": str(meta.get("entry_zone", "") or "").lower(),
        }
        per_context_weight = context_budget / max(1, len(context_keys))
        for ctx_key, ctx_val in context_keys.items():
            if not ctx_val or ctx_val in {"none", "unknown", "no_zone"}:
                continue
            ctx_profile = context_profiles.get(f"{ctx_key}:{ctx_val}", {})
            ctx_n = int(ctx_profile.get("n", 0) or 0)
            if ctx_n <= 0:
                continue
            score += per_context_weight * min(1.0, ctx_n / max(1, self.min_manual_winners))
            context_hits[ctx_key] = {"value": ctx_val, "n": ctx_n}
        if context_hits:
            reasons.append(f"context_hits={context_hits}")

        best_pnl_pct = max(self._safe_float(ss.get("pnl_pct")), self._safe_float(side_profile.get("pnl_pct")))
        if best_pnl_pct > 0:
            score += self.profit_weight * min(1.0, best_pnl_pct / max(1.0, self.min_pnl_pct * 4.0))
            reasons.append(f"manual_profit_pct_sum={best_pnl_pct:.2f}")

        score = max(0.0, min(1.0, score))
        if score < self.min_match_score:
            return None

        confidence_boost = min(self.max_confidence_boost, self.max_confidence_boost * score)
        capital_score_mult = 1.0 + (self.max_capital_score_mult - 1.0) * score
        profile = {
            "symbol_side": ss,
            "side": side_profile,
            "hour": hour_profile,
            "context_hits": context_hits,
            "manual_winners": profiles.get("manual_winners", 0),
        }
        return ManualWinnerMatch(
            score=round(score, 4),
            confidence_boost=round(confidence_boost, 4),
            capital_score_mult=round(capital_score_mult, 4),
            reason="; ".join(reasons[:4]),
            profile=profile,
        )

    def apply_to_signal(self, symbol: str, signal) -> ManualWinnerMatch | None:
        match = self.score_signal(symbol, getattr(signal, "side", ""), signal)
        if match is None:
            return None
        old_conf = float(getattr(signal, "confidence", 0.0) or 0.0)
        old_capital = float(getattr(signal, "capital_score", 0.0) or 0.0)
        signal.confidence = round(min(0.99, old_conf + match.confidence_boost), 4)
        signal.capital_score = round(max(old_capital, old_capital * match.capital_score_mult), 4)
        signal.metadata["manual_learning_match"] = asdict(match)
        signal.metadata["manual_learning_confidence_before"] = round(old_conf, 4)
        signal.metadata["manual_learning_capital_score_before"] = round(old_capital, 4)
        signal.reasons.append(f"manual_learning_boost score={match.score:.2f}")
        return match
