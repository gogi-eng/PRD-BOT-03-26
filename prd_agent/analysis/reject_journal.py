"""Журнал отказов до ордера.

Одна jsonl-строка на каждый вход, который не ушёл в order/create.
Автоторговля не ждёт «да» человека. Ордер отсюда не ставится.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, NoReturn, Optional, Sequence

logger = logging.getLogger("prd_agent.reject_journal")

RejectSource = Literal[
    "risk_guard",
    "supervisor_v4",
    "scanner",
    "timesfm",
    "orderbook",
    "hour",
    "notional",
]
RejectVerdict = Literal["PASS", "BLOCK"]

SOURCES: tuple[RejectSource, ...] = (
    "hour",
    "notional",
    "orderbook",
    "timesfm",
    "risk_guard",
    "supervisor_v4",
    "scanner",
)
DEFAULT_FILENAME = "reject_before_order.jsonl"
MIN_NOTIONAL_USDT = 5.0
_NOTE = "Журнал отказов до ордера. Из этой строки ордер не ставится."

_HOUR_RE = re.compile(r"час\s+(\d{1,2})", re.IGNORECASE)
_LOCAL_HOUR_RE = re.compile(r"local_hour\s*=\s*(\d{1,2})", re.IGNORECASE)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _assert_never(value: NoReturn) -> NoReturn:
    raise ValueError(f"неизвестный источник отказа: {value}")


def _as_source(value: Any) -> Optional[RejectSource]:
    text = str(value or "").strip().lower().replace("-", "_")
    if text == "supervisor":
        text = "supervisor_v4"
    if text == "risk":
        text = "risk_guard"
    if text in SOURCES:
        return text  # type: ignore[return-value]
    return None


def _hour_number(reason: str) -> Optional[int]:
    text = str(reason or "")
    match = _HOUR_RE.search(text) or _LOCAL_HOUR_RE.search(text)
    if not match:
        return None
    try:
        hour = int(match.group(1))
    except (TypeError, ValueError):
        return None
    if 0 <= hour <= 23:
        return hour
    return None


def _is_hour_reason(reason: str) -> bool:
    text = str(reason or "").lower()
    if _hour_number(reason) is None:
        return False
    return (
        "заблок" in text
        or "blocked" in text
        or "local_hour" in text
        or "block_entry" in text
    )


def _is_notional_reason(reason: str) -> bool:
    text = str(reason or "").lower()
    if "110094" in text:
        return True
    if "номинал" in text and ("5" in text or "минимум" in text or "ниже" in text):
        return True
    if "min_notional" in text or "min notional" in text:
        return True
    return False


def _is_orderbook_reason(reason: str) -> bool:
    text = str(reason or "").lower()
    return "стакан" in text or "orderbook" in text or "книг" in text


def _is_timesfm_reason(reason: str) -> bool:
    text = str(reason or "").lower()
    return "timesfm" in text or text.startswith("tfm")


def _is_risk_guard_reason(reason: str) -> bool:
    text = str(reason or "").lower()
    needles = (
        "дневн",
        "лимит сделок",
        "убыточн",
        "кулдаун",
        "пауза после стопа",
        "макс. позиций",
        "макс позиций",
        "emergency",
        "kill switch",
        "auto-stop",
        "riskguard",
        "risk_guard",
        "[daily_loss]",
        "[symbol_loss]",
        "[concentration]",
        "[trade_limit]",
        "[consecutive]",
    )
    return any(item in text for item in needles)


def _is_supervisor_reason(reason: str) -> bool:
    text = str(reason or "").lower()
    return "supervisor" in text or "supervisor_v4" in text


def _is_scanner_reason(reason: str) -> bool:
    text = str(reason or "").lower()
    return "scanner" in text or "сканер" in text


def classify_source(reason: str, hinted: str = "") -> Optional[RejectSource]:
    """Сначала узкие причины (час, номинал, стакан, TimesFM), потом подсказка источника."""
    if _is_hour_reason(reason):
        return "hour"
    if _is_notional_reason(reason):
        return "notional"
    if _is_orderbook_reason(reason):
        return "orderbook"
    if _is_timesfm_reason(reason):
        return "timesfm"
    if _is_risk_guard_reason(reason):
        return "risk_guard"
    hinted_source = _as_source(hinted)
    if hinted_source is not None:
        return hinted_source
    if _is_supervisor_reason(reason):
        return "supervisor_v4"
    if _is_scanner_reason(reason):
        return "scanner"
    return None


def _strip_source_prefix(reason: str) -> str:
    text = str(reason or "").strip()
    for prefix in (
        "supervisor_v4:",
        "supervisor:",
        "quality_gate:",
        "entry_guard:",
        "timesfm:",
        "scanner:",
    ):
        if text.lower().startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def plain_reason(source: RejectSource, reason: str) -> str:
    """Короткая причина простыми словами — как в разборе trader-skill."""
    raw = _strip_source_prefix(reason)
    if source == "hour":
        hour = _hour_number(reason)
        if hour is None:
            return "час заблокирован"
        return f"час {hour} заблокирован"
    if source == "notional":
        return "номинал ниже 5 USDT"
    if source == "orderbook":
        lowered = raw.lower()
        if "пуст" in lowered:
            return "стакан пуст"
        return "стакан против"
    if source == "timesfm":
        return "TimesFM против входа"
    if source == "risk_guard":
        lowered = raw.lower()
        if "[symbol_loss]" in lowered or ("убыточн" in lowered and "монет" in lowered):
            return "замок убытков по монете"
        if "[concentration]" in lowered or "макс. позиций" in lowered or "макс позиций" in lowered:
            return "потолок концентрации"
        if any(item in lowered for item in ("дневн", "лимит сделок", "[daily_loss]", "[trade_limit]")):
            return "дневной лимит"
        return raw or "риск не пустил"
    if source == "supervisor_v4":
        return raw or "супервизор не пустил"
    if source == "scanner":
        return raw or "сканер не пустил"
    return _assert_never(source)


def _plain_reasons(
    source: RejectSource,
    reason: str,
    extra_reasons: Optional[Sequence[str]] = None,
) -> list[str]:
    primary = plain_reason(source, reason)
    out: list[str] = []
    for item in (primary, *(extra_reasons or ())):
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out or [primary]


def build_row(
    *,
    symbol: str,
    reason: str,
    mark_price: Any = 0.0,
    intended_size: Any = 0.0,
    side: str = "",
    source: RejectSource,
    ts: Optional[str] = None,
    verdict: RejectVerdict = "BLOCK",
    extra_reasons: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    stamp = ts or datetime.now(timezone.utc).isoformat()
    reasons = _plain_reasons(source, reason, extra_reasons)
    return {
        "ts": stamp,
        "symbol": str(symbol or "").upper(),
        "side": str(side or "").strip(),
        "source": source,
        "verdict": "PASS" if verdict == "PASS" else "BLOCK",
        "reason": reasons[0],
        "reasons": reasons,
        "mark_price": round(_finite_float(mark_price), 8),
        "intended_size": round(_finite_float(intended_size), 8),
        "note": _NOTE,
    }


class RejectJournal:
    """Пишет data/reject_before_order.jsonl. Ордер не ставит."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def in_data_dir(cls, data_dir: Path, filename: str = DEFAULT_FILENAME) -> "RejectJournal":
        return cls(Path(data_dir) / filename)

    def maybe_record(
        self,
        *,
        symbol: str,
        reason: str,
        mark_price: Any = 0.0,
        intended_size: Any = 0.0,
        side: str = "",
        source: str = "",
        extra: Optional[Mapping[str, Any]] = None,
        verdict: RejectVerdict = "BLOCK",
        extra_reasons: Optional[Sequence[str]] = None,
    ) -> Optional[dict[str, Any]]:
        resolved = classify_source(reason, source)
        if resolved is None:
            return None
        return self.record(
            symbol=symbol,
            reason=reason,
            mark_price=mark_price,
            intended_size=intended_size,
            side=side,
            source=resolved,
            extra=extra,
            verdict=verdict,
            extra_reasons=extra_reasons,
        )

    def record(
        self,
        *,
        symbol: str,
        reason: str,
        mark_price: Any = 0.0,
        intended_size: Any = 0.0,
        side: str = "",
        source: RejectSource,
        extra: Optional[Mapping[str, Any]] = None,
        verdict: RejectVerdict = "BLOCK",
        extra_reasons: Optional[Sequence[str]] = None,
    ) -> Optional[dict[str, Any]]:
        sym = str(symbol or "").upper().strip()
        if not sym:
            return None
        if not str(reason or "").strip():
            return None
        row = build_row(
            symbol=sym,
            reason=reason,
            mark_price=mark_price,
            intended_size=intended_size,
            side=side,
            source=source,
            verdict=verdict,
            extra_reasons=extra_reasons,
        )
        if extra:
            for key, value in extra.items():
                if key in row:
                    continue
                row[key] = value
        try:
            self._append(row)
        except OSError as exc:
            logger.warning("reject journal write %s: %s", sym, exc)
            return None
        return row

    def _append(self, row: dict[str, Any]) -> None:
        payload = json.dumps(row, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
