#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from ai.claude_client import ClaudeClient


class AIDecisionEngine:
    """Claude/OpenClaw decision layer for entry filtering."""

    def __init__(
        self,
        cfg: Optional[Any] = None,
        *,
        client: Optional[Any] = None,
        gateway_url: str = "ws://127.0.0.1:18789",
        confidence_threshold: int = 70,
        strict_min_confidence: int = 65,
        max_tokens: int = 300,
        timeout_sec: int = 20,
        fail_open: bool = True,
        enforce_direction_match: bool = True,
        blend_weight: float = 0.25,
        enabled: bool = False,
    ):
        # Config-aware initialization for direct use from TradingBot.
        if cfg is not None and hasattr(cfg, "get"):
            enabled = bool(cfg.get("ai_claude", "enabled", default=enabled))
            gateway_url = str(cfg.get("ai_claude", "gateway_url", default=gateway_url))
            max_tokens = int(cfg.get("ai_claude", "max_tokens", default=max_tokens))
            timeout_sec = int(cfg.get("ai_claude", "timeout_sec", default=timeout_sec))
            confidence_threshold = int(
                cfg.get("ai_claude", "confidence_threshold", default=confidence_threshold)
            )
            strict_min_confidence = int(
                cfg.get("ai_claude", "strict_min_confidence", default=strict_min_confidence)
            )
            # Backward-compatible alias.
            strict_min_confidence = int(
                cfg.get("ai_claude", "min_confidence", default=strict_min_confidence)
            )
            fail_open = bool(cfg.get("ai_claude", "fail_open", default=fail_open))
            enforce_direction_match = bool(
                cfg.get("ai_claude", "enforce_direction_match", default=enforce_direction_match)
            )
            blend_weight = float(cfg.get("ai_claude", "blend_weight", default=blend_weight))

        self.enabled = bool(enabled)
        self.confidence_threshold = max(0, min(100, int(confidence_threshold)))
        self.strict_min_confidence = max(0, min(100, int(strict_min_confidence)))
        self.fail_open = bool(fail_open)
        self.enforce_direction_match = bool(enforce_direction_match)
        self.blend_weight = max(0.0, min(1.0, float(blend_weight)))
        self.client = client

        if self.client is None and self.enabled:
            try:
                self.client = ClaudeClient(
                    url=gateway_url,
                    max_tokens=max_tokens,
                    timeout_sec=timeout_sec,
                )
            except Exception as exc:
                print(f"[AI_CLAUDE] init error: {exc}")
                self.client = None

    def build_prompt(self, symbol: str, data: Dict[str, Any]) -> str:
        return f"""
You are a professional crypto trader.

Analyze the data and return STRICT JSON:

{{
  "decision": "LONG or SHORT or SKIP",
  "confidence": 0-100,
  "reason": "short explanation"
}}

DATA:
symbol: {symbol}
price: {data.get("price", 0.0)}
rsi: {data.get("rsi", 50.0)}
volume: {data.get("volume", data.get("volume_ratio", 0.0))}
trend: {data.get("trend", "neutral")}
orderflow: {data.get("orderflow", 0.0)}
liquidations: {data.get("liquidations", "neutral")}

RULES:
- Avoid trades in conflicting signals
- Prefer high probability setups
- Ignore weak trends
""".strip()

    @staticmethod
    def _normalize_decision(raw: Dict[str, Any]) -> Dict[str, Any]:
        decision = str(raw.get("decision", "SKIP")).strip().upper()
        if decision == "BUY":
            decision = "LONG"
        elif decision == "SELL":
            decision = "SHORT"
        if decision not in {"LONG", "SHORT", "SKIP"}:
            decision = "SKIP"
        try:
            confidence = int(float(raw.get("confidence", 0)))
        except Exception:
            confidence = 0
        confidence = max(0, min(100, confidence))
        reason = str(raw.get("reason", ""))
        return {"decision": decision, "confidence": confidence, "reason": reason}

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        parsed = json.loads(response)
        if not isinstance(parsed, dict):
            raise ValueError("response is not a JSON object")
        return self._normalize_decision(parsed)

    @staticmethod
    def _proposed_to_decision(proposed_signal: str) -> str:
        side = str(proposed_signal or "").upper()
        if side == "BUY":
            return "LONG"
        if side == "SELL":
            return "SHORT"
        return "SKIP"

    def _allow_result(self, reason: str = "allowed", blended_confidence: Optional[float] = None, **extra) -> Dict[str, Any]:
        result = {"allow": True, "reason": reason, "reject_reason": "", "blended_confidence": blended_confidence}
        result.update(extra)
        return result

    def _reject_result(self, reject_reason: str, **extra) -> Dict[str, Any]:
        result = {"allow": False, "reason": reject_reason, "reject_reason": reject_reason, "blended_confidence": None}
        result.update(extra)
        return result

    async def get_decision(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return self._allow_result("ai_claude_disabled")

        symbol = str(data.get("symbol", "") or "")
        proposed_signal = str(data.get("proposed_signal", data.get("side", "SKIP")) or "SKIP").upper()
        proposed_decision = self._proposed_to_decision(proposed_signal)
        base_confidence = float(data.get("confidence", 0.0) or 0.0)

        if self.client is None:
            if self.fail_open:
                return self._allow_result("ai_claude_unavailable_fail_open")
            return self._reject_result("ai_claude_unavailable")

        prompt = self.build_prompt(symbol, data)
        try:
            response = await asyncio.to_thread(self.client.send_prompt, prompt)
        except Exception as exc:
            if self.fail_open:
                return self._allow_result(f"ai_claude_error_fail_open({type(exc).__name__})")
            return self._reject_result("ai_claude_error")

        if not response:
            if self.fail_open:
                return self._allow_result("ai_claude_empty_fail_open")
            return self._reject_result("ai_claude_empty_response")

        try:
            normalized = self._parse_json_response(response)
        except Exception:
            if self.fail_open:
                return self._allow_result("ai_claude_parse_fail_open")
            return self._reject_result("ai_claude_parse_error")

        decision = normalized["decision"]
        confidence = int(normalized["confidence"])
        reason = normalized.get("reason", "")

        if confidence < self.strict_min_confidence:
            return self._reject_result(
                "ai_claude_confidence_strict_low",
                decision=decision,
                confidence=confidence,
                raw_reason=reason,
            )
        if confidence < self.confidence_threshold:
            return self._reject_result(
                "ai_claude_confidence_threshold_low",
                decision=decision,
                confidence=confidence,
                raw_reason=reason,
            )
        if decision == "SKIP":
            return self._reject_result(
                "ai_claude_skip",
                decision=decision,
                confidence=confidence,
                raw_reason=reason,
            )
        if self.enforce_direction_match and proposed_decision in {"LONG", "SHORT"} and decision != proposed_decision:
            return self._reject_result(
                "ai_claude_direction_mismatch",
                decision=decision,
                confidence=confidence,
                raw_reason=reason,
            )

        blended_confidence = round(
            (1.0 - self.blend_weight) * base_confidence + self.blend_weight * (confidence / 100.0),
            4,
        )
        return self._allow_result(
            "ai_claude_approved",
            blended_confidence=blended_confidence,
            decision=decision,
            confidence=confidence,
            raw_reason=reason,
        )
