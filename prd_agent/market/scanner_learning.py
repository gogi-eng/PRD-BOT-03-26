"""
Уроки MARKET/SPIKE SCANNER по факту закрытой сделки.

Раньше событие писалось только если цена гипотетического сетапа из
pending_market_setups доходила до цели или отмены. Закрытые сделки сканера
в этот список не попадали, поэтому после 7 июля 2026 журнал уроков замер
на летней выборке, а адаптивный фильтр остался в режиме stricter.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows
    fcntl = None  # type: ignore

from prd_agent.positions.bot_position_registry import load_registry

SCANNER_TRADE_SOURCES = frozenset({"MARKET_SCANNER", "SPIKE_SCANNER"})


@contextmanager
def state_file_lock(path: Path) -> Iterator[None]:
    """Один писатель файла состояния на все процессы. Иначе урок сканера затирается."""
    if fcntl is None:
        yield
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / (path.name + ".lock")
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def is_scanner_trade_source(source: Any) -> bool:
    return str(source or "").strip().upper() in SCANNER_TRADE_SOURCES


def learning_event_key(item: Mapping[str, Any]) -> str:
    explicit = str(item.get("id") or "").strip()
    if explicit:
        return explicit
    return (
        f"{item.get('checked_at')}:{item.get('symbol')}:"
        f"{item.get('scenario')}:{item.get('outcome')}"
    )


def outcome_from_pnl(pnl: float) -> str:
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "neutral"


def scenario_from_side(side: str) -> str:
    raw = str(side or "").strip().upper()
    if raw in {"BUY", "LONG"}:
        return "PUMP"
    if raw in {"SELL", "SHORT"}:
        return "DUMP"
    return ""


def _as_event_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def merge_learning_events(
    memory: Any,
    disk: Any,
    cap: int,
) -> list[dict[str, Any]]:
    """Диск и память вместе. Старые уроки не выбрасываются, повтор по id не пишется."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _as_event_list(disk) + _as_event_list(memory):
        key = learning_event_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(dict(item))
    if cap > 0 and len(merged) > cap:
        return merged[-cap:]
    return merged


def prefer_newer_filters(memory: Any, disk: Any) -> Any:
    """Если на диске фильтр новее, не затирать его старой копией из памяти."""
    if not isinstance(disk, dict) or not disk:
        return memory if isinstance(memory, dict) else disk
    if not isinstance(memory, dict) or not memory:
        return disk
    if str(disk.get("updated_at") or "") > str(memory.get("updated_at") or ""):
        return disk
    return memory


def learning_events_cap(learning_window: int) -> int:
    return max(50, int(learning_window) * 4)


def append_closed_scanner_learning_event(
    state: dict[str, Any],
    *,
    source: str,
    symbol: str,
    pnl: float,
    order_id: str = "",
    side: str = "",
    entry_price: float = 0.0,
    exit_price: float = 0.0,
    score: float = 0.0,
    range_pct: float = 0.0,
    atr_pct: float = 0.0,
    volume_ratio: float = 0.0,
    filters: Optional[Mapping[str, Any]] = None,
    checked_at: str = "",
    cap: int = 50,
) -> Optional[dict[str, Any]]:
    """Добавляет урок по закрытой сделке сканера. Чужой источник — ничего не пишет."""
    if not is_scanner_trade_source(source):
        return None
    sym = str(symbol or "").upper().strip()
    if not sym:
        return None
    events = state.get("market_scanner_learning_events")
    if not isinstance(events, list):
        events = []
        state["market_scanner_learning_events"] = events
    when = checked_at or datetime.now(timezone.utc).isoformat()
    oid = str(order_id or "").strip()
    event_id = f"closed:{oid}" if oid else f"closed:{sym}:{when}"
    if any(isinstance(item, dict) and learning_event_key(item) == event_id for item in events):
        return None
    outcome = outcome_from_pnl(float(pnl))
    event = {
        "checked_at": when,
        "id": event_id,
        "symbol": sym,
        "scenario": scenario_from_side(side),
        "entry_price": float(entry_price or 0),
        "current_price": float(exit_price or 0),
        "target": 0.0,
        "invalidation": 0.0,
        "score": float(score or 0),
        "range_pct": float(range_pct or 0),
        "atr_pct": float(atr_pct or 0),
        "volume_ratio": float(volume_ratio or 0),
        "outcome": outcome,
        "pnl": float(pnl),
        "source": str(source).strip().upper(),
        "order_id": oid,
        "kind": "closed_trade",
        "filters": dict(filters or {}),
    }
    events.append(event)
    if cap > 0:
        state["market_scanner_learning_events"] = events[-cap:]
    return event


def decided_window(events: Any, learning_window: int) -> list[dict[str, Any]]:
    rows = _as_event_list(events)
    window = rows[-max(1, int(learning_window)) :]
    return [item for item in window if item.get("outcome") in {"win", "loss"}]


def window_signature(decided: list[dict[str, Any]]) -> str:
    return "|".join(learning_event_key(item) for item in decided)


def stale_stricter_sample_still_in_window(
    current_filters: Any,
    decided: list[dict[str, Any]],
) -> bool:
    """Летняя выборка ещё целиком внутри окна — режим stricter не трогаем."""
    if not isinstance(current_filters, dict):
        return False
    if str(current_filters.get("mode") or "") != "stricter":
        return False
    signature = str(current_filters.get("learning_window_signature") or "")
    if not signature:
        sample = int(current_filters.get("sample") or 0)
        return sample > 0 and len(decided) <= sample
    stale_ids = [part for part in signature.split("|") if part]
    if not stale_ids:
        return False
    window_ids = {learning_event_key(item) for item in decided}
    return all(item_id in window_ids for item_id in stale_ids)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _threshold_dict(
    *,
    min_score: float,
    min_volume_ratio: float,
    max_range_pct: float,
    max_atr_pct: float,
    bounds: Mapping[str, float],
) -> dict[str, float]:
    return {
        "min_score_to_notify": int(
            _clamp(min_score, bounds["score_floor"], bounds["score_ceiling"])
        ),
        "min_volume_ratio": _clamp(
            min_volume_ratio, bounds["vol_floor"], bounds["vol_ceiling"]
        ),
        "max_range_pct": _clamp(
            max_range_pct, bounds["range_floor"], bounds["range_ceiling"]
        ),
        "max_atr_pct": _clamp(max_atr_pct, bounds["atr_floor"], bounds["atr_ceiling"]),
    }


def propose_adaptive_filters(
    *,
    events: Any,
    learning_window: int,
    min_events: int,
    baseline: Mapping[str, Any],
    current_filters: Any,
    bounds: Mapping[str, float],
    updated_at: str = "",
) -> Optional[dict[str, Any]]:
    """
    Новое окно, в котором летней выборки уже нет, может уйти из stricter.

    Пока все id той выборки ещё в окне, сохранённые пороги не пересчитываются.
    """
    decided = decided_window(events, learning_window)
    if len(decided) < int(min_events):
        return None
    signature = window_signature(decided)
    stored = current_filters if isinstance(current_filters, dict) else {}
    if stored.get("learning_window_signature") == signature:
        return None
    if stale_stricter_sample_still_in_window(stored, decided):
        return None

    wins = sum(1 for item in decided if item.get("outcome") == "win")
    losses = sum(1 for item in decided if item.get("outcome") == "loss")
    winrate = wins / max(1, wins + losses)
    base_score = float(baseline.get("min_score_to_notify") or 0)
    base_vol = float(baseline.get("min_volume_ratio") or 0)
    base_range = float(baseline.get("max_range_pct") or 0)
    base_atr = float(baseline.get("max_atr_pct") or 0)

    if winrate < 0.40 and losses >= 3:
        mode = "stricter"
        thresholds = _threshold_dict(
            min_score=base_score + 3,
            min_volume_ratio=base_vol + 0.10,
            max_range_pct=base_range - 0.25,
            max_atr_pct=base_atr - 0.04,
            bounds=bounds,
        )
    elif winrate > 0.62 and wins >= 4:
        mode = "softer"
        thresholds = _threshold_dict(
            min_score=base_score - 1,
            min_volume_ratio=base_vol - 0.03,
            max_range_pct=base_range + 0.10,
            max_atr_pct=base_atr,
            bounds=bounds,
        )
    else:
        mode = "baseline"
        thresholds = _threshold_dict(
            min_score=base_score,
            min_volume_ratio=base_vol,
            max_range_pct=base_range,
            max_atr_pct=base_atr,
            bounds=bounds,
        )

    proposed = dict(thresholds)
    proposed.update(
        {
            "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "sample": len(decided),
            "wins": wins,
            "losses": losses,
            "winrate": round(winrate, 4),
            "learning_window_signature": signature,
        }
    )
    return proposed


def lookup_scanner_entry_from_journal(
    journal_path: Path,
    symbol: str,
    order_id: str = "",
) -> Optional[dict[str, Any]]:
    """Вход сканера с диска. Память журнала другого процесса его не видит."""
    path = Path(journal_path)
    if not path.exists():
        return None
    sym = str(symbol or "").upper()
    oid = str(order_id or "").strip()
    if not sym:
        return None
    latest: Optional[dict[str, Any]] = None
    matched: Optional[dict[str, Any]] = None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("event") or "").lower() != "entered":
            continue
        if str(row.get("symbol") or "").upper() != sym:
            continue
        if not is_scanner_trade_source(row.get("source")):
            continue
        latest = row
        row_oid = str(row.get("order_id") or "").strip()
        if oid and row_oid and row_oid == oid:
            matched = row
    return matched or latest


def lookup_scanner_source_from_registry(data_dir: Path, symbol: str) -> str:
    data = load_registry(Path(data_dir))
    symbols = data.get("symbols")
    if not isinstance(symbols, dict):
        return ""
    row = symbols.get(str(symbol or "").upper())
    if not isinstance(row, dict):
        return ""
    source = str(row.get("source") or "")
    return source if is_scanner_trade_source(source) else ""


def record_closed_trade_lesson(
    state: dict[str, Any],
    *,
    source: str,
    symbol: str,
    pnl: float,
    order_id: str = "",
    side: str = "",
    entry_price: float = 0.0,
    exit_price: float = 0.0,
    score: float = 0.0,
    range_pct: float = 0.0,
    atr_pct: float = 0.0,
    volume_ratio: float = 0.0,
    filters: Optional[Mapping[str, Any]] = None,
    learning_window: int = 30,
    min_events: int = 6,
    baseline: Optional[Mapping[str, Any]] = None,
    bounds: Optional[Mapping[str, float]] = None,
    updated_at: str = "",
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Пишет урок и, если окно уже не летнее, предлагает новые пороги."""
    event = append_closed_scanner_learning_event(
        state,
        source=source,
        symbol=symbol,
        pnl=pnl,
        order_id=order_id,
        side=side,
        entry_price=entry_price,
        exit_price=exit_price,
        score=score,
        range_pct=range_pct,
        atr_pct=atr_pct,
        volume_ratio=volume_ratio,
        filters=filters,
        cap=learning_events_cap(learning_window),
    )
    if event is None or baseline is None or bounds is None:
        return event, None
    proposed = propose_adaptive_filters(
        events=state.get("market_scanner_learning_events"),
        learning_window=learning_window,
        min_events=min_events,
        baseline=baseline,
        current_filters=state.get("market_scanner_adaptive_filters"),
        bounds=bounds,
        updated_at=updated_at,
    )
    if proposed is not None:
        state["market_scanner_adaptive_filters"] = proposed
    return event, proposed


def resolve_scanner_close_source(
    pending: Any,
    journal_path: Path,
    data_dir: Path,
    symbol: str,
    order_id: str = "",
) -> tuple[str, dict[str, Any]]:
    """Источник закрытия: память журнала, потом файл журнала, потом реестр бота."""
    meta = dict(pending) if isinstance(pending, dict) else {}
    source = str(meta.get("source") or "")
    if is_scanner_trade_source(source):
        return source.strip().upper(), meta
    found = lookup_scanner_entry_from_journal(journal_path, symbol, order_id)
    if found is not None:
        return str(found.get("source") or "").strip().upper(), found
    registered = lookup_scanner_source_from_registry(data_dir, symbol)
    if registered:
        meta = dict(meta)
        meta["source"] = registered
        return registered, meta
    return "", meta
