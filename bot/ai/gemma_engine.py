"""
Local Gemma (Ollama) — фильтр входа. См. +Gemma.txt: ollama pull, API /api/generate.

Не заменяет Quality Gate и движок сигналов: только доп. слой approve/reject + confidence.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Union

logger = logging.getLogger("BOT")


def config_as_dict(cfg: Union[None, Dict[str, Any], Any]) -> Dict[str, Any]:
    """BotConfig/ dict / None -> plain dict for nested .get("gemma")."""
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        return cfg
    raw = getattr(cfg, "raw", None)
    if isinstance(raw, dict):
        return raw
    return {}

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


class GemmaOllama:
    """HTTP-клиент к Ollama (/api/generate). Модель по умолчанию — лёгкая gemma2:2b; можно gemma:7b."""

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = "gemma2:2b",
        timeout_sec: float = 12.0,
        fail_open: bool = True,
    ):
        self.host = str(host).rstrip("/")
        self.model = model
        self.timeout_sec = float(timeout_sec)
        self.fail_open = bool(fail_open)
        self.url = f"{self.host}/api/generate"

    @classmethod
    def from_config(cls, cfg: Union[None, Dict[str, Any], Any]) -> Optional["GemmaOllama"]:
        d = config_as_dict(cfg)
        if not d:
            return None
        g = d.get("gemma") or {}
        if not g.get("enabled", True):
            return None
        return cls(
            host=str(g.get("host", "http://127.0.0.1:11434")),
            model=str(g.get("model", "gemma2:2b")),
            timeout_sec=float(g.get("timeout_sec", 12)),
            fail_open=bool(g.get("fail_open", True)),
        )

    def analyze_trade(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Возвращает approve (bool), confidence 0..100, reason (str)."""
        prompt = self._build_prompt(data)
        if requests is None:
            return self._fail("requests not installed")

        try:
            r = requests.post(
                self.url,
                json={"model": self.model, "prompt": prompt, "stream": False},
                timeout=self.timeout_sec,
            )
            r.raise_for_status()
            body = r.json()
            text = (body.get("response") or "").strip()
            return self._parse_response(text)
        except Exception as e:
            logger.warning("GemmaOllama request error: %s", e)
            return self._fail(str(e))

    def _fail(self, msg: str) -> Dict[str, Any]:
        if self.fail_open:
            return {"approve": True, "confidence": 50, "reason": f"Gemma fail_open: {msg}"}
        return {"approve": False, "confidence": 0, "reason": f"Gemma: {msg}"}

    def _build_prompt(self, data: Dict[str, Any]) -> str:
        side = str(data.get("side") or data.get("proposed_signal") or "NEUTRAL")
        sym = str(data.get("symbol", ""))
        return f"""You are a professional crypto perps scalping filter.

Analyze this proposed entry (reject weak, late, or chop; prefer momentum).

Symbol: {sym}
Direction: {side}
Model confidence 0-100: {data.get("confidence", 0)}
Trend: {data.get("trend", "unknown")}
ATR%: {data.get("atr", 0)}
ADX: {data.get("adx", 0)}
Volume proxy: {data.get("volume", 0)}
Orderflow imbalance: {data.get("imbalance", 0)}

Reply with ONLY a single JSON object, no markdown, no other text:
{{"approve": true or false, "confidence": 0-100, "reason": "short text"}}"""

    @staticmethod
    def _parse_response(text: str) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"approve": True, "confidence": 50, "reason": "empty_response"}

        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            text = text[start : end + 1]

        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            return {"approve": True, "confidence": 50, "reason": "json_parse_error"}

        approve = bool(out.get("approve", True))
        try:
            conf = int(float(out.get("confidence", 50)))
        except (TypeError, ValueError):
            conf = 50
        conf = max(0, min(100, conf))
        reason = str(out.get("reason", ""))[:500]
        return {"approve": approve, "confidence": conf, "reason": reason or "ok"}
