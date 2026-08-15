"""Unit tests for LLM gateway AIAI.BY (OpenAI-compatible) provider."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

from prd_agent.ai.llm_gateway import chat_sync, load_llm_settings


def test_load_aiai_from_config():
    cfg = {
        "ai": {"provider": "aiai"},
        "aiai": {
            "api_key": "sk-aiai-test",
            "model": "gemini-2.0-flash",
            "base_url": "https://api.aiai.by/v1",
            "timeout_sec": 28,
        },
        "free_claude_code": {"enabled": False},
    }
    s = load_llm_settings(cfg)
    assert s.provider == "aiai"
    assert s.uses_aiai is True
    assert s.uses_openrouter is False
    assert s.uses_deepseek is False
    assert s.uses_fcc is False
    assert s.aiai_api_key == "sk-aiai-test"
    assert s.aiai_model == "gemini-2.0-flash"
    assert s.aiai_base_url == "https://api.aiai.by/v1"
    assert s.timeout_sec == 28.0
    assert s.has_credentials() is True
    assert s.provider_label == "AIAI.BY"


def test_load_aiai_from_env_key():
    cfg = {
        "ai": {"provider": "aiai"},
        "aiai": {"api_key": "", "model": "gpt-4o-mini"},
        "free_claude_code": {"enabled": False},
    }
    with patch.dict(os.environ, {"AIAI_API_KEY": "sk-from-env", "AIAI_BY_API_KEY": ""}, clear=False):
        s = load_llm_settings(cfg)
    assert s.aiai_api_key == "sk-from-env"
    assert s.aiai_model == "gpt-4o-mini"
    assert s.has_credentials() is True


def test_load_aiai_from_alt_env_key():
    cfg = {
        "ai": {"provider": "aiai"},
        "aiai": {"api_key": ""},
        "free_claude_code": {"enabled": False},
    }
    env = {"AIAI_API_KEY": "", "AIAI_BY_API_KEY": "sk-alt-env"}
    with patch.dict(os.environ, env, clear=False):
        s = load_llm_settings(cfg)
    assert s.aiai_api_key == "sk-alt-env"
    assert s.has_credentials() is True


def test_fcc_enabled_does_not_override_aiai():
    cfg = {
        "ai": {"provider": "aiai"},
        "aiai": {"api_key": "sk-x"},
        "free_claude_code": {"enabled": True, "base_url": "http://127.0.0.1:8082"},
    }
    s = load_llm_settings(cfg)
    assert s.provider == "aiai"
    assert s.uses_aiai is True


def test_aiai_missing_key():
    cfg = {
        "ai": {"provider": "aiai"},
        "aiai": {"api_key": ""},
        "free_claude_code": {"enabled": False},
    }
    with patch.dict(os.environ, {"AIAI_API_KEY": "", "AIAI_BY_API_KEY": ""}, clear=False):
        s = load_llm_settings(cfg)
        assert s.has_credentials() is False
        body, err = chat_sync(s, system="sys", user="hi")
        assert body is None
        assert err is not None
        assert "key not set" in err.lower() or "не задан" in err.lower() or "AIAI" in err


def test_aiai_chat_sync_mock_http():
    cfg = {
        "ai": {"provider": "aiai"},
        "aiai": {
            "api_key": "sk-mock",
            "model": "gemini-2.0-flash",
            "base_url": "https://api.aiai.by/v1",
        },
        "free_claude_code": {"enabled": False},
    }
    s = load_llm_settings(cfg)
    fake_body = {
        "choices": [{"message": {"content": "OK from aiai"}}],
        "usage": {"total_tokens": 10},
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_body).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False
    with patch("prd_agent.ai.llm_gateway.urllib.request.urlopen", return_value=mock_resp) as mocked:
        body, err = chat_sync(s, system="sys", user="ping", max_tokens=16)
    assert err is None
    assert body is not None
    assert body["choices"][0]["message"]["content"] == "OK from aiai"
    req = mocked.call_args[0][0]
    assert req.full_url == "https://api.aiai.by/v1/chat/completions"
    assert "Bearer sk-mock" in req.headers.get("Authorization", "")
