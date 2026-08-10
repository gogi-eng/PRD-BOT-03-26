"""
Единый шлюз LLM для бота: OpenRouter, DeepSeek (OpenAI-compatible) или Free Claude Code (FCC).

FCC: Anthropic Messages API http://127.0.0.1:8082/v1/messages
DeepSeek: https://api.deepseek.com/v1/chat/completions (ключ в .env: DEEPSEEK_API_KEY)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("prd_agent.llm")

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_DEEPSEEK_BASE = "https://api.deepseek.com/v1"


@dataclass
class LLMSettings:
    provider: str  # openrouter | fcc | deepseek
    timeout_sec: float
    # OpenRouter
    openrouter_api_key: str
    openrouter_model: str
    # DeepSeek (OpenAI-compatible)
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = _DEFAULT_DEEPSEEK_BASE
    # Free Claude Code
    fcc_base_url: str = "http://127.0.0.1:8082"
    fcc_auth_token: str = "freecc"
    fcc_model: str = "claude-3-5-haiku-20241022"

    @property
    def uses_fcc(self) -> bool:
        return self.provider == "fcc"

    @property
    def uses_deepseek(self) -> bool:
        return self.provider == "deepseek"

    @property
    def uses_openrouter(self) -> bool:
        return self.provider == "openrouter"

    @property
    def provider_label(self) -> str:
        if self.uses_fcc:
            return "FCC"
        if self.uses_deepseek:
            return "DeepSeek"
        return "OpenRouter"

    def has_credentials(self) -> bool:
        """Есть ли чем вызывать LLM (FCC — локальный прокси, ключ в админке FCC)."""
        if self.uses_fcc:
            return True
        if self.uses_deepseek:
            return bool(self.deepseek_api_key)
        return bool(self.openrouter_api_key)


def load_llm_settings(cfg: Dict[str, Any]) -> LLMSettings:
    ai = cfg.get("ai", {}) if isinstance(cfg.get("ai"), dict) else {}
    o = cfg.get("openrouter", {}) if isinstance(cfg.get("openrouter"), dict) else {}
    d = cfg.get("deepseek", {}) if isinstance(cfg.get("deepseek"), dict) else {}
    f = cfg.get("free_claude_code", {}) if isinstance(cfg.get("free_claude_code"), dict) else {}
    provider = str(ai.get("provider", o.get("provider", "openrouter"))).strip().lower()
    # FCC включается явно; не перетираем deepseek/openrouter
    if f.get("enabled") is True and provider not in ("openrouter", "deepseek"):
        provider = "fcc"
    if f.get("enabled") is False and provider == "fcc":
        provider = "openrouter"

    timeout = float(
        d.get("timeout_sec")
        if provider == "deepseek" and d.get("timeout_sec") is not None
        else f.get("timeout_sec", o.get("timeout_sec", ai.get("timeout_sec", 30)))
        or 30
    )

    return LLMSettings(
        provider=provider,
        timeout_sec=timeout,
        openrouter_api_key=str(
            o.get("api_key", "") or os.environ.get("OPENROUTER_API_KEY", "")
        ).strip(),
        openrouter_model=str(o.get("model", "google/gemini-2.5-flash")),
        deepseek_api_key=str(
            d.get("api_key", "") or os.environ.get("DEEPSEEK_API_KEY", "")
        ).strip(),
        deepseek_model=str(d.get("model", "deepseek-chat") or "deepseek-chat"),
        deepseek_base_url=str(
            d.get("base_url", _DEFAULT_DEEPSEEK_BASE) or _DEFAULT_DEEPSEEK_BASE
        ).rstrip("/"),
        fcc_base_url=str(f.get("base_url", "http://127.0.0.1:8082")).rstrip("/"),
        fcc_auth_token=str(
            f.get("auth_token", "") or os.environ.get("FCC_AUTH_TOKEN", "freecc")
        ).strip(),
        fcc_model=str(f.get("model", "claude-3-5-haiku-20241022")),
    )


def _extract_anthropic_text(body: Dict[str, Any]) -> str:
    parts: List[str] = []
    for block in body.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts).strip()


def _extract_openai_text(body: Dict[str, Any]) -> str:
    return str(
        ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    ).strip()


async def chat_async(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int = 700,
    temperature: float = 0.2,
    title: str = "PRD-BOT-ALL",
) -> Tuple[str, Optional[str]]:
    """Возвращает (текст ответа, ошибка или None)."""
    if settings.uses_fcc:
        return await _chat_fcc_async(
            settings, system=system, user=user, max_tokens=max_tokens, temperature=temperature
        )
    if settings.uses_deepseek:
        return await _chat_deepseek_async(
            settings, system=system, user=user, max_tokens=max_tokens, temperature=temperature
        )
    return await _chat_openrouter_async(
        settings,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        title=title,
    )


def chat_sync(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int = 180,
    temperature: float = 0.1,
    title: str = "PRD-BOT Telegram",
    timeout_sec: Optional[float] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Синхронный вызов для telegram_signal_agent.
    OpenRouter/DeepSeek: (полный JSON body, None) для учёта бюджета.
    FCC: body в формате pseudo-openrouter {choices: [{message: {content}}]}.
    """
    t = float(timeout_sec if timeout_sec is not None else settings.timeout_sec)
    if settings.uses_fcc:
        text, err = _chat_fcc_sync(
            settings,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_sec=t,
        )
        if err:
            return None, err
        return {"choices": [{"message": {"content": text}}], "usage": {}}, None
    if settings.uses_deepseek:
        return _chat_deepseek_sync(
            settings,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_sec=t,
        )
    return _chat_openrouter_sync(
        settings,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        title=title,
        timeout_sec=t,
    )


async def _chat_openai_compatible_async(
    *,
    url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    timeout_sec: float,
    label: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, Optional[str]]:
    if not api_key:
        return "", f"{label} API key не задан"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_sec),
            ) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    return "", f"{label} HTTP {resp.status}: {str(body)[:300]}"
                return _extract_openai_text(body if isinstance(body, dict) else {}), None
    except Exception as exc:
        logger.exception("%s async: %s", label.lower(), exc)
        return "", str(exc)


def _chat_openai_compatible_sync(
    *,
    url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    timeout_sec: float,
    label: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not api_key:
        return None, f"{label} key not set"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body, None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return None, f"{label} HTTP {exc.code}: {detail}"
    except Exception as exc:
        return None, str(exc)


async def _chat_openrouter_async(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    title: str,
) -> Tuple[str, Optional[str]]:
    return await _chat_openai_compatible_async(
        url=_OPENROUTER_URL,
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_sec=settings.timeout_sec,
        label="OpenRouter",
        extra_headers={"X-Title": title},
    )


async def _chat_deepseek_async(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> Tuple[str, Optional[str]]:
    url = f"{settings.deepseek_base_url}/chat/completions"
    return await _chat_openai_compatible_async(
        url=url,
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_sec=settings.timeout_sec,
        label="DeepSeek",
    )


async def _chat_fcc_async(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> Tuple[str, Optional[str]]:
    text, err, _ = await _fcc_request_async(
        settings, system=system, user=user, max_tokens=max_tokens, temperature=temperature
    )
    return text, err


async def _fcc_request_async(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    url = f"{settings.fcc_base_url}/v1/messages"
    payload = {
        "model": settings.fcc_model,
        "max_tokens": max_tokens,
        "stream": False,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    if settings.fcc_auth_token:
        headers["x-api-key"] = settings.fcc_auth_token
        headers["Authorization"] = f"Bearer {settings.fcc_auth_token}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=settings.timeout_sec),
            ) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    return "", f"FCC HTTP {resp.status}: {str(body)[:300]}", {}
                return _extract_anthropic_text(body), None, body if isinstance(body, dict) else {}
    except aiohttp.ClientConnectorError:
        return (
            "",
            f"FCC недоступен ({settings.fcc_base_url}). Запустите: uv run fcc-server",
            {},
        )
    except Exception as exc:
        logger.exception("fcc async: %s", exc)
        return "", str(exc), {}


def _chat_openrouter_sync(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    title: str,
    timeout_sec: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    return _chat_openai_compatible_sync(
        url=_OPENROUTER_URL,
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_sec=timeout_sec,
        label="OpenRouter",
        extra_headers={"X-Title": title},
    )


def _chat_deepseek_sync(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    timeout_sec: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    url = f"{settings.deepseek_base_url}/chat/completions"
    return _chat_openai_compatible_sync(
        url=url,
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_sec=timeout_sec,
        label="DeepSeek",
    )


def _chat_fcc_sync(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    timeout_sec: float,
) -> Tuple[str, Optional[str]]:
    url = f"{settings.fcc_base_url}/v1/messages"
    payload = {
        "model": settings.fcc_model,
        "max_tokens": max_tokens,
        "stream": False,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    if settings.fcc_auth_token:
        headers["x-api-key"] = settings.fcc_auth_token
        headers["Authorization"] = f"Bearer {settings.fcc_auth_token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return _extract_anthropic_text(body), None
    except urllib.error.URLError as exc:
        return "", f"FCC недоступен ({settings.fcc_base_url}): {exc.reason}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return "", f"FCC HTTP {exc.code}: {detail}"
    except Exception as exc:
        return "", str(exc)


async def health_check(settings: LLMSettings) -> Tuple[bool, str]:
    if settings.uses_fcc:
        url = f"{settings.fcc_base_url}/health"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        return True, "FCC OK"
                    return False, f"FCC HTTP {resp.status}"
        except Exception as exc:
            return False, f"FCC: {exc}"
    if settings.uses_deepseek:
        if settings.deepseek_api_key:
            return True, f"DeepSeek key задан ({settings.deepseek_model})"
        return False, "Нет DeepSeek ключа (DEEPSEEK_API_KEY или deepseek.api_key)"
    if settings.openrouter_api_key:
        return True, "OpenRouter key задан"
    return False, "Нет OpenRouter ключа"
