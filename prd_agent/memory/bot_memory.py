"""
Долговременная память бота: режим торговли, исходы сделок, предпочтения пользователя.
Локальный JSON по умолчанию; опционально mem0ai (pip install mem0ai, bot_memory.use_mem0: true).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prd_agent.bot_memory")

_MAX_ITEMS = 200
_MAX_CONTEXT_CHARS = 1200


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BotMemory:
    def __init__(self, cfg: Dict[str, Any]):
        bm = cfg.get("bot_memory", {}) or {}
        self.enabled = bool(bm.get("enabled", True))
        self.use_mem0 = bool(bm.get("use_mem0", False))
        root = Path(cfg.get("_root") or ".")
        rel = str(bm.get("path", "data/bot_memory") or "data/bot_memory")
        self.store_dir = root / rel
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.json_path = self.store_dir / "memories.json"
        self._mem0 = None
        self._user_id = str(bm.get("user_id", "prd_bot_owner"))
        if self.enabled and self.use_mem0:
            self._init_mem0()

    def _init_mem0(self) -> None:
        try:
            from mem0 import Memory  # type: ignore

            self._mem0 = Memory()
            logger.info("bot_memory: mem0 enabled")
        except Exception as exc:
            logger.warning("bot_memory: mem0 unavailable (%s), using JSON", exc)
            self._mem0 = None
            self.use_mem0 = False

    def _load_json(self) -> List[Dict[str, Any]]:
        if not self.json_path.exists():
            return []
        try:
            data = json.loads(self.json_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data[-_MAX_ITEMS:]
        except Exception:
            pass
        return []

    def _save_json(self, items: List[Dict[str, Any]]) -> None:
        trimmed = items[-_MAX_ITEMS:]
        self.json_path.write_text(
            json.dumps(trimmed, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, text: str, *, category: str = "general", metadata: Optional[Dict[str, Any]] = None) -> None:
        if not self.enabled or not (text or "").strip():
            return
        entry = {
            "ts": _utc_now(),
            "category": category,
            "text": text.strip()[:500],
            "metadata": metadata or {},
        }
        items = self._load_json()
        items.append(entry)
        self._save_json(items)
        if self._mem0 is not None:
            try:
                self._mem0.add(text, user_id=self._user_id, metadata={"category": category})
            except Exception as exc:
                logger.debug("mem0 add failed: %s", exc)

    def remember_runtime_mode(self, mode: str) -> None:
        self.add(f"Режим торговли: {mode}", category="runtime_mode")

    def remember_trade_outcome(
        self,
        symbol: str,
        side: str,
        pnl_usdt: float,
        *,
        source: str = "",
    ) -> None:
        sign = "+" if pnl_usdt >= 0 else ""
        self.add(
            f"Сделка {symbol} {side} PnL {sign}{pnl_usdt:.2f} USDT ({source or 'bot'})",
            category="trade_outcome",
            metadata={"symbol": symbol, "pnl_usdt": pnl_usdt},
        )

    def remember_supervisor_skip(self, reason: str, count: int = 1) -> None:
        if count >= 5:
            self.add(
                f"Частый пропуск супервизора: {reason[:120]} (×{count})",
                category="supervisor",
            )

    def search_context(self, query: str = "bot preferences trading mode", limit: int = 8) -> str:
        if not self.enabled:
            return ""
        snippets: List[str] = []
        if self._mem0 is not None:
            try:
                hits = self._mem0.search(query, user_id=self._user_id, limit=limit)
                for h in hits or []:
                    mem = h.get("memory") if isinstance(h, dict) else str(h)
                    if mem:
                        snippets.append(str(mem)[:200])
            except Exception as exc:
                logger.debug("mem0 search failed: %s", exc)
        if not snippets:
            items = self._load_json()
            for it in reversed(items[-limit:]):
                snippets.append(f"[{it.get('category', '?')}] {it.get('text', '')}")
        text = "\n".join(f"• {s}" for s in snippets[:limit])
        return text[:_MAX_CONTEXT_CHARS]

    def context_for_manager(self) -> str:
        return self.search_context("режим торговли SIGNAL LIVE блокировки исходы сделок")
