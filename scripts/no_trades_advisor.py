#!/usr/bin/env python3
"""
Проверка простоя бота: если долго нет сделок — отчёт и мягкие рекомендации по config.yaml.
Сброс убытка: снимает AUTO-STOP / EMERGENCY и обнуляет счётчики риска в файлах состояния.

Источники: trade_history.json, data/executed_trades.jsonl, логи, закрытый PnL Bybit (если есть ключи).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[misc, assignment]

# Границы как в prd_agent.evolution.self_improver (ослабление = в сторону большего числа сделок)
RELAX_BOUNDS: Dict[Tuple[str, str], Tuple[float, float, float]] = {
    ("trading", "min_signal_confidence"): (0.55, 0.85, 0.05),
    ("risk", "cooldown_after_loss_sec"): (60, 900, 60),
    ("risk", "cooldown_after_stop_hours"): (0, 6, 1),
    ("trading", "risk_pct_per_trade"): (0.1, 1.5, 0.05),
}

TRADE_TS_KEYS = (
    "closed_at",
    "close_time",
    "exit_time",
    "timestamp",
    "ts",
    "time",
    "opened_at",
    "open_time",
    "created_at",
    "updatedTime",
    "updated_time",
)

LOG_TRADE_PATTERNS = (
    re.compile(r"CLOSED\s+\w+", re.I),
    re.compile(r"Order OK\s+", re.I),
    re.compile(r"\[TRADE\]", re.I),
    re.compile(r"place_order.*success", re.I),
    re.compile(r"сделк[аи].*закрыт", re.I),
)

LOG_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
)

LOG_RISK_BLOCK_PATTERNS = (
    re.compile(r"AUTO-STOP", re.I),
    re.compile(r"EMERGENCY\s*STOP", re.I),
    re.compile(r"\bEMERGENCY\b.*(?:stop|торгов)", re.I),
    re.compile(r"STOPPED:", re.I),
    re.compile(r"Дневной убыток", re.I),
    re.compile(r"убытков подряд", re.I),
    re.compile(r"Skip\s+\w+.*(?:Кулдаун|Пауза после|STOPPED|EMERGENCY)", re.I),
)

RISK_STATE_REMOVE_KEYS = (
    "runtime_tuning",
    "runtime_ai_approval",
    "defensive_mode",
    "loss_streak",
    "blocked_symbols",
    "symbol_loss_streaks",
)

RISK_COUNTER_KEYS: Dict[str, Any] = {
    "status": "ACTIVE",
    "stop_reason": "",
    "block_reason": "",
    "consecutive_losses": 0,
    "blocked": False,
    "emergency": False,
    "emergency_stop": False,
    "daily_loss_usdt": 0.0,
    "daily_loss_pct": 0.0,
    "pnl_today_usdt": 0.0,
    "pnl_today_pct": 0.0,
    "pnl_today": 0.0,
    "last_loss_time": None,
    "auto_stop_time": None,
}


@dataclass
class TradeHit:
    ts: datetime
    source: str
    detail: str = ""


@dataclass
class PeriodResult:
    hours: float
    trade_count: int
    sources: Dict[str, int] = field(default_factory=dict)


@dataclass
class RiskBlockInfo:
    blocked: bool
    reasons: List[str] = field(default_factory=list)
    state_files: List[str] = field(default_factory=list)
    log_hits: List[str] = field(default_factory=list)
    snapshot: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LossResetResult:
    applied: bool
    backups: List[str] = field(default_factory=list)
    changes: List[str] = field(default_factory=list)
    files_touched: List[str] = field(default_factory=list)
    note: str = ""


@dataclass
class AdvisorReport:
    root: Path
    periods: List[PeriodResult]
    last_trade: Optional[TradeHit]
    idle_hours: Optional[float]
    config_path: Path
    current: Dict[str, Any]
    suggestions: List[Dict[str, Any]]
    data_sources_checked: List[str]
    risk: Optional[RiskBlockInfo] = None
    loss_reset: Optional[LossResetResult] = None


def _project_root(explicit: Optional[Path]) -> Path:
    if explicit:
        return explicit.resolve()
    cwd = Path.cwd().resolve()
    for candidate in (cwd, cwd.parent):
        if (candidate / "config.yaml").exists() or (candidate / "config.example.yaml").exists():
            return candidate
    return cwd


def _ensure_dotenv(root: Path) -> None:
    env_path = root / ".env"
    if load_dotenv is not None:
        load_dotenv(env_path, override=False)
        return
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "да")


def _env(*names: str) -> Optional[str]:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    return None


def load_config_soft(root: Path) -> Tuple[Dict[str, Any], Path]:
    _ensure_dotenv(root)
    cfg_path = root / "config.yaml"
    if not cfg_path.exists():
        example = root / "config.example.yaml"
        if example.exists():
            cfg_path = example
        else:
            raise FileNotFoundError(
                f"Не найден config.yaml в {root}. Скопируйте config.example.yaml."
            )
    with cfg_path.open("r", encoding="utf-8") as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}

    if api_key := _env("BYBIT_API_KEY"):
        _nested_set(data, ("bybit", "api_key"), api_key)
    if api_secret := _env("BYBIT_API_SECRET"):
        _nested_set(data, ("bybit", "api_secret"), api_secret)
    if (testnet_raw := _env("BYBIT_TESTNET")) is not None:
        _nested_set(data, ("bybit", "testnet"), _parse_bool(testnet_raw))
    if token := _env("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"):
        _nested_set(data, ("telegram", "bot_token"), token)
    if chat_id := _env("TELEGRAM_CHAT_ID"):
        _nested_set(data, ("telegram", "chat_id"), chat_id)
    if channel_id := _env("TELEGRAM_CHANNEL_ID"):
        _nested_set(data, ("telegram", "channel_id"), channel_id)

    data["_root"] = str(root)
    return data, cfg_path


def _nested_set(data: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    cur: Dict[str, Any] = data
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def _get_nested(data: Dict[str, Any], path: Tuple[str, ...], default: Any = None) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _resolve_cfg_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_json_file(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def discover_risk_state_files(root: Path, cfg: Dict[str, Any]) -> List[Path]:
    """Пути файлов состояния риска (ScalpBot + unified-agent)."""
    candidates: List[Path] = []

    cfg_paths = (
        _get_nested(cfg, ("adaptive_recommendations", "state_path")),
        _get_nested(cfg, ("manual_trade_learning", "state_path")),
        _get_nested(cfg, ("risk", "state_path")),
    )
    for raw in cfg_paths:
        if raw:
            candidates.append(_resolve_cfg_path(root, str(raw)))

    defaults = (
        root / "adaptive_recommendations_state.json",
        root / "manual_trade_learning_state.json",
        root / "data" / "adaptive_recommendations_state.json",
        root / "data" / "manual_trade_learning_state.json",
        root / "data" / "risk_guard_state.json",
        root / "data" / "risk_state.json",
        root / "data" / "guard_state.json",
        root / "bot" / "adaptive_recommendations_state.json",
        root / "bot" / "manual_trade_learning_state.json",
    )
    candidates.extend(defaults)

    data_dir = root / "data"
    if data_dir.is_dir():
        for pattern in ("risk*.json", "*guard*state*.json", "*risk*state*.json"):
            candidates.extend(sorted(data_dir.glob(pattern)))

    seen: set[str] = set()
    out: List[Path] = []
    for path in candidates:
        resolved = path.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file():
            out.append(resolved)
    return out


def _reset_day_stats_block(block: Any) -> Tuple[Any, List[str]]:
    changes: List[str] = []
    if not isinstance(block, dict):
        return block, changes
    fresh = dict(block)
    for key, val in (
        ("trades", 0),
        ("wins", 0),
        ("losses", 0),
        ("net_pnl_usdt", 0.0),
        ("net_pnl_pct", 0.0),
        ("pnl_today", 0.0),
        ("pnl_today_usdt", 0.0),
        ("pnl_today_pct", 0.0),
        ("consecutive_losses", 0),
        ("daily_loss_usdt", 0.0),
        ("daily_loss_pct", 0.0),
    ):
        if key in fresh and fresh[key] != val:
            fresh[key] = val
            changes.append(f"day_stats.{key}")
    if "date" in fresh:
        fresh["date"] = _today_iso()
        changes.append("day_stats.date")
    return fresh, changes


def _reset_risk_json_payload(data: Any, filename: str) -> Tuple[Any, List[str]]:
    changes: List[str] = []
    if not isinstance(data, dict):
        return data, changes

    updated = dict(data)
    for key, val in RISK_COUNTER_KEYS.items():
        if key in updated and updated[key] != val:
            updated[key] = val
            changes.append(key)

    for key in RISK_STATE_REMOVE_KEYS:
        if key in updated:
            updated.pop(key, None)
            changes.append(f"removed:{key}")

    for nested_key in ("day_stats", "daily_stats", "daily", "risk", "guard"):
        if nested_key in updated:
            updated[nested_key], nested_changes = _reset_day_stats_block(updated[nested_key])
            changes.extend(f"{nested_key}.{c}" if not c.startswith("day_stats.") else c for c in nested_changes)

    # manual_trade_learning_state.json — профили победителей не трогаем
    if filename.startswith("manual_trade_learning"):
        for key in list(updated.keys()):
            if key in ("profiles", "symbols", "winners"):
                continue
            if key.endswith("_losses") or key.endswith("_streak") or key in ("blocked", "cooldown_until"):
                if updated.get(key) not in (0, 0.0, "", None, False):
                    updated[key] = 0 if isinstance(updated[key], (int, float)) else ""
                    changes.append(key)

    return updated, changes


def _fresh_risk_guard_state() -> Dict[str, Any]:
    return {
        "status": "ACTIVE",
        "stop_reason": "",
        "block_reason": "",
        "consecutive_losses": 0,
        "blocked": False,
        "emergency": False,
        "day_stats": {
            "date": _today_iso(),
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "net_pnl_usdt": 0.0,
            "net_pnl_pct": 0.0,
            "consecutive_losses": 0,
        },
        "last_loss_time": None,
        "auto_stop_time": None,
        "reset_at": datetime.now(timezone.utc).isoformat(),
        "reset_by": "no_trades_advisor",
    }


def scan_logs_for_risk_blocks(root: Path, hours: float = 48.0) -> Tuple[List[str], bool]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    hits: List[str] = []
    blocked = False
    for path in _log_files(root):
        try:
            text = _tail_text(path)
        except OSError:
            continue
        for line in text.splitlines():
            if not any(p.search(line) for p in LOG_RISK_BLOCK_PATTERNS):
                continue
            ts: Optional[datetime] = None
            m = LOG_TS_RE.match(line)
            if m:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        ts = datetime.strptime(m.group(1), fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
            if ts is not None and ts < cutoff:
                continue
            snippet = line.strip()[:160]
            hits.append(f"{path.name}: {snippet}")
            blocked = True
    return hits[-8:], blocked


def inspect_risk_state(root: Path, cfg: Dict[str, Any]) -> RiskBlockInfo:
    files = discover_risk_state_files(root, cfg)
    reasons: List[str] = []
    snapshot: Dict[str, Any] = {}

    for path in files:
        data = _load_json_file(path, default={})
        if not isinstance(data, dict):
            continue
        snap_keys = (
            "status",
            "stop_reason",
            "block_reason",
            "consecutive_losses",
            "blocked",
            "emergency",
            "runtime_tuning",
        )
        part = {k: data.get(k) for k in snap_keys if k in data}
        if "day_stats" in data and isinstance(data["day_stats"], dict):
            part["day_stats"] = data["day_stats"]
        if part:
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                rel = str(path)
            snapshot[rel] = part

        status = str(data.get("status", "")).upper()
        if status in ("STOPPED", "EMERGENCY"):
            reasons.append(f"{path.name}: status={status}")
        if data.get("blocked") is True:
            reasons.append(f"{path.name}: blocked=true")
        if data.get("emergency") is True or data.get("emergency_stop") is True:
            reasons.append(f"{path.name}: emergency=true")
        stop_reason = str(data.get("stop_reason") or data.get("block_reason") or "").strip()
        if stop_reason:
            reasons.append(f"{path.name}: {stop_reason}")
        cons = data.get("consecutive_losses")
        if isinstance(cons, (int, float)) and cons > 0:
            reasons.append(f"{path.name}: consecutive_losses={int(cons)}")
        day = data.get("day_stats") if isinstance(data.get("day_stats"), dict) else {}
        pnl = day.get("net_pnl_usdt", data.get("pnl_today_usdt", data.get("pnl_today")))
        try:
            if pnl is not None and float(pnl) < 0:
                reasons.append(f"{path.name}: daily PnL={float(pnl):.2f}")
        except (TypeError, ValueError):
            pass
        if data.get("runtime_tuning"):
            reasons.append(f"{path.name}: defensive runtime_tuning активен")

    log_hits, log_blocked = scan_logs_for_risk_blocks(root)
    if log_blocked:
        reasons.append("В логах найдены AUTO-STOP / EMERGENCY / кулдаун")

    blocked = bool(reasons)
    return RiskBlockInfo(
        blocked=blocked,
        reasons=reasons[:12],
        state_files=[str(p) for p in files],
        log_hits=log_hits,
        snapshot=snapshot,
    )


def reset_loss_state(
    root: Path,
    cfg: Dict[str, Any],
    apply: bool,
) -> LossResetResult:
    files = discover_risk_state_files(root, cfg)
    risk_path = (root / "data" / "risk_guard_state.json").resolve()
    file_resolved = {p.resolve() for p in files}
    if risk_path not in file_resolved:
        files.append(risk_path)

    if not files and not apply:
        return LossResetResult(
            applied=False,
            note="Файлы состояния риска не найдены — будет создан data/risk_guard_state.json при --apply.",
        )

    sandbox = root / "data" / "sandbox"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backups: List[str] = []
    changes: List[str] = []
    touched: List[str] = []

    planned: List[str] = []
    for path in files:
        if path == risk_path and not path.is_file():
            planned.append(f"создать {path.relative_to(root)} (ACTIVE, счётчики=0)")
            continue
        if path.is_file():
            data = _load_json_file(path, default={})
            _, file_changes = _reset_risk_json_payload(data if isinstance(data, dict) else {}, path.name)
            if file_changes or path.name.startswith(("adaptive_", "manual_trade")):
                planned.append(f"{path.name}: {', '.join(file_changes) or 'сброс defensive/runtime'}")
            else:
                planned.append(f"{path.name}: обнулить счётчики риска")

    if not apply:
        return LossResetResult(
            applied=False,
            changes=planned,
            files_touched=[str(p) for p in files if p.is_file()],
            note="Предпросмотр. Для записи добавьте --apply.",
        )

    for path in files:
        if path == risk_path and not path.is_file():
            _save_json_file(path, _fresh_risk_guard_state())
            touched.append(str(path))
            changes.append(f"создан {path.name}")
            continue
        if not path.is_file():
            continue
        original = _load_json_file(path, default={})
        backup = sandbox / f"{path.stem}_backup_loss_reset_{stamp}{path.suffix}"
        shutil.copy2(path, backup)
        backups.append(str(backup))

        updated, file_changes = _reset_risk_json_payload(
            original if isinstance(original, dict) else {},
            path.name,
        )
        _save_json_file(path, updated)
        touched.append(str(path))
        if file_changes:
            changes.append(f"{path.name}: {', '.join(file_changes)}")
        else:
            changes.append(f"{path.name}: обновлён")

    if not risk_path.exists():
        _save_json_file(risk_path, _fresh_risk_guard_state())
        touched.append(str(risk_path))
        changes.append(f"создан {risk_path.name}")

    log_path = root / "data" / "loss_reset_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "no_trades_advisor",
        "backups": backups,
        "changes": changes,
        "files": touched,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return LossResetResult(
        applied=True,
        backups=backups,
        changes=changes,
        files_touched=touched,
        note="Сброс выполнен. Если бот уже запущен — нажмите «Сброс Guard» / «Сброс риска» в Telegram или перезапустите сервис.",
    )


def parse_trade_ts(row: Dict[str, Any]) -> Optional[datetime]:
    for key in TRADE_TS_KEYS:
        val = row.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            ts = float(val)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        text = str(val).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _trade_history_paths(root: Path) -> List[Path]:
    names = (
        root / "trade_history.json",
        root / "reports" / "trade_history.json",
        root / "bot" / "trade_history.json",
        root / "data" / "trade_history.json",
    )
    return [p for p in names if p.is_file()]


def load_trade_history_rows(root: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    checked: List[str] = []
    for path in _trade_history_paths(root):
        checked.append(str(path))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            rows.extend(x for x in data if isinstance(x, dict))
        elif isinstance(data, dict) and isinstance(data.get("trades"), list):
            rows.extend(x for x in data["trades"] if isinstance(x, dict))
    return rows, checked


def load_executed_trades(root: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    paths = [
        root / "data" / "executed_trades.jsonl",
        root / "data" / "monitor" / "executed_trades.jsonl",
    ]
    rows: List[Dict[str, Any]] = []
    checked: List[str] = []
    for path in paths:
        if not path.is_file():
            continue
        checked.append(str(path))
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except json.JSONDecodeError:
                continue
    return rows, checked


def _log_files(root: Path) -> List[Path]:
    candidates = [
        root / "bot.log",
        root / "bot_agent.log",
        root / "data" / "bot.log",
        root / "logs" / "bot.log",
    ]
    if (root / "logs").is_dir():
        candidates.extend(sorted((root / "logs").glob("*.log")))
    return [p for p in candidates if p.is_file()]


def _tail_text(path: Path, max_bytes: int = 3_000_000) -> str:
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(-max_bytes, os.SEEK_END)
        chunk = f.read()
    return chunk.decode("utf-8", errors="replace")


def scan_logs_for_trades(root: Path, hours: float) -> Tuple[List[TradeHit], List[str]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    hits: List[TradeHit] = []
    checked: List[str] = []
    for path in _log_files(root):
        checked.append(str(path))
        try:
            text = _tail_text(path)
        except OSError:
            continue
        for line in text.splitlines():
            if not any(p.search(line) for p in LOG_TRADE_PATTERNS):
                continue
            ts: Optional[datetime] = None
            m = LOG_TS_RE.match(line)
            if m:
                try:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    try:
                        ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(
                            tzinfo=timezone.utc
                        )
                    except ValueError:
                        ts = None
            if ts is None or ts < cutoff:
                continue
            hits.append(TradeHit(ts=ts, source=f"log:{path.name}", detail=line[:120]))
    return hits, checked


def trades_in_window(rows: Iterable[Dict[str, Any]], hours: float) -> List[TradeHit]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: List[TradeHit] = []
    for row in rows:
        ts = parse_trade_ts(row)
        if ts is None or ts < cutoff:
            continue
        sym = str(row.get("symbol", row.get("pair", "?")))
        out.append(TradeHit(ts=ts, source="history", detail=sym))
    return out


async def fetch_bybit_closed(
    cfg: Dict[str, Any], root: Path, hours: float
) -> Tuple[List[TradeHit], str]:
    bybit = cfg.get("bybit") or {}
    if not bybit.get("api_key") or not bybit.get("api_secret"):
        return [], "bybit: нет ключей в .env"

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from prd_agent.exchange.bybit_adapter import BybitAdapter
        from prd_agent.analysis.trade_monitor import TradeMonitor
    except ImportError as exc:
        return [], f"bybit: модуль prd_agent недоступен ({exc})"

    adapter = BybitAdapter(cfg)
    monitor = TradeMonitor(root / "data")
    try:
        rows = await monitor.fetch_closed_pnl(adapter, hours)
    finally:
        await adapter.close()

    hits: List[TradeHit] = []
    for row in rows:
        ts_ms = row.get("updatedTime") or row.get("createdTime")
        if ts_ms is None:
            continue
        try:
            ts = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError):
            continue
        sym = str(row.get("symbol", "?"))
        hits.append(TradeHit(ts=ts, source="bybit_closed_pnl", detail=sym))
    note = "bybit: OK" if adapter.uses_prd_client else "bybit: упрощённый клиент (без closed-pnl)"
    if not hits and "упрощённый" in note:
        return [], note + " — подключите PRD-репозиторий в config signals.prd_repo_path"
    return hits, note


def collect_all_hits(
    root: Path,
    cfg: Dict[str, Any],
    hours: float,
    use_bybit: bool,
) -> Tuple[List[TradeHit], List[str]]:
    history_rows, hist_paths = load_trade_history_rows(root)
    exec_rows, exec_paths = load_executed_trades(root)
    log_hits, log_paths = scan_logs_for_trades(root, hours)

    hits = trades_in_window(history_rows, hours)
    hits.extend(trades_in_window(exec_rows, hours))
    hits.extend(log_hits)

    sources = hist_paths + exec_paths + log_paths

    if use_bybit:
        try:
            bybit_hits, note = asyncio.run(fetch_bybit_closed(cfg, root, hours))
            sources.append(note)
            hits.extend(bybit_hits)
        except Exception as exc:  # pragma: no cover
            sources.append(f"bybit: ошибка {exc}")

    # Уникальность по (ts, source, detail)
    uniq: Dict[Tuple[str, str, str], TradeHit] = {}
    for h in hits:
        key = (h.ts.isoformat(), h.source, h.detail)
        uniq[key] = h
    return list(uniq.values()), sources


def analyze_period(
    root: Path,
    cfg: Dict[str, Any],
    hours: float,
    use_bybit: bool,
) -> Tuple[PeriodResult, List[TradeHit], List[str]]:
    hits, sources = collect_all_hits(root, cfg, hours, use_bybit)
    by_source: Dict[str, int] = {}
    for h in hits:
        bucket = h.source.split(":")[0]
        by_source[bucket] = by_source.get(bucket, 0) + 1
    return PeriodResult(hours=hours, trade_count=len(hits), sources=by_source), hits, sources


def _current_thresholds(cfg: Dict[str, Any]) -> Dict[str, Any]:
    trading = cfg.get("trading") or {}
    risk = cfg.get("risk") or {}
    return {
        "min_signal_confidence": float(trading.get("min_signal_confidence", 0.65)),
        "risk_pct_per_trade": float(trading.get("risk_pct_per_trade", 0.5)),
        "max_positions": int(trading.get("max_positions", 3)),
        "cooldown_after_loss_sec": int(risk.get("cooldown_after_loss_sec", 300)),
        "cooldown_after_stop_hours": int(risk.get("cooldown_after_stop_hours", 2)),
        "max_trades_per_day": int(risk.get("max_trades_per_day", 20)),
    }


def build_relax_suggestions(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    proposals: List[Dict[str, Any]] = []
    for path_tuple, (lo, hi, step) in RELAX_BOUNDS.items():
        section, key = path_tuple
        current_raw = _get_nested(cfg, path_tuple)
        if current_raw is None:
            continue
        try:
            current = float(current_raw)
        except (TypeError, ValueError):
            continue

        if key.endswith("_sec") or key == "cooldown_after_stop_hours" or key == "max_consecutive_losses":
            delta = -step
            new_val = int(round(current + delta))
            new_val = max(int(lo), min(int(hi), new_val))
            if new_val == int(current):
                continue
        else:
            delta = -step
            new_val = round(current + delta, 3)
            new_val = max(lo, min(hi, new_val))
            if abs(new_val - current) < 1e-9:
                continue

        proposals.append(
            {
                "risk": "low",
                "path": list(path_tuple),
                "delta": delta,
                "old": current,
                "new": new_val,
                "summary": _relax_summary(path_tuple, current, new_val),
            }
        )
    return proposals


def _relax_summary(path: Tuple[str, str], old: float, new: float) -> str:
    labels = {
        ("trading", "min_signal_confidence"): "Снизить порог уверенности сигнала",
        ("risk", "cooldown_after_loss_sec"): "Уменьшить паузу после убытка",
        ("risk", "cooldown_after_stop_hours"): "Уменьшить паузу после AUTO-STOP",
        ("trading", "risk_pct_per_trade"): "Слегка увеличить риск на сделку",
    }
    title = labels.get(path, f"Изменить {'.'.join(path)}")
    return f"{title}: {old} → {new}"


def apply_suggestions(
    config_path: Path,
    root: Path,
    proposals: Sequence[Dict[str, Any]],
) -> List[str]:
    if not config_path.exists():
        raise FileNotFoundError(config_path)
    sandbox = root / "data" / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    backup = sandbox / (
        "config_backup_no_trades_"
        + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + ".yaml"
    )
    shutil.copy2(config_path, backup)

    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    applied: List[str] = []
    for prop in proposals:
        path_tuple = tuple(prop["path"])
        if path_tuple not in RELAX_BOUNDS:
            continue
        lo, hi, _ = RELAX_BOUNDS[path_tuple]
        new_val = prop["new"]
        if path_tuple[1] in ("cooldown_after_loss_sec", "cooldown_after_stop_hours"):
            new_val = int(max(int(lo), min(int(hi), int(new_val))))
        else:
            new_val = max(lo, min(hi, float(new_val)))
        _nested_set(data, path_tuple, new_val)
        applied.append(f"{'.'.join(path_tuple)} = {new_val} (было {prop['old']})")

    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)

    log_path = root / "data" / "self_improvement_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "risk": "low",
        "applied": True,
        "source": "no_trades_advisor",
        "summary": "Ослабление порогов из-за отсутствия сделок",
        "changes": applied,
        "backup": str(backup),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return [f"Резервная копия: {backup}", *applied]


def format_report_ru(report: AdvisorReport, dry_run: bool) -> str:
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("  СОВЕТНИК: ДОЛГО НЕТ СДЕЛОК")
    lines.append("=" * 60)
    lines.append(f"Папка бота: {report.root}")
    lines.append(f"Конфиг: {report.config_path.name}")
    lines.append("")

    if report.idle_hours is not None:
        h = report.idle_hours
        if h >= 24:
            lines.append(f"⏱ Без сделок примерно: {h:.1f} ч ({h / 24:.1f} сут.)")
        else:
            lines.append(f"⏱ Без сделок примерно: {h:.1f} ч")
        if report.last_trade:
            lines.append(
                f"   Последняя активность: {report.last_trade.ts.strftime('%Y-%m-%d %H:%M UTC')} "
                f"({report.last_trade.source})"
            )
    else:
        lines.append("⏱ В доступных источниках сделок не найдено (или нет истории).")

    lines.append("")
    lines.append("Проверка по периодам:")
    for pr in report.periods:
        if pr.trade_count < 0:
            lines.append(f"  • {pr.hours:g} ч: — (проверка пропущена)")
            continue
        status = "✅ есть сделки" if pr.trade_count > 0 else "❌ сделок нет"
        src = ", ".join(f"{k}={v}" for k, v in sorted(pr.sources.items())) or "—"
        lines.append(f"  • {pr.hours:g} ч: {status} (всего {pr.trade_count}; {src})")

    lines.append("")
    lines.append("Текущие пороги входа / риска:")
    cur = report.current
    lines.append(f"  • min_signal_confidence: {cur['min_signal_confidence']}")
    lines.append(f"  • cooldown_after_loss_sec: {cur['cooldown_after_loss_sec']} сек")
    lines.append(f"  • cooldown_after_stop_hours: {cur['cooldown_after_stop_hours']} ч")
    lines.append(f"  • risk_pct_per_trade: {cur['risk_pct_per_trade']}%")
    lines.append(f"  • max_positions: {cur['max_positions']}")
    lines.append(f"  • max_trades_per_day: {cur['max_trades_per_day']}")

    if report.suggestions:
        lines.append("")
        lines.append("💡 Рекомендуется ОСЛАБИТЬ (больше входов, выше риск просадки):")
        for s in report.suggestions:
            lines.append(f"  • {s['summary']}")
        if dry_run:
            lines.append("")
            lines.append("Чтобы применить автоматически: добавьте флаг --apply")
            lines.append("(создаётся резервная копия config в data/sandbox/)")
    else:
        lines.append("")
        lines.append("Сделки есть — менять пороги не требуется.")

    if report.risk is not None:
        lines.append("")
        lines.append("🛡 Состояние риска (Guard):")
        if report.risk.blocked:
            lines.append("  ⚠️ Обнаружена возможная блокировка торговли:")
            for reason in report.risk.reasons[:8]:
                lines.append(f"    • {reason}")
            if report.risk.log_hits:
                lines.append("  Последние строки в логах:")
                for hit in report.risk.log_hits[-3:]:
                    lines.append(f"    • {hit[:120]}")
        else:
            lines.append("  ✅ Явных блокировок в файлах и логах не найдено.")
        if report.risk.state_files:
            lines.append(f"  Файлы состояния: {len(report.risk.state_files)}")

    if report.loss_reset is not None:
        lines.append("")
        lines.append("♻️ Сброс убытка / риск-стопа:")
        if report.loss_reset.applied:
            lines.append("  ✅ Выполнен сброс счётчиков (ACTIVE, убытки=0).")
            for line in report.loss_reset.changes[:10]:
                lines.append(f"    • {line}")
            for backup in report.loss_reset.backups[:5]:
                lines.append(f"    📦 {backup}")
        else:
            if report.loss_reset.changes:
                lines.append("  План сброса (добавьте --apply для записи):")
                for line in report.loss_reset.changes[:10]:
                    lines.append(f"    • {line}")
            if report.loss_reset.note:
                lines.append(f"  {report.loss_reset.note}")
        if report.loss_reset.note and report.loss_reset.applied:
            lines.append(f"  ℹ️ {report.loss_reset.note}")

    need_reset_hint = report.risk is not None and report.risk.blocked
    if need_reset_hint and (report.loss_reset is None or not report.loss_reset.applied):
        lines.append("")
        lines.append("💡 Сбросить блокировку риска:")
        lines.append("   python scripts/no_trades_advisor.py --reset-loss --apply")
    elif dry_run and report.suggestions and (report.risk is None or report.risk.blocked):
        lines.append("")
        lines.append("💡 Если бот стоит из-за AUTO-STOP / дневного убытка:")
        lines.append("   python scripts/no_trades_advisor.py --reset-loss --apply")

    lines.append("")
    lines.append("Проверенные источники:")
    for src in report.data_sources_checked[:12]:
        lines.append(f"  • {src}")
    if len(report.data_sources_checked) > 12:
        lines.append(f"  • ... и ещё {len(report.data_sources_checked) - 12}")

    lines.append("")
    lines.append("⚠️ Это не гарантия прибыли. Ослабление порогов увеличивает число сделок,")
    lines.append("   но может увеличить убытки. Следите за дневным лимитом убытка в config.")
    lines.append("=" * 60)
    return "\n".join(lines)


async def send_telegram(cfg: Dict[str, Any], text: str) -> bool:
    import aiohttp

    tg = cfg.get("telegram") or {}
    token = tg.get("bot_token", "")
    chat_id = tg.get("channel_id") or tg.get("chat_id", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json()
            return bool(data.get("ok"))


def run_advisor(
    root: Path,
    periods: Sequence[float],
    use_bybit: bool,
    apply: bool,
    telegram: bool,
    reset_loss: bool = False,
    reset_only: bool = False,
) -> AdvisorReport:
    cfg, config_path = load_config_soft(root)
    risk_info = inspect_risk_state(root, cfg)

    all_sources: List[str] = []
    period_results: List[PeriodResult] = []
    all_hits: List[TradeHit] = []

    if not reset_only:
        for hours in sorted(periods):
            pr, hits, sources = analyze_period(root, cfg, hours, use_bybit)
            period_results.append(pr)
            all_hits.extend(hits)
            for s in sources:
                if s not in all_sources:
                    all_sources.append(s)
    else:
        for hours in sorted(periods):
            period_results.append(PeriodResult(hours=hours, trade_count=-1))

    last_trade: Optional[TradeHit] = None
    if all_hits:
        last_trade = max(all_hits, key=lambda h: h.ts)

    idle_hours: Optional[float] = None
    if last_trade:
        idle_hours = (
            datetime.now(timezone.utc) - last_trade.ts
        ).total_seconds() / 3600.0

    max_period = max(periods) if periods else 24.0
    primary = next((p for p in period_results if p.hours == max_period), None)
    need_advice = (
        not reset_only
        and primary is not None
        and primary.trade_count == 0
    )

    suggestions: List[Dict[str, Any]] = []
    if need_advice:
        suggestions = build_relax_suggestions(cfg)

    loss_reset_result: Optional[LossResetResult] = None
    should_reset = reset_loss or (need_advice and risk_info.blocked)
    if reset_loss:
        loss_reset_result = reset_loss_state(root, cfg, apply=apply)
    elif should_reset:
        loss_reset_result = LossResetResult(
            applied=False,
            note="Блокировка риска обнаружена. Запустите: --reset-loss --apply",
        )

    report = AdvisorReport(
        root=root,
        periods=period_results,
        last_trade=last_trade,
        idle_hours=idle_hours,
        config_path=config_path,
        current=_current_thresholds(cfg),
        suggestions=suggestions,
        data_sources_checked=all_sources,
        risk=risk_info,
        loss_reset=loss_reset_result,
    )

    text = format_report_ru(report, dry_run=not apply)
    print(text)

    if need_advice and apply and suggestions:
        if config_path.name != "config.yaml":
            print("\n⚠️ --apply для config пропущен: активен только config.yaml (не example).")
        else:
            applied = apply_suggestions(config_path, root, suggestions)
            print("\n✅ Применено в config.yaml:")
            for line in applied:
                print(f"  {line}")

    if loss_reset_result and loss_reset_result.applied:
        print("\n✅ Сброс убытка выполнен:")
        for line in loss_reset_result.changes[:12]:
            print(f"  {line}")
        for backup in loss_reset_result.backups[:5]:
            print(f"  📦 {backup}")

    notify_tg = telegram and (need_advice or reset_loss)
    if notify_tg:
        short_parts: List[str] = []
        if need_advice:
            short_parts.append(
                f"⚠️ Нет сделок за {max_period:g} ч\n"
                f"Порог conf: {report.current['min_signal_confidence']}\n"
            )
            if suggestions:
                short_parts.append(
                    "Рекомендации:\n"
                    + "\n".join(f"• {s['summary']}" for s in suggestions[:4])
                )
            else:
                short_parts.append("Проверьте, запущена ли торговля (кнопка Старт в Telegram).")
        if risk_info.blocked:
            short_parts.append(
                "🛡 Блокировка риска:\n"
                + "\n".join(f"• {r}" for r in risk_info.reasons[:4])
            )
        if loss_reset_result and loss_reset_result.applied:
            short_parts.append("♻️ Сброс убытка выполнен (ACTIVE, счётчики=0).")
        elif reset_loss and not apply:
            short_parts.append("♻️ Сброс убытка: нужен флаг --apply для записи.")
        short = "\n\n".join(short_parts)
        try:
            ok = asyncio.run(send_telegram(cfg, short))
            print("\nTelegram:", "отправлено" if ok else "не отправлено (проверьте .env)")
        except Exception as exc:  # pragma: no cover
            print(f"\nTelegram: ошибка {exc}")

    return report


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Проверка простоя бота и рекомендации по ослаблению порогов входа.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Корень проекта (где config.yaml). По умолчанию — текущая папка.",
    )
    parser.add_argument(
        "--hours",
        type=float,
        action="append",
        dest="periods",
        help="Период проверки в часах (можно несколько раз). По умолчанию 6 и 24.",
    )
    parser.add_argument(
        "--no-bybit",
        action="store_true",
        help="Не опрашивать Bybit API (только файлы и логи).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить изменения: config.yaml и/или сброс убытка (с резервной копией).",
    )
    parser.add_argument(
        "--reset-loss",
        action="store_true",
        help="Сброс AUTO-STOP / EMERGENCY и дневных счётчиков убытка в файлах состояния.",
    )
    parser.add_argument(
        "--reset-loss-only",
        action="store_true",
        help="Только сброс убытка, без полной проверки сделок (быстрее).",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Отправить краткое уведомление в Telegram (нужен .env).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Код выхода 0 даже при отсутствии сделок (для cron без алертов).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = _project_root(args.root)
    periods = args.periods or [6.0, 24.0]
    periods = sorted(set(periods))
    reset_loss = args.reset_loss or args.reset_loss_only

    try:
        report = run_advisor(
            root=root,
            periods=periods,
            use_bybit=not args.no_bybit,
            apply=args.apply,
            telegram=args.telegram,
            reset_loss=reset_loss,
            reset_only=args.reset_loss_only,
        )
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2

    if args.reset_loss_only:
        return 0

    max_period = max(periods)
    primary = next(p for p in report.periods if p.hours == max_period)
    if primary.trade_count == 0 and not args.quiet:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
