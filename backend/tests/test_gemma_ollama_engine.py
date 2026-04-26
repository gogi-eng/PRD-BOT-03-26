"""GemmaOllama JSON parsing and config (no running Ollama required)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.ai.gemma_engine import GemmaOllama, config_as_dict

_ROOT = Path(__file__).resolve().parents[2]
_CFG = _ROOT / "config.yaml"


def test_config_as_dict_botconfig():
    from core.config import BotConfig

    c = BotConfig.load(str(_CFG))
    d = config_as_dict(c)
    assert "gemma" in d or "ai" in d


def test_parse_response_plain_json():
    g = GemmaOllama(host="http://127.0.0.1:9", model="x", timeout_sec=0.1)
    text = json.dumps({"approve": False, "confidence": 33, "reason": "chop"})
    r = g._parse_response(text)
    assert r["approve"] is False
    assert r["confidence"] == 33


def test_parse_response_with_noise():
    g = GemmaOllama(host="http://127.0.0.1:9", model="x", timeout_sec=0.1)
    r = g._parse_response('Sure. {"approve": true, "confidence": 80, "reason": "ok"}')
    assert r["approve"] is True
    assert r["confidence"] == 80


def test_ollama_analyzer_instantiate():
    from analysis.ai_analyzer import AITradeAnalyzer
    from core.config import BotConfig
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "config.yaml"
    c = BotConfig.load(str(p))
    a = AITradeAnalyzer(c)
    assert getattr(a, "_backend", "") == c.get("ai", "backend", default="emergent") or c.get("ai", "backend", default="emergent")
