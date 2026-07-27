"""
Политика закрытия по обратному сигналу (opposite_signal_exit).

Кейс DEXE 24.07: SPIKE открыл SELL, own_multi_agent дал Buy → принудительный EXIT в минус.
Правило: не закрывать SPIKE-позицию, если обратный сигнал от own-агентов.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from prd_agent.positions.bot_position_registry import load_registry

# Источники входа SPIKE / spike_scalp (как в register_bot_open / journal).
_SPIKE_SOURCES = frozenset(
    {
        "SPIKE_SCANNER",
        "SPIKE",
        "spike_scalp",
        "spike",
        "pump_dump_spike",
    }
)

# Обратные сигналы «своих» агентов — не должны сносить SPIKE.
_OWN_SIGNAL_SOURCES = frozenset(
    {
        "own_multi_agent",
        "own_agent",
        "multi_agent",
        "hybrid",
    }
)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def is_spike_position_source(source: str, *, pump_dump: bool = False) -> bool:
    """Открытая позиция считается SPIKE по source (и pump_dump+spike в тексте)."""
    raw = str(source or "").strip()
    if not raw:
        return False
    if raw.upper() in {"SPIKE_SCANNER", "SPIKE"}:
        return True
    low = raw.lower()
    if low in {s.lower() for s in _SPIKE_SOURCES}:
        return True
    if "spike_scanner" in low or "spike_scalp" in low or "pump_dump_spike" in low:
        return True
    # register_bot_open(..., pump_dump=True) для SPIKE — но один pump_dump без spike
    # может быть обычный pump_dump_mode; требуем spike в source.
    if pump_dump and "spike" in low:
        return True
    return False


def is_own_signal_source(source: str) -> bool:
    """Сигнал от own / multi-agent / hybrid (не SPIKE, не telegram)."""
    low = _norm(source)
    if not low:
        return False
    if low in _OWN_SIGNAL_SOURCES:
        return True
    if low.startswith("own_"):
        return True
    if "own_multi_agent" in low or "multi_agent" in low:
        return True
    return False


def lookup_open_entry_meta(data_dir: Path, symbol: str) -> Tuple[str, bool]:
    """
    source + pump_dump открытой позиции: реестр → bot_trade_log → trade_history.
    """
    sym = str(symbol or "").upper()
    if not sym:
        return "", False

    data = load_registry(data_dir)
    symbols = data.get("symbols") if isinstance(data, dict) else {}
    if isinstance(symbols, dict):
        row = symbols.get(sym)
        if isinstance(row, dict):
            src = str(row.get("source") or "")
            pd_flag = bool(row.get("pump_dump"))
            if src or pd_flag:
                return src, pd_flag

    # Последняя запись в bot_trade_log по символу
    log_path = Path(data_dir) / "bot_trade_log.jsonl"
    src_log = _last_source_from_jsonl(log_path, sym, source_key="source")
    if src_log:
        return src_log, "spike" in src_log.lower()

    # Последний entered в trade_history
    journal = Path(data_dir) / "trades" / "trade_history.jsonl"
    if not journal.exists():
        journal = Path(data_dir) / "trade_history.jsonl"
    src_j = _last_entered_source(journal, sym)
    if src_j:
        return src_j, "spike" in src_j.lower()
    return "", False


def _last_source_from_jsonl(path: Path, symbol: str, *, source_key: str = "source") -> str:
    if not path.exists():
        return ""
    last = ""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("symbol", "") or "").upper() != symbol:
                continue
            src = str(row.get(source_key) or "")
            if src:
                last = src
    except OSError:
        return ""
    return last


def _last_entered_source(path: Path, symbol: str) -> str:
    if not path.exists():
        return ""
    last = ""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("symbol", "") or "").upper() != symbol:
                continue
            event = str(row.get("event", "") or "").lower()
            if event == "entered":
                src = str(row.get("source") or "")
                if src:
                    last = src
            elif event in {"closed", "exit", "closed_exchange"}:
                last = ""
    except OSError:
        return ""
    return last


def opposite_exit_cfg(positions_cfg: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    p = positions_cfg if isinstance(positions_cfg, Mapping) else {}
    opp = p.get("opposite_signal_exit") if isinstance(p.get("opposite_signal_exit"), dict) else {}
    return dict(opp)


def signal_confidence_pct(confidence: Any) -> float:
    conf = float(confidence or 0)
    return conf * 100.0 if conf <= 1.0 else conf


def opposite_exit_min_confidence_pct(positions_cfg: Optional[Mapping[str, Any]]) -> float:
    return float(opposite_exit_cfg(positions_cfg).get("min_confidence_pct", 0) or 0)


def opposite_exit_min_hold_min(positions_cfg: Optional[Mapping[str, Any]]) -> float:
    return float(opposite_exit_cfg(positions_cfg).get("min_position_age_min", 0) or 0)


def should_block_opposite_exit_for_weak_or_young(
    *,
    confidence: Any,
    position_age_min: Optional[float],
    positions_cfg: Optional[Mapping[str, Any]] = None,
) -> tuple[bool, str]:
    """
    True = не закрывать по обратному сигналу (слишком молодая позиция / слабый conf).
  """
    min_conf = opposite_exit_min_confidence_pct(positions_cfg)
    conf_pct = signal_confidence_pct(confidence)
    if min_conf > 0 and conf_pct < min_conf:
        return True, f"conf {conf_pct:.0f}%<{min_conf:.0f}%"
    min_age = opposite_exit_min_hold_min(positions_cfg)
    if min_age > 0:
        if position_age_min is None:
            return True, "position_age_unknown"
        if position_age_min < min_age:
            return True, f"age {position_age_min:.1f}min<{min_age:.0f}min"
    return False, ""


def should_skip_opposite_exit_for_spike_own(
    *,
    position_source: str,
    position_pump_dump: bool = False,
    signal_source: str,
    positions_cfg: Optional[Mapping[str, Any]] = None,
) -> bool:
    """
    True = не закрывать: SPIKE-позиция + обратный own-сигнал.

    Config:
      positions.opposite_signal_exit.skip_spike_on_own_signal: true  (default)
    """
    opp = opposite_exit_cfg(positions_cfg)
    if not bool(opp.get("skip_spike_on_own_signal", True)):
        return False
    if not is_spike_position_source(position_source, pump_dump=position_pump_dump):
        return False
    if not is_own_signal_source(signal_source):
        return False
    return True
