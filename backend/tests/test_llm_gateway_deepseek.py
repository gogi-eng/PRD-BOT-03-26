"""Unit tests for LLM gateway DeepSeek / OpenRouter / FCC settings."""
from __future__ import annotations

import os
from unittest.mock import patch

from prd_agent.ai.llm_gateway import LLMSettings, load_llm_settings


def test_load_deepseek_from_config():
    cfg = {
        "ai": {"provider": "deepseek"},
        "deepseek": {
            "api_key": "sk-test",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "timeout_sec": 25,
        },
        "free_claude_code": {"enabled": False},
    }
    s = load_llm_settings(cfg)
    assert s.provider == "deepseek"
    assert s.uses_deepseek is True
    assert s.uses_openrouter is False
    assert s.uses_fcc is False
    assert s.deepseek_api_key == "sk-test"
    assert s.deepseek_model == "deepseek-chat"
    assert s.deepseek_base_url == "https://api.deepseek.com/v1"
    assert s.timeout_sec == 25.0
    assert s.has_credentials() is True
    assert s.provider_label == "DeepSeek"


def test_load_deepseek_from_env_key():
    cfg = {
        "ai": {"provider": "deepseek"},
        "deepseek": {"api_key": "", "model": "deepseek-reasoner"},
        "free_claude_code": {"enabled": False},
    }
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-from-env"}, clear=False):
        s = load_llm_settings(cfg)
    assert s.deepseek_api_key == "sk-from-env"
    assert s.deepseek_model == "deepseek-reasoner"
    assert s.has_credentials() is True


def test_fcc_enabled_does_not_override_deepseek():
    cfg = {
        "ai": {"provider": "deepseek"},
        "deepseek": {"api_key": "sk-x"},
        "free_claude_code": {"enabled": True, "base_url": "http://127.0.0.1:8082"},
    }
    s = load_llm_settings(cfg)
    assert s.provider == "deepseek"
    assert s.uses_deepseek is True


def test_openrouter_still_default():
    cfg = {
        "ai": {"provider": "openrouter"},
        "openrouter": {"api_key": "or-key", "model": "google/gemini-2.5-flash"},
        "free_claude_code": {"enabled": False},
    }
    s = load_llm_settings(cfg)
    assert s.uses_openrouter is True
    assert s.openrouter_api_key == "or-key"
    assert s.has_credentials() is True
    assert s.provider_label == "OpenRouter"


def test_deepseek_missing_key():
    cfg = {
        "ai": {"provider": "deepseek"},
        "deepseek": {"api_key": ""},
        "free_claude_code": {"enabled": False},
    }
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
        s = load_llm_settings(cfg)
        # load_llm_settings reads env; empty string means no key
        if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            assert s.has_credentials() is False
        else:
            # host already has a real key — still a valid deepseek settings object
            assert s.uses_deepseek is True
