#!/usr/bin/env python3
from __future__ import annotations

import json
import threading
from typing import Optional

try:
    import websocket
except Exception:  # pragma: no cover - optional dependency
    websocket = None


class ClaudeClient:
    """Minimal OpenClaw websocket client for Claude completions."""

    def __init__(
        self,
        url: str = "ws://127.0.0.1:18789",
        timeout_sec: int = 15,
        max_tokens: int = 300,
    ):
        self.url = str(url)
        self.timeout_sec = max(3, int(timeout_sec))
        self.max_tokens = max(32, int(max_tokens))
        self.ws = None
        self.lock = threading.Lock()
        self.connect()

    def connect(self):
        if websocket is None:
            raise RuntimeError("websocket-client dependency is missing")
        self.ws = websocket.create_connection(self.url, timeout=self.timeout_sec)

    def _ensure_connection(self):
        if self.ws is None:
            self.connect()

    @staticmethod
    def _extract_completion(data: dict) -> str:
        for key in ("completion", "text", "response", "content"):
            value = data.get(key)
            if isinstance(value, str):
                return value
        payload = data.get("payload")
        if isinstance(payload, dict):
            for key in ("completion", "text", "response", "content"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
        return ""

    def send_prompt(self, prompt: str) -> str:
        with self.lock:
            try:
                self._ensure_connection()
                request = {
                    "type": "completion",
                    "payload": {
                        "prompt": str(prompt),
                        "max_tokens": self.max_tokens,
                    },
                }
                self.ws.send(json.dumps(request, ensure_ascii=False))
                response = self.ws.recv()
                data = json.loads(response)
                return self._extract_completion(data)
            except Exception as exc:
                print(f"[Claude ERROR] {exc}")
                try:
                    if self.ws is not None:
                        self.ws.close()
                except Exception:
                    pass
                self.ws = None
                try:
                    self.connect()
                except Exception as reconnect_exc:
                    print(f"[Claude RECONNECT ERROR] {reconnect_exc}")
                return ""
