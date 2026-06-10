"""Общий реестр символов, открытых ботом (orchestrator + telegram_signal_agent)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
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
    symbols[sym] = {
        "stop_loss": float(stop_loss or 0),
        "take_profit": float(take_profit or 0),
        "opened_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(source or ""),
        "pump_dump": bool(pump_dump),
    }
    save_registry(data_dir, data)


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


def reconcile_registry_with_exchange(
    data_dir: Path,
    live_symbols: Iterable[str],
    *,
    journal_path: Optional[Path] = None,
) -> List[str]:
    """
    Удаляет из реестра символы, которых нет на бирже и нет открытой записи в журнале.
    Возвращает список удалённых символов.
    """
    live = {str(s).upper() for s in live_symbols if str(s).strip()}
    journal_open = symbols_open_in_journal(journal_path) if journal_path else set()
    data = load_registry(data_dir)
    symbols = data.get("symbols")
    if not isinstance(symbols, dict):
        return []
    removed: List[str] = []
    for sym in list(symbols.keys()):
        sym_u = str(sym).upper()
        if sym_u in live:
            continue
        if sym_u in journal_open:
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
