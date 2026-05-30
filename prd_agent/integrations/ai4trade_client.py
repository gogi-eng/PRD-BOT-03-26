"""Клиент API ai4trade.ai (токен, heartbeat, лента подписок)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://ai4trade.ai/api"


def parse_credentials_text(raw: str) -> dict[str, Any]:
    text = raw.strip().lstrip("\ufeff")
    if not text:
        raise ValueError("файл credentials пустой")
    if "\n" not in text and not text.startswith("{"):
        return {"token": text.strip().strip('"').strip("'")}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        if '"token"' in text and not text.lstrip().startswith("{"):
            wrapped = "{" + text.strip().strip(",") + "}"
            try:
                data = json.loads(wrapped)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(data, dict) and data.get("token"):
                    return data
        raise ValueError(f"неверный JSON credentials: {exc}") from exc
    if isinstance(data, str):
        return {"token": data}
    if not isinstance(data, dict) or not data.get("token"):
        raise ValueError('в credentials нужен ключ "token"')
    return data


def load_credentials(path: Path) -> dict[str, Any]:
    creds = parse_credentials_text(path.read_text(encoding="utf-8"))
    if creds.get("agent_id") and creds.get("agent_name"):
        return creds
    r = requests.get(
        f"{BASE_URL}/claw/agents/me",
        headers=_auth_headers(creds),
        timeout=30,
    )
    r.raise_for_status()
    me = r.json()
    creds.setdefault("agent_id", me.get("id"))
    creds.setdefault("agent_name", me.get("name"))
    creds.setdefault("platform", "https://ai4trade.ai")
    return creds


def _auth_headers(creds: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {creds['token']}"}


def heartbeat(creds: dict[str, Any]) -> dict[str, Any]:
    agent_id = int(creds.get("agent_id") or 0)
    r = requests.post(
        f"{BASE_URL}/claw/agents/heartbeat",
        json={"agent_id": agent_id, "status": "alive"},
        headers=_auth_headers(creds),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def fetch_following_feed(creds: dict[str, Any], *, limit: int = 30) -> list[dict[str, Any]]:
    r = requests.get(
        f"{BASE_URL}/signals/feed",
        params={"limit": limit, "sort": "following"},
        headers=_auth_headers(creds),
        timeout=45,
    )
    r.raise_for_status()
    return r.json().get("signals") or []


def fetch_signal_detail(creds: dict[str, Any], signal_id: int) -> dict[str, Any] | None:
    """Детали сигнала, если heartbeat дал только id."""
    r = requests.get(
        f"{BASE_URL}/signals/{signal_id}",
        headers=_auth_headers(creds),
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    body = r.json()
    if isinstance(body, dict) and "signal" in body:
        return body["signal"]
    return body if isinstance(body, dict) else None
