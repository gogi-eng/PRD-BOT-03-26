"""Журнал цены после крупной ликвидации.

Печать от 10000 USDT записывается сразу. Позже в ту же строку
дописывается, куда ушла цена через 1, 5 и 15 минут.
Ордер из этой строки не ставится. Торгового сигнала здесь нет.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger("prd_agent.bybit")

LARGE_LIQUIDATION_USDT = 10_000.0
HORIZONS: tuple[tuple[str, int], ...] = (("1m", 1), ("5m", 5), ("15m", 15))
LATE_GRACE_MS = 45_000
LATE_GIVE_UP_TRIES = 4
_NOTE = "Журнал только для разбора. Из этой строки ордер не ставится."


def is_large_liquidation(notional: Any, threshold: float = LARGE_LIQUIDATION_USDT) -> bool:
    """Порог включительный: 10000 USDT уже считается крупной печатью."""
    try:
        value = float(notional)
        floor = float(threshold)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(value) or not math.isfinite(floor):
        return False
    return value >= floor


def positive_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def canonical_side(side: Any) -> str:
    text = str(side or "").strip().lower()
    if text in ("buy", "long"):
        return "Buy"
    if text in ("sell", "short"):
        return "Sell"
    return ""


def event_time_ms(raw: Any) -> int:
    try:
        ts = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    if ts < 0:
        return 0
    if 0 < ts < 10**12:
        return ts * 1000
    return ts


def move_pct(price_then: float, price_later: float) -> float | None:
    then = positive_price(price_then)
    later = positive_price(price_later)
    if then is None or later is None:
        return None
    return (later - then) / then * 100.0


def move_direction(pct: float) -> str:
    if pct > 0:
        return "up"
    if pct < 0:
        return "down"
    return "flat"


def outcome_vs_print(side: str, direction: str) -> str:
    """Buy в ленте: ликвидировали лонги, продолжение — цена ниже.

    Sell в ленте: ликвидировали шорты, продолжение — цена выше.
    """
    if direction == "flat":
        return "flat"
    canon = canonical_side(side)
    if canon == "":
        return "unknown"
    if direction == "up":
        continued = canon == "Sell"
    elif direction == "down":
        continued = canon == "Buy"
    else:
        raise ValueError(f"неизвестное направление цены: {direction}")
    if continued:
        return "continued"
    return "reversed"


def describe_horizon(minutes: int, direction: str, pct: float, vs_print: str) -> str:
    if direction == "flat":
        return f"через {minutes} мин цена на месте"
    if direction == "up":
        where = "выше"
    elif direction == "down":
        where = "ниже"
    else:
        raise ValueError(f"неизвестное направление цены: {direction}")
    if vs_print == "continued":
        what = "продолжение"
    elif vs_print == "reversed":
        what = "разворот"
    elif vs_print == "unknown":
        what = "сторона ленты неясна"
    elif vs_print == "flat":
        return f"через {minutes} мин цена на месте"
    else:
        raise ValueError(f"неизвестный итог окна: {vs_print}")
    return f"через {minutes} мин цена {where} на {abs(pct):.4f}%, {what}"


def close_at(
    candles: list[tuple[int, float]] | None,
    ts_ms: int,
    bar_ms: int = 60_000,
) -> float | None:
    """Закрытие той минутной свечи, внутри которой лежит нужное время.

    Соседняя свеча не подставляется: для разбора нужна цена именно этого окна.
    """
    if not candles or ts_ms <= 0 or bar_ms <= 0:
        return None
    for start, close in sorted(candles, key=lambda item: item[0]):
        price = positive_price(close)
        if price is None:
            continue
        if start <= ts_ms < start + bar_ms:
            return price
        if start > ts_ms:
            break
    return None


def followup_paths(liquidation_log: Path) -> tuple[Path, Path]:
    log = Path(liquidation_log)
    return (
        log.with_name("liquidation_followups.jsonl"),
        log.with_name("liquidation_followup_summary.txt"),
    )


def _empty_counts() -> dict[str, int]:
    return {"continued": 0, "reversed": 0, "flat": 0, "wait": 0, "missed": 0, "unknown": 0}


def summary_counts(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    sides: dict[str, dict[str, dict[str, int]]] = {
        "Buy": {key: _empty_counts() for key, _minutes in HORIZONS},
        "Sell": {key: _empty_counts() for key, _minutes in HORIZONS},
    }
    for row in rows:
        side = canonical_side(row.get("S")) or "?"
        bucket = sides.setdefault(side, {key: _empty_counts() for key, _minutes in HORIZONS})
        horizons = row.get("horizons") or {}
        if not isinstance(horizons, Mapping):
            continue
        for key, _minutes in HORIZONS:
            slot = horizons.get(key) or {}
            if not isinstance(slot, Mapping):
                continue
            counts = bucket[key]
            status = str(slot.get("status") or "wait")
            if status == "wait":
                counts["wait"] += 1
                continue
            if status == "missed":
                counts["missed"] += 1
                continue
            if status != "filled":
                counts["unknown"] += 1
                continue
            outcome = str(slot.get("vs_print") or "")
            if outcome in ("continued", "reversed", "flat", "unknown"):
                counts[outcome] += 1
            else:
                counts["unknown"] += 1
    return {"n": len(rows), "sides": sides}


def render_summary(rows: list[Mapping[str, Any]], threshold: float = LARGE_LIQUIDATION_USDT) -> str:
    counts = summary_counts(rows)
    if float(threshold).is_integer():
        floor = str(int(threshold))
    else:
        floor = str(threshold)
    lines = [
        f"Крупные ликвидации от {floor} USDT.",
        _NOTE,
        "Buy в ленте: ликвидировали лонги. Продолжение — цена ниже. Разворот — цена выше.",
        "Sell в ленте: ликвидировали шорты. Продолжение — цена выше. Разворот — цена ниже.",
        "",
        f"Всего записей: {counts['n']}",
    ]
    titles = {"Buy": "Buy", "Sell": "Sell", "?": "Сторона не указана"}
    ordered = ["Buy", "Sell"]
    extra = [name for name in counts["sides"] if name not in ("Buy", "Sell")]
    for side in ordered + extra:
        title = titles.get(side, f"Сторона {side}")
        side_n = 0
        for row in rows:
            name = canonical_side(row.get("S")) or "?"
            if name == side:
                side_n += 1
        lines.append("")
        lines.append(f"{title}. Записей: {side_n}.")
        for key, minutes in HORIZONS:
            item = counts["sides"][side][key]
            lines.append(
                f"  {minutes} мин: продолжение {item['continued']}, "
                f"разворот {item['reversed']}, без хода {item['flat']}, "
                f"ещё ждём {item['wait']}, нет цены {item['missed']}"
            )
    lines.append("")
    return "\n".join(lines)


def _record_id(symbol: str, ts_ms: int, side: str, price: float, size: float) -> str:
    return f"{symbol}|{ts_ms}|{side}|{price!r}|{size!r}"


def _new_record(row: Mapping[str, Any], *, threshold: float) -> dict[str, Any] | None:
    notional_raw = row.get("notional")
    if notional_raw is None:
        price_guess = positive_price(row.get("p"))
        size_guess = positive_price(row.get("v"))
        notional_raw = (price_guess or 0.0) * (size_guess or 0.0)
    if not is_large_liquidation(notional_raw, threshold):
        return None
    price = positive_price(row.get("p"))
    if price is None:
        return None
    try:
        size = float(row.get("v") or 0.0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(size) or size < 0:
        return None
    ts_ms = event_time_ms(row.get("T"))
    if ts_ms <= 0:
        return None
    side_raw = str(row.get("S") or "")
    side = canonical_side(side_raw) or side_raw
    symbol = str(row.get("symbol") or "").upper()
    if not symbol:
        return None
    notional = float(notional_raw)
    horizons: dict[str, Any] = {}
    for key, minutes in HORIZONS:
        horizons[key] = {
            "minutes": minutes,
            "due_ms": ts_ms + minutes * 60_000,
            "status": "wait",
            "price": None,
            "direction": None,
            "pct": None,
            "vs_print": None,
            "source": None,
            "reading": None,
            "attempts": 0,
        }
    return {
        "id": _record_id(symbol, ts_ms, side, price, size),
        "symbol": symbol,
        "S": side,
        "v": size,
        "p": price,
        "T": ts_ms,
        "notional": notional,
        "price_then": price,
        "threshold_usdt": float(threshold),
        "note": _NOTE,
        "horizons": horizons,
    }


class LiquidationFollowupJournal:
    """Один файл, одна строка на крупную печать. Позже строка дополняется."""

    def __init__(
        self,
        path: Path,
        summary_path: Path | None = None,
        *,
        threshold: float = LARGE_LIQUIDATION_USDT,
    ) -> None:
        self.path = Path(path)
        self.summary_path = (
            Path(summary_path) if summary_path is not None else self.path.with_name("liquidation_followup_summary.txt")
        )
        self.threshold = float(threshold)
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._loaded = False

    @classmethod
    def beside(cls, liquidation_log: Path) -> LiquidationFollowupJournal:
        path, summary = followup_paths(liquidation_log)
        return cls(path, summary)

    def note_print(self, row: Mapping[str, Any]) -> dict[str, Any] | None:
        """Записать цену печати. Ниже порога — ничего. Ордер не ставится."""
        fresh = _new_record(row, threshold=self.threshold)
        if fresh is None:
            return None
        with self._lock:
            self._load()
            if any(item.get("id") == fresh["id"] for item in self._rows):
                return None
            self._rows.append(fresh)
            self._dump()
        logger.info(
            "крупная ликвидация %s %s %.0f USDT по цене %s. Цену дальше записываю. %s",
            fresh["symbol"],
            fresh["S"],
            fresh["notional"],
            fresh["price_then"],
            _NOTE,
        )
        return fresh

    def due_slots(self, now_ms: int, grace_ms: int = LATE_GRACE_MS) -> list[dict[str, Any]]:
        with self._lock:
            self._load()
            found: list[dict[str, Any]] = []
            for row in self._rows:
                horizons = row.get("horizons") or {}
                for key, _minutes in HORIZONS:
                    slot = horizons.get(key) or {}
                    if slot.get("status") != "wait":
                        continue
                    due = int(slot.get("due_ms") or 0)
                    if now_ms < due:
                        continue
                    found.append(
                        {
                            "id": row.get("id"),
                            "symbol": row.get("symbol"),
                            "key": key,
                            "due_ms": due,
                            "late": now_ms > due + int(grace_ms),
                        }
                    )
            return found

    def has_pending(self) -> bool:
        with self._lock:
            self._load()
            for row in self._rows:
                horizons = row.get("horizons") or {}
                for key, _minutes in HORIZONS:
                    slot = horizons.get(key) or {}
                    if slot.get("status") == "wait":
                        return True
            return False

    def seconds_until_next(self, now_ms: int) -> float | None:
        with self._lock:
            self._load()
            nearest: int | None = None
            for row in self._rows:
                horizons = row.get("horizons") or {}
                for key, _minutes in HORIZONS:
                    slot = horizons.get(key) or {}
                    if slot.get("status") != "wait":
                        continue
                    due = int(slot.get("due_ms") or 0)
                    if nearest is None or due < nearest:
                        nearest = due
            if nearest is None:
                return None
            return max(0.0, (nearest - int(now_ms)) / 1000.0)

    def fill(
        self,
        record_id: str,
        key: str,
        price_later: float,
        now_ms: int,
        *,
        source: str,
    ) -> bool:
        """Дописать цену окна, если его время уже наступило и строка ещё ждёт."""
        later = positive_price(price_later)
        if later is None or key not in {name for name, _minutes in HORIZONS}:
            return False
        with self._lock:
            self._load()
            slot, row = self._waiting_slot(record_id, key)
            if slot is None or row is None:
                return False
            due = int(slot.get("due_ms") or 0)
            if int(now_ms) < due:
                return False
            then = positive_price(row.get("price_then"))
            if then is None:
                return False
            pct = move_pct(then, later)
            if pct is None:
                return False
            pct = round(pct, 6)
            direction = move_direction(pct)
            vs_print = outcome_vs_print(str(row.get("S") or ""), direction)
            minutes = int(slot.get("minutes") or 0)
            slot["status"] = "filled"
            slot["price"] = later
            slot["direction"] = direction
            slot["pct"] = pct
            slot["vs_print"] = vs_print
            slot["source"] = str(source or "")
            slot["reading"] = describe_horizon(minutes, direction, pct, vs_print)
            self._dump()
            return True

    def note_late_miss(self, record_id: str, key: str, *, give_up_after: int = LATE_GIVE_UP_TRIES) -> bool:
        """Поздняя цена не нашлась. После нескольких попыток окно закрывается как «нет цены»."""
        with self._lock:
            self._load()
            slot, _row = self._waiting_slot(record_id, key)
            if slot is None:
                return False
            attempts = int(slot.get("attempts") or 0) + 1
            slot["attempts"] = attempts
            if attempts >= int(give_up_after):
                slot["status"] = "missed"
            self._dump()
            return slot.get("status") == "missed"

    def summary_text(self) -> str:
        with self._lock:
            self._load()
            return render_summary(self._rows, self.threshold)

    def _waiting_slot(self, record_id: str, key: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        for row in self._rows:
            if row.get("id") != record_id:
                continue
            horizons = row.get("horizons") or {}
            slot = horizons.get(key)
            if isinstance(slot, dict) and slot.get("status") == "wait":
                return slot, row
            return None, row
        return None, None

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._rows = []
        if not self.path.is_file():
            return
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("liquidation follow-up read: %s", exc)
            return
        for line in text.splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("id"):
                self._rows.append(item)

    def _dump(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self._rows)
            self._replace(self.path, body)
            self._replace(self.summary_path, render_summary(self._rows, self.threshold))
        except OSError as exc:
            logger.warning("liquidation follow-up write: %s", exc)

    @staticmethod
    def _replace(path: Path, text: str) -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
