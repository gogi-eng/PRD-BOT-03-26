#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))

from engine.ai_decision import AIDecisionEngine


BOT_MAIN_PATH = Path(__file__).resolve().parents[2] / "bot" / "main.py"
BOT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "bot" / "config.yaml"
BOT_REQ_PATH = Path(__file__).resolve().parents[2] / "bot" / "requirements.txt"


def test_config_has_ai_claude_section():
    source = BOT_CONFIG_PATH.read_text(encoding="utf-8")
    assert "ai_claude:" in source
    assert "enabled: false" in source
    assert "gateway_url: \"ws://127.0.0.1:18789\"" in source
    assert "min_confidence: 65" in source
    assert "enforce_direction_match: true" in source
    assert "fail_open: true" in source


def test_main_wires_claude_gate():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "from engine.ai_decision import AIDecisionEngine" in source
    assert "self.ai_claude_engine = AIDecisionEngine(self.cfg)" in source
    assert "claude_decision = await self.ai_claude_engine.get_decision(claude_data)" in source
    assert "claude_decision.get(\"reject_reason\", \"ai_claude_rejected\")" in source
    assert "[AI_CLAUDE]" in source


def test_requirements_include_websocket_client():
    source = BOT_REQ_PATH.read_text(encoding="utf-8")
    assert "websocket-client" in source


def test_ai_decision_engine_parses_strict_json():
    engine = AIDecisionEngine(cfg=None, client=None)
    parsed = engine._parse_json_response(
        '{"decision":"LONG","confidence":82,"reason":"trend continuation"}'
    )
    assert parsed["decision"] == "LONG"
    assert parsed["confidence"] == 82
    assert parsed["reason"] == "trend continuation"

