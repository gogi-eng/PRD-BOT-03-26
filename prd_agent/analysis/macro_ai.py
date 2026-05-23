"""
Макро-брифинг через OpenRouter + заголовки RSS (whale_news).
Не использует CDC AI Agent — только свой ключ OpenRouter.
"""
from __future__ import annotations

import html
import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

from telegram_agent.world_feed import fetch_rss_items

logger = logging.getLogger("prd_agent.macro_ai")


class MacroAI:
    def __init__(self, cfg: Dict[str, Any]):
        o = cfg.get("openrouter", {})
        m = cfg.get("macro_ai", {})
        self.enabled = bool(m.get("enabled", True))
        self.api_key = str(o.get("api_key", "") or os.environ.get("OPENROUTER_API_KEY", "")).strip()
        self.model = str(o.get("model", "google/gemini-2.0-flash-001"))
        self.timeout = float(o.get("timeout_sec", 30))
        self.max_headlines = int(m.get("max_headlines", 8))
        wn = cfg.get("whale_news", {})
        self.rss_urls: List[str] = list(wn.get("rss_urls", []))
        self.include_positions = bool(m.get("include_positions", True))

    def _collect_headlines(self) -> List[str]:
        titles: List[str] = []
        for url in self.rss_urls[:5]:
            for item in fetch_rss_items(url, max_items=5):
                t = str(item.get("title", "")).strip()
                if t and t not in titles:
                    titles.append(t[:200])
                if len(titles) >= self.max_headlines:
                    return titles
        return titles

    async def _call_openrouter(self, prompt: str) -> str:
        if not self.api_key:
            return "OpenRouter API key не задан (openrouter.api_key или OPENROUTER_API_KEY)."
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты аналитик крипторынка для трейдера фьючерсов Bybit. "
                        "Отвечай кратко на русском, 8–12 пунктов, без воды. "
                        "Не давай финансовых советов «вложите всё» — только риски и контекст."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 700,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "PRD-BOT-ALL Macro AI",
        }
        url = "https://openrouter.ai/api/v1/chat/completions"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    return f"OpenRouter HTTP {resp.status}: {str(body)[:300]}"
                return str(
                    ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                ).strip()

    async def build_briefing(
        self,
        *,
        positions: Optional[List[Dict[str, Any]]] = None,
        watch_symbols: Optional[List[str]] = None,
    ) -> str:
        if not self.enabled:
            return "Модуль macro_ai отключён в config.yaml"
        headlines = self._collect_headlines()
        pos_lines: List[str] = []
        if self.include_positions and positions:
            for p in positions[:6]:
                sym = p.get("symbol", "?")
                side = p.get("side", "")
                upnl = float(p.get("unrealisedPnl", 0) or 0)
                pos_lines.append(f"{sym} {side} uPnL={upnl:+.2f}")
        syms = ", ".join((watch_symbols or [])[:10]) or "BTCUSDT, ETHUSDT"
        prompt = f"""Сделай макро-брифинг для perpetual futures Bybit.

Пары в фокусе: {syms}

Открытые позиции на бирже:
{chr(10).join(pos_lines) if pos_lines else "нет открытых"}

Свежие заголовки RSS:
{chr(10).join(f"- {h}" for h in headlines) if headlines else "- нет данных"}

Структура ответа:
1) Общий тон рынка (risk-on / risk-off)
2) BTC/ETH — что важно
3) Риски на 24ч (ликвидации, макро, регуляторика)
4) Рекомендация по режиму: осторожно / нейтрально / агрессивно (без конкретных цен)
"""
        try:
            text = await self._call_openrouter(prompt)
            if not text:
                return "OpenRouter вернул пустой ответ."
            safe = html.escape(text[:3500])
            return f"<b>🧠 Макро-анализ (OpenRouter)</b>\n\n{safe}"
        except Exception as exc:
            logger.exception("macro_ai: %s", exc)
            return f"Ошибка macro_ai: {exc}"
