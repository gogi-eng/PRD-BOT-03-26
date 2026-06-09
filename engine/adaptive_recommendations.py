#!/usr/bin/env python3
"""Runtime analyzer and safe auto-tuning for the live trading bot.

The engine never rewrites config.yaml. It reads recent closed trades, stores
runtime tuning in a state file, and applies bounded defensive changes in memory.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger("BOT")


@dataclass
class RecommendationReport:
    text: str
    signature: str
    trades: int
    pnl: float
    winrate: float


class AdaptiveRecommendationEngine:
    """Analyze closed trades, report weak spots, and apply bounded runtime protection."""

    def __init__(self, bot_dir: Path, cfg):
        self.bot_dir = Path(bot_dir)
        self.enabled = bool(cfg.get("adaptive_recommendations", "enabled", default=True))
        self.telegram_enabled = bool(cfg.get("adaptive_recommendations", "telegram_enabled", default=True))
        self.interval_sec = max(60, int(cfg.get("adaptive_recommendations", "interval_sec", default=1800) or 1800))
        self.repeat_unchanged_sec = max(
            self.interval_sec,
            int(cfg.get("adaptive_recommendations", "repeat_unchanged_sec", default=6 * 3600) or 6 * 3600),
        )
        self.lookback_hours = max(1.0, float(cfg.get("adaptive_recommendations", "lookback_hours", default=24.0) or 24.0))
        self.min_trades = max(1, int(cfg.get("adaptive_recommendations", "min_trades", default=3) or 3))
        self.bot_only = bool(cfg.get("adaptive_recommendations", "bot_only", default=True))
        self.max_symbols = max(1, int(cfg.get("adaptive_recommendations", "max_symbols", default=6) or 6))
        self.auto_apply_enabled = bool(cfg.get("adaptive_recommendations", "auto_apply_enabled", default=False))
        self.auto_apply_interval_sec = max(
            60,
            int(cfg.get("adaptive_recommendations", "auto_apply_interval_sec", default=self.interval_sec) or self.interval_sec),
        )
        self.auto_apply_min_trades = max(
            self.min_trades,
            int(cfg.get("adaptive_recommendations", "auto_apply_min_trades", default=max(6, self.min_trades)) or max(6, self.min_trades)),
        )
        self.auto_apply_notify = bool(cfg.get("adaptive_recommendations", "auto_apply_notify", default=True))
        self.ai_approval_enabled = bool(cfg.get("adaptive_recommendations", "ai_approval_enabled", default=True))
        self.ai_approval_model = str(
            cfg.get(
                "adaptive_recommendations",
                "ai_approval_model",
                default=cfg.get("openrouter", "model", default="google/gemini-2.5-flash"),
            )
            or "google/gemini-2.5-flash"
        )
        raw_models = cfg.get("adaptive_recommendations", "ai_approval_models", default=[]) or []
        if isinstance(raw_models, str):
            raw_models = [item.strip() for item in raw_models.split(",") if item.strip()]
        self.ai_approval_models = [str(item).strip() for item in raw_models if str(item).strip()]
        if not self.ai_approval_models:
            self.ai_approval_models = [self.ai_approval_model]
        self.ai_approval_timeout_sec = float(cfg.get("adaptive_recommendations", "ai_approval_timeout_sec", default=25.0) or 25.0)
        self.ai_approval_min_confidence = int(cfg.get("adaptive_recommendations", "ai_approval_min_confidence", default=70) or 70)
        self.trade_history_path = self._resolve_path(
            cfg.get("adaptive_recommendations", "trade_history_path", default="trade_history.json")
        )
        self.state_path = self._resolve_path(
            cfg.get("adaptive_recommendations", "state_path", default="adaptive_recommendations_state.json")
        )
        self._state = self._load_json(
            self.state_path,
            default={"last_ts": 0.0, "last_signature": "", "last_telegram_ts": 0.0},
        )

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.bot_dir / path

    @staticmethod
    def _load_json(path: Path, default):
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
        except Exception as exc:
            logger.warning(f"[ADAPTIVE RECOMMEND] failed to read {path.name}: {exc}")
        return default

    @staticmethod
    def _save_json(path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, float(value)))

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        try:
            dt = datetime.fromisoformat(str(value))
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _load_trades(self) -> list[dict]:
        loaded = self._load_json(self.trade_history_path, default=[])
        return loaded if isinstance(loaded, list) else []

    def _recent_rows(self, rows: list[dict]) -> list[dict]:
        dated = [(self._parse_time(row.get("time")), row) for row in rows]
        dated = [(dt, row) for dt, row in dated if dt is not None]
        if not dated:
            return []
        anchor = max(dt for dt, _ in dated)
        cutoff = anchor - timedelta(hours=self.lookback_hours)
        recent = [row for dt, row in dated if dt >= cutoff]
        if self.bot_only:
            recent = [row for row in recent if str(row.get("origin", "")).lower() == "bot"]
        return recent

    def _group_stats(self, rows: list[dict], key: str) -> dict[str, dict[str, float]]:
        stats: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0})
        for row in rows:
            name = str(row.get(key, "") or "unknown").upper() if key == "side" else str(row.get(key, "") or "unknown")
            pnl = self._safe_float(row.get("pnl"))
            stats[name]["n"] += 1
            stats[name]["pnl"] += pnl
            stats[name]["wins"] += 1 if pnl > 0 else 0
            stats[name]["losses"] += 1 if pnl < 0 else 0
        return stats

    @staticmethod
    def _worst_items(stats: dict[str, dict[str, float]], limit: int) -> list[tuple[str, dict[str, float]]]:
        return sorted(stats.items(), key=lambda item: (item[1]["pnl"], -item[1]["n"]))[:limit]

    def build_report(self) -> RecommendationReport | None:
        if not self.enabled:
            return None
        rows = self._recent_rows(self._load_trades())
        if len(rows) < self.min_trades:
            return None
        pnl = sum(self._safe_float(row.get("pnl")) for row in rows)
        wins = sum(1 for row in rows if self._safe_float(row.get("pnl")) > 0)
        losses = sum(1 for row in rows if self._safe_float(row.get("pnl")) < 0)
        winrate = wins / len(rows) if rows else 0.0
        worst_reasons = self._worst_items(self._group_stats(rows, "reason"), 4)
        worst_symbols = self._worst_items(self._group_stats(rows, "symbol"), self.max_symbols)
        worst_sides = self._worst_items(self._group_stats(rows, "side"), 2)
        recommendations = []
        if pnl < 0 and winrate < 0.45:
            recommendations.append("Включить strict-mode: ниже риск, выше confidence/SMC/orderflow.")
        if losses >= max(3, len(rows) // 2):
            recommendations.append("Серия слабая: ограничить новые входы до стабилизации.")
        if worst_symbols:
            bad = [name for name, data in worst_symbols if data["pnl"] < 0 and data["losses"] >= 1]
            if bad:
                recommendations.append("Кандидаты в cooldown/blacklist: " + ", ".join(bad[: self.max_symbols]))
        if not recommendations:
            recommendations.append("Режим наблюдения: явного ухудшения не найдено, config не менять.")
        lines = [
            f"[ADAPTIVE RECOMMEND] window={self.lookback_hours:.0f}h trades={len(rows)} pnl={pnl:.2f} winrate={winrate*100:.1f}% wins={wins} losses={losses}",
            "[ADAPTIVE RECOMMEND] worst_reasons: " + self._format_group(worst_reasons),
            "[ADAPTIVE RECOMMEND] worst_symbols: " + self._format_group(worst_symbols),
            "[ADAPTIVE RECOMMEND] worst_sides: " + self._format_group(worst_sides),
        ]
        for idx, rec in enumerate(recommendations, 1):
            lines.append(f"[ADAPTIVE RECOMMEND] recommendation {idx}: {rec}")
        lines.append("[ADAPTIVE RECOMMEND] mode=runtime: config.yaml не изменяется автоматически.")
        sig_src = "|".join([str(len(rows)), f"{pnl:.2f}", f"{winrate:.3f}", str(rows[-1].get("time", ""))])
        return RecommendationReport("\n".join(lines), hashlib.sha1(sig_src.encode("utf-8")).hexdigest()[:12], len(rows), pnl, winrate)

    def _format_group(self, items: list[tuple[str, dict[str, float]]]) -> str:
        chunks = []
        for name, data in items:
            n = int(data["n"])
            wr = data["wins"] / n * 100 if n else 0.0
            chunks.append(f"{name}: n={n}, PnL={data['pnl']:.2f}, WR={wr:.0f}%")
        return "; ".join(chunks) if chunks else "нет данных"

    def _runtime_base_values(self, bot) -> dict[str, Any]:
        base = self._state.get("runtime_base_values")
        if isinstance(base, dict) and base:
            return base
        base = {
            "risk_per_trade_pct": float(getattr(bot.controls, "risk_per_trade_pct", 0.2) or 0.2),
            "max_positions": int(getattr(bot.controls, "max_positions", 1) or 1),
            "entry_threshold": float(getattr(bot.entry_engine, "entry_threshold", 0.62) or 0.62),
            "entry_threshold_soft": float(bot.entry_engine.entry_threshold_soft) if getattr(bot.entry_engine, "entry_threshold_soft", None) is not None else None,
            "sell_entry_threshold": float(getattr(bot, "sell_entry_threshold", getattr(bot.entry_engine, "entry_threshold", 0.62)) or 0.62),
            "quality_min_confidence": float(getattr(bot, "quality_gate_min_confidence", 0.74) or 0.74),
            "quality_min_expected_edge": float(getattr(bot, "quality_gate_min_expected_edge", 0.56) or 0.56),
            "min_orderflow_imbalance": float(getattr(bot.entry_engine, "min_orderflow_imbalance", 0.05) or 0.05),
            "min_smc_score": float(getattr(bot.entry_engine, "min_smc_score", 0.60) or 0.60),
            "tp_pct": float(getattr(bot.controls, "tp_pct", 2.0) or 2.0),
            "sl_pct": float(getattr(bot.controls, "sl_pct", 1.2) or 1.2),
            "trailing_activation_atr": float(getattr(bot.exit_engine, "trailing_activation_atr", 0.8) or 0.8),
            "trailing_distance_atr": float(getattr(bot.exit_engine, "trailing_distance_atr", 1.2) or 1.2),
            "trailing_min_distance_pct": float(getattr(bot.exit_engine, "trailing_min_distance_pct", 0.0) or 0.0),
            "tp_cap_atr_mult": float(getattr(bot.exit_engine, "tp_cap_atr_mult", 8.0) or 8.0),
            "hard_sl_atr_mult": float(getattr(bot.exit_engine, "hard_sl_atr_mult", 1.8) or 1.8),
            "early_exit_min_profit_atr": float(getattr(bot.exit_engine, "early_exit_min_profit_atr", 0.35) or 0.35),
        }
        self._state["runtime_base_values"] = base
        return base

    def _runtime_signature(self, rows: list[dict]) -> str:
        tail = rows[-max(1, min(len(rows), 12)) :]
        src = "|".join(f"{row.get('time','')}:{row.get('symbol','')}:{row.get('side','')}:{row.get('pnl','')}:{row.get('reason','')}" for row in tail)
        return hashlib.sha1(src.encode("utf-8")).hexdigest()[:12]

    def _build_runtime_tuning(self, rows: list[dict], bot) -> dict[str, Any]:
        base = self._runtime_base_values(bot)
        pnl = sum(self._safe_float(row.get("pnl")) for row in rows)
        wins = sum(1 for row in rows if self._safe_float(row.get("pnl")) > 0)
        losses = sum(1 for row in rows if self._safe_float(row.get("pnl")) < 0)
        winrate = wins / len(rows) if rows else 0.0
        by_side = self._group_stats(rows, "side")
        by_reason = self._group_stats(rows, "reason")
        sell = by_side.get("SELL", {"n": 0, "wins": 0, "pnl": 0.0})
        sell_wr = sell["wins"] / sell["n"] if sell["n"] else 1.0
        if pnl < 0 and winrate < 0.40 and losses >= 3:
            level, mode = 2, "strict"
        elif pnl < 0 or winrate < 0.48:
            level, mode = 1, "cautious"
        else:
            level, mode = 0, "base"
        values = dict(base)
        if level > 0:
            values["risk_per_trade_pct"] = self._clamp(float(base["risk_per_trade_pct"]) * (0.50 if level == 2 else 0.75), 0.05, float(base["risk_per_trade_pct"]))
            values["max_positions"] = max(1, min(int(base["max_positions"]), 1 if level == 2 else 2))
            values["entry_threshold"] = self._clamp(float(base["entry_threshold"]) + (0.06 if level == 2 else 0.03), float(base["entry_threshold"]), 0.72)
            if base.get("entry_threshold_soft") is not None:
                values["entry_threshold_soft"] = self._clamp(float(base["entry_threshold_soft"]) + (0.04 if level == 2 else 0.02), float(base["entry_threshold_soft"]), max(float(base["entry_threshold_soft"]), float(values["entry_threshold"]) - 0.01))
            values["sell_entry_threshold"] = self._clamp(float(base["sell_entry_threshold"]) + (0.08 if sell["n"] >= 2 and sell_wr < 0.40 else (0.04 if level == 2 else 0.02)), float(base["sell_entry_threshold"]), 0.76)
            values["quality_min_confidence"] = self._clamp(float(base["quality_min_confidence"]) + (0.06 if level == 2 else 0.03), float(base["quality_min_confidence"]), 0.84)
            values["quality_min_expected_edge"] = self._clamp(float(base["quality_min_expected_edge"]) + (0.08 if level == 2 else 0.04), float(base["quality_min_expected_edge"]), 0.70)
            values["min_orderflow_imbalance"] = self._clamp(float(base["min_orderflow_imbalance"]) + (0.035 if level == 2 else 0.015), float(base["min_orderflow_imbalance"]), 0.16)
            values["min_smc_score"] = self._clamp(float(base["min_smc_score"]) + (0.05 if level == 2 else 0.025), float(base["min_smc_score"]), 0.78)
            values["tp_pct"] = self._clamp(float(base["tp_pct"]) * (0.90 if level == 2 else 0.95), 0.8, float(base["tp_pct"]))
            values["sl_pct"] = self._clamp(float(base["sl_pct"]) * (0.88 if level == 2 else 0.94), 0.6, float(base["sl_pct"]))
            values["trailing_activation_atr"] = self._clamp(float(base["trailing_activation_atr"]) * (0.85 if level == 2 else 0.93), 0.45, float(base["trailing_activation_atr"]))
            values["trailing_distance_atr"] = self._clamp(float(base["trailing_distance_atr"]) * (0.90 if level == 2 else 0.95), 0.75, float(base["trailing_distance_atr"]))
            values["tp_cap_atr_mult"] = self._clamp(float(base["tp_cap_atr_mult"]) * (0.88 if level == 2 else 0.94), 3.0, float(base["tp_cap_atr_mult"]))
            values["hard_sl_atr_mult"] = self._clamp(float(base["hard_sl_atr_mult"]) * (0.90 if level == 2 else 0.96), 1.0, float(base["hard_sl_atr_mult"]))
            if by_reason.get("early_exit", {}).get("pnl", 0.0) < 0:
                values["early_exit_min_profit_atr"] = self._clamp(float(base["early_exit_min_profit_atr"]) + 0.15, float(base["early_exit_min_profit_atr"]), 0.9)
        return {
            "mode": mode,
            "level": level,
            "signature": self._runtime_signature(rows),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": {"trades": len(rows), "pnl": round(pnl, 4), "wins": wins, "losses": losses, "winrate": round(winrate, 4), "sell_trades": int(sell["n"]), "sell_winrate": round(sell_wr, 4) if sell["n"] else None},
            "values": values,
        }

    def _apply_runtime_values(self, bot, values: dict[str, Any]) -> None:
        bot.controls.risk_per_trade_pct = float(values["risk_per_trade_pct"])
        bot.controls.max_positions = int(values["max_positions"])
        bot.risk_guard.max_positions = int(values["max_positions"])
        bot.controls.tp_pct = float(values["tp_pct"])
        bot.controls.sl_pct = float(values["sl_pct"])
        bot.entry_engine.entry_threshold = float(values["entry_threshold"])
        if values.get("entry_threshold_soft") is not None:
            bot.entry_engine.entry_threshold_soft = float(values["entry_threshold_soft"])
        bot.entry_engine.min_orderflow_imbalance = float(values["min_orderflow_imbalance"])
        bot.entry_engine.min_smc_score = float(values["min_smc_score"])
        bot.sell_entry_threshold = float(values["sell_entry_threshold"])
        bot.quality_gate_min_confidence = float(values["quality_min_confidence"])
        bot.quality_gate_min_expected_edge = float(values["quality_min_expected_edge"])
        bot.entry_min_orderflow_imbalance_norm = float(values["min_orderflow_imbalance"])
        bot.entry_min_smc_score = float(values["min_smc_score"])
        bot.exit_engine.trailing_activation_atr = float(values["trailing_activation_atr"])
        bot.exit_engine.trailing_distance_atr = float(values["trailing_distance_atr"])
        bot.exit_engine.trailing_min_distance_pct = float(values["trailing_min_distance_pct"])
        bot.exit_engine.tp_cap_atr_mult = float(values["tp_cap_atr_mult"])
        bot.exit_engine.hard_sl_atr_mult = float(values["hard_sl_atr_mult"])
        bot.exit_engine.early_exit_min_profit_atr = float(values["early_exit_min_profit_atr"])

    def _ai_approval_cache_key(self, tuning: dict[str, Any]) -> str:
        return f"{tuning.get('signature')}:{tuning.get('mode')}:{tuning.get('level')}"

    def _request_single_ai_approval(self, tuning: dict[str, Any], model: str) -> dict[str, Any]:
        cache_key = f"{self._ai_approval_cache_key(tuning)}:{model}"
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            return {"approve": False, "confidence": 0, "reason": "OPENROUTER_API_KEY missing; runtime tuning blocked", "cache_key": cache_key, "model": model}
        role_hint = "strict risk auditor" if "gpt" in model.lower() else "conservative trading risk analyst"
        prompt = (
            f"Ты {role_hint} крипто-фьючерсного торгового бота. Проверь предложенную runtime-подстройку "
            "параметров перед применением. Разрешай только защитные изменения: снижение риска, ужесточение "
            "входов, более осторожные TP/SL/trailing. Запрещай увеличение риска, плеча или агрессии.\n\n"
            "Верни только JSON без markdown:\n"
            '{"approve": true/false, "confidence": 0-100, "reason": "коротко по-русски"}\n\n'
            f"Предложенная подстройка:\n{json.dumps(tuning, ensure_ascii=False)[:6000]}"
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Capital protection first. Approve only conservative risk-reducing runtime changes."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.05,
            "max_tokens": 220,
        }
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "PRD-SCALP Adaptive Runtime Approval",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.ai_approval_timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            start, end = text.find("{"), text.rfind("}")
            if 0 <= start < end:
                text = text[start : end + 1]
            parsed = json.loads(text)
            return {
                "approve": bool(parsed.get("approve")),
                "confidence": max(0, min(100, int(self._safe_float(parsed.get("confidence"), 0)))),
                "reason": str(parsed.get("reason", ""))[:400],
                "cache_key": cache_key,
                "model": model,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            return {"approve": False, "confidence": 0, "reason": f"OpenRouter HTTP {exc.code}: {detail}", "cache_key": cache_key, "model": model}
        except Exception as exc:
            return {"approve": False, "confidence": 0, "reason": f"AI approval failed: {exc}", "cache_key": cache_key, "model": model}

    def _request_ai_approval(self, tuning: dict[str, Any]) -> dict[str, Any]:
        if not self.ai_approval_enabled:
            return {"approve": True, "confidence": 100, "reason": "ai_approval_disabled"}
        cache_key = self._ai_approval_cache_key(tuning)
        cached = self._state.get("runtime_ai_approval", {})
        if isinstance(cached, dict) and cached.get("cache_key") == cache_key:
            return cached
        approvals = [self._request_single_ai_approval(tuning, model) for model in self.ai_approval_models]
        approval = {
            "approve": all(
                bool(item.get("approve"))
                and int(item.get("confidence", 0) or 0) >= self.ai_approval_min_confidence
                for item in approvals
            ),
            "confidence": min((int(item.get("confidence", 0) or 0) for item in approvals), default=0),
            "reason": " | ".join(f"{item.get('model', 'AI')}: {item.get('reason', '')}" for item in approvals)[:700],
            "cache_key": cache_key,
            "models": self.ai_approval_models,
            "approvals": approvals,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._state["runtime_ai_approval"] = approval
        return approval

    def _ai_allows_tuning(self, tuning: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        approval = self._request_ai_approval(tuning)
        ok = bool(approval.get("approve")) and int(approval.get("confidence", 0) or 0) >= self.ai_approval_min_confidence
        return ok, approval

    async def maybe_emit(self, tg=None) -> RecommendationReport | None:
        if not self.enabled:
            return None
        now_ts = time.time()
        if now_ts - float(self._state.get("last_ts", 0.0) or 0.0) < self.interval_sec:
            return None
        report = self.build_report()
        self._state["last_ts"] = now_ts
        if report is None:
            self._save_json(self.state_path, self._state)
            return None
        changed = report.signature != str(self._state.get("last_signature", "") or "")
        should_repeat = now_ts - float(self._state.get("last_telegram_ts", 0.0) or 0.0) >= self.repeat_unchanged_sec
        if changed or should_repeat:
            for line in report.text.splitlines():
                logger.info(line)
            if tg and self.telegram_enabled:
                try:
                    await tg.send_message(self._telegram_text(report.text))
                    self._state["last_telegram_ts"] = now_ts
                except Exception as exc:
                    logger.warning(f"[ADAPTIVE RECOMMEND] Telegram failed: {exc}")
            self._state["last_signature"] = report.signature
        self._save_json(self.state_path, self._state)
        return report

    async def maybe_apply_runtime_tuning(self, bot, tg=None) -> dict[str, Any] | None:
        if not self.enabled or not self.auto_apply_enabled:
            return None
        stored = self._state.get("runtime_tuning")
        stored_approval = self._state.get("runtime_ai_approval", {})
        stored_key = self._ai_approval_cache_key(stored) if isinstance(stored, dict) else ""
        stored_allowed = (
            not self.ai_approval_enabled
            or (
                isinstance(stored_approval, dict)
                and stored_approval.get("cache_key") == stored_key
                and bool(stored_approval.get("approve"))
                and int(stored_approval.get("confidence", 0) or 0) >= self.ai_approval_min_confidence
            )
        )
        if isinstance(stored, dict) and isinstance(stored.get("values"), dict) and stored_allowed:
            self._apply_runtime_values(bot, stored["values"])
        now_ts = time.time()
        if now_ts - float(self._state.get("last_runtime_apply_ts", 0.0) or 0.0) < self.auto_apply_interval_sec:
            return stored if isinstance(stored, dict) else None
        rows = self._recent_rows(self._load_trades())
        if len(rows) < self.auto_apply_min_trades:
            self._state["last_runtime_apply_ts"] = now_ts
            self._save_json(self.state_path, self._state)
            return None
        tuning = self._build_runtime_tuning(rows, bot)
        previous = self._state.get("runtime_tuning", {})
        changed = not isinstance(previous, dict) or previous.get("signature") != tuning.get("signature") or previous.get("mode") != tuning.get("mode")
        allowed, approval = self._ai_allows_tuning(tuning)
        self._state["runtime_tuning"] = tuning
        tuning["ai_approval"] = approval
        self._state["last_runtime_apply_ts"] = now_ts
        if allowed:
            self._apply_runtime_values(bot, tuning["values"])
        else:
            logger.warning(
                "[ADAPTIVE AUTO] blocked by AI approval: approve=%s confidence=%s reason=%s",
                approval.get("approve"),
                approval.get("confidence"),
                approval.get("reason"),
            )
        self._save_json(self.state_path, self._state)
        if changed:
            metrics = tuning["metrics"]
            logger.info(
                "[ADAPTIVE AUTO] mode=%s ai=%s/%s trades=%s pnl=%.2f winrate=%.1f%% risk=%.3f max_pos=%s entry=%.2f sell=%.2f qconf=%.2f qedge=%.2f trail=%.2f/%.2f",
                tuning["mode"], approval.get("approve"), approval.get("confidence"), metrics["trades"], metrics["pnl"], metrics["winrate"] * 100.0,
                tuning["values"]["risk_per_trade_pct"], tuning["values"]["max_positions"],
                tuning["values"]["entry_threshold"], tuning["values"]["sell_entry_threshold"],
                tuning["values"]["quality_min_confidence"], tuning["values"]["quality_min_expected_edge"],
                tuning["values"]["trailing_activation_atr"], tuning["values"]["trailing_distance_atr"],
            )
            if tg and self.auto_apply_notify:
                try:
                    await tg.send_message(self._telegram_runtime_tuning_text(tuning, applied=allowed))
                except Exception as exc:
                    logger.warning(f"[ADAPTIVE AUTO] Telegram failed: {exc}")
        return tuning

    @staticmethod
    def _telegram_text(text: str) -> str:
        cleaned = text.replace("[ADAPTIVE RECOMMEND] ", "")
        cleaned = cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return "<b>ADAPTIVE RECOMMEND</b>\n<code>" + cleaned[:3500] + "</code>"

    @staticmethod
    def _telegram_runtime_tuning_text(tuning: dict[str, Any], applied: bool = True) -> str:
        metrics = tuning.get("metrics", {})
        values = tuning.get("values", {})
        approval = tuning.get("ai_approval", {})
        mode = str(tuning.get("mode", "unknown"))
        status = "ПРИМЕНЕНО В ПАМЯТИ БОТА" if applied else "НЕ ПРИМЕНЕНО: AI НЕ РАЗРЕШИЛ"
        learning_note = {
            "strict": "Статистика слабая: бот переходит в строгую защиту капитала.",
            "cautious": "Статистика смешанная: бот временно ужесточает фильтры.",
            "base": "Статистика приемлемая: бот держит базовые параметры из config.yaml.",
        }.get(mode, "Бот обновил оценку качества последних сделок.")
        sell_winrate = metrics.get("sell_winrate")
        sell_winrate_text = "нет" if sell_winrate is None else f"{float(sell_winrate) * 100:.1f}%"
        code_action = (
            "Если такой режим повторяется несколько циклов подряд, эти значения стоит перенести в config.yaml "
            "и проверить код входов/выходов: entry, quality_gate, TP/SL, trailing и SELL-фильтр."
        )
        text = (
            "ОБУЧЕНИЕ ОСНОВНОГО БОТА\n"
            f"Статус: {status}\n"
            f"Режим обучения: {mode}\n"
            f"Вывод: {learning_note}\n\n"
            "Статистика окна:\n"
            f"- сделок: {metrics.get('trades')}\n"
            f"- PnL: {float(metrics.get('pnl', 0.0)):.2f} USDT\n"
            f"- winrate: {float(metrics.get('winrate', 0.0)) * 100:.1f}%\n"
            f"- wins/losses: {metrics.get('wins')}/{metrics.get('losses')}\n"
            f"- SELL trades/winrate: {metrics.get('sell_trades')}/{sell_winrate_text}\n\n"
            "AI-разрешение перед правкой:\n"
            f"- approve: {approval.get('approve')}\n"
            f"- confidence: {approval.get('confidence')}\n"
            f"- reason: {approval.get('reason')}\n\n"
            "Предложенные параметры:\n"
            f"- risk_per_trade_pct: {float(values.get('risk_per_trade_pct', 0.0)):.3f}\n"
            f"- max_positions: {values.get('max_positions')}\n"
            f"- entry_threshold: {float(values.get('entry_threshold', 0.0)):.2f}\n"
            f"- sell_entry_threshold: {float(values.get('sell_entry_threshold', 0.0)):.2f}\n"
            f"- quality confidence/edge: {float(values.get('quality_min_confidence', 0.0)):.2f}/"
            f"{float(values.get('quality_min_expected_edge', 0.0)):.2f}\n"
            f"- orderflow/SMC: {float(values.get('min_orderflow_imbalance', 0.0)):.3f}/"
            f"{float(values.get('min_smc_score', 0.0)):.2f}\n"
            f"- TP/SL pct: {float(values.get('tp_pct', 0.0)):.2f}/"
            f"{float(values.get('sl_pct', 0.0)):.2f}\n"
            f"- trailing ATR activation/distance: {float(values.get('trailing_activation_atr', 0.0)):.2f}/"
            f"{float(values.get('trailing_distance_atr', 0.0)):.2f}\n\n"
            "Что это значит:\n"
            "- config.yaml не переписывался автоматически.\n"
            "- runtime-правки действуют только в памяти работающего процесса.\n"
            f"- {code_action}"
        )
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return "<b>ADAPTIVE AUTO</b>\n<code>" + text[:3500] + "</code>"
