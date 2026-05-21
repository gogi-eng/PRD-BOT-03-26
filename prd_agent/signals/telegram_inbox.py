"""
Чтение сигналов из JSONL (telegram_signal_agent или ручная очередь).
Поддерживает плоский формат и вложенный audit-формат; для unified — только одобренные.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from telegram_agent.signal_parse import enrich_parsed_signal_levels


SIDE_RE = re.compile(r"\b(LONG|SHORT|BUY|SELL|ЛОНГ|ШОРТ)\b", re.I)
SYMBOL_RE = re.compile(r"\b([A-Z]{2,10}USDT)\b")

_INBOX_ACTIONS = frozenset({"approved_notify", "analyze_only", "executed"})


class TelegramInbox:
    def __init__(self, cfg: Dict[str, Any], root: Path):
        sig = cfg.get("signals", {})
        rel = sig.get("telegram_signals_jsonl", "reports/telegram_signals/signals_inbox.jsonl")
        self.path = (root / rel).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._only_approved = bool(sig.get("telegram_inbox_only_approved", True))
        self._min_conf = float(sig.get("min_telegram_confidence", 0.55))
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
                uid = self._row_uid(row, line)
            except json.JSONDecodeError:
                uid = str(hash(line))
            self._seen_ids.add(uid)
        self._offset = self.path.stat().st_size

    @staticmethod
    def _row_uid(row: Dict[str, Any], line: str) -> str:
        nested = row.get("signal") if isinstance(row.get("signal"), dict) else {}
        return str(
            row.get("message_id")
            or nested.get("message_id")
            or row.get("id")
            or hash(line)
        )

    def _normalize_row(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Плоская строка inbox или audit {signal, review, action}."""
        if row.get("symbol") and row.get("side"):
            return dict(row)
        nested = row.get("signal")
        review = row.get("review") if isinstance(row.get("review"), dict) else {}
        action = str(row.get("action", ""))
        if not isinstance(nested, dict):
            return None
        if self._only_approved:
            if action and action not in _INBOX_ACTIONS:
                return None
            if not bool(review.get("approve")):
                return None
        sym = str(nested.get("symbol", "")).upper()
        side_raw = str(nested.get("side", "")).upper()
        if not sym or side_raw not in ("BUY", "SELL", "LONG", "SHORT"):
            return None
        side = "Buy" if side_raw in ("BUY", "LONG") else "Sell"
        conf_raw = float(review.get("confidence", nested.get("confidence", 0)) or 0)
        conf = conf_raw / 100.0 if conf_raw > 1 else conf_raw
        if conf < self._min_conf:
            return None
        return {
            "symbol": sym,
            "side": side,
            "confidence": conf,
            "entry": float(nested.get("entry", 0) or 0),
            "stop_loss": float(nested.get("stop_loss", 0) or 0),
            "take_profit": float(nested.get("take_profit", 0) or 0),
            "channel": nested.get("source") or row.get("channel"),
            "message_id": nested.get("message_id"),
            "reason": str(review.get("reason", nested.get("reason", "")))[:200],
        }

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
        if float(parsed.get("confidence", 0.72)) < self._min_conf:
            return None
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
            uid = self._row_uid(row, line)
            if uid in self._seen_ids:
                continue
            self._seen_ids.add(uid)
            norm = self._normalize_row(row)
            if norm:
                out.append(norm)
                continue
            text = row.get("text") or row.get("message") or row.get("raw_text") or ""
            nested = row.get("signal") if isinstance(row.get("signal"), dict) else {}
            if nested:
                text = str(nested.get("raw_text", "")) or text
            parsed = self._parse_text_signal(str(text))
            if parsed:
                parsed["channel"] = row.get("channel") or nested.get("source") or row.get("chat_title")
                out.append(parsed)
        return out
