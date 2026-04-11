#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class SymbolQualityFilter:
    """Semi-dynamic symbol quality filter from feedback/backtest outcomes."""

    def __init__(self, bot_dir: Path, cfg):
        self.enabled = cfg.get("symbol_quality", "enabled", default=True)
        self.min_trades = int(cfg.get("symbol_quality", "min_trades", default=4))
        self.min_winrate = float(cfg.get("symbol_quality", "min_winrate", default=0.35))
        self.min_avg_pnl_pct = float(cfg.get("symbol_quality", "min_avg_pnl_pct", default=-0.8))
        self.max_recent_losses = int(cfg.get("symbol_quality", "max_recent_losses", default=3))
        self.lookback_per_symbol = int(cfg.get("symbol_quality", "lookback_per_symbol", default=30))
        self.cache_ttl_sec = int(cfg.get("symbol_quality", "cache_ttl_sec", default=180))
        self.whitelist_bypass = cfg.get("symbol_quality", "whitelist_bypass", default=True)
        self.feedback_only = cfg.get("symbol_quality", "feedback_only", default=True)
        dataset_path = cfg.get("symbol_quality", "dataset_path", default="signal_only_feedback_data.json")

        self.dataset_path = (Path(dataset_path) if Path(dataset_path).is_absolute() else bot_dir / dataset_path)
        self._cache_time: datetime | None = None
        self._stats: dict[str, dict] = {}

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _safe_dt(value):
        try:
            dt = datetime.fromisoformat(str(value))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime.fromtimestamp(0, tz=timezone.utc)

    def _reload_if_needed(self):
        now = datetime.now(timezone.utc)
        if self._cache_time and (now - self._cache_time).total_seconds() < self.cache_ttl_sec:
            return

        rows = []
        if self.dataset_path.exists():
            try:
                with open(self.dataset_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, list):
                        rows = loaded
            except Exception:
                rows = []

        per_symbol: dict[str, list[dict]] = {}
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            if not symbol:
                continue
            if self.feedback_only and row.get("source") != "signal_only_feedback":
                continue
            if row.get("result") not in {"win", "loss"}:
                continue
            per_symbol.setdefault(symbol, []).append(row)

        stats: dict[str, dict] = {}
        for symbol, symbol_rows in per_symbol.items():
            symbol_rows.sort(key=lambda r: self._safe_dt(r.get("entry_time")))
            recent = symbol_rows[-self.lookback_per_symbol :]
            n = len(recent)
            wins = sum(1 for r in recent if r.get("result") == "win")
            losses = n - wins
            winrate = wins / n if n else 0.0
            pnls = [self._safe_float(r.get("pnl_pct", 0.0)) for r in recent]
            avg_pnl = (sum(pnls) / n) if n else 0.0

            consecutive_losses = 0
            for row in reversed(recent):
                if row.get("result") == "loss":
                    consecutive_losses += 1
                else:
                    break

            stats[symbol] = {
                "trades": n,
                "wins": wins,
                "losses": losses,
                "winrate": round(winrate, 4),
                "avg_pnl": round(avg_pnl, 4),
                "consecutive_losses": consecutive_losses,
            }

        self._stats = stats
        self._cache_time = now

    def allow(self, symbol: str, is_whitelisted: bool = False) -> tuple[bool, str, dict]:
        if not self.enabled:
            return True, "disabled", {}
        if is_whitelisted and self.whitelist_bypass:
            return True, "whitelist_bypass", {}

        self._reload_if_needed()
        data = self._stats.get(str(symbol).upper(), {})
        trades = int(data.get("trades", 0))
        if trades < self.min_trades:
            return True, "insufficient_history", data

        winrate = self._safe_float(data.get("winrate", 0.0))
        avg_pnl = self._safe_float(data.get("avg_pnl", 0.0))
        consecutive_losses = int(data.get("consecutive_losses", 0))

        if consecutive_losses >= self.max_recent_losses:
            return False, "consecutive_losses", data

        if winrate < self.min_winrate and avg_pnl <= self.min_avg_pnl_pct:
            return False, "low_quality", data

        return True, "ok", data
