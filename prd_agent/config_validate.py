"""Семантическая проверка config.yaml (типы, диапазоны, опечатки в ключах)."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Number = Union[int, float]

# Частые опечатки → правильное имя ключа
_TYPO_HINTS: Dict[str, Dict[str, str]] = {
    "trading": {
        "max_position": "max_positions",
        "max_open_positions": "max_positions",
        "min_confidence": "min_signal_confidence",
        "signal_confidence": "min_signal_confidence",
        "risk_per_trade": "risk_pct_per_trade",
        "risk_percent": "risk_pct_per_trade",
        "loop_interval": "loop_interval_sec",
        "symbols_blacklist": "symbol_blacklist",
    },
    "risk": {
        "max_daily_loss": "max_daily_loss_pct",
        "daily_loss_pct": "max_daily_loss_pct",
        "max_loss_per_day": "max_daily_loss_usdt",
        "consecutive_losses": "max_consecutive_losses",
        "max_trades": "max_trades_per_day",
    },
    "quality_gate": {
        "min_rr": "min_rr_ratio",
        "rr_ratio": "min_rr_ratio",
        "confidence": "min_confidence",
    },
    "bybit": {
        "apiKey": "api_key",
        "secret": "api_secret",
        "api_secret_key": "api_secret",
    },
    "telegram": {
        "token": "bot_token",
        "botToken": "bot_token",
        "chatId": "chat_id",
    },
}

_REQUIRED_SECTIONS = ("bybit", "telegram", "trading", "risk")


class _BybitSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = ""
    api_secret: str = ""
    read_api_key: str = ""
    read_api_secret: str = ""
    testnet: bool = False
    category: str = "linear"


class _TelegramSection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bot_token: str = ""
    chat_id: str = ""
    channel_id: str = ""
    allowed_user_ids: List[int] = Field(default_factory=list)
    control_polling_enabled: bool = True


class _TradingSection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    leverage: int = Field(ge=1, le=100)
    loop_interval_sec: float = Field(default=60, ge=15, le=600)
    max_positions: int = Field(ge=1, le=20)
    risk_pct_per_trade: float = Field(default=0.35, ge=0.05, le=5.0)
    min_signal_confidence: float = Field(default=0.85, ge=0.0, le=1.0)


class _RiskSection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    max_consecutive_losses: int = Field(ge=1, le=20)
    max_daily_loss_pct: float = Field(ge=0.5, le=50.0)
    max_daily_loss_usdt: float = Field(default=50, ge=1)
    max_trades_per_day: int = Field(default=38, ge=1, le=200)


class _QualityGateSection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    min_rr_ratio: float = Field(default=2.0, ge=1.0, le=5.0)
    min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)


class _SupervisorV4Section(BaseModel):
    model_config = ConfigDict(extra="ignore")
    panic_consecutive_losses: int = Field(default=3, ge=1, le=20)
    panic_minutes: int = Field(default=90, ge=5, le=1440)


class _BybitMonitorSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    interval_sec: float = Field(default=300, ge=60, le=3600)
    notify_telegram: bool = False
    llm_summary: bool = True
    kline_interval: str = "15"
    kline_limit: int = Field(default=96, ge=10, le=500)
    include_funding: bool = True
    include_oi: bool = True
    include_liquidations: bool = True
    alert_upnl_change_usdt: float = Field(default=15.0, ge=1.0, le=10000.0)
    max_symbols: int = Field(default=8, ge=1, le=30)


def _section(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    block = data.get(name)
    return block if isinstance(block, dict) else {}


def _require_section(errors: List[str], data: Dict[str, Any], name: str) -> Dict[str, Any]:
    block = _section(data, name)
    if not block:
        errors.append(f"Отсутствует или пустая секция: {name}")
    return block


def _check_typo_keys(errors: List[str], section: str, block: Dict[str, Any]) -> None:
    hints = _TYPO_HINTS.get(section, {})
    for key in block:
        if key in hints:
            errors.append(
                f"{section}.{key}: неизвестный ключ — возможно имелось в виду "
                f"'{hints[key]}'?"
            )


def _pydantic_errors(section: str, exc: ValidationError) -> List[str]:
    out: List[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "ошибка")
        typ = err.get("type", "")
        if typ == "extra_forbidden":
            extra = loc or "?"
            out.append(f"{section}: лишний ключ '{extra}' — опечатка или устаревший параметр")
        elif loc:
            out.append(f"{section}.{loc}: {msg}")
        else:
            out.append(f"{section}: {msg}")
    return out


def _validate_model(
    errors: List[str],
    section: str,
    block: Dict[str, Any],
    model: type[BaseModel],
) -> None:
    if not block:
        return
    _check_typo_keys(errors, section, block)
    try:
        model.model_validate(block)
    except ValidationError as exc:
        errors.extend(_pydantic_errors(section, exc))


def _num(
    errors: List[str],
    block: Dict[str, Any],
    key: str,
    *,
    lo: float,
    hi: float,
    path: str,
    integer: bool = False,
) -> None:
    if key not in block:
        return
    raw = block.get(key)
    if isinstance(raw, bool):
        errors.append(f"{path}.{key}: ожидалось число, получен bool")
        return
    try:
        val = float(raw)
    except (TypeError, ValueError):
        errors.append(f"{path}.{key}: должно быть числом, сейчас {raw!r}")
        return
    if integer and val != int(val):
        errors.append(f"{path}.{key}: должно быть целым числом")
        return
    if val < lo or val > hi:
        errors.append(f"{path}.{key}: допустимо {lo}..{hi}, сейчас {val}")


def validate_config_data(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["config.yaml: корень должен быть объектом (mapping)"]

    for name in _REQUIRED_SECTIONS:
        _require_section(errors, data, name)

    bybit = _section(data, "bybit")
    telegram = _section(data, "telegram")
    trading = _section(data, "trading")
    risk = _section(data, "risk")
    qg = _section(data, "quality_gate")
    sup = _section(data, "supervisor_v4")
    bybit_monitor = _section(data, "bybit_monitor")

    _validate_model(errors, "bybit", bybit, _BybitSection)
    _validate_model(errors, "telegram", telegram, _TelegramSection)
    _validate_model(errors, "trading", trading, _TradingSection)
    _validate_model(errors, "risk", risk, _RiskSection)
    if qg:
        _validate_model(errors, "quality_gate", qg, _QualityGateSection)
    if sup:
        _validate_model(errors, "supervisor_v4", sup, _SupervisorV4Section)
    if bybit_monitor:
        _validate_model(errors, "bybit_monitor", bybit_monitor, _BybitMonitorSection)

    api_cache = _section(data, "api_cache")
    _num(errors, api_cache, "price_ttl_sec", lo=1, hi=120, path="api_cache")
    _num(errors, api_cache, "klines_ttl_sec", lo=5, hi=300, path="api_cache")
    _num(errors, api_cache, "max_parallel_requests", lo=1, hi=20, path="api_cache", integer=True)

    al = _section(trading, "adaptive_loop")
    _num(errors, al, "base_sec", lo=15, hi=600, path="trading.adaptive_loop")
    _num(errors, al, "active_sec", lo=15, hi=300, path="trading.adaptive_loop")
    _num(errors, al, "idle_sec", lo=30, hi=900, path="trading.adaptive_loop")

    pos_sync = _section(data, "position_sync")
    _num(errors, pos_sync, "alert_cooldown_sec", lo=60, hi=7200, path="position_sync")

    return len(errors) == 0, errors
