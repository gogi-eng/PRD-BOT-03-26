"""Общий реестр символов, открытых ботом (orchestrator + telegram_signal_agent)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger("prd_agent.positions.registry")

_REGISTRY_FILE = "bot_open_symbols.json"
_SUCCESS_ACTIONS = frozenset({"executed", "scanner_executed"})


def registry_path(data_dir: Path) -> Path:
    return Path(data_dir) / _REGISTRY_FILE


def _empty() -> Dict[str, Any]:
    return {"symbols": {}, "updated_at": ""}


def load_registry(data_dir: Path) -> Dict[str, Any]:
    path = registry_path(data_dir)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    symbols = data.get("symbols")
    if not isinstance(symbols, dict):
        data["symbols"] = {}
    return data


def save_registry(data_dir: Path, data: Dict[str, Any]) -> None:
    path = registry_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def register_bot_open(
    data_dir: Path,
    symbol: str,
    *,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
    source: str = "",
    pump_dump: bool = False,
) -> None:
    sym = str(symbol or "").upper()
    if not sym:
        return
    data = load_registry(data_dir)
    symbols = data.setdefault("symbols", {})
    if not isinstance(symbols, dict):
        symbols = {}
        data["symbols"] = symbols
    opened_at = datetime.now(timezone.utc).isoformat()
    symbols[sym] = {
        "stop_loss": float(stop_loss or 0),
        "take_profit": float(take_profit or 0),
        "opened_at_utc": opened_at,
        "source": str(source or ""),
        "pump_dump": bool(pump_dump),
    }
    save_registry(data_dir, data)
    _append_bot_trade_log(data_dir, sym, opened_at=opened_at, source=source)


def _bot_trade_log_path(data_dir: Path) -> Path:
    return Path(data_dir) / "bot_trade_log.jsonl"


def _append_bot_trade_log(data_dir: Path, symbol: str, *, opened_at: str, source: str = "") -> None:
    path = _bot_trade_log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "symbol": str(symbol or "").upper(),
        "opened_at_utc": opened_at,
        "source": str(source or ""),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def bot_symbols_from_trade_log(data_dir: Path, *, within_hours: float = 168.0) -> Set[str]:
    """Символы, которые бот открывал за последние N часов (журнал register_bot_open)."""
    path = _bot_trade_log_path(data_dir)
    if not path.exists():
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    out: Set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sym = str(row.get("symbol", "") or "").upper()
            if not sym:
                continue
            raw_ts = str(row.get("opened_at_utc", "") or "")
            try:
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            except ValueError:
                out.add(sym)
                continue
            if ts >= cutoff:
                out.add(sym)
    except OSError:
        return set()
    return out


def unregister_bot_symbol(data_dir: Path, symbol: str) -> None:
    sym = str(symbol or "").upper()
    if not sym:
        return
    data = load_registry(data_dir)
    symbols = data.get("symbols")
    if not isinstance(symbols, dict) or sym not in symbols:
        return
    symbols.pop(sym, None)
    save_registry(data_dir, data)


def bot_symbols_from_registry(data_dir: Path) -> Set[str]:
    data = load_registry(data_dir)
    symbols = data.get("symbols") or {}
    if not isinstance(symbols, dict):
        return set()
    return {str(s).upper() for s in symbols.keys() if str(s).strip()}


def bot_levels_from_registry(data_dir: Path) -> Dict[str, Dict[str, float]]:
    data = load_registry(data_dir)
    symbols = data.get("symbols") or {}
    if not isinstance(symbols, dict):
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for sym, row in symbols.items():
        if not isinstance(row, dict):
            continue
        out[str(sym).upper()] = {
            "stop_loss": float(row.get("stop_loss", 0) or 0),
            "take_profit": float(row.get("take_profit", 0) or 0),
            "opened_at_utc": str(row.get("opened_at_utc") or ""),
        }
    return out


def symbols_open_in_journal(journal_path: Path) -> Set[str]:
    if not journal_path.exists():
        return set()
    open_syms: Dict[str, str] = {}
    try:
        for line in journal_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sym = str(row.get("symbol", "") or "").upper()
            if not sym:
                continue
            event = str(row.get("event", "") or "").lower()
            if event == "entered":
                open_syms[sym] = "entered"
            elif event in {"closed", "exit", "closed_exchange"}:
                open_syms.pop(sym, None)
    except OSError:
        return set()
    return set(open_syms.keys())


def symbols_from_telegram_audit(audit_path: Path) -> Set[str]:
    """Последний успешный executed/scanner_executed по символу."""
    if not audit_path.exists():
        return set()
    last_ok: Dict[str, bool] = {}
    try:
        for line in audit_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            action = str(row.get("action", "") or "")
            if action not in _SUCCESS_ACTIONS:
                continue
            sig = row.get("signal") if isinstance(row.get("signal"), dict) else {}
            sym = str(sig.get("symbol", "") or "").upper()
            if not sym:
                continue
            result = row.get("execution_result") if isinstance(row.get("execution_result"), dict) else {}
            last_ok[sym] = bool(result.get("success"))
    except OSError:
        return set()
    return {sym for sym, ok in last_ok.items() if ok}


def close_journal_ghosts(
    journal_path: Path,
    live_symbols: Iterable[str],
    *,
    protect_symbols: Optional[Iterable[str]] = None,
) -> List[str]:
    """
    Закрывает в журнале «зависшие» entered без closed, если позиции нет на бирже.
    Символы из protect_symbols (активное сопровождение) не трогаем.
    """
    if not journal_path.exists():
        return []
    live = {str(s).upper() for s in live_symbols if str(s).strip()}
    protect = {str(s).upper() for s in (protect_symbols or []) if str(s).strip()}
    open_syms = symbols_open_in_journal(journal_path)
    ghosts = sorted(sym for sym in open_syms if sym not in live and sym not in protect)
    if not ghosts:
        return []
    now = datetime.now(timezone.utc).isoformat()
    try:
        with journal_path.open("a", encoding="utf-8") as f:
            for sym in ghosts:
                row = {
                    "event": "closed",
                    "symbol": sym,
                    "reason": "sync_ghost_cleanup",
                    "pnl": 0.0,
                    "source": "sync",
                    "ts": now,
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return []
    logger.info("Journal ghost cleanup: closed %d stale open(s)", len(ghosts))
    return ghosts


def reconcile_registry_with_exchange(
    data_dir: Path,
    live_symbols: Iterable[str],
    *,
    journal_path: Optional[Path] = None,
) -> List[str]:
    """
    Удаляет из реестра символы, которых нет на бирже.
    Возвращает список удалённых символов.
    """
    live = {str(s).upper() for s in live_symbols if str(s).strip()}
    data = load_registry(data_dir)
    symbols = data.get("symbols")
    if not isinstance(symbols, dict):
        return []
    removed: List[str] = []
    for sym in list(symbols.keys()):
        sym_u = str(sym).upper()
        if sym_u in live:
            continue
        symbols.pop(sym, None)
        removed.append(sym_u)
    if removed:
        save_registry(data_dir, data)
        logger.info("Registry reconcile: removed %d stale symbol(s)", len(removed))
    return removed


def merge_open_sources(
    data_dir: Path,
    *,
    journal_path: Optional[Path] = None,
    telegram_audit_path: Optional[Path] = None,
    include_telegram_audit: bool = False,
) -> Set[str]:
    """Объединяет реестр + журнал (audit Telegram — только если явно включён)."""
    found: Set[str] = set(bot_symbols_from_registry(data_dir))
    if journal_path:
        found |= symbols_open_in_journal(journal_path)
    if include_telegram_audit and telegram_audit_path:
        found |= symbols_from_telegram_audit(telegram_audit_path)
    return found


def source_implies_bot(source: str) -> bool:
    """Источник сигнала/входа указывает на автоторговлю бота."""
    s = str(source or "").strip().lower()
    if not s:
        return False
    return s not in {"manual", "sync", "sync_ghost_cleanup", "unknown"}


def _origin_from_journal_row(row: Dict[str, Any]) -> str:
    explicit = str(row.get("origin") or "").strip().lower()
    if explicit in {"bot", "manual"}:
        return explicit
    source = str(row.get("source") or "")
    return "bot" if source_implies_bot(source) else "manual"


def origin_for_open_symbol(
    journal_path: Path,
    symbol: str,
    *,
    order_id: str = "",
) -> str:
    """
    Определяет origin открытой позиции по журналу entered/closed.
    Вызывать до удаления символа из реестра при закрытии на бирже.
    """
    sym = str(symbol or "").upper()
    if not sym or not journal_path.exists():
        return ""
    stack: Dict[str, List[str]] = {}
    matched = ""
    try:
        lines = journal_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        row_sym = str(row.get("symbol", "") or "").upper()
        if not row_sym:
            continue
        event = str(row.get("event", "") or "").lower()
        if event == "entered":
            origin = _origin_from_journal_row(row)
            stack.setdefault(row_sym, []).append(origin)
        elif event == "closed":
            oid = str(row.get("order_id", "") or "")
            if row_sym in stack and stack[row_sym]:
                closing_origin = stack[row_sym].pop()
                if order_id and oid and oid == order_id:
                    matched = closing_origin
            elif row_sym in stack:
                stack.pop(row_sym, None)
    if matched:
        return matched
    open_stack = stack.get(sym) or []
    if open_stack:
        return open_stack[-1]
    return ""


def had_bot_entered_before(
    journal_path: Path,
    symbol: str,
    before: datetime,
    *,
    lookback_hours: float = 168.0,
) -> bool:
    """Был ли bot-вход по символу в журнале незадолго до закрытия."""
    sym = str(symbol or "").upper()
    if not sym or not journal_path.exists():
        return False
    earliest = before - timedelta(hours=lookback_hours)
    try:
        lines = journal_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("event", "") or "").lower() != "entered":
            continue
        if str(row.get("symbol", "") or "").upper() != sym:
            continue
        ts_raw = str(row.get("ts", "") or "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts > before or ts < earliest:
            continue
        if _origin_from_journal_row(row) == "bot":
            return True
    return False


def resolve_closed_origin(
    data_dir: Path,
    symbol: str,
    *,
    order_id: str = "",
    journal_path: Optional[Path] = None,
    telegram_audit_path: Optional[Path] = None,
    bot_symbols: Optional[Iterable[str]] = None,
) -> str:
    """Классификация закрытой сделки: bot vs manual."""
    sym = str(symbol or "").upper()
    if not sym:
        return "bot"
    live_bot = {str(s).upper() for s in (bot_symbols or []) if str(s).strip()}
    if sym in live_bot or sym in bot_symbols_from_registry(data_dir):
        return "bot"
    if journal_path:
        from_journal = origin_for_open_symbol(journal_path, sym, order_id=order_id)
        if from_journal:
            return from_journal
    if telegram_audit_path and sym in symbols_from_telegram_audit(telegram_audit_path):
        return "bot"
    if sym in bot_symbols_from_trade_log(data_dir):
        return "bot"
    return "manual"
