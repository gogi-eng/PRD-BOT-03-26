#!/usr/bin/env python3
"""
AI Trade Analyzer — фильтр сигналов через LLM.
Использует Gemini 3 Flash через Emergent Universal Key.
"""
from __future__ import annotations
import os
import asyncio
from typing import Dict, Optional
from datetime import datetime, timezone
from collections import deque
from dotenv import load_dotenv

load_dotenv(override=True)

try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    EMERGENT_AVAILABLE = True
except ImportError:
    EMERGENT_AVAILABLE = False
    print("[AI] emergentintegrations not installed")


class AITradeAnalyzer:
    """
    AI фильтр для финальной проверки входа.
    Анализирует все данные и даёт BUY/SELL/WAIT.
    """

    SYSTEM_PROMPT = """You are the final breakout-risk filter for an AI-driven crypto futures fund.
The strategy is momentum continuation, not RSI reversal.

PRIMARY VALID SETUPS:
1. Breakout continuation after local structure break
2. Retest / pullback continuation in an existing trend
3. Momentum continuation with aligned orderflow and trend

Approve strong trend-continuation setups unless there is EXPLICIT evidence of a fake breakout:
- clear contradiction between trend and orderflow
- move is very extended/too late
- obvious trap / reversal signs

Do NOT reject just because RSI is high/low. This strategy trades trends.

RESPONSE FORMAT (STRICT):
DECISION: [BUY/SELL/WAIT]
CONFIDENCE: [0-100]%
REASON: [brief explanation, 1-2 sentences]
RISK: [LOW/MEDIUM/HIGH]"""

    def __init__(self):
        self.api_key = os.getenv("EMERGENT_LLM_KEY", "")
        self.enabled = EMERGENT_AVAILABLE and bool(self.api_key)
        self.min_confidence = 52
        self.fail_open = True
        self.require_direction_match = True
        self.uniformity_guard_enabled = True
        self.uniformity_window = 8
        self.uniformity_conf_spread_max = 3
        self._recent_ai = deque(maxlen=50)
        self._cache: Dict[str, Dict] = {}
        self._cache_ttl = 600

        if self.enabled:
            print("[AI] AI analyzer initialized (Gemini 3 Flash)")
        else:
            print("[AI] AI disabled - running without AI filter")

    def _create_chat(self, session_id: str):
        if not EMERGENT_AVAILABLE or not self.api_key:
            return None
        chat = LlmChat(
            api_key=self.api_key,
            session_id=session_id,
            system_message=self.SYSTEM_PROMPT,
        )
        chat.with_model("gemini", "gemini-3-flash-preview")
        return chat

    def _format_data(self, symbol: str, data: Dict) -> str:
        lines = [
            f"=== ANALYSIS {symbol} ===",
            f"Price: ${data.get('price', 0):.6f}",
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "MARKET REGIME:",
            f"  Regime: {data.get('regime', 'unknown')}",
            f"  Trend: {data.get('trend', 'neutral')}",
            f"  HTF Trend: {data.get('htf_trend', 'neutral')}",
            f"  ADX: {data.get('adx', 0):.1f}",
            f"  ATR%: {data.get('atr_pct', 0):.2f}%",
            f"  Volatility: {data.get('volatility', 'normal')}",
            "",
            "TRANSFORMER MODEL:",
            f"  Prob up: {data.get('transformer_prob_up', 0):.2f}",
            f"  Prob down: {data.get('transformer_prob_down', 0):.2f}",
            f"  Prob flat: {data.get('transformer_prob_flat', 0):.2f}",
            "",
            "ORDERFLOW:",
            f"  Bullish ratio: {data.get('orderflow_bullish_ratio', 1):.2f}",
            f"  Bearish ratio: {data.get('orderflow_bearish_ratio', 1):.2f}",
            f"  Spread: {data.get('spread_pct', 0):.4f}%",
            "",
            "LIQUIDATION HEATMAP:",
            f"  Magnet: {data.get('liq_magnet', 'neutral')}",
            f"  Signal: {data.get('liq_signal', 0)}",
            f"  Target level: {data.get('liq_target', 0):.4f}",
            f"  Distance to target: {data.get('liq_distance_pct', 0):.3f}%",
            "",
            "PROPOSED ENTRY:",
            f"  Signal: {data.get('proposed_signal', 'NONE')}",
            f"  Confluence: {data.get('confluence_score', 0):.0%}",
            "",
            "=== END DATA ===",
            "",
            "Based on ALL data above, give your recommendation in the specified format.",
        ]
        return "\n".join(lines)

    def _parse_response(self, response: str) -> Dict:
        result = {"decision": "WAIT", "confidence": 0, "reason": "", "risk": "HIGH"}
        for line in response.strip().split("\n"):
            line = line.strip()
            if line.startswith("DECISION:"):
                d = line.replace("DECISION:", "").strip().upper()
                if d in ["BUY", "SELL", "WAIT"]:
                    result["decision"] = d
            elif line.startswith("CONFIDENCE:"):
                try:
                    result["confidence"] = int(float(line.replace("CONFIDENCE:", "").replace("%", "").strip()))
                except (ValueError, TypeError):
                    pass
            elif line.startswith("REASON:"):
                result["reason"] = line.replace("REASON:", "").strip()
            elif line.startswith("RISK:"):
                r = line.replace("RISK:", "").strip().upper()
                if r in ["LOW", "MEDIUM", "HIGH"]:
                    result["risk"] = r
        return result

    @staticmethod
    def _direction_mismatch(proposed_signal: str, ai_decision: str) -> bool:
        proposed = str(proposed_signal or "").upper()
        decision = str(ai_decision or "").upper()
        if proposed not in ["BUY", "SELL"] or decision not in ["BUY", "SELL"]:
            return False
        return proposed != decision

    def _record_ai_output(self, decision: str, confidence: int):
        d = str(decision or "").upper()
        if d not in ["BUY", "SELL"]:
            return
        self._recent_ai.append((d, int(confidence)))

    def _uniform_bias_detected(self) -> bool:
        if not self.uniformity_guard_enabled:
            return False
        if self.uniformity_window <= 1:
            return False
        if len(self._recent_ai) < self.uniformity_window:
            return False
        sample = list(self._recent_ai)[-self.uniformity_window :]
        directions = [d for d, _ in sample]
        confs = [c for _, c in sample]
        if len(set(directions)) != 1:
            return False
        return (max(confs) - min(confs)) <= int(self.uniformity_conf_spread_max)

    async def analyze(self, symbol: str, analysis_data: Dict) -> Dict:
        """
        Анализирует данные и возвращает AI-рекомендацию.

        Returns:
            {"decision": "BUY"|"SELL"|"WAIT", "confidence": 0-100,
             "reason": str, "risk": str, "should_trade": bool}
        """
        if not self.enabled:
            proposed = analysis_data.get("proposed_signal", "NEUTRAL")
            confluence = analysis_data.get("confluence_score", 0)
            if proposed in ["BUY", "SELL"] and confluence >= 0.60:
                return {
                    "decision": proposed, "confidence": int(confluence * 100),
                    "reason": "AI disabled, confluence sufficient",
                    "risk": "MEDIUM", "should_trade": True,
                }
            return {"decision": "WAIT", "confidence": 0, "reason": "AI disabled, low confluence",
                    "risk": "HIGH", "should_trade": False}

        # Cache
        cache_key = f"{symbol}_{analysis_data.get('proposed_signal', '')}_{analysis_data.get('confluence_score', 0):.2f}"
        now = datetime.now(timezone.utc)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if (now - cached["time"]).total_seconds() < self._cache_ttl:
                return cached["result"]

        try:
            prompt = self._format_data(symbol, analysis_data)
            session_id = f"trade_{symbol}_{now.strftime('%Y%m%d_%H%M%S')}"
            chat = self._create_chat(session_id)
            if chat is None:
                proposed = analysis_data.get("proposed_signal", "NEUTRAL")
                return {
                    "decision": proposed, "confidence": 50,
                    "reason": "AI library unavailable",
                    "risk": "MEDIUM", "should_trade": proposed in ["BUY", "SELL"],
                }

            print(f"[AI] Analyzing {symbol}...")
            response = await chat.send_message(UserMessage(text=prompt))
            result = self._parse_response(response)

            proposed = analysis_data.get("proposed_signal", "NEUTRAL")
            if self.require_direction_match and self._direction_mismatch(proposed, result.get("decision", "WAIT")):
                result["should_trade"] = False
                result["risk"] = "HIGH"
                result["reason"] = f"direction_mismatch ai={result.get('decision')} proposed={proposed}"
                self._cache[cache_key] = {"time": now, "result": result}
                print(f"[AI] {symbol}: {result['decision']} ({result['confidence']}%) - SKIP (direction mismatch)")
                return result

            self._record_ai_output(result.get("decision", "WAIT"), result.get("confidence", 0))
            if self._uniform_bias_detected():
                result["should_trade"] = False
                result["risk"] = "HIGH"
                result["reason"] = "uniform_confidence_bias"
                self._cache[cache_key] = {"time": now, "result": result}
                print(f"[AI] {symbol}: {result['decision']} ({result['confidence']}%) - SKIP (uniform bias)")
                return result

            result["should_trade"] = (
                result["decision"] in ["BUY", "SELL"] and
                result["confidence"] >= self.min_confidence
            )
            self._cache[cache_key] = {"time": now, "result": result}
            trade_status = "ENTER" if result["should_trade"] else "SKIP"
            print(f"[AI] {symbol}: {result['decision']} ({result['confidence']}%) - {trade_status}")
            return result

        except Exception as e:
            print(f"[AI] Error analyzing {symbol}: {e}")
            if self.fail_open:
                proposed = analysis_data.get("proposed_signal", "NEUTRAL")
                return {
                    "decision": proposed, "confidence": 50,
                    "reason": f"AI error - fail open: {str(e)[:30]}",
                    "risk": "HIGH", "should_trade": proposed in ["BUY", "SELL"],
                }
            return {"decision": "WAIT", "confidence": 0, "reason": f"AI error: {str(e)[:30]}",
                    "risk": "HIGH", "should_trade": False}
