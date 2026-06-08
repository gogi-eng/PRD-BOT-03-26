"""
Единый шлюз LLM для бота: OpenRouter напрямую или Free Claude Code (FCC) proxy.

FCC: Anthropic Messages API http://127.0.0.1:8082/v1/messages
Ключи провайдеров задаются в админке FCC, не в .env бота (кроме auth_token).
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
DEFAULT_OPENROUTER_MODEL = "google/gemini-2.5-flash"
_RETIRED_MODEL_FALLBACKS = {
    "google/gemini-2.0-flash-001": DEFAULT_OPENROUTER_MODEL,
    "google/gemini-2.0-flash": DEFAULT_OPENROUTER_MODEL,
}


def resolve_openrouter_model(model: str) -> str:
    m = (model or "").strip() or DEFAULT_OPENROUTER_MODEL
    return _RETIRED_MODEL_FALLBACKS.get(m, m)


@dataclass
class LLMSettings:
    provider: str  # openrouter | fcc
    timeout_sec: float
    # OpenRouter
    openrouter_api_key: str
    openrouter_model: str
    # Free Claude Code
    fcc_base_url: str
    fcc_auth_token: str
    fcc_model: str

    @property
    def uses_fcc(self) -> bool:
        return self.provider == "fcc"


def load_llm_settings(cfg: Dict[str, Any]) -> LLMSettings:
    ai = cfg.get("ai", {}) if isinstance(cfg.get("ai"), dict) else {}
    o = cfg.get("openrouter", {}) if isinstance(cfg.get("openrouter"), dict) else {}
    f = cfg.get("free_claude_code", {}) if isinstance(cfg.get("free_claude_code"), dict) else {}
    provider = str(ai.get("provider", o.get("provider", "openrouter"))).strip().lower()
    if f.get("enabled") is True and provider != "openrouter":
        provider = "fcc"
    if f.get("enabled") is False and provider == "fcc":
        provider = "openrouter"
    return LLMSettings(
        provider=provider,
        timeout_sec=float(
            f.get("timeout_sec", o.get("timeout_sec", ai.get("timeout_sec", 30))) or 30
        ),
        openrouter_api_key=str(o.get("api_key", "") or os.environ.get("OPENROUTER_API_KEY", "")).strip(),
        openrouter_model=resolve_openrouter_model(
            str(o.get("model", DEFAULT_OPENROUTER_MODEL))
        ),
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
    OpenRouter: возвращает (полный JSON body, None) для учёта бюджета.
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
    return _chat_openrouter_sync(
        settings,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        title=title,
        timeout_sec=t,
    )


async def _chat_openrouter_async(
    settings: LLMSettings,
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    title: str,
) -> Tuple[str, Optional[str]]:
    if not settings.openrouter_api_key:
        return "", "OpenRouter API key не задан"
    models = [settings.openrouter_model]
    fallback = resolve_openrouter_model(settings.openrouter_model)
    if fallback not in models:
        models.append(fallback)
    if DEFAULT_OPENROUTER_MODEL not in models:
        models.append(DEFAULT_OPENROUTER_MODEL)
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "X-Title": title,
    }
    last_err = ""
    try:
        async with aiohttp.ClientSession() as session:
            for model in models:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                async with session.post(
                    _OPENROUTER_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=settings.timeout_sec),
                ) as resp:
                    body = await resp.json()
                    if resp.status >= 400:
                        last_err = f"OpenRouter HTTP {resp.status}: {str(body)[:300]}"
                        if resp.status == 404 and model != models[-1]:
                            logger.warning("openrouter model %s unavailable, retry next", model)
                            continue
                        return "", last_err
                    text = str(
                        ((body.get("choices") or [{}])[0].get("message") or {}).get(
                            "content"
                        )
                        or ""
                    ).strip()
                    if model != settings.openrouter_model:
                        logger.info(
                            "openrouter: использован fallback model=%s (был %s)",
                            model,
                            settings.openrouter_model,
                        )
                    return text, None
        return "", last_err or "OpenRouter: нет доступной модели"
    except Exception as exc:
        logger.exception("openrouter async: %s", exc)
        return "", str(exc)


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
    if not settings.openrouter_api_key:
        return None, "OpenRouter key not set"
    models = [settings.openrouter_model]
    fallback = resolve_openrouter_model(settings.openrouter_model)
    if fallback not in models:
        models.append(fallback)
    if DEFAULT_OPENROUTER_MODEL not in models:
        models.append(DEFAULT_OPENROUTER_MODEL)
    last_err: Optional[str] = None
    for model in models:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            _OPENROUTER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "X-Title": title,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if model != settings.openrouter_model:
                    logger.info(
                        "openrouter sync: fallback model=%s (был %s)",
                        model,
                        settings.openrouter_model,
                    )
                return body, None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            last_err = f"OpenRouter HTTP {exc.code}: {detail}"
            if exc.code == 404 and model != models[-1]:
                continue
            return None, last_err
        except Exception as exc:
            return None, str(exc)
    return None, last_err or "OpenRouter: нет доступной модели"


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
    if settings.openrouter_api_key:
        return True, "OpenRouter key задан"
    return False, "Нет OpenRouter ключа"
