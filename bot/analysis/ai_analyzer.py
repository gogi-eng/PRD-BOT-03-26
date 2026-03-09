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

    SYSTEM_PROMPT = """You are an aggressive crypto trader with 10+ years of experience.
Your goal is to MAKE MONEY, not avoid trades!

RULES:
1. Be AGGRESSIVE - better to enter and take profit than miss a move!
2. If 2+ indicators agree - ENTER!
3. Volatility = OPPORTUNITY
4. Trade with the trend
5. Liquidity sweep = high-probability reversal entry

RESPONSE FORMAT (STRICT):
DECISION: [BUY/SELL/WAIT]
CONFIDENCE: [0-100]%
REASON: [brief explanation, 1-2 sentences]
RISK: [LOW/MEDIUM/HIGH]"""

    def __init__(self):
        self.api_key = os.getenv("EMERGENT_LLM_KEY", "")
        self.enabled = EMERGENT_AVAILABLE and bool(self.api_key)
        self.min_confidence = 60
        self.fail_open = True
        self._cache: Dict[str, Dict] = {}
        self._cache_ttl = 120

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
            f"  RSI: {data.get('rsi', 50):.1f}",
            f"  ATR%: {data.get('atr_pct', 0):.2f}%",
            f"  Volatility: {data.get('volatility', 'normal')}",
            "",
            "LIQUIDITY SWEEP:",
            f"  Detected: {data.get('sweep_detected', False)}",
            f"  Direction: {data.get('sweep_direction', 0)} (1=bullish, -1=bearish)",
            f"  Strength: {data.get('sweep_strength', 0):.2f}",
            f"  Description: {data.get('sweep_description', 'none')}",
            "",
            "FUNDING:",
            f"  Rate: {data.get('funding_rate', 0)*100:.4f}%",
            f"  Sentiment: {data.get('funding_sentiment', 'neutral')}",
            f"  OI Change: {data.get('oi_change', 0)*100:.1f}%",
            "",
            "LIQUIDATION CLUSTERS:",
            f"  Magnet: {data.get('liq_magnet', 'neutral')}",
            f"  Signal: {data.get('liq_signal', 0)}",
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
            if proposed in ["BUY", "SELL"] and confluence >= 0.5:
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
