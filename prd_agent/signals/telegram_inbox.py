"""
Чтение сигналов из JSONL (telegram_signal_agent или ручная очередь).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from telegram_agent.signal_parse import enrich_parsed_signal_levels


SIDE_RE = re.compile(r"\b(LONG|SHORT|BUY|SELL|ЛОНГ|ШОРТ)\b", re.I)
SYMBOL_RE = re.compile(r"\b([A-Z]{2,10}USDT)\b")


class TelegramInbox:
    def __init__(self, cfg: Dict[str, Any], root: Path):
        sig = cfg.get("signals", {})
        rel = sig.get("telegram_signals_jsonl", "reports/telegram_signals/signals.jsonl")
        self.path = (root / rel).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._offset = 0
        self._seen_ids: Set[str] = set()
        backlog = int(sig.get("telegram_inbox_backlog_lines", 100))
        if self.path.exists():
            if backlog > 0:
                self._load_backlog(backlog)
            else:
                self._offset = self.path.stat().st_size

    def _load_backlog(self, max_lines: int) -> None:
        """При старте читает последние N строк (иначе только новые после запуска)."""
        try:
            lines = self.path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return
        tail = lines[-max_lines:] if len(lines) > max_lines else lines
        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                uid = str(row.get("message_id") or row.get("id") or hash(line))
            except json.JSONDecodeError:
                uid = str(hash(line))
            self._seen_ids.add(uid)
        self._offset = self.path.stat().st_size

    def _parse_text_signal(self, text: str) -> Optional[Dict[str, Any]]:
        if not text or len(text) < 8:
            return None
        sym_m = SYMBOL_RE.search(text.upper())
        if not sym_m:
            return None
        side_m = SIDE_RE.search(text)
        if not side_m:
            return None
        side_raw = side_m.group(1).upper()
        side = "Buy" if side_raw in ("LONG", "BUY", "ЛОНГ") else "Sell"
        parsed = {"symbol": sym_m.group(1), "side": side, "confidence": 0.72, "raw_text": text}
        enrich_parsed_signal_levels(parsed, text)
        return parsed

    def poll(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size <= self._offset:
            return []
        with self.path.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(self._offset)
            chunk = f.read()
            self._offset = f.tell()

        out: List[Dict[str, Any]] = []
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                parsed = self._parse_text_signal(line)
                if parsed:
                    out.append(parsed)
                continue
            uid = str(row.get("message_id") or row.get("id") or row.get("hash") or hash(line))
            if uid in self._seen_ids:
                continue
            self._seen_ids.add(uid)
            if row.get("symbol") and row.get("side"):
                out.append(row)
                continue
            text = row.get("text") or row.get("message") or row.get("raw_text") or ""
            parsed = self._parse_text_signal(str(text))
            if parsed:
                parsed["channel"] = row.get("channel") or row.get("chat_title")
                out.append(parsed)
        return out
