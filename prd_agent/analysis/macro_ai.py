"""
Макро-брифинг: RSS (whale_news) + LLM через OpenRouter или Free Claude Code (FCC).
"""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, List, Optional

from prd_agent.ai.llm_gateway import chat_async, load_llm_settings
from telegram_agent.world_feed import fetch_rss_items

logger = logging.getLogger("prd_agent.macro_ai")


class MacroAI:
    def __init__(self, cfg: Dict[str, Any]):
        m = cfg.get("macro_ai", {})
        self.enabled = bool(m.get("enabled", True))
        self._llm = load_llm_settings(cfg)
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

    async def build_briefing(
        self,
        *,
        positions: Optional[List[Dict[str, Any]]] = None,
        watch_symbols: Optional[List[str]] = None,
    ) -> str:
        if not self.enabled:
            return "Модуль macro_ai отключён в config.yaml"
        if not self._llm.has_credentials():
            return "AI не настроен: OPENROUTER_API_KEY, DEEPSEEK_API_KEY или free_claude_code."
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
            text, err = await chat_async(
                self._llm,
                system=(
                    "Ты аналитик крипторынка для трейдера фьючерсов Bybit. "
                    "Отвечай кратко на русском, 8–12 пунктов, без воды. "
                    "Не давай финансовых советов «вложите всё» — только риски и контекст."
                ),
                user=prompt,
                max_tokens=700,
                temperature=0.2,
                title="PRD-BOT-ALL Macro AI",
            )
            if err:
                return f"Ошибка AI ({self._llm.provider_label}): {err}"
            if not text:
                return "AI вернул пустой ответ."
            safe = html.escape(text[:3500])
            backend = self._llm.provider_label
            return f"<b>🧠 Макро-анализ ({backend})</b>\n\n{safe}"
        except Exception as exc:
            logger.exception("macro_ai: %s", exc)
            return f"Ошибка macro_ai: {exc}"
