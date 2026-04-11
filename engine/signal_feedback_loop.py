#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


@dataclass
class SignalOutcome:
    record: dict[str, Any]
    reason: str


class SignalFeedbackLoop:
    def __init__(self, bot_dir: Path, cfg):
        self.bot_dir = bot_dir
        self.enabled = cfg.get("feedback_loop", "enabled", default=True)
        self.max_pending_hours = float(cfg.get("feedback_loop", "max_pending_hours", default=12.0))
        self.retrain_daily = cfg.get("feedback_loop", "retrain_daily", default=True)
        self.retrain_hour_utc = int(cfg.get("feedback_loop", "retrain_hour_utc", default=0))
        self.min_new_labels_for_retrain = int(cfg.get("feedback_loop", "min_new_labels_for_retrain", default=8))

        dataset_path_raw = cfg.get("feedback_loop", "dataset_path", default="training_data.json")
        queue_path_raw = cfg.get("feedback_loop", "queue_path", default="signal_feedback_queue.json")
        state_path_raw = cfg.get("feedback_loop", "state_path", default="signal_feedback_state.json")

        self.dataset_path = self._resolve_path(dataset_path_raw)
        self.queue_path = self._resolve_path(queue_path_raw)
        self.state_path = self._resolve_path(state_path_raw)

        self._queue = self._load_json(self.queue_path, default=[])
        self._state = self._load_json(
            self.state_path,
            default={
                "new_labels_since_retrain": 0,
                "quality_labels_since_retrain": 0,
                "last_retrain_attempt_date": "",
                "last_retrain_success_date": "",
            },
        )
        # Backward-compatible state migration
        if "quality_labels_since_retrain" not in self._state:
            self._state["quality_labels_since_retrain"] = int(self._state.get("new_labels_since_retrain", 0))
            self._save_json(self.state_path, self._state)

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.bot_dir / path

    @staticmethod
    def _load_json(path: Path, default):
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
        except Exception:
            pass
        return default

    @staticmethod
    def _save_json(path: Path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def register_signal(self, symbol: str, signal):
        if not self.enabled:
            return

        now = datetime.now(timezone.utc)
        payload = {
            "id": f"{symbol}_{signal.side}_{int(now.timestamp())}",
            "created_at": now.isoformat(),
            "symbol": symbol,
            "side": signal.side,
            "entry_price": float(signal.entry_price),
            "stop_loss": float(signal.stop_loss),
            "take_profit": float(signal.take_profit),
            "rr_ratio": float(signal.rr_ratio),
            "confidence": float(signal.confidence),
            "composite_score": float(signal.metadata.get("composite_score", 0.0)),
            "trend_score": float(signal.metadata.get("trend_score", 0.0)),
            "orderflow_score": float(signal.metadata.get("orderflow_score", 0.0)),
            "ai_score": float(signal.metadata.get("ai_score", 0.0)),
            "normalized_imbalance": float(signal.metadata.get("normalized_imbalance", 0.0)),
            "htf_4h_trend": int(signal.metadata.get("htf_4h_trend", 0) or 0),
            "trained_model_prob": signal.metadata.get("trained_model_prob"),
            "entry_zone": signal.metadata.get("entry_zone", "none"),
        }
        self._queue.append(payload)
        self._save_json(self.queue_path, self._queue)

    async def process_pending(
        self,
        get_price: Callable[[str], Awaitable[float]],
    ) -> list[SignalOutcome]:
        if not self.enabled or not self._queue:
            return []

        now = datetime.now(timezone.utc)
        remaining = []
        outcomes: list[SignalOutcome] = []

        for signal in self._queue:
            symbol = signal.get("symbol", "")
            side = str(signal.get("side", "")).upper()
            if not symbol or side not in {"BUY", "SELL"}:
                continue

            try:
                current_price = float(await get_price(symbol))
            except Exception:
                remaining.append(signal)
                continue

            outcome_reason = self._resolve_outcome_reason(signal, current_price, now)
            if outcome_reason is None:
                remaining.append(signal)
                continue

            record = self._build_training_record(signal, current_price, now, outcome_reason)
            self._append_training_record(record)
            outcomes.append(SignalOutcome(record=record, reason=outcome_reason))

        self._queue = remaining
        self._save_json(self.queue_path, self._queue)
        if outcomes:
            self._state["new_labels_since_retrain"] = int(self._state.get("new_labels_since_retrain", 0)) + len(outcomes)
            self._save_json(self.state_path, self._state)
        return outcomes

    def add_quality_labels(self, count: int):
        if count <= 0:
            return
        self._state["quality_labels_since_retrain"] = int(self._state.get("quality_labels_since_retrain", 0)) + int(count)
        self._save_json(self.state_path, self._state)

    def _resolve_outcome_reason(self, signal: dict[str, Any], current_price: float, now: datetime) -> str | None:
        side = str(signal.get("side", "")).upper()
        sl = self._safe_float(signal.get("stop_loss"), 0.0)
        tp = self._safe_float(signal.get("take_profit"), 0.0)

        if side == "BUY":
            if current_price <= sl and sl > 0:
                return "stop_loss"
            if current_price >= tp and tp > 0:
                return "take_profit"
        else:
            if current_price >= sl and sl > 0:
                return "stop_loss"
            if current_price <= tp and tp > 0:
                return "take_profit"

        try:
            created_at = datetime.fromisoformat(str(signal.get("created_at")))
        except Exception:
            created_at = now

        age_hours = (now - created_at).total_seconds() / 3600
        if age_hours >= self.max_pending_hours:
            return "timeout"
        return None

    def _build_training_record(
        self,
        signal: dict[str, Any],
        current_price: float,
        now: datetime,
        reason: str,
    ) -> dict[str, Any]:
        side = str(signal.get("side", "")).upper()
        entry = self._safe_float(signal.get("entry_price"), 0.0)

        if side == "BUY":
            pnl_pct = ((current_price - entry) / entry * 100) if entry > 0 else 0.0
        else:
            pnl_pct = ((entry - current_price) / entry * 100) if entry > 0 else 0.0

        result = "win" if pnl_pct > 0 else "loss"
        return {
            "symbol": signal.get("symbol", ""),
            "side": side,
            "entry_price": round(entry, 8),
            "stop_loss": round(self._safe_float(signal.get("stop_loss"), 0.0), 8),
            "take_profit": round(self._safe_float(signal.get("take_profit"), 0.0), 8),
            "rr_ratio": round(self._safe_float(signal.get("rr_ratio"), 0.0), 4),
            "entry_time": signal.get("created_at"),
            "exit_time": now.isoformat(),
            "exit_price": round(current_price, 8),
            "pnl_pct": round(pnl_pct, 4),
            "result": result,
            "exit_reason": reason,
            "source": "signal_only_feedback",
            "confidence": round(self._safe_float(signal.get("confidence"), 0.0), 4),
            "composite_score": round(self._safe_float(signal.get("composite_score"), 0.0), 4),
            "trend_score": round(self._safe_float(signal.get("trend_score"), 0.0), 4),
            "orderflow_score": round(self._safe_float(signal.get("orderflow_score"), 0.0), 4),
            "ai_score": round(self._safe_float(signal.get("ai_score"), 0.0), 4),
            "normalized_imbalance": round(self._safe_float(signal.get("normalized_imbalance"), 0.0), 4),
            "htf_4h_trend": int(signal.get("htf_4h_trend", 0) or 0),
            "trained_model_prob": signal.get("trained_model_prob"),
            "entry_zone": signal.get("entry_zone", "none"),
        }

    def _append_training_record(self, record: dict[str, Any]):
        dataset = self._load_json(self.dataset_path, default=[])
        if not isinstance(dataset, list):
            dataset = []
        dataset.append(record)
        self._save_json(self.dataset_path, dataset)

    def should_run_daily_retrain(self, now: datetime | None = None) -> bool:
        if not self.enabled or not self.retrain_daily:
            return False

        now = now or datetime.now(timezone.utc)
        if now.hour < self.retrain_hour_utc:
            return False

        quality_labels = int(
            self._state.get(
                "quality_labels_since_retrain",
                self._state.get("new_labels_since_retrain", 0),
            )
        )
        if quality_labels <= 0:
            quality_labels = int(self._state.get("new_labels_since_retrain", 0))
        if quality_labels < self.min_new_labels_for_retrain:
            return False

        today = now.date().isoformat()
        if self._state.get("last_retrain_attempt_date", "") == today:
            return False
        return True

    def get_retrain_status(self) -> dict:
        """Return retrain progress info for Telegram command."""
        quality_labels = int(
            self._state.get(
                "quality_labels_since_retrain",
                self._state.get("new_labels_since_retrain", 0),
            )
        )
        total_labels = int(self._state.get("new_labels_since_retrain", 0))
        dataset_size = 0
        try:
            dataset = self._load_json(self.dataset_path, default=[])
            if isinstance(dataset, list):
                dataset_size = len(dataset)
        except Exception:
            pass
        return {
            "quality_labels": quality_labels,
            "total_labels": total_labels,
            "min_for_retrain": self.min_new_labels_for_retrain,
            "progress_pct": min(100, int(quality_labels / max(1, self.min_new_labels_for_retrain) * 100)),
            "dataset_size": dataset_size,
            "last_retrain_attempt": self._state.get("last_retrain_attempt_date", "—"),
            "last_retrain_success": self._state.get("last_retrain_success_date", "—"),
            "retrain_hour_utc": self.retrain_hour_utc,
            "enabled": self.enabled and self.retrain_daily,
        }

    def mark_retrain_attempt(self, success: bool = False):
        """Mark a retrain attempt and optionally reset counters on success."""
        today = datetime.now(timezone.utc).date().isoformat()
        self._state["last_retrain_attempt_date"] = today
        if success:
            self._state["last_retrain_success_date"] = today
            self._state["new_labels_since_retrain"] = 0
            self._state["quality_labels_since_retrain"] = 0
        self._save_json(self.state_path, self._state)
