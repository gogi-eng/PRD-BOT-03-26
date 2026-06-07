#!/usr/bin/env python3
"""Telegram user-session signal agent.

Reads messages from channels visible to the user's Telegram account, extracts
crypto futures signals, optionally asks OpenRouter for a second opinion, and can
execute approved signals on Bybit.

Safety / execution:
- Channel auto (`telegram_signal_agent.auto_execute`): after AI + risk guards (OpenRouter для постов каналов).
- Scanner→Bybit (`market_scanner_auto_execute`): без OpenRouter, только риск-ворота объёмы/спред; см. сообщения MARKET SCANNER.
- При `execution_sr_zones.enabled`: перед ордером SL/TP поджимаются к зонам поддержки/сопротивления (анализатор `analysis/structure_zones.py`) с доп. отступом в ATR.

Defaults to review in config.yaml:
- every signal is written to reports/telegram_signals/signals.jsonl;
- duplicate Telegram message IDs are ignored;
- if a message has no SL/TP, optional defaults virtualize levels when allow_auto_take_profit / defaults are enabled.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

try:
    from telethon import TelegramClient, events
    from telethon import utils as telethon_utils
except Exception:  # pragma: no cover
    TelegramClient = None  # type: ignore
    events = None  # type: ignore
    telethon_utils = None  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TG_LOGGING_CONFIGURED = False


def _tg_stderr_is_same_file_as(log_path: Path) -> bool:
    """True when stderr writes to the same inode as ``log_path`` (e.g. ``>> telegram_signal_agent.log 2>&1``)."""
    try:
        if sys.stderr is None:
            return False
        if sys.stderr.isatty():
            return False
        if not log_path.exists():
            return False
        st_err = os.fstat(sys.stderr.fileno())
        st_log = os.stat(log_path)
        return st_err.st_ino == st_log.st_ino and st_err.st_dev == st_log.st_dev
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def ensure_telegram_agent_logging(repo_dir: Path) -> None:
    """Root logging для агента: stderr + ``telegram_signal_agent.log``, префикс с датой и временем."""
    global _TG_LOGGING_CONFIGURED
    if _TG_LOGGING_CONFIGURED:
        return
    _TG_LOGGING_CONFIGURED = True
    repo_root = repo_dir.resolve()
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    log_path = (repo_root / "telegram_signal_agent.log").resolve()
    stderr_is_log = _tg_stderr_is_same_file_as(log_path)

    def _has_stderr() -> bool:
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr:
                return True
        return False

    def _has_tg_log_file() -> bool:
        for h in root.handlers:
            if isinstance(h, logging.FileHandler):
                try:
                    if Path(h.baseFilename).resolve() == log_path:
                        return True
                except (OSError, ValueError):
                    pass
        return False

    force_stderr = os.environ.get("PRD_LOG_STDERR", "").strip().lower() in {"1", "true", "yes"}

    if stderr_is_log:
        if not _has_stderr():
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(fmt)
            root.addHandler(sh)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        return

    if not _has_stderr() and (force_stderr or sys.stderr.isatty()):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    if not _has_tg_log_file():
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


LOG = logging.getLogger("TG_AGENT")
WORLD_LOG = logging.getLogger("AGENT_WORLD")


TG_MARKET_SCANNER_LEARNING_FOOTER = (
    "──────────────\n"
    "Что значит winrate в этой строке:\n"
    "• Это доля виртуальных исходов прошлых наблюдений сканера: цена уперлась в «Цель» "
    "(win) или сначала в «Отмену сценария» (loss) — не PnL по счёту и не сделки с биржи.\n"
    "• По этому сообщению торговля не включается; меняются только пороги уведомлений сканера.\n"
)

from engine.execution_engine import ExecutionEngine  # noqa: E402
from exchange.bybit_client import BybitClient  # noqa: E402
from telegram_agent.channel_auto_block import (  # noqa: E402
    STATE_BLOCKS,
    from_agent_cfg as channel_auto_block_from_cfg,
    is_blocked as channel_is_blocked,
    record_outcome as channel_record_outcome,
    refresh_auto_blocks,
)
from telegram_agent.market_regime import (  # noqa: E402
    classify_regime,
    market_regime_from_agent_cfg,
)
from telegram_agent.signal_excursion import (  # noqa: E402
    aggregate_excursions_by_channel,
    enqueue_signal_excursion,
    evaluate_signal_excursions,
    excursion_track_id,
    signal_excursion_from_agent_cfg,
)
from telegram_agent.execution_limits import ExecutionLimiter  # noqa: E402
from telegram_agent.risk_pipeline import RiskPipeline, compute_rr  # noqa: E402
from telegram_agent.signal_agent_panel import start_control_panel_task  # noqa: E402
from analysis.structure_zones import StructureZoneAnalyzer  # noqa: E402
from telegram_agent.photo_ocr import telethon_photo_ocr_text  # noqa: E402
from telegram_agent.signal_parse import enrich_parsed_signal_levels  # noqa: E402
from telegram_agent.signal_quality import (  # noqa: E402
    passes_quality_gate,
    rule_based_review,
)
from telegram_agent.sr_execution_adjust import (  # noqa: E402
    adjust_telegram_sl_tp_with_sr_zones,
    infer_side_from_zones,
)


@dataclass
class TelegramSignal:
    source: str
    message_id: int
    message_time_utc: str
    received_at_utc: str
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    leverage: int
    confidence: int
    reason: str
    raw_text: str
    parser_confidence: int = 0
    market_regime: str = "unknown"


@dataclass
class MarketSetup:
    checked_at_utc: str
    symbol: str
    scenario: str
    score: int
    price: float
    turnover_24h: float
    range_low: float
    range_high: float
    consolidation_bars: int
    range_pct: float
    atr_pct: float
    volume_ratio: float
    bos_level: float
    fvg_low: float
    fvg_high: float
    invalidation: float
    target: float
    reasons: list[str]
    # True только при реальном пробое (bos_up/bos_down), не «у границы без BOS».
    confirmed_bos: bool = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def stretch_take_profit_for_min_rr(side: str, entry: float, sl: float, tp: float, min_rr: float) -> float:
    """Расширяет TP в сторону прибыли при том же SL, пока RR < min_rr (для MARKET SCANNER геометрия часто даёт RR≈1)."""
    side_u = str(side or "").upper()
    min_rr_f = float(min_rr or 0.0)
    e, slv, tpv = float(entry or 0.0), float(sl or 0.0), float(tp or 0.0)
    if min_rr_f <= 0.0 or e <= 0.0 or slv <= 0.0 or tpv <= 0.0:
        return tpv
    if side_u == "SELL":
        risk = slv - e
        reward = e - tpv
        if risk <= 1e-12 or reward <= 1e-12:
            return tpv
        need_reward = min_rr_f * risk
        if reward + 1e-12 >= need_reward:
            return tpv
        return e - need_reward
    if side_u == "BUY":
        risk = e - slv
        reward = tpv - e
        if risk <= 1e-12 or reward <= 1e-12:
            return tpv
        need_reward = min_rr_f * risk
        if reward + 1e-12 >= need_reward:
            return tpv
        return e + need_reward
    return tpv


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("Не установлен пакет PyYAML (pip install pyyaml) — агент не может прочитать config.yaml")
    if not path.exists():
        raise RuntimeError(f"Не найден {path}")
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise RuntimeError(f"{path.name} пустой — восстановите из git: git checkout -- {path.name}")
    try:
        data = yaml.safe_load(raw)
    except Exception as exc:
        raise RuntimeError(f"Ошибка разбора YAML в {path.name}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{path.name}: ожидался объект YAML-словарь в корне")
    return data


def get_cfg(cfg: dict[str, Any], section: str, key: str, default: Any = None) -> Any:
    node = cfg.get(section, {})
    if isinstance(node, dict):
        return node.get(key, default)
    return default


def normalize_symbol(raw: str) -> str:
    s = re.sub(r"[^A-Z0-9]", "", raw.upper())
    if s.endswith("PERP"):
        s = s[:-4]
    if s.endswith("USD") and not s.endswith("USDT"):
        s = s[:-3]
    if not s.endswith("USDT"):
        s = f"{s}USDT"
    return s


def looks_like_trade_symbol(symbol: str) -> bool:
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    if not base or base.isdigit():
        return False
    if not re.search(r"[A-Z]", base):
        return False
    return 2 <= len(base) <= 12


def normalize_chat_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lstrip("@").lower())


def _chat_name_compact(value: Any) -> str:
    """Letters/digits only, lower — stable match for ignored_chats when Telegram varies spacing/symbols."""
    return re.sub(r"[^a-z0-9]+", "", normalize_chat_name(value))


PRICE_RE = re.compile(
    r"(?<![A-Za-zА-Яа-я0-9])\$?\s*(\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?)(?!\s*[%xX):])",
    flags=re.IGNORECASE,
)

# Названия монет без тикера (частые пропуски парсера)
COIN_NAME_TO_BASE: dict[str, str] = {
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
    "ETHER": "ETH",
    "SOLANA": "SOL",
    "RIPPLE": "XRP",
    "POLYGON": "MATIC",
    "COSMOS": "ATOM",
    "DOGECOIN": "DOGE",
    "CHAINLINK": "LINK",
}

_LONG_SIDE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bLONG\b",
        r"\bBUY\b",
        r"\bLONGING\b",
        r"ЛОНГ\b",
        r"В\s+ЛОНГ",
        r"ОТКРЫВАЕМ\s+ЛОНГ",
        r"\bLONG[\s_-]+ПОЗ",
        r"ПОКУП",
        r"\bПОКУПКА\b",
        r"К\s+ПОКУПКЕ",
        r"ПОКУПА(?:ЕМ|Ю)",
        r"🟢\s*(LONG|ЛОНГ|BUY)\b",
        r"(LONG|ЛОНГ)\s+[Оо]Т\s+\d",
    )
)

_SHORT_SIDE_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bSHORT\b",
        r"\bSELL\b",
        r"\bSHORTING\b",
        r"\bSHORT[\s_-]+ПОЗ",
        r"ШОРТ\b",
        r"В\s+ШОРТ",
        r"ОТКРЫВАЕМ\s+ШОРТ",
        r"\bSHORT\s+POSITION\b",
        r"ПРОДА",
        r"\bПРОДАЖА\b",
        r"К\s+ПРОДАЖЕ",
        r"ПРОДАЁМ\b",
        r"ПРОДАЕМ\b",
        r"🔴\s*(SHORT|ШОРТ|SELL)\b",
        r"(SHORT|ШОРТ)\s+[Оо]Т\s+\d",
    )
)


def _earliest_regex_start(text: str, patterns: tuple[re.Pattern[str], ...]) -> int | None:
    earliest: int | None = None
    for rx in patterns:
        match = rx.search(text)
        if match:
            start = match.start()
            earliest = start if earliest is None or start < earliest else earliest
    return earliest


def _side_flags(segment: str) -> tuple[bool, bool]:
    return (
        _earliest_regex_start(segment, _LONG_SIDE_PATTERNS) is not None,
        _earliest_regex_start(segment, _SHORT_SIDE_PATTERNS) is not None,
    )


def _symbol_anchor_start(raw: str, symbol: str) -> int:
    """Позиция первого «якоря» тикера в тексте (для окна направления)."""
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    if not base:
        return 0
    patterns = (
        rf"\b{re.escape(base)}\s*[/\\\-\.]+\s*USDT\b",
        rf"#\s*{re.escape(base)}\b",
        rf"\$\s*{re.escape(base)}\b(?![A-Za-z0-9])",
        rf"`\s*{re.escape(base)}\s*`",
        rf"\b{re.escape(base)}\s+USDT\b",
        rf"\b{re.escape(base)}USDT\b",
    )
    earliest: int | None = None
    for pat in patterns:
        m = re.search(pat, raw, flags=re.IGNORECASE)
        if m:
            pos = m.start()
            earliest = pos if earliest is None or pos < earliest else earliest
    name_key = base.upper()
    if name_key == "BTC" and re.search(r"\bBITCOIN\b", raw, re.IGNORECASE):
        m = re.search(r"\bBITCOIN\b", raw, re.IGNORECASE)
        pos = m.start()
        earliest = pos if earliest is None or pos < earliest else earliest
    elif name_key == "ETH" and re.search(r"\b(ETHEREUM|ETHER)\b", raw, re.IGNORECASE):
        m = re.search(r"\b(ETHEREUM|ETHER)\b", raw, re.IGNORECASE)
        pos = m.start()
        earliest = pos if earliest is None or pos < earliest else earliest
    if earliest is None:
        m = re.search(rf"\b{re.escape(base)}\b", raw, re.IGNORECASE)
        if m:
            earliest = m.start()
    return int(earliest if earliest is not None else 0)


def _narrow_around_anchor(raw: str, symbol: str, before: int = 520, after: int = 2400) -> str:
    a = _symbol_anchor_start(raw, symbol)
    lo = max(0, a - before)
    hi = min(len(raw), (a + after) if a > 0 else len(raw))
    return raw[lo:hi]


def resolve_trade_side(raw: str, symbol: str | None) -> str | None:
    """LONG+SHORT в длинном посте: берём ту сторону, что ближе к блоку символа, иначе — раньше по тексту."""
    gl, gs = _side_flags(raw)
    if not gl and not gs:
        return None
    if gl and not gs:
        return "BUY"
    if gs and not gl:
        return "SELL"
    lg = _earliest_regex_start(raw, _LONG_SIDE_PATTERNS)
    sg = _earliest_regex_start(raw, _SHORT_SIDE_PATTERNS)
    if symbol:
        slim = _narrow_around_anchor(raw, symbol)
        wl, ws = _side_flags(slim)
        if wl and not ws:
            return "BUY"
        if ws and not wl:
            return "SELL"
        lloc = _earliest_regex_start(slim, _LONG_SIDE_PATTERNS)
        sloc = _earliest_regex_start(slim, _SHORT_SIDE_PATTERNS)
        if lloc is not None and sloc is not None and lloc != sloc:
            return "BUY" if lloc < sloc else "SELL"
    if lg is not None and sg is not None and lg != sg:
        return "BUY" if lg < sg else "SELL"
    return None


def extract_prices(text: str) -> list[float]:
    prices: list[float] = []
    for match in PRICE_RE.finditer(text or ""):
        value = safe_float(match.group(1))
        if value > 0:
            prices.append(value)
    return prices


def extract_price_after(patterns: list[str], text: str, max_chars: int = 180) -> float:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if not match:
            continue
        chunk = text[match.end() : match.end() + max_chars]
        prices = extract_prices(chunk)
        if prices:
            return prices[0]
    return 0.0


def extract_first_number(patterns: list[str], text: str) -> float:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            nums = re.findall(r"\d+(?:[.,]\d+)?", match.group(0))
            if nums:
                return safe_float(nums[0])
    return 0.0


def extract_parser_confidence(text: str) -> int:
    """Extract numeric confidence/strength from signal text (0 if not found)."""
    raw = text or ""
    patterns = [
        r"\b(?:уверенност|уверен|confiden(?:ce)?|confidence)\s*[:\-–]?\s*(\d{1,3})\s*%?",
        r"\b(?:вероятност|probability)\s*[:\-–]?\s*(\d{1,3})\s*%?",
        r"\b(?:сила\s*сигнала|signal\s*strength)\s*[:\-–]?\s*(\d{1,3})\s*%?",
        r"\b(\d{1,3})\s*%\s*(?:уверен|confidence)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            value = int(safe_float(match.group(1)))
            if 0 <= value <= 100:
                return value
    return 0


def extract_signal_symbol(upper: str, raw: str = "") -> str | None:
    """Тикер из типовых шаблонов каналов (+ $COIN, BASE / USDT, полные имена)."""
    patterns = [
        r"СИГНАЛ\s+ПО\s+#([A-Z0-9]{2,20})\b",
        r"ТОРГОВЫЙ\s+СИГНАЛ\s+ПО\s+#([A-Z0-9]{2,20})\b",
        r"\b(?:SYMBOL|PAIR|TICKER|COIN|BASE|ПАРА|СИМВОЛ|МОНЕТА|ИНСТРУМЕНТ|АКТИВ)\s*[:：.\-–]\s*([A-Z0-9]{2,20})(?:/USDT|USDT|\.P)?\b",
        r"\$\s*([A-Z0-9]{2,20})\b(?![A-Z0-9])",
        r"\b([A-Z0-9]{2,20})\s*[/\\\-\.]+\s*USDT\b(?![A-Z])",
        r"\b([A-Z0-9]{2,20})\s*[/\\\-\.]+\s*USD\b(?![A-Z])",
        r"\b([A-Z0-9]{2,20})(?:/USDT|USDT\.P|\.P\b|USDT|/USD)\b",
        r"#\s*([A-Z0-9]{2,20})\b",
        r"\b(?:BINANCE|BYBIT|OKX|BINGX|GATE|KU|KUCOIN)\s*[:：]?\s*([A-Z0-9]{2,20})(?:\.P|USDT)?\b",
        r"\b([A-Z]{2,15})\s+USDT\b",
        r"\b([A-Z]{2,12})USDT\b",
        r"\b([A-Z0-9]{2,20})\s+PERP\b",
    ]
    seen: set[str] = set()
    for pattern in patterns:
        match = re.search(pattern, upper)
        if not match:
            continue
        sym = normalize_symbol(match.group(1))
        key = sym
        if key in seen:
            continue
        seen.add(key)
        if looks_like_trade_symbol(sym):
            return sym

    u = upper
    items = sorted(((a, b) for a, b in COIN_NAME_TO_BASE.items()), key=lambda ab: -len(ab[0]))
    for alias, base in items:
        if len(alias) < 4:
            continue
        if not re.search(rf"\b{re.escape(alias)}\b", u):
            continue
        sym = normalize_symbol(base)
        if looks_like_trade_symbol(sym):
            return sym

    raw = raw or ""
    m = re.search(r"`\s*([A-Za-z0-9]{2,20})(?:USDT)?\s*`", raw, flags=re.IGNORECASE)
    if m:
        sym = normalize_symbol(m.group(1).upper())
        if looks_like_trade_symbol(sym):
            return sym
    return None


# Сообщения от trading_bot / signal_agent в личный чат — не торговые сигналы
_BOT_ECHO_TEXT_MARKERS = (
    "TELEGRAM SIGNAL AGENT",
    "PRD Agent — отчёт",
    "PRD Unified — панель",
    "PRD Unified",
    "Глобальный анализ",
    "📊 PRD Agent",
    "📊 PRD Unified",
    "🌍 Глобальный анализ",
)


def is_bot_echo_notification_text(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 12:
        return False
    return any(m in t for m in _BOT_ECHO_TEXT_MARKERS)


_SIMPLE_SYMBOL_STOPWORDS = frozenset({
    "LONG",
    "SHORT",
    "TELEGRAM",
    "PRD",
    "SKIPPED",
    "AGENT",
    "UNIFIED",
    "SIGNAL",
    "GLOBAL",
    "ОТЧЁТ",
    "ОТЧЕТ",
    "ПАНЕЛЬ",
    "HIGH",
    "LOW",
    "OPEN",
    "CLOSE",
    "VOLUME",
    "ENTRY",
    "EXIT",
    "STOP",
    "LOSS",
    "TAKE",
    "PROFIT",
    "TARGET",
    "ZONE",
    "PAIR",
    "COIN",
    "BASE",
    "BUY",
    "SELL",
    "ONLY",
    "FROM",
    "WITH",
    "THAT",
    "THIS",
    "TP",
    "SL",
    "USDT",
    "PERP",
    "SPOT",
    "PUMP",
    "DUMP",
})


def extract_signal_symbol_simple(upper: str, raw: str = "") -> str | None:
    """Короткий тикер: слова вроде ETH, SOL, 1000PEPE (без обязательного USDT в тексте)."""
    u = upper or ""
    for m in re.finditer(r"\b([A-Z][A-Z0-9]{1,14})\b(?:\s*/\s*USDT)?", u):
        token = m.group(1)
        if token in _SIMPLE_SYMBOL_STOPWORDS:
            continue
        sym = normalize_symbol(token)
        if looks_like_trade_symbol(sym):
            return sym
    return extract_signal_symbol(upper, raw)


def looks_like_unparsed_signal(text: str) -> bool:
    """Heuristic for optional logging: side cue + possible coin mention."""
    raw = text or ""
    upper = raw.upper()
    has_side = bool(
        re.search(
            r"\b(LONG|BUY|SHORT|SELL)\b|ЛОНГ|ШОРТ|ПОКУП|ПРОДА|ПОКУПКА|ПРОДАЖА|В\s+ЛОНГ|В\s+ШОРТ|🔴|🟢",
            upper,
        )
    )
    has_coin = bool(
        re.search(
            r"[A-Z0-9]{2,15}(?:\s*[/\-.]\s*)?USDT\b|#\s*[A-Z0-9]{2,15}\b|\$[A-Z0-9]{2,15}\b|\bUSDT\b|"
            r"\b(BITCOIN|ETHEREUM|SOLANA|DOGECOIN)\b",
            upper,
        )
    )
    return has_side and has_coin


def parse_signal_text(
    text: str,
    default_leverage: int,
    *,
    simple: bool = False,
    allow_missing_side: bool = False,
    skip_enrich: bool = False,
) -> dict[str, Any] | None:
    raw = text or ""
    upper = raw.upper()

    if simple:
        symbol = extract_signal_symbol_simple(upper, raw) or extract_signal_symbol(upper, raw)
    else:
        symbol = extract_signal_symbol(upper, raw)
    if not symbol:
        return None

    side = resolve_trade_side(raw, symbol)
    if not side and not allow_missing_side:
        return None

    if simple:
        entry = 0.0
        stop_loss = 0.0
        take_profit = 0.0
        if re.search(r"\d", raw):
            entry = extract_price_after(
                [
                    r"\b(?:PRICE|ЦЕНА|ENTRY|ВХОД)\s*[:：]\s*\$?\s*\d",
                    r"\b@\s*\d+(?:[.,]\d+)?",
                ],
                raw,
            )
            stop_loss = extract_price_after(
                [r"\b(?:SL|STOP)\b[^\n:：-]*[:：-]?", r"\bСТОП\b[^\n:：-]*[:：-]?"],
                raw,
                max_chars=200,
            )
            take_profit = extract_price_after(
                [r"\b(?:TP|TARGET)\b[^\n:：-]*[:：-]?", r"\bТЕЙК\b[^\n:：-]*[:：-]?"],
                raw,
                max_chars=240,
            )
    else:
        entry = extract_price_after(
            [
                r"\b(ENTRY|ENTER|BUY ZONE|SELL ZONE|ENTRY\s*ZONE|AVG|AVERAGE)\b[^\n:：-]*[:：-]?",
                r"\b(ТВХ|ТОЧКА ВХОДА|МОЯ ТВХ|ВХОД|ЗОНА\s*ВХОДА)\b[^\n:：-]*[:：-]?",
                r"(?:КУПИТЬ|BUY|LONG|SHORT|SELL)\s+ПО\b[^\n\d]{0,8}[:：]?\s*\$?\s*\d",
                r"(?:^|\n)\s*(?:УРОВЕНЬ\s+)?0[.,]\d+\s*[:：]",
                r"\b@\s*\d+(?:[.,]\d+)?",
                r"\b(?:PRICE|ЦЕНА|КУРС|ЦЕНЫ)\s*[:：]\s*\$?\s*\d",
                r"(?:ЛИМИТ|LIMIT)\s*[:：]\s*\$?\s*\d",
            ],
            raw,
        )
        if entry <= 0:
            entry = extract_first_number(
                [
                    r"\b@\s*\d+(?:[.,]\d+)?",
                    r"(?:~|≈)\s*\d+(?:[.,]\d+)?",
                    r"\b(?:ОТ|FROM|AT)\s+\d+(?:[.,]\d+)?",
                    r"=\s*\d+(?:[.,]\d+)?\s*(?:USDT|usdт)?",
                ],
                raw,
            )
        stop_loss = extract_price_after(
            [
                r"\b(SL\d*|SL|STOP|STOP\s*LOSS)\b[^\n:：-]*[:：-]?",
                r"\b(SL|STOP)\s*\d+\b[^\n:：]{0,14}[:：-]?",
                r"\b(СТОП|СТОП-ЛОСС|СТОП\s*ЛОСС|СТОПЫ)\b[^\n:：-]*[:：-]?",
                r"\b(?:РИСК|RISK)\s*[:：]",
                r"⛔\s*Стоп-лосс",
                r"(?:🛑|❌)\s*(?:Стоп|STOP|SL)",
                r"Уровень\s+Фибоначчи\s*1",
            ],
            raw,
            max_chars=280,
        )
        take_profit = extract_price_after(
            [
                r"\b(TP\d*|TP|TAKE|TAKE\s*PROFIT|TARGET)\b[^\n:：-]*[:：-]?",
                r"\b(ТЕЙКИ|ТЕЙК|ТЕЙК-ПРОФИТ|ТЕЙК\s*ПРОФИТ|ЦЕЛИ?)\b[^\n:：-]*[:：-]?",
                r"Тейк-профит:?\s*\$?",
                r"Тейки:?\s*",
                r"(?:✅|🎯)\s*(?:TP|ТЕЙК|TARGET|ЦЕЛЬ)",
            ],
            raw,
            max_chars=360,
        )
    leverage = int(
        extract_first_number(
            [
                r"\b\d{1,3}\s*[xXхХ]\b",
                r"\b[xXхХ]\s*\d{1,3}\b",
                r"(?:LEVERAGE|ПЛЕЧ|ПЛЕЧО)\s*[:=×xX]?\s*\d{1,3}",
                r"\bLEV\s*[:=]?\s*\d{1,3}\b",
            ],
            raw,
        )
        or default_leverage
    )
    parsed = {
        "symbol": symbol,
        "side": side or "",
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "leverage": max(1, min(leverage, 25)),
        "parser_confidence": extract_parser_confidence(raw),
    }
    if not skip_enrich and str(parsed.get("side") or "").upper() in {"BUY", "SELL"}:
        enrich_parsed_signal_levels(parsed, raw)
    return parsed


def _telegram_retry_after(exc: urllib.error.HTTPError, default_sec: float = 5.0) -> float:
    header_value = exc.headers.get("Retry-After") if exc.headers else None
    if header_value:
        return max(default_sec, safe_float(header_value, default_sec))
    try:
        body = json.loads(exc.read().decode("utf-8", errors="replace"))
        retry_after = ((body.get("parameters") or {}).get("retry_after")) if isinstance(body, dict) else None
        return max(default_sec, safe_float(retry_after, default_sec))
    except Exception:
        return default_sec


def telegram_send(token: str, chat_id: str, text: str, timeout_sec: float = 15.0, max_retries: int = 1) -> bool:
    if not token or not chat_id:
        return False
    chunks = [text[i : i + 3800] for i in range(0, len(text), 3800)] or [text]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in chunks:
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": "true"}
        ).encode("utf-8")
        for attempt in range(max_retries + 1):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST"), timeout=timeout_sec) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                if not body.get("ok"):
                    LOG.warning("Telegram send rejected: %s", body)
                    return False
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < max_retries:
                    delay = min(_telegram_retry_after(exc), 30.0)
                    LOG.warning("Telegram rate limit 429, sleep %.1fs", delay)
                    time.sleep(delay)
                    continue
                LOG.warning("Telegram send HTTP %s: %s", exc.code, exc)
                return False
            except Exception as exc:
                LOG.warning("Telegram send failed: %s", exc)
                return False
    return True


def human_status(action: str, reason: str = "") -> str:
    reason_l = str(reason or "").lower()
    if action == "executed":
        return "ИСПОЛНЕНО"
    if action == "execute_failed":
        return "ОТКЛОНЕНО: ошибка исполнения"
    if action == "scanner_executed":
        return "ИСПОЛНЕНО (сканер)"
    if action == "scanner_execute_failed":
        return "ОШИБКА ИСПОЛНЕНИЯ (сканер)"
    if action == "scanner_blocked":
        return "ОТКЛОНЕНО (сканер)"
    if action == "approved_notify":
        return "ОДОБРЕНО, автоисполнение выключено"
    if action == "rejected_notify":
        return "ОТКЛОНЕНО"
    if action == "duplicate":
        return "ОТКЛОНЕНО: дубль сигнала"
    if action == "risk_reject":
        return "ОТКЛОНЕНО: риск-фильтр"
    if action == "limit_reject":
        return "ОТКЛОНЕНО: лимит исполнения"
    if action == "error":
        if "no stop-loss" in reason_l or "require_stop_loss" in reason_l:
            return "ОТКЛОНЕНО: нет SL / require_stop_loss (строка входа в анализе считается полноценной)"
        if "symbol not listed" in reason_l:
            return "ОТКЛОНЕНО: пары нет на Bybit USDT linear"
        if "cannot resolve price" in reason_l:
            return "ОТКЛОНЕНО: не определена цена входа"
        return "ОТКЛОНЕНО: ошибка конвейера — см. reason в signals.jsonl"
    if "no stop-loss" in reason_l or "require_stop_loss" in reason_l:
        return "ОТКЛОНЕНО: нет стоп-лосса"
    if "symbol not listed" in reason_l:
        return "ОТКЛОНЕНО: пары нет на Bybit USDT linear"
    if "cannot resolve price" in reason_l:
        return "ОТКЛОНЕНО: не определена цена входа"
    return str(action or "ОТКЛОНЕНО")


def openrouter_review(
    cfg: dict[str, Any],
    signal: TelegramSignal,
    timeout_sec: float,
    *,
    budget_agent: Any = None,
    budget_kind: str = "telegram",
) -> dict[str, Any]:
    from prd_agent.ai.llm_gateway import chat_sync, load_llm_settings

    settings = load_llm_settings(cfg)
    if not settings.uses_fcc and not settings.openrouter_api_key:
        return {"approve": True, "confidence": signal.confidence, "reason": "AI key not set"}
    if budget_agent is not None and not settings.uses_fcc:
        ok, bmsg = budget_agent._openrouter_budget_allow(budget_kind)
        if not ok:
            return {"approve": False, "confidence": 0, "reason": bmsg}
    prompt = f"""Проверь торговый сигнал на фьючерсы Bybit.
Верни только JSON:
{{"approve": true/false, "confidence": 0-100, "reason": "кратко"}}

Сигнал:
symbol={signal.symbol}
side={signal.side}
entry={signal.entry}
sl={signal.stop_loss}
tp={signal.take_profit}
leverage={signal.leverage}
market_regime={signal.market_regime}

Исходный текст:
{signal.raw_text[:2500]}
"""
    body, err = chat_sync(
        settings,
        system="You are a conservative crypto futures signal risk filter. Capital protection first.",
        user=prompt,
        max_tokens=180,
        temperature=0.1,
        title="PRD-BOT Telegram Signal Agent",
        timeout_sec=timeout_sec,
    )
    if err:
        label = "FCC" if settings.uses_fcc else "OpenRouter"
        return {"approve": False, "confidence": 0, "reason": f"{label}: {err}"}
    if budget_agent is not None and body and not settings.uses_fcc:
        budget_agent._openrouter_budget_record(budget_kind, body)
    _msg = ((body or {}).get("choices") or [{}])[0].get("message") or {}
    text = str(_msg.get("content") or "")
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        text = text[start : end + 1]
    try:
        out = json.loads(text)
    except Exception:
        return {"approve": False, "confidence": 0, "reason": "OpenRouter JSON parse error"}
    return {
        "approve": bool(out.get("approve", False)),
        "confidence": max(0, min(100, int(safe_float(out.get("confidence"), 0)))),
        "reason": str(out.get("reason", ""))[:300],
    }


def openrouter_world_extract(
    cfg: dict[str, Any],
    *,
    title: str,
    summary: str,
    link: str,
    timeout_sec: float,
    max_summary_chars: int,
    budget_agent: Any = None,
    budget_kind: str = "world",
) -> dict[str, Any]:
    """LLM: из новости извлечь гипотезу сделки Bybit USDT linear или has_trade=false."""
    from prd_agent.ai.llm_gateway import chat_sync, load_llm_settings

    settings = load_llm_settings(cfg)
    if not settings.uses_fcc and not settings.openrouter_api_key:
        return {"has_trade": False, "confidence": 0, "reason": "AI key not set"}
    if budget_agent is not None and not settings.uses_fcc:
        ok, bmsg = budget_agent._openrouter_budget_allow(budget_kind)
        if not ok:
            return {"has_trade": False, "confidence": 0, "reason": bmsg}
    body = (summary or "")[: max(200, max_summary_chars)]
    prompt = f"""По фрагменту новости определи, есть ли ЯВНАЯ торговая идея для linear USDT perpetual на Bybit (не спот, не другие биржи, не стейкинг).
Верни ТОЛЬКО JSON:
- Если идеи нет: {{"has_trade": false, "confidence": 0-100, "reason": "кратко"}}
- Если идея есть: {{"has_trade": true, "symbol": "Напр. BTCUSDT", "side": "BUY" или "SELL", "entry": число или 0, "stop_loss": число или 0, "take_profit": число или 0, "leverage": целое, "confidence": 0-100, "reason": "кратко"}}

Заголовок: {title}
Текст: {body}
Ссылка: {link}
"""
    data, err = chat_sync(
        settings,
        system="Conservative extractor. Only Bybit USDT linear perps. Output JSON only.",
        user=prompt,
        max_tokens=420,
        temperature=0.05,
        title="PRD-BOT Agent-World Extract",
        timeout_sec=timeout_sec,
    )
    if err:
        label = "FCC" if settings.uses_fcc else "OpenRouter"
        return {"has_trade": False, "confidence": 0, "reason": f"{label}: {err}"}
    if budget_agent is not None and data and not settings.uses_fcc:
        budget_agent._openrouter_budget_record(budget_kind, data)
    _msg = ((data or {}).get("choices") or [{}])[0].get("message") or {}
    text = str(_msg.get("content") or "")
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        text = text[start : end + 1]
    try:
        out = json.loads(text)
    except Exception:
        return {"has_trade": False, "confidence": 0, "reason": "OpenRouter JSON parse error (world)"}
    if not bool(out.get("has_trade")):
        return {
            "has_trade": False,
            "confidence": max(0, min(100, int(safe_float(out.get("confidence"), 0)))),
            "reason": str(out.get("reason", ""))[:320],
        }
    return {
        "has_trade": True,
        "symbol": str(out.get("symbol", "")).upper().strip(),
        "side": str(out.get("side", "")).upper().strip(),
        "entry": safe_float(out.get("entry"), 0.0),
        "stop_loss": safe_float(out.get("stop_loss"), 0.0),
        "take_profit": safe_float(out.get("take_profit"), 0.0),
        "leverage": max(1, int(safe_float(out.get("leverage", 10), 10))),
        "confidence": max(0, min(100, int(safe_float(out.get("confidence"), 0)))),
        "reason": str(out.get("reason", ""))[:320],
    }


class TelegramSignalAgent:
    def __init__(self, repo_dir: Path):
        self.repo_dir = repo_dir
        ensure_telegram_agent_logging(repo_dir)
        if load_dotenv is not None:
            load_dotenv(repo_dir / ".env", override=True)
        self.cfg = load_yaml(repo_dir / "config.yaml")
        self.agent_cfg = self.cfg.get("telegram_signal_agent", {})
        if not isinstance(self.agent_cfg, dict):
            self.agent_cfg = {}
        self.enabled = bool(self.agent_cfg.get("enabled", True))
        self.auto_execute = bool(self.agent_cfg.get("auto_execute", False))
        self.telegram_notify = bool(self.agent_cfg.get("telegram_notify", True))
        self.allowed_chats = list(self.agent_cfg.get("allowed_chats", []) or [])
        _ign_raw = list(self.agent_cfg.get("ignored_chats", []) or [])
        self.ignored_chats = {normalize_chat_name(x) for x in _ign_raw}
        self.ignored_chats_compact: set[str] = set()
        for x in _ign_raw:
            c = _chat_name_compact(x)
            if len(c) >= 3:
                self.ignored_chats_compact.add(c)
        for sub in list(self.agent_cfg.get("ignored_substrings", []) or []):
            c = _chat_name_compact(str(sub))
            if len(c) >= 4:
                self.ignored_chats_compact.add(c)
        self._ignored_peer_ids: set[int] = set()
        for raw_pid in list(self.agent_cfg.get("ignored_peer_ids", []) or []):
            try:
                self._ignored_peer_ids.add(int(raw_pid))
            except (TypeError, ValueError):
                pass
        _notify_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if _notify_chat.lstrip("-").isdigit():
            self._ignored_peer_ids.add(int(_notify_chat))
        self.trusted_signal_sources = {
            normalize_chat_name(x) for x in (self.agent_cfg.get("trusted_signal_sources", []) or [])
        }
        self.notify_invalid_symbols = bool(self.agent_cfg.get("notify_invalid_symbols", False))
        self.log_unparsed_signal_hints = bool(self.agent_cfg.get("log_unparsed_signal_hints", False))
        self.signal_parse_simple = bool(self.agent_cfg.get("signal_parse_simple", False))
        self.infer_side_from_sr = bool(self.agent_cfg.get("infer_side_from_sr", False))
        self.photo_ocr_enabled = bool(self.agent_cfg.get("photo_ocr_enabled", False))
        self.photo_ocr_max_bytes = int(self.agent_cfg.get("photo_ocr_max_bytes", 4_194_304) or 4_194_304)
        self.infer_sr_near_tolerance_pct = float(self.agent_cfg.get("infer_sr_near_tolerance_pct", 0.35) or 0.35)
        self.max_chats_once = int(self.agent_cfg.get("max_chats_once", 25))
        self.scan_delay_sec = float(self.agent_cfg.get("scan_delay_sec", 0.35))
        self.lookback_hours = float(self.agent_cfg.get("lookback_hours", 6.0))
        self.duplicate_signal_cooldown_sec = int(
            self.agent_cfg.get("duplicate_signal_cooldown_sec", 1800)
        )
        self.timezone_offset = int(get_cfg(self.cfg, "timezone_offset", "", 3) or 3)
        self.default_leverage = int(self.agent_cfg.get("default_leverage", get_cfg(self.cfg, "trading", "leverage", 10)))
        self.max_leverage = int(self.agent_cfg.get("max_leverage", 10))
        self.margin_usdt = float(self.agent_cfg.get("margin_usdt", 3.0))
        self.max_notional_usdt = float(self.agent_cfg.get("max_notional_usdt", 30.0))
        self.execution_balance_reserve_pct = float(self.agent_cfg.get("execution_balance_reserve_pct", 18))
        self.market_scan_post_exec_delay_sec = float(self.agent_cfg.get("market_scan_post_exec_delay_sec", 2.0))
        self.auto_execute_max_open_positions = int(self.agent_cfg.get("auto_execute_max_open_positions", 0) or 0)
        self.default_sl_pct = float(self.agent_cfg.get("default_sl_pct", 1.2))
        self.default_tp_pct = float(self.agent_cfg.get("default_tp_pct", 2.4))
        self.require_stop_loss = bool(self.agent_cfg.get("require_stop_loss", True))
        _sq = self.agent_cfg.get("signal_quality", {})
        self.signal_quality_cfg = _sq if isinstance(_sq, dict) else {}
        self.notify_only_approved = bool(self.agent_cfg.get("notify_only_approved", True))
        self.audit_jsonl_enabled = bool(self.agent_cfg.get("audit_jsonl_enabled", True))
        self.inbox_jsonl_enabled = bool(self.agent_cfg.get("inbox_jsonl_enabled", True))
        rel_inbox = str(
            self.agent_cfg.get("inbox_jsonl", "reports/telegram_signals/signals_inbox.jsonl")
        )
        self.inbox_jsonl = (repo_dir / rel_inbox).resolve()
        if self.inbox_jsonl_enabled:
            self.inbox_jsonl.parent.mkdir(parents=True, exist_ok=True)
        _cp = self.agent_cfg.get("channel_prune", {})
        self.channel_prune_cfg = _cp if isinstance(_cp, dict) else {}
        self.allow_auto_take_profit = bool(self.agent_cfg.get("allow_auto_take_profit", True))
        self.min_openrouter_confidence = int(self.agent_cfg.get("min_openrouter_confidence", 65))
        self.min_openrouter_confidence_trusted = int(
            self.agent_cfg.get("min_openrouter_confidence_trusted", 55)
        )
        self.auto_execute_require_high_signal = bool(
            self.agent_cfg.get("auto_execute_require_high_signal", True)
        )
        self.auto_execute_min_parser_confidence = int(
            self.agent_cfg.get("auto_execute_min_parser_confidence", 82)
        )
        self.auto_execute_min_ai_confidence = int(
            self.agent_cfg.get("auto_execute_min_ai_confidence", 55)
        )
        self.level_zone_buffer_pct = float(self.agent_cfg.get("level_zone_buffer_pct", 0.05))
        _sr_iv = str(self.agent_cfg.get("market_scanner_interval", "5"))
        _srz = self.agent_cfg.get("execution_sr_zones")
        if isinstance(_srz, dict):
            self.sr_exec_enabled = bool(_srz.get("enabled", True))
            self.sr_exec_kline_interval = str(_srz.get("kline_interval") or _sr_iv)
            self.sr_exec_kline_limit = int(_srz.get("kline_limit", 120) or 120)
            self.sr_exec_sl_extra_atr = float(_srz.get("sl_extra_buffer_atr", 0.1) or 0.0)
            self.sr_exec_tp_extra_atr = float(_srz.get("tp_extra_buffer_atr", 0.08) or 0.0)
        else:
            self.sr_exec_enabled = False
            self.sr_exec_kline_interval = _sr_iv
            self.sr_exec_kline_limit = 120
            self.sr_exec_sl_extra_atr = 0.1
            self.sr_exec_tp_extra_atr = 0.08
        self.use_bot_default_sl_tp_on_execute = bool(
            self.agent_cfg.get("use_bot_default_sl_tp_on_execute", True)
        )
        self.channel_rating_enabled = bool(self.agent_cfg.get("channel_rating_enabled", True))
        self.channel_rating_initial_score = float(self.agent_cfg.get("channel_rating_initial_score", 50))
        self.channel_rating_win_delta = float(self.agent_cfg.get("channel_rating_win_delta", 5))
        self.channel_rating_loss_delta = float(self.agent_cfg.get("channel_rating_loss_delta", 5))
        self.channel_rating_eval_after_min = float(self.agent_cfg.get("channel_rating_eval_after_min", 30))
        self.channel_rating_timeout_hours = float(self.agent_cfg.get("channel_rating_timeout_hours", 24))
        self.channel_rating_use_virtual_levels = bool(self.agent_cfg.get("channel_rating_use_virtual_levels", True))
        self.channel_rating_check_interval_sec = float(self.agent_cfg.get("channel_rating_check_interval_sec", 300))
        self.channel_rating_max_pending = int(self.agent_cfg.get("channel_rating_max_pending", 500))
        # По умолчанию включено: агент параллельно с Telethon крутит Bybit market scan (уведомления MARKET SCANNER).
        # Выключить можно явно: telegram_signal_agent.market_scanner_enabled: false
        self.market_scanner_enabled = bool(self.agent_cfg.get("market_scanner_enabled", True))
        self.market_scanner_interval_sec = float(self.agent_cfg.get("market_scanner_interval_sec", 600))
        self.market_scanner_min_24h_volume_usdt = float(
            self.agent_cfg.get("market_scanner_min_24h_volume_usdt", 5_000_000)
        )
        self.market_scanner_max_symbols = int(self.agent_cfg.get("market_scanner_max_symbols", 80))
        self.market_scanner_interval = str(self.agent_cfg.get("market_scanner_interval", "5"))
        self.market_scanner_klines_limit = int(self.agent_cfg.get("market_scanner_klines_limit", 120))
        self.market_scanner_consolidation_bars = int(self.agent_cfg.get("market_scanner_consolidation_bars", 36))
        self.market_scanner_max_range_pct = float(self.agent_cfg.get("market_scanner_max_range_pct", 4.5))
        self.market_scanner_max_atr_pct = float(self.agent_cfg.get("market_scanner_max_atr_pct", 0.75))
        self.market_scanner_bos_buffer_pct = float(self.agent_cfg.get("market_scanner_bos_buffer_pct", 0.12))
        self.market_scanner_min_volume_ratio = float(self.agent_cfg.get("market_scanner_min_volume_ratio", 1.4))
        self.market_scanner_min_fvg_pct = float(self.agent_cfg.get("market_scanner_min_fvg_pct", 0.12))
        self.market_scanner_min_score_to_notify = int(self.agent_cfg.get("market_scanner_min_score_to_notify", 65))
        self.market_scanner_symbol_cooldown_sec = int(self.agent_cfg.get("market_scanner_symbol_cooldown_sec", 7200))
        self.market_scanner_top_n = int(self.agent_cfg.get("market_scanner_top_n", 5))
        self.market_scanner_auto_execute_default = bool(self.agent_cfg.get("market_scanner_auto_execute", False))
        self.market_scanner_execute_min_score = int(self.agent_cfg.get("market_scanner_execute_min_score", 75))
        self.market_scanner_execute_require_confirmed_bos = bool(
            self.agent_cfg.get("market_scanner_execute_require_confirmed_bos", False)
        )
        self.market_scanner_stretch_tp_to_min_rr = bool(
            self.agent_cfg.get("market_scanner_stretch_tp_to_min_rr", True)
        )
        # Кнопки unified-бота (run_unified) — тот же TELEGRAM_TOKEN; панель агента по умолчанию выкл.
        self.control_panel_enabled = bool(self.agent_cfg.get("control_panel_enabled", False))
        if os.getenv("PRD_UNIFIED_POLLING", "").strip().lower() in ("1", "true", "yes", "on"):
            self.control_panel_enabled = False
        self.market_scanner_learning_enabled = bool(self.agent_cfg.get("market_scanner_learning_enabled", True))
        self.market_scanner_learning_timeout_hours = float(
            self.agent_cfg.get("market_scanner_learning_timeout_hours", 6)
        )
        self.market_scanner_learning_min_events = int(self.agent_cfg.get("market_scanner_learning_min_events", 6))
        self.market_scanner_learning_window = int(self.agent_cfg.get("market_scanner_learning_window", 30))
        self.market_scanner_min_score_floor = int(self.agent_cfg.get("market_scanner_min_score_floor", 60))
        self.market_scanner_min_score_ceiling = int(self.agent_cfg.get("market_scanner_min_score_ceiling", 82))
        self.market_scanner_min_volume_ratio_floor = float(
            self.agent_cfg.get("market_scanner_min_volume_ratio_floor", 1.1)
        )
        self.market_scanner_min_volume_ratio_ceiling = float(
            self.agent_cfg.get("market_scanner_min_volume_ratio_ceiling", 2.2)
        )
        self.market_scanner_max_range_pct_floor = float(self.agent_cfg.get("market_scanner_max_range_pct_floor", 2.2))
        self.market_scanner_max_range_pct_ceiling = float(
            self.agent_cfg.get("market_scanner_max_range_pct_ceiling", 6.0)
        )
        self.market_scanner_max_atr_pct_floor = float(self.agent_cfg.get("market_scanner_max_atr_pct_floor", 0.35))
        self.market_scanner_max_atr_pct_ceiling = float(self.agent_cfg.get("market_scanner_max_atr_pct_ceiling", 1.1))
        self.market_scanner_blacklist = {
            str(symbol).upper() for symbol in get_cfg(self.cfg, "trading", "blacklist_symbols", []) or []
        }
        self.openrouter_model = str(
            self.agent_cfg.get("openrouter_model")
            or get_cfg(self.cfg, "openrouter", "model", "google/gemini-2.0-flash-001")
        )
        self.openrouter_timeout_sec = float(self.agent_cfg.get("openrouter_timeout_sec", 25))
        _p1k = self.agent_cfg.get("openrouter_price_per_1k_tokens_usd")
        _fallback_price = float(
            get_cfg(self.cfg, "openrouter", "price_per_1k_tokens_usd", 0.00012) or 0.00012
        )
        if _p1k is not None and str(_p1k).strip() != "":
            v = float(_p1k)
            self.openrouter_price_per_1k_usd = v if v > 0 else _fallback_price
        else:
            self.openrouter_price_per_1k_usd = _fallback_price
        self.openrouter_daily_budget_telegram_usd = float(self.agent_cfg.get("openrouter_daily_budget_usd", 0) or 0)
        self.state_path = repo_dir / str(self.agent_cfg.get("state_path", "telegram_signal_agent_state.json"))
        self.out_dir = repo_dir / str(self.agent_cfg.get("out_dir", "reports/telegram_signals"))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.signals_jsonl = self.out_dir / "signals.jsonl"
        self.state = self._load_state()
        self._ensure_runtime_controls_defaults()
        state_changed = self._restore_market_scanner_learning_events_from_file()
        self._apply_market_scanner_adaptive_filters()
        if self._recalculate_market_scanner_adaptive_filters():
            state_changed = True
        if state_changed:
            self._save_state()
        self.risk_pipeline = RiskPipeline.from_agent_cfg(self.agent_cfg.get("risk_guards"))
        self.exec_limiter = ExecutionLimiter.from_agent_cfg(self.state, self.agent_cfg.get("execution_limits"))
        _ab_raw = self.agent_cfg.get("channel_auto_block", {})
        self.channel_auto_block_cfg = channel_auto_block_from_cfg(_ab_raw if isinstance(_ab_raw, dict) else None)
        self.channel_auto_block_notify_telegram = bool(_ab_raw.get("notify_telegram", True)) if isinstance(_ab_raw, dict) else True
        _mr_raw = self.agent_cfg.get("market_regime")
        self.market_regime_cfg = market_regime_from_agent_cfg(_mr_raw if isinstance(_mr_raw, dict) else None)
        self.regime_min_confidence_factors: dict[str, float] = {"trend": 0.92, "chop": 1.12, "unknown": 1.0}
        _rw = self.agent_cfg.get("regime_min_confidence_factors")
        if isinstance(_rw, dict):
            for k, v in _rw.items():
                try:
                    self.regime_min_confidence_factors[str(k).lower()] = float(v)
                except (TypeError, ValueError):
                    pass
        _se_raw = self.agent_cfg.get("signal_excursion")
        self.signal_excursion_cfg = signal_excursion_from_agent_cfg(_se_raw if isinstance(_se_raw, dict) else None)
        self.signal_excursion_jsonl = self.out_dir / "signal_mfe_mae.jsonl"
        _cdr = self.agent_cfg.get("channel_daily_report")
        if isinstance(_cdr, dict):
            self.channel_daily_report_enabled = bool(_cdr.get("enabled", False))
            self.channel_daily_report_hour_utc = max(0, min(23, int(_cdr.get("hour_utc", 8) or 8)))
            self.channel_daily_report_minute_utc = max(0, min(59, int(_cdr.get("minute_utc", 0) or 0)))
        else:
            self.channel_daily_report_enabled = False
            self.channel_daily_report_hour_utc = 8
            self.channel_daily_report_minute_utc = 0
        _aw = self.cfg.get("agent_world")
        if isinstance(_aw, dict):
            self.agent_world_enabled = bool(_aw.get("enabled", False))
            self.agent_world_queue_path = repo_dir / str(_aw.get("queue_path", "reports/world/world_events.jsonl"))
            self.agent_world_poll_sec = float(_aw.get("poll_interval_sec", 180))
            self.agent_world_allow_auto_exec = bool(_aw.get("allow_auto_execute", False))
            self.agent_world_max_summary_chars = int(_aw.get("max_summary_chars", 2200) or 2200)
            self.openrouter_daily_budget_world_usd = float(_aw.get("openrouter_daily_budget_usd", 0) or 0)
        else:
            self.agent_world_enabled = False
            self.agent_world_queue_path = repo_dir / "reports/world/world_events.jsonl"
            self.agent_world_poll_sec = 180.0
            self.agent_world_allow_auto_exec = False
            self.agent_world_max_summary_chars = 2200
            self.openrouter_daily_budget_world_usd = 0.0
        if self.channel_auto_block_cfg.enabled:
            newly_b = refresh_auto_blocks(
                self.state,
                self.channel_auto_block_cfg,
                trusted_keys=self.trusted_signal_sources,
                now=utc_now(),
            )
            if newly_b:
                self._save_state()
                if self.channel_auto_block_notify_telegram:
                    self._notify_channel_auto_blocks(newly_b)
                for key, reason in newly_b:
                    LOG.warning("Channel auto-block: %s (%s)", key, reason)
        self.bybit: BybitClient | None = None
        self.execution: ExecutionEngine | None = None
        self._valid_symbols: set[str] | None = None

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {"seen": []}

    def _save_state(self) -> None:
        seen = list(dict.fromkeys(self.state.get("seen", [])))[-5000:]
        self.state["seen"] = seen
        self._cleanup_signal_fingerprints()
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, ensure_ascii=False, indent=2)

    def _runtime_controls_dict(self) -> dict[str, Any]:
        node = self.state.get("agent_runtime_controls")
        if isinstance(node, dict):
            return node
        blank: dict[str, Any] = {}
        self.state["agent_runtime_controls"] = blank
        return blank

    def _ensure_runtime_controls_defaults(self) -> None:
        """Персистентные флаги (панель + рестарт). По умолчанию синхронизируются из config.yaml при старте."""
        rtc = self._runtime_controls_dict()
        rtc.setdefault("pause_all_execution", False)
        sync_yaml = bool(self.agent_cfg.get("runtime_controls_sync_yaml", True))
        if sync_yaml:
            rtc["channel_auto_execute"] = bool(self.auto_execute)
            rtc["market_scanner_auto_execute"] = bool(self.market_scanner_auto_execute_default)
        else:
            rtc.setdefault("channel_auto_execute", bool(self.auto_execute))
            rtc.setdefault("market_scanner_auto_execute", bool(self.market_scanner_auto_execute_default))

    def _effective_channel_auto_execute(self) -> bool:
        rtc = self._runtime_controls_dict()
        if bool(rtc.get("pause_all_execution")):
            return False
        return bool(rtc.get("channel_auto_execute", self.auto_execute))

    def _effective_market_scanner_auto_execute(self) -> bool:
        rtc = self._runtime_controls_dict()
        if bool(rtc.get("pause_all_execution")):
            return False
        return bool(rtc.get("market_scanner_auto_execute", self.market_scanner_auto_execute_default))

    def _any_live_execution_enabled(self) -> bool:
        return self._effective_channel_auto_execute() or self._effective_market_scanner_auto_execute()

    def _sync_execution_dry_run(self) -> None:
        if self.execution is None:
            return
        controls = getattr(self.execution, "controls", None)
        if controls is None:
            return
        setattr(controls, "dry_run", not self._any_live_execution_enabled())

    def _market_scan_telegram_footer(self) -> str:
        if self._effective_market_scanner_auto_execute():
            return (
                "──────────────\n"
                "Исполнение:\n"
                "• При «MARKET SCANNER→Bybit» агент пытается открыть ордер после этого наблюдения "
                "(без OpenRouter). Размер: margin_usdt×leverage из config, ограничен max_notional_usdt.\n"
                "• SL = «Отмена сценария», TP = «Цель» из текста выше.\n"
                "• Управление авто можно переключать кнопками (/start или /panel в этом чате с ботом).\n"
            )
        return (
            "──────────────\n"
            "Что это значит:\n"
            "• Сканер прислал идею. Чтобы включить попытку реальных ордеров после таких наблюдений — "
            "включите «MARKET SCANNER→Bybit» в Telegram-панели агента (/start).\n"
            "• Для каналов отдельно настраивается авто после AI через кнопку «Каналы auto» или config.\n"
        )

    def _openrouter_budget_reset_if_needed(self) -> None:
        node = self.state.setdefault("openrouter_budget", {})
        day = utc_now().strftime("%Y-%m-%d")
        if str(node.get("day", "")) != day:
            node["day"] = day
            node["telegram"] = {"usd_approx": 0.0, "calls": 0, "tokens": 0}
            node["world"] = {"usd_approx": 0.0, "calls": 0, "tokens": 0}

    def _openrouter_budget_allow(self, kind: str) -> tuple[bool, str]:
        self._openrouter_budget_reset_if_needed()
        cap = (
            self.openrouter_daily_budget_telegram_usd
            if kind == "telegram"
            else self.openrouter_daily_budget_world_usd
        )
        if cap <= 0:
            return True, ""
        sub = (self.state.get("openrouter_budget", {}) or {}).get(kind)
        spent = float(sub.get("usd_approx", 0) or 0) if isinstance(sub, dict) else 0.0
        if spent >= cap - 1e-9:
            return False, f"OpenRouter: суточный бюджет ~${cap:.2f} исчерпан ({kind})"
        return True, ""

    def _openrouter_budget_record(self, kind: str, response_body: dict[str, Any]) -> None:
        self._openrouter_budget_reset_if_needed()
        usage = response_body.get("usage") if isinstance(response_body, dict) else None
        total = 0
        if isinstance(usage, dict):
            total = int(safe_float(usage.get("total_tokens"), 0.0))
        est = (total / 1000.0) * float(self.openrouter_price_per_1k_usd)
        node_parent = self.state.setdefault("openrouter_budget", {})
        node = node_parent.setdefault(kind, {"usd_approx": 0.0, "calls": 0, "tokens": 0})
        node["usd_approx"] = float(node.get("usd_approx", 0)) + est
        node["calls"] = int(node.get("calls", 0)) + 1
        node["tokens"] = int(node.get("tokens", 0)) + total
        try:
            self._save_state()
        except Exception:
            pass

    def _seen_key(self, source: str, message_id: int) -> str:
        return f"{source}:{message_id}"

    def _mark_seen(self, source: str, message_id: int) -> None:
        self.state.setdefault("seen", []).append(self._seen_key(source, message_id))
        self._save_state()

    def _is_seen(self, source: str, message_id: int) -> bool:
        return self._seen_key(source, message_id) in set(self.state.get("seen", []))

    def _is_trusted_source(self, source: str) -> bool:
        if not self.trusted_signal_sources:
            return False
        return normalize_chat_name(source) in self.trusted_signal_sources

    def _apply_level_zone_buffer(self, parsed: dict[str, Any]) -> None:
        """Widen SL/TP slightly from parsed zone levels (percent of price move direction)."""
        buf = self.level_zone_buffer_pct / 100.0
        if buf <= 0:
            return
        side = str(parsed.get("side", "")).upper()
        sl = float(parsed.get("stop_loss") or 0.0)
        tp = float(parsed.get("take_profit") or 0.0)
        if side == "BUY":
            if sl > 0:
                sl = sl * (1.0 - buf)
            if tp > 0:
                tp = tp * (1.0 + buf)
        elif side == "SELL":
            if sl > 0:
                sl = sl * (1.0 + buf)
            if tp > 0:
                tp = tp * (1.0 - buf)
        parsed["stop_loss"] = sl
        parsed["take_profit"] = tp

    def _apply_bot_default_sl_tp_to_signal(self, signal: TelegramSignal) -> None:
        """Replace SL/TP with default_sl_pct / default_tp_pct (+ level buffer) for live execution."""
        entry = float(signal.entry)
        side = str(signal.side).upper()
        if entry <= 0 or side not in {"BUY", "SELL"}:
            return
        sl = entry * (1 - self.default_sl_pct / 100.0) if side == "BUY" else entry * (1 + self.default_sl_pct / 100.0)
        tp = entry * (1 + self.default_tp_pct / 100.0) if side == "BUY" else entry * (1 - self.default_tp_pct / 100.0)
        tmp = {"side": side, "stop_loss": sl, "take_profit": tp}
        self._apply_level_zone_buffer(tmp)
        signal.stop_loss = float(tmp["stop_loss"])
        signal.take_profit = float(tmp["take_profit"])

    async def _apply_sr_zones_to_signal(self, signal: TelegramSignal) -> bool:
        """Поджимает SL/TP к зонам SMC (поддержка/сопротивление + доп. отступ в единицах ATR)."""
        if not getattr(self, "sr_exec_enabled", False):
            return False
        try:
            await self._ensure_execution()
        except Exception as exc:
            LOG.debug("SR zones: execution init: %s", exc)
            return False
        if self.bybit is None:
            return False
        try:
            kl = await self.bybit.get_klines(
                str(signal.symbol).upper(),
                interval=str(self.sr_exec_kline_interval),
                limit=max(20, int(self.sr_exec_kline_limit)),
            )
        except Exception as exc:
            LOG.warning("SR zones: klines %s: %s", signal.symbol, exc)
            return False
        try:
            min_rr_ke = 0.0
            if getattr(self, "risk_pipeline", None) and self.risk_pipeline.cfg.enabled:
                min_rr_ke = float(self.risk_pipeline.cfg.min_rr or 0.0)
            changed = adjust_telegram_sl_tp_with_sr_zones(
                signal,
                kl,
                sl_extra_atr=self.sr_exec_sl_extra_atr,
                tp_extra_atr=self.sr_exec_tp_extra_atr,
                preserve_min_rr=min_rr_ke,
            )
        except Exception as exc:
            LOG.warning("SR zones: adjust %s: %s", signal.symbol, exc)
            return False
        if changed:
            LOG.info(
                "SR zones (поддержка/сопротивление): %s %s SL=%s TP=%s",
                signal.symbol,
                signal.side,
                signal.stop_loss,
                signal.take_profit,
            )
        return changed

    @staticmethod
    def _signal_fingerprint(source: str, parsed: dict[str, Any]) -> str:
        return "|".join(
            [
                re.sub(r"\s+", " ", str(source or "").strip().lower()),
                str(parsed.get("symbol", "")).upper(),
                str(parsed.get("side", "")).upper(),
                f"{safe_float(parsed.get('entry')):.10g}",
                f"{safe_float(parsed.get('stop_loss')):.10g}",
                f"{safe_float(parsed.get('take_profit')):.10g}",
            ]
        )

    def _cleanup_signal_fingerprints(self) -> None:
        rows = self.state.get("signal_fingerprints", {})
        if not isinstance(rows, dict):
            self.state["signal_fingerprints"] = {}
            return
        if self.duplicate_signal_cooldown_sec <= 0:
            self.state["signal_fingerprints"] = rows
            return
        cutoff = utc_now() - timedelta(seconds=self.duplicate_signal_cooldown_sec)
        kept = {}
        for key, value in rows.items():
            try:
                ts = datetime.fromisoformat(str(value))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    kept[key] = value
            except Exception:
                continue
        self.state["signal_fingerprints"] = kept

    def _is_duplicate_signal(self, fingerprint: str) -> bool:
        if self.duplicate_signal_cooldown_sec <= 0:
            return False
        self._cleanup_signal_fingerprints()
        return fingerprint in self.state.get("signal_fingerprints", {})

    def _mark_signal_fingerprint(self, fingerprint: str) -> None:
        if self.duplicate_signal_cooldown_sec <= 0:
            return
        rows = self.state.setdefault("signal_fingerprints", {})
        if isinstance(rows, dict):
            rows[fingerprint] = utc_now().isoformat()

    def _is_recent_message(self, message_dt: datetime | None) -> bool:
        if self.lookback_hours <= 0:
            return True
        if message_dt is None:
            return False
        dt = message_dt
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_sec = (utc_now() - dt.astimezone(timezone.utc)).total_seconds()
        return age_sec <= self.lookback_hours * 3600

    def _channel_rating(self, source: str) -> dict[str, Any]:
        key = normalize_chat_name(source)
        ratings = self.state.setdefault("channel_ratings", {})
        if not isinstance(ratings, dict):
            ratings = {}
            self.state["channel_ratings"] = ratings
        row = ratings.setdefault(
            key,
            {
                "source": source,
                "score": self.channel_rating_initial_score,
                "wins": 0,
                "losses": 0,
                "neutrals": 0,
                "last_signal_at": "",
                "last_update_at": "",
            },
        )
        row["source"] = source or row.get("source", "")
        row["score"] = max(0.0, min(100.0, safe_float(row.get("score"), self.channel_rating_initial_score)))
        return row

    def _format_channel_rating(self, source: str) -> str:
        row = self._channel_rating(source)
        score = safe_float(row.get("score"), self.channel_rating_initial_score)
        return (
            f"{score:.0f}/100 "
            f"(плюс:{int(row.get('wins', 0) or 0)} "
            f"минус:{int(row.get('losses', 0) or 0)} "
            f"нейтр:{int(row.get('neutrals', 0) or 0)})"
        )

    def _rating_levels(self, signal: TelegramSignal) -> tuple[float, float, str]:
        sl = safe_float(signal.stop_loss)
        tp = safe_float(signal.take_profit)
        if signal.entry <= 0 or signal.side not in {"BUY", "SELL"}:
            return 0.0, 0.0, "missing"
        if sl > 0 and tp > 0:
            return sl, tp, "signal"
        if not self.channel_rating_use_virtual_levels:
            return sl, tp, "incomplete"
        if sl <= 0:
            sl = (
                signal.entry * (1 - self.default_sl_pct / 100.0)
                if signal.side == "BUY"
                else signal.entry * (1 + self.default_sl_pct / 100.0)
            )
        if tp <= 0:
            tp = (
                signal.entry * (1 + self.default_tp_pct / 100.0)
                if signal.side == "BUY"
                else signal.entry * (1 - self.default_tp_pct / 100.0)
            )
        return sl, tp, "virtual"

    def _is_ignored_source(self, source: str) -> bool:
        """True if source matches ignored_chats (exact or compact/substring for Telegram title quirks)."""
        n = normalize_chat_name(source)
        if n in self.ignored_chats:
            return True
        compact = _chat_name_compact(source)
        if not compact:
            return False
        if compact in self.ignored_chats_compact:
            return True
        for ign in self.ignored_chats_compact:
            if len(ign) >= 6 and ign in compact:
                return True
        return False

    def _track_signal_for_rating(self, signal: TelegramSignal) -> None:
        if not self.channel_rating_enabled:
            return
        if self._is_trusted_source(signal.source):
            return
        if signal.entry <= 0 or signal.side not in {"BUY", "SELL"} or not signal.symbol:
            return
        sl, tp, level_source = self._rating_levels(signal)
        if sl <= 0 or tp <= 0:
            return
        source_key = normalize_chat_name(signal.source)
        track_id = f"{source_key}:{signal.message_id}:{signal.symbol}:{signal.side}"
        pending = self.state.setdefault("pending_signal_reviews", [])
        if not isinstance(pending, list):
            pending = []
            self.state["pending_signal_reviews"] = pending
        if any(str(item.get("id", "")) == track_id for item in pending if isinstance(item, dict)):
            return
        now = utc_now()
        self._channel_rating(signal.source)["last_signal_at"] = now.isoformat()
        pending.append(
            {
                "id": track_id,
                "source": signal.source,
                "source_key": source_key,
                "message_id": signal.message_id,
                "symbol": signal.symbol,
                "side": signal.side,
                "entry": signal.entry,
                "stop_loss": sl,
                "take_profit": tp,
                "level_source": level_source,
                "created_at": now.isoformat(),
                "evaluate_after": (now + timedelta(minutes=self.channel_rating_eval_after_min)).isoformat(),
                "expires_at": (now + timedelta(hours=self.channel_rating_timeout_hours)).isoformat(),
            }
        )
        del pending[:-max(1, self.channel_rating_max_pending)]

    def _regime_confidence_factor(self, regime: str) -> float:
        r = str(regime or "unknown").lower()
        return float(self.regime_min_confidence_factors.get(r, self.regime_min_confidence_factors.get("unknown", 1.0)))

    def _scaled_openrouter_min_confidence(self, base: int, regime: str) -> int:
        f = self._regime_confidence_factor(regime)
        return int(min(100, max(0, math.ceil(int(base) * f))))

    async def _maybe_classify_regime(self, signal: TelegramSignal) -> None:
        if not self.market_regime_cfg.enabled:
            signal.market_regime = "unknown"
            return
        await self._ensure_execution()
        if self.bybit is None:
            signal.market_regime = "unknown"
            return
        try:
            signal.market_regime = await classify_regime(self.bybit, signal.symbol, self.market_regime_cfg)
        except Exception:
            signal.market_regime = "unknown"

    def _post_signal_analytics(self, signal: TelegramSignal) -> None:
        self._track_signal_for_rating(signal)
        self._enqueue_signal_excursion(signal)

    def _enqueue_signal_excursion(self, signal: TelegramSignal) -> None:
        if not self.signal_excursion_cfg.enabled:
            return
        if signal.entry <= 0 or str(signal.side).upper() not in {"BUY", "SELL"} or not signal.symbol:
            return
        sk = normalize_chat_name(signal.source)
        tid = excursion_track_id(sk, signal.message_id, signal.symbol, signal.side)
        enqueue_signal_excursion(
            self.state,
            cfg=self.signal_excursion_cfg,
            track_id=tid,
            source=signal.source,
            source_key=sk,
            message_id=signal.message_id,
            symbol=signal.symbol,
            side=signal.side,
            entry=float(signal.entry),
            created_at=utc_now(),
            market_regime=signal.market_regime,
        )

    async def _evaluate_pending_excursions(self) -> None:
        if not self.signal_excursion_cfg.enabled:
            return
        pend = self.state.get("pending_signal_excursions", [])
        if not isinstance(pend, list) or not pend:
            return
        await self._ensure_execution()
        if self.bybit is None:
            return
        valid = await self._get_valid_symbols()
        changed = await evaluate_signal_excursions(
            self.state,
            cfg=self.signal_excursion_cfg,
            bybit=self.bybit,
            valid_symbols=valid,
            now=utc_now(),
            jsonl_path=self.signal_excursion_jsonl,
        )
        if changed:
            self._save_state()

    def _build_daily_channel_report_text(self) -> str:
        ratings = self.state.get("channel_ratings", {})
        if not isinstance(ratings, dict):
            ratings = {}
        blocks = self.state.get(STATE_BLOCKS, {})
        if not isinstance(blocks, dict):
            blocks = {}
        exc_agg = aggregate_excursions_by_channel(self.signal_excursion_jsonl)
        all_keys = set(ratings.keys()) | set(blocks.keys())
        scored: list[tuple[float, str]] = []
        for key in all_keys:
            row = ratings.get(key)
            sc = safe_float(row.get("score"), 0.0) if isinstance(row, dict) else 0.0
            scored.append((sc, key))
        scored.sort(key=lambda t: (-t[0], t[1]))
        lines = [
            "TELEGRAM SIGNAL AGENT — ежедневная сводка по каналам",
            f"Время (UTC): {utc_now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]
        if not scored:
            lines.append("(пока нет рейтингов и блокировок в state)")
        for _sc, key in scored[:80]:
            row = ratings.get(key) if isinstance(ratings.get(key), dict) else {}
            label = str(row.get("source", key)) if row else str(key)
            if row:
                score = safe_float(row.get("score"), self.channel_rating_initial_score)
                line = (
                    f"• {label}: {score:.0f}/100 "
                    f"W{int(row.get('wins', 0) or 0)}/"
                    f"L{int(row.get('losses', 0) or 0)}/"
                    f"N{int(row.get('neutrals', 0) or 0)}"
                )
            else:
                line = f"• {label}: (нет рейтинга)"
            if key in blocks:
                line += " [автоблок]"
            ex = exc_agg.get(str(key).lower(), {})
            if ex:
                parts: list[str] = []
                for hk in sorted(ex.keys(), key=lambda x: float(x)):
                    b = ex[hk]
                    if isinstance(b, dict):
                        parts.append(
                            f"{hk}h MFE~{b.get('avg_mfe_pct')}% "
                            f"MAE~{b.get('avg_mae_pct')}% (n={b.get('n')})"
                        )
                if parts:
                    line += "\n  Экскурсии: " + "; ".join(parts)
            lines.append(line)
        lines.append("")
        lines.append(
            f"MFE/MAE: окна из конфига signal_excursion; файл {self.signal_excursion_jsonl.name}."
        )
        return "\n".join(lines)

    async def _daily_channel_report_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(50)
                if not self.channel_daily_report_enabled:
                    continue
                now = utc_now()
                if now.hour != self.channel_daily_report_hour_utc or now.minute != self.channel_daily_report_minute_utc:
                    continue
                day = now.strftime("%Y-%m-%d")
                if self.state.get("last_channel_daily_report_ymd") == day:
                    continue
                if not self.telegram_notify:
                    self.state["last_channel_daily_report_ymd"] = day
                    self._save_state()
                    continue
                token = os.getenv("TELEGRAM_TOKEN", "")
                chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
                text = self._build_daily_channel_report_text()
                if len(text) > 3500:
                    text = text[:3490] + "…"
                if telegram_send(token, chat_id, text, max_retries=2):
                    self.state["last_channel_daily_report_ymd"] = day
                    self._save_state()
                    LOG.info("Daily channel report sent (%s)", day)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("Daily channel report error: %s", exc)

    def _append_rating_event(self, row: dict[str, Any]) -> None:
        path = self.out_dir / "channel_rating_events.jsonl"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _apply_channel_rating_result(
        self,
        item: dict[str, Any],
        current_price: float,
        outcome: str,
    ) -> None:
        source = str(item.get("source", ""))
        row = self._channel_rating(source)
        if outcome == "win":
            row["wins"] = int(row.get("wins", 0) or 0) + 1
            row["score"] = min(100.0, safe_float(row.get("score"), self.channel_rating_initial_score) + self.channel_rating_win_delta)
        elif outcome == "loss":
            row["losses"] = int(row.get("losses", 0) or 0) + 1
            row["score"] = max(0.0, safe_float(row.get("score"), self.channel_rating_initial_score) - self.channel_rating_loss_delta)
        else:
            outcome = "neutral"
            row["neutrals"] = int(row.get("neutrals", 0) or 0) + 1
        row["last_update_at"] = utc_now().isoformat()
        self._append_rating_event(
            {
                "checked_at": row["last_update_at"],
                "source": source,
                "symbol": item.get("symbol"),
                "side": item.get("side"),
                "entry": item.get("entry"),
                "stop_loss": item.get("stop_loss"),
                "take_profit": item.get("take_profit"),
                "level_source": item.get("level_source"),
                "current_price": current_price,
                "outcome": outcome,
                "rating": row,
            }
        )
        if self.channel_auto_block_cfg.enabled:
            sk = normalize_chat_name(source)
            channel_record_outcome(
                self.state,
                sk,
                outcome,
                str(item.get("symbol", "")),
                utc_now(),
            )
            newly = refresh_auto_blocks(
                self.state,
                self.channel_auto_block_cfg,
                trusted_keys=self.trusted_signal_sources,
                now=utc_now(),
            )
            if newly:
                self._save_state()
                if self.channel_auto_block_notify_telegram:
                    self._notify_channel_auto_blocks(newly)
                for key, reason in newly:
                    LOG.warning("Channel auto-block: %s (%s)", key, reason)

    async def _evaluate_pending_signal_reviews(self) -> None:
        if not self.channel_rating_enabled:
            return
        pending = self.state.get("pending_signal_reviews", [])
        if not isinstance(pending, list) or not pending:
            return
        await self._ensure_execution()
        assert self.bybit is not None
        valid_symbols = await self._get_valid_symbols()
        now = utc_now()
        keep: list[dict[str, Any]] = []
        changed = False
        for item in pending:
            if not isinstance(item, dict):
                continue
            try:
                evaluate_after = datetime.fromisoformat(str(item.get("evaluate_after", "")))
                if evaluate_after.tzinfo is None:
                    evaluate_after = evaluate_after.replace(tzinfo=timezone.utc)
            except Exception:
                changed = True
                continue
            if evaluate_after > now:
                keep.append(item)
                continue
            try:
                expires_at = datetime.fromisoformat(str(item.get("expires_at", "")))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except Exception:
                expires_at = now
            symbol = str(item.get("symbol", "")).upper()
            side = str(item.get("side", "")).upper()
            entry = safe_float(item.get("entry"))
            sl = safe_float(item.get("stop_loss"))
            tp = safe_float(item.get("take_profit"))
            if entry <= 0 or sl <= 0 or tp <= 0 or side not in {"BUY", "SELL"} or not symbol:
                changed = True
                continue
            if symbol not in valid_symbols:
                LOG.info("Rating check skipped invalid symbol: %s", symbol)
                changed = True
                continue
            try:
                current_price = await self.bybit.get_price(symbol)
                if current_price <= 0:
                    keep.append(item)
                    continue
                outcome = ""
                if side == "BUY":
                    if current_price >= tp:
                        outcome = "win"
                    elif current_price <= sl:
                        outcome = "loss"
                else:
                    if current_price <= tp:
                        outcome = "win"
                    elif current_price >= sl:
                        outcome = "loss"
                if not outcome and now >= expires_at:
                    outcome = "neutral"
                if outcome:
                    self._apply_channel_rating_result(item, current_price, outcome)
                    changed = True
                else:
                    keep.append(item)
            except Exception as exc:
                LOG.warning("Rating check failed for %s: %s", symbol, exc)
                keep.append(item)
        if changed or len(keep) != len(pending):
            self.state["pending_signal_reviews"] = keep[-max(1, self.channel_rating_max_pending):]
            self._save_state()

    def _touch_channel_activity(
        self,
        source: str,
        message_dt: datetime | None = None,
        *,
        had_signal: bool = False,
    ) -> None:
        """Учёт активности канала для scripts/prune_inactive_telegram_channels.py."""
        src = str(source or "").strip()
        if not src:
            return
        key = normalize_chat_name(src)
        now = (message_dt or utc_now()).astimezone(timezone.utc)
        node = self.state.setdefault("channel_activity", {})
        if not isinstance(node, dict):
            node = {}
            self.state["channel_activity"] = node
        row = node.setdefault(key, {"source": src})
        if not isinstance(row, dict):
            row = {"source": src}
            node[key] = row
        row["source"] = src
        if not row.get("first_seen_at"):
            row["first_seen_at"] = now.isoformat()
        row["last_post_at"] = now.isoformat()
        if had_signal:
            row["last_signal_at"] = now.isoformat()

    def _append_unified_inbox(self, signal: TelegramSignal, review: dict[str, Any]) -> None:
        """Чистая очередь для unified-бота: только одобренные качественные сигналы."""
        if not self.inbox_jsonl_enabled:
            return
        if not bool(review.get("approve")):
            return
        self._touch_channel_activity(signal.source, had_signal=True)
        side = str(signal.side or "").upper()
        if side not in {"BUY", "SELL"}:
            return
        conf = float(review.get("confidence", 0) or 0) / 100.0
        if conf <= 0:
            conf = float(signal.confidence or 0) / 100.0
        row = {
            "symbol": str(signal.symbol).upper(),
            "side": "Buy" if side == "BUY" else "Sell",
            "confidence": min(0.98, max(0.35, conf if conf <= 1 else conf / 100.0)),
            "entry": float(signal.entry or 0),
            "stop_loss": float(signal.stop_loss or 0),
            "take_profit": float(signal.take_profit or 0),
            "channel": signal.source,
            "message_id": signal.message_id,
            "reason": str(review.get("reason", ""))[:200],
            "parser_confidence": int(signal.parser_confidence or 0),
            "market_regime": signal.market_regime,
        }
        with open(self.inbox_jsonl, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _append_signal(self, signal: TelegramSignal, review: dict[str, Any], action: str, result: dict[str, Any] | None) -> None:
        if not self.audit_jsonl_enabled:
            return
        audit: dict[str, Any] = {
            "logged_entry": float(signal.entry),
            "logged_stop_loss": float(signal.stop_loss),
            "logged_take_profit": float(signal.take_profit),
            "logged_leverage": int(signal.leverage),
        }
        if isinstance(result, dict) and result:
            audit["fill_avg_price"] = result.get("avg_price")
            audit["executed_qty"] = result.get("executed_qty")
            audit["order_id"] = result.get("orderId")
            if result.get("error"):
                audit["exec_error"] = str(result.get("error"))[:500]
        if review.get("levels_for_exec"):
            audit["levels_note"] = str(review.get("levels_for_exec"))
        row = {
            "signal": asdict(signal),
            "review": review,
            "action": action,
            "status": human_status(action, str(review.get("reason", ""))),
            "channel_rating": self._channel_rating(signal.source),
            "execution_result": result or {},
            "execution_audit": audit,
        }
        with open(self.signals_jsonl, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def _ensure_execution(self) -> None:
        if self.execution is not None:
            self._sync_execution_dry_run()
            return
        api_key = os.getenv("BYBIT_API_KEY", "")
        api_secret = os.getenv("BYBIT_API_SECRET", "")
        if self._any_live_execution_enabled() and (not api_key or not api_secret):
            raise RuntimeError("BYBIT_API_KEY/BYBIT_API_SECRET are missing")
        self.bybit = BybitClient(
            api_key,
            api_secret,
            testnet=bool(get_cfg(self.cfg, "bybit", "testnet", False)),
            category=str(get_cfg(self.cfg, "bybit", "category", "linear")),
        )
        controls = SimpleNamespace(dry_run=not self._any_live_execution_enabled())
        self.execution = ExecutionEngine(self.bybit, controls, tg=None)

    async def _get_valid_symbols(self) -> set[str]:
        if self._valid_symbols is not None:
            return self._valid_symbols
        await self._ensure_execution()
        assert self.bybit is not None
        symbols: set[str] = set()
        cursor = ""
        for _ in range(10):
            params: dict[str, Any] = {"category": self.bybit.category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            result = await self.bybit._request("GET", "/v5/market/instruments-info", params)
            rows = result.get("list", []) if isinstance(result, dict) else []
            for item in rows:
                symbol = str(item.get("symbol", "")).upper()
                status = str(item.get("status", "")).lower()
                if symbol.endswith("USDT") and looks_like_trade_symbol(symbol) and status == "trading":
                    symbols.add(symbol)
            cursor = str(result.get("nextPageCursor", "") if isinstance(result, dict) else "")
            if not cursor:
                break
        if not symbols:
            tickers = await self.bybit.get_tickers()
            symbols = {
                str(item.get("symbol", "")).upper()
                for item in tickers
                if looks_like_trade_symbol(str(item.get("symbol", "")).upper())
                and str(item.get("symbol", "")).upper().endswith("USDT")
            }
        self._valid_symbols = symbols
        return symbols

    def _format_signal_time(self, message_time_utc: str) -> str:
        try:
            dt = datetime.fromisoformat(message_time_utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local = dt.astimezone(timezone(timedelta(hours=self.timezone_offset)))
            return f"{local.strftime('%Y-%m-%d %H:%M:%S')} UTC{self.timezone_offset:+d}"
        except Exception:
            return message_time_utc

    async def _check_signal_spread(self, signal: TelegramSignal) -> tuple[bool, str]:
        if self.risk_pipeline.cfg.max_spread_pct <= 0:
            return True, ""
        await self._ensure_execution()
        assert self.bybit is not None
        try:
            book = await self.bybit.get_orderbook(signal.symbol, limit=5)
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            if not bids or not asks:
                return False, "стакан пуст"
            bid = float(bids[0][0])
            ask = float(asks[0][0])
            return self.risk_pipeline.check_spread(bid, ask)
        except Exception as exc:
            return False, f"spread check failed: {exc}"

    async def _price_and_complete_levels(self, parsed: dict[str, Any], raw_for_enrich: str = "") -> dict[str, Any]:
        await self._ensure_execution()
        assert self.bybit is not None
        symbol = str(parsed["symbol"]).upper()
        parsed["symbol"] = symbol
        valid_symbols = await self._get_valid_symbols()
        if symbol not in valid_symbols:
            raise RuntimeError(f"Symbol not listed on Bybit USDT perpetual: {symbol}")
        price = float(parsed.get("entry") or 0.0)
        if price <= 0:
            price = await self.bybit.get_price(symbol)
        if price <= 0:
            raise RuntimeError(f"Cannot resolve price for {symbol}")

        if self.infer_side_from_sr:
            kl: list[dict] = []
            try:
                kl = await self.bybit.get_klines(
                    symbol,
                    interval=str(self.sr_exec_kline_interval),
                    limit=max(20, int(self.sr_exec_kline_limit)),
                )
            except Exception as exc:
                LOG.warning("infer_side_from_sr: klines %s: %s", symbol, exc)
            inferred: str | None = None
            if len(kl) >= 6:
                zc = StructureZoneAnalyzer().analyze(kl, price)
                inferred = infer_side_from_zones(
                    price,
                    zc,
                    near_tolerance_pct=self.infer_sr_near_tolerance_pct,
                )
            if inferred:
                parsed["side"] = inferred
                LOG.info("infer_side_from_sr: %s side=%s (mark≈%s)", symbol, inferred, price)

        side = str(parsed.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            raise RuntimeError(
                "Нет направления сделки: задайте LONG/SHORT в тексте или включите infer_side_from_sr "
                "и проверьте наличие свечей/зон S/R"
            )
        parsed["side"] = side

        raw_ex = raw_for_enrich or ""
        if raw_ex and self.infer_side_from_sr:
            enrich_parsed_signal_levels(parsed, raw_ex)

        sl = float(parsed.get("stop_loss") or 0.0)
        tp = float(parsed.get("take_profit") or 0.0)
        parsed.update({"entry": price, "stop_loss": sl, "take_profit": tp})
        if sl <= 0 and self.require_stop_loss:
            raise RuntimeError("Signal has no stop-loss and require_stop_loss=true")
        if sl <= 0:
            sl = price * (1 - self.default_sl_pct / 100.0) if side == "BUY" else price * (1 + self.default_sl_pct / 100.0)
        if tp <= 0 and self.allow_auto_take_profit:
            tp = price * (1 + self.default_tp_pct / 100.0) if side == "BUY" else price * (1 - self.default_tp_pct / 100.0)
        parsed.update({"entry": price, "stop_loss": sl, "take_profit": tp})
        self._apply_level_zone_buffer(parsed)
        return parsed

    async def _execute(self, signal: TelegramSignal) -> dict[str, Any]:
        await self._ensure_execution()
        assert self.bybit is not None and self.execution is not None
        cap = int(self.auto_execute_max_open_positions or 0)
        if cap > 0:
            open_positions = await self.bybit.get_positions()
            n_open = len(open_positions)
            if n_open >= cap:
                return {
                    "success": False,
                    "orderId": "",
                    "error": (
                        f"лимит открытых позиций {cap}: на Bybit уже {n_open} "
                        f"(telegram_signal_agent.auto_execute_max_open_positions)"
                    ),
                }
        price = signal.entry or await self.bybit.get_price(signal.symbol)
        leverage = max(1, min(int(signal.leverage or self.default_leverage), self.max_leverage))
        notional = min(self.margin_usdt * leverage, self.max_notional_usdt)
        avail = await self.bybit.get_usdt_available_balance()
        if avail > 0 and price > 0:
            reserve = max(0.0, min(90.0, float(self.execution_balance_reserve_pct))) / 100.0
            usable = avail * (1.0 - reserve)
            # Грубая оценка: начальная маржа ≈ notional/leverage (USDT linear).
            max_notional_from_wallet = usable * float(leverage)
            if max_notional_from_wallet > 0 and max_notional_from_wallet + 1e-9 < notional:
                LOG.info(
                    "Caps notional by wallet: planned=%.4f USDT max_from_avail≈%.4f (avail=%.4f reserve=%.0f%%)",
                    notional,
                    max_notional_from_wallet,
                    avail,
                    float(self.execution_balance_reserve_pct),
                )
                notional = min(notional, max_notional_from_wallet)
        qty = notional / price if price > 0 else 0.0
        if qty <= 0:
            raise RuntimeError("Calculated qty <= 0")
        result = await self.execution.execute_entry(
            symbol=signal.symbol,
            side=signal.side,
            qty=qty,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            leverage=leverage,
            reason=f"telegram_signal:{signal.source}",
            preferred_price=price,
        )
        if str(signal.source) == "MARKET_SCANNER" and self.market_scan_post_exec_delay_sec > 0:
            await asyncio.sleep(self.market_scan_post_exec_delay_sec)
        return result

    def _notify_market_scanner_execution(
        self,
        *,
        signal: TelegramSignal,
        action: str,
        review: dict[str, Any],
        result: dict[str, Any] | None,
    ) -> None:
        """Коротко о попытке реального входа после MARKET SCANNER (без OpenRouter)."""
        if not self.telegram_notify:
            return
        token = os.getenv("TELEGRAM_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        status = human_status(action, str(review.get("reason", "")))
        text_lines = [
            "MARKET SCANNER → BYBIT",
            f"Статус: {status}",
            f"Монета: {signal.symbol} {signal.side} x{signal.leverage}",
            f"Entry≈ {signal.entry:.8g} SL={signal.stop_loss:.8g} TP={signal.take_profit:.8g}",
            f"Скорер наблюдения: {signal.confidence}/100 "
            f"(min_exec≥{self.market_scanner_execute_min_score}) без OpenRouter",
            f"Пауза/флаги: pause={self._runtime_controls_dict().get('pause_all_execution')} "
            f"каналы_eff={self._effective_channel_auto_execute()} "
            f"сканер_eff={self._effective_market_scanner_auto_execute()}",
        ]
        reason_txt = str(review.get("reason") or "").strip()
        if reason_txt:
            text_lines.append(f"Причина: {reason_txt}")
        if isinstance(result, dict) and result:
            text_lines.append(
                (
                    f"Bybit: success={result.get('success')} "
                    f"error={result.get('error') or ''} "
                    f"order={result.get('orderId', '')}"
                ).strip(),
            )
            err = str(result.get("error") or "")
            if "110007" in err or "not enough" in err.lower():
                text_lines.append(
                    "Подсказка: Bybit 110007 — не хватает свободной маржи (часто после предыдущего входа). "
                    "Пополните UNIFIED USDT, уменьшите margin_usdt/max_notional, увеличьте execution_balance_reserve_pct "
                    "или ограничьте число входов сканера за один цикл."
                )
        if not telegram_send(token, chat_id, "\n".join(text_lines)):
            LOG.warning("Scanner exec notify skipped %s %s", action, signal.symbol)

    async def _try_execute_market_setup(self, setup: MarketSetup) -> None:
        if not self._effective_market_scanner_auto_execute():
            return
        if int(setup.score or 0) < int(self.market_scanner_execute_min_score):
            LOG.info(
                "Market scanner exec skipped: score=%s < min_exec=%s",
                setup.score,
                self.market_scanner_execute_min_score,
            )
            return
        if self.market_scanner_execute_require_confirmed_bos and not bool(
            getattr(setup, "confirmed_bos", False)
        ):
            LOG.info(
                "Market scanner exec skipped: confirmed BOS required (symbol=%s score=%s)",
                setup.symbol,
                setup.score,
            )
            return
        scen = str(setup.scenario or "").upper()
        side = "BUY" if scen == "PUMP" else "SELL" if scen == "DUMP" else ""
        if side not in {"BUY", "SELL"}:
            LOG.warning("Market scanner exec: unknown scenario %r", scen)
            return

        digest = hashlib.sha256(
            f"{setup.symbol}|{setup.checked_at_utc}|{scen}|{setup.score}|exec".encode("utf-8")
        ).hexdigest()
        msg_id = int(digest[:8], 16) % (2**30)
        lev = max(1, min(int(self.default_leverage), int(self.max_leverage)))

        sig = TelegramSignal(
            source="MARKET_SCANNER",
            message_id=msg_id,
            message_time_utc=str(setup.checked_at_utc or utc_now().isoformat()),
            received_at_utc=utc_now().isoformat(),
            symbol=str(setup.symbol).upper(),
            side=side,
            entry=float(setup.price),
            stop_loss=float(setup.invalidation),
            take_profit=float(setup.target),
            leverage=lev,
            confidence=int(setup.score or 0),
            reason=f"market_scan_{scen.lower()}",
            raw_text=(
                f"MARKET_SCANNER_EXEC {setup.symbol} {scen} score={setup.score} "
                f"bos={setup.bos_level:g} tgt={setup.target:g} inv={setup.invalidation:g}"
            ),
            parser_confidence=max(0, min(99, int(setup.score or 0))),
        )
        min_rr_need = float(getattr(self.risk_pipeline.cfg, "min_rr", 0.0) or 0.0)
        if (
            self.market_scanner_stretch_tp_to_min_rr
            and bool(getattr(self.risk_pipeline.cfg, "enabled", True))
            and min_rr_need > 0.0
        ):
            old_tp = float(sig.take_profit)
            new_tp = stretch_take_profit_for_min_rr(side, sig.entry, sig.stop_loss, sig.take_profit, min_rr_need)
            if abs(new_tp - old_tp) > max(1e-12, abs(old_tp) * 1e-10):
                rr_b = compute_rr(side, sig.entry, sig.stop_loss, old_tp)
                rr_a = compute_rr(side, sig.entry, sig.stop_loss, new_tp)
                LOG.info(
                    "Market scanner: TP подогнан под risk_guards.min_rr=%s (%s %s TP %.8g→%.8g RR %.3f→%.3f)",
                    min_rr_need,
                    sig.symbol,
                    side,
                    old_tp,
                    new_tp,
                    rr_b,
                    rr_a,
                )
                sig.take_profit = float(new_tp)

        await self._maybe_classify_regime(sig)

        await self._apply_sr_zones_to_signal(sig)

        if (
            self.market_scanner_stretch_tp_to_min_rr
            and bool(getattr(self.risk_pipeline.cfg, "enabled", True))
            and min_rr_need > 0.0
        ):
            rr_chk = compute_rr(side, sig.entry, sig.stop_loss, sig.take_profit)
            if rr_chk + 1e-9 < min_rr_need:
                old_tp2 = float(sig.take_profit)
                new_tp2 = stretch_take_profit_for_min_rr(
                    side, sig.entry, sig.stop_loss, sig.take_profit, min_rr_need
                )
                if abs(new_tp2 - old_tp2) > max(1e-12, abs(old_tp2) * 1e-10):
                    rr_after = compute_rr(side, sig.entry, sig.stop_loss, new_tp2)
                    LOG.info(
                        "Market scanner: после S/R TP подогнан под min_rr (%s TP %.8g→%.8g RR %.3f→%.3f)",
                        sig.symbol,
                        old_tp2,
                        new_tp2,
                        rr_chk,
                        rr_after,
                    )
                    sig.take_profit = float(new_tp2)

        r1_ok, r1_reason = self.risk_pipeline.pre_openrouter(sig)
        if not r1_ok:
            review = {"approve": False, "confidence": sig.confidence, "reason": r1_reason, "effective_min_ai": "—"}
            self._post_signal_analytics(sig)
            self._append_signal(sig, review, "scanner_blocked", None)
            self._notify_market_scanner_execution(signal=sig, action="scanner_blocked", review=review, result=None)
            return

        spread_ok, spread_reason = await self._check_signal_spread(sig)
        if not spread_ok:
            review = {"approve": False, "confidence": sig.confidence, "reason": spread_reason, "effective_min_ai": "—"}
            self._post_signal_analytics(sig)
            self._append_signal(sig, review, "scanner_blocked", None)
            self._notify_market_scanner_execution(signal=sig, action="scanner_blocked", review=review, result=None)
            return

        lim_ok, lim_msg = self.exec_limiter.can_execute()
        if not lim_ok:
            review = {"approve": False, "confidence": sig.confidence, "reason": lim_msg, "effective_min_ai": "—"}
            self._post_signal_analytics(sig)
            self._append_signal(sig, review, "scanner_blocked", None)
            self._notify_market_scanner_execution(signal=sig, action="scanner_blocked", review=review, result=None)
            return

        ex_ok, ex_msg = self.risk_pipeline.pre_execute_auto(sig, channel_score=0.0, trusted_source=True)
        if not ex_ok:
            review = {"approve": False, "confidence": sig.confidence, "reason": ex_msg, "effective_min_ai": "—"}
            self._post_signal_analytics(sig)
            self._append_signal(sig, review, "scanner_blocked", None)
            self._notify_market_scanner_execution(signal=sig, action="scanner_blocked", review=review, result=None)
            return

        result: dict[str, Any] | None = None
        action = "scanner_execute_failed"
        try:
            result = await self._execute(sig)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        ok_bool = bool(result and result.get("success"))
        if ok_bool:
            self.exec_limiter.record_successful_execute()
            self._save_state()
            action = "scanner_executed"
        review_ok = {
            "approve": True,
            "confidence": sig.confidence,
            "reason": "market_scanner_auto",
            "effective_min_ai": "—",
        }
        self._post_signal_analytics(sig)
        self._append_signal(sig, review_ok, action, result)
        self._notify_market_scanner_execution(signal=sig, action=action, review=review_ok, result=result)

    async def _run_signal_core(
        self,
        source: str,
        message_id: int,
        message_dt: datetime | None,
        text: str,
        parsed: dict[str, Any],
        *,
        force_no_execute: bool = False,
    ) -> None:
        parser_conf = int(parsed.get("parser_confidence", 0) or 0)
        signal = TelegramSignal(
            source=source,
            message_id=message_id,
            message_time_utc=(message_dt or utc_now()).astimezone(timezone.utc).isoformat(),
            received_at_utc=utc_now().isoformat(),
            symbol=parsed["symbol"],
            side=parsed["side"],
            entry=float(parsed["entry"]),
            stop_loss=float(parsed["stop_loss"]),
            take_profit=float(parsed["take_profit"]),
            leverage=max(1, min(int(parsed["leverage"]), self.max_leverage)),
            confidence=50,
            reason="parsed",
            raw_text=text[:4000],
            parser_confidence=parser_conf,
        )
        await self._maybe_classify_regime(signal)
        risk_ok, risk_reason = self.risk_pipeline.pre_openrouter(signal)
        if not risk_ok:
            review_pre = {"approve": False, "confidence": 0, "reason": risk_reason}
            self._post_signal_analytics(signal)
            self._append_signal(signal, review_pre, "risk_reject", None)
            self._notify_signal(signal, review_pre, "risk_reject", None)
            return
        spread_ok, spread_reason = await self._check_signal_spread(signal)
        if not spread_ok:
            review_pre = {"approve": False, "confidence": 0, "reason": spread_reason}
            self._post_signal_analytics(signal)
            self._append_signal(signal, review_pre, "risk_reject", None)
            self._notify_signal(signal, review_pre, "risk_reject", None)
            return
        trusted = self._is_trusted_source(source)
        rb = rule_based_review(parsed, signal.market_regime)
        skip_ai_below = int(self.signal_quality_cfg.get("openrouter_skip_if_structure_score_ge", 78))
        use_rules_only = (
            bool(self.signal_quality_cfg.get("prefer_rule_based_when_structured", True))
            and rb.get("approve")
            and int(rb.get("confidence", 0)) >= skip_ai_below
        )
        if use_rules_only:
            review = dict(rb)
        else:
            min_for_ai = int(self.signal_quality_cfg.get("openrouter_min_structure_score", 45))
            if int(rb.get("confidence", 0)) < min_for_ai and not trusted:
                review = {"approve": False, "confidence": int(rb.get("confidence", 0)), "reason": "structure_too_weak_for_ai"}
            else:
                review = openrouter_review(
                    self.cfg,
                    signal,
                    self.openrouter_timeout_sec,
                    budget_agent=self,
                    budget_kind="telegram",
                )
        signal.confidence = int(review.get("confidence", 0) or 0)
        signal.reason = str(review.get("reason", ""))
        ai_conf = int(review.get("confidence", 0) or 0)
        base_min_ai = self.min_openrouter_confidence_trusted if trusted else self.min_openrouter_confidence
        min_ai = self._scaled_openrouter_min_confidence(base_min_ai, signal.market_regime)
        approved = bool(review.get("approve")) and ai_conf >= min_ai
        should_auto = self._effective_channel_auto_execute() and approved and not force_no_execute
        if should_auto and self.auto_execute_require_high_signal:
            parser_ok = (
                parser_conf >= self.auto_execute_min_parser_confidence
                if self.auto_execute_min_parser_confidence > 0
                else True
            )
            min_exec_ai = self._scaled_openrouter_min_confidence(
                self.auto_execute_min_ai_confidence, signal.market_regime
            )
            should_auto = parser_ok and ai_conf >= min_exec_ai
        used_bot_levels = False
        sr_refined = False
        if should_auto and self.use_bot_default_sl_tp_on_execute:
            self._apply_bot_default_sl_tp_to_signal(signal)
            used_bot_levels = True
        if should_auto and getattr(self, "sr_exec_enabled", False):
            sr_refined = await self._apply_sr_zones_to_signal(signal)
        blocked = False
        block_action = "risk_reject"
        block_detail = ""
        if should_auto:
            r2_ok, r2_reason = self.risk_pipeline.pre_openrouter(signal)
            if not r2_ok:
                should_auto = False
                blocked = True
                block_detail = r2_reason
                block_action = "risk_reject"
        if should_auto:
            lim_ok, lim_msg = self.exec_limiter.can_execute()
            if not lim_ok:
                should_auto = False
                blocked = True
                block_detail = lim_msg
                block_action = "limit_reject"
        if should_auto:
            ch_row = self._channel_rating(signal.source)
            ex_ok, ex_msg = self.risk_pipeline.pre_execute_auto(
                signal,
                channel_score=safe_float(ch_row.get("score"), 0.0),
                trusted_source=trusted,
            )
            if not ex_ok:
                should_auto = False
                blocked = True
                block_detail = ex_msg
                block_action = "risk_reject"
        result: dict[str, Any] | None = None
        action = "analyze_only"
        if should_auto:
            result = await self._execute(signal)
            action = "executed" if result.get("success") else "execute_failed"
            if result.get("success"):
                self.exec_limiter.record_successful_execute()
                self._save_state()
        elif blocked:
            action = block_action
        else:
            action = "approved_notify" if approved else "rejected_notify"
        review_out = dict(review)
        review_out["effective_min_ai"] = min_ai
        if block_detail:
            review_out["block_detail"] = block_detail
        if sr_refined:
            if used_bot_levels:
                review_out["levels_for_exec"] = (
                    f"Исполнение: сначала SL/TP по боту ({self.default_sl_pct}% / {self.default_tp_pct}%), "
                    "затем подгонка к зонам поддержки/сопротивления (S/R + отступ в ATR)"
                )
            else:
                review_out["levels_for_exec"] = (
                    "Исполнение: SL/TP по зонам поддержки/сопротивления (S/R + отступ в ATR)"
                )
        elif used_bot_levels:
            review_out["levels_for_exec"] = (
                f"Исполнение: SL/TP по боту ({self.default_sl_pct}% / {self.default_tp_pct}%), не из поста"
            )
        self._post_signal_analytics(signal)
        self._append_signal(signal, review_out, action, result)
        if approved:
            self._append_unified_inbox(signal, review_out)
        self._notify_signal(signal, review_out, action, result)

    async def process_message(
        self,
        source: str,
        message_id: int,
        message_dt: datetime | None,
        text: str,
        *,
        telethon_client: Any = None,
        telethon_message: Any = None,
    ) -> None:
        if self._is_seen(source, message_id):
            return
        if not self._is_recent_message(message_dt):
            self._mark_seen(source, message_id)
            return
        if self._is_ignored_source(source):
            self._mark_seen(source, message_id)
            return
        if is_bot_echo_notification_text(text):
            self._mark_seen(source, message_id)
            return
        self._touch_channel_activity(source, message_dt, had_signal=False)
        if self.channel_auto_block_cfg.enabled and channel_is_blocked(self.state, normalize_chat_name(source)):
            LOG.info("Skip auto-blocked source: %r", source)
            self._mark_seen(source, message_id)
            return

        parts: list[str] = []
        base_txt = (text or "").strip()
        if base_txt:
            parts.append(base_txt)
        if self.photo_ocr_enabled and telethon_client is not None and telethon_message is not None:
            try:
                ocr = await telethon_photo_ocr_text(
                    telethon_client,
                    telethon_message,
                    max_bytes=int(self.photo_ocr_max_bytes or 0),
                )
                if ocr:
                    parts.append(ocr)
            except Exception as exc:
                LOG.warning("photo OCR failed: %s", exc)
        combined = "\n".join(parts).strip()
        if not combined:
            self._mark_seen(source, message_id)
            return

        parsed = parse_signal_text(
            combined,
            self.default_leverage,
            simple=self.signal_parse_simple,
            allow_missing_side=self.infer_side_from_sr,
            skip_enrich=self.infer_side_from_sr,
        )
        if not parsed:
            if self.log_unparsed_signal_hints and looks_like_unparsed_signal(combined):
                preview = re.sub(r"\s+", " ", combined[:160]).strip()
                LOG.info("Parse skip (hint): source=%r … %s", source, preview)
            self._mark_seen(source, message_id)
            return
        trusted = self._is_trusted_source(source)
        ok_q, q_reason = passes_quality_gate(
            parsed, combined, self.signal_quality_cfg, trusted=trusted
        )
        if not ok_q:
            LOG.debug("Quality skip %r: %s", source, q_reason)
            self._mark_seen(source, message_id)
            return
        fingerprint = self._signal_fingerprint(source, parsed)
        if self._is_duplicate_signal(fingerprint):
            self._mark_seen(source, message_id)
            return

        try:
            parsed = await self._price_and_complete_levels(parsed, raw_for_enrich=combined)
            await self._run_signal_core(source, message_id, message_dt, combined, parsed, force_no_execute=False)
        except Exception as exc:
            action = "error"
            err = str(exc)
            signal = TelegramSignal(
                source=source,
                message_id=message_id,
                message_time_utc=(message_dt or utc_now()).astimezone(timezone.utc).isoformat(),
                received_at_utc=utc_now().isoformat(),
                symbol=str(parsed.get("symbol", "")),
                side=str(parsed.get("side", "")),
                entry=safe_float(parsed.get("entry")),
                stop_loss=safe_float(parsed.get("stop_loss")),
                take_profit=safe_float(parsed.get("take_profit")),
                leverage=int(parsed.get("leverage", self.default_leverage) or self.default_leverage),
                confidence=0,
                reason=err,
                raw_text=combined[:4000],
                parser_confidence=int(parsed.get("parser_confidence", 0) or 0),
            )
            review = {"approve": False, "confidence": 0, "reason": err}
            self._post_signal_analytics(signal)
            self._append_signal(signal, review, action, None)
            if self.notify_invalid_symbols or not err.startswith("Symbol not listed on Bybit"):
                self._notify_signal(signal, review, action, None)
        finally:
            if "fingerprint" in locals():
                self._mark_signal_fingerprint(fingerprint)
            self._mark_seen(source, message_id)

    async def _handle_world_event(self, event: dict[str, Any]) -> None:
        title = str(event.get("title", "") or "")
        link = str(event.get("link", "") or "")
        summary = str(event.get("summary", "") or "")
        raw = f"{title}\n{summary}\n{link}"
        eid = str(event.get("id", "") or "")
        msg_id = int(hashlib.sha256(eid.encode()).hexdigest()[:12], 16) % (2**30) if eid else int(time.time() * 1000) % (2**30)
        ext = openrouter_world_extract(
            self.cfg,
            title=title,
            summary=summary,
            link=link,
            timeout_sec=self.openrouter_timeout_sec,
            max_summary_chars=self.agent_world_max_summary_chars,
            budget_agent=self,
            budget_kind="world",
        )
        skip_path = self.out_dir / "world_feed_skipped.jsonl"
        if not ext.get("has_trade"):
            with open(skip_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": eid, "extract": ext, "title": title[:200]}, ensure_ascii=False) + "\n")
            return
        sym = normalize_symbol(str(ext.get("symbol", "")))
        if not looks_like_trade_symbol(sym):
            with open(skip_path, "a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"id": eid, "reason": "bad_symbol", "symbol": sym, "extract": ext}, ensure_ascii=False)
                    + "\n"
                )
            return
        side = str(ext.get("side", "")).upper()
        if side not in {"BUY", "SELL"}:
            with open(skip_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": eid, "reason": "bad_side", "extract": ext}, ensure_ascii=False) + "\n")
            return
        lev = max(1, min(int(safe_float(ext.get("leverage"), self.default_leverage)), self.max_leverage))
        parsed: dict[str, Any] = {
            "symbol": sym,
            "side": side,
            "entry": float(safe_float(ext.get("entry"), 0.0)),
            "stop_loss": float(safe_float(ext.get("stop_loss"), 0.0)),
            "take_profit": float(safe_float(ext.get("take_profit"), 0.0)),
            "leverage": lev,
            "parser_confidence": int(safe_float(ext.get("confidence"), 0.0)),
        }
        try:
            priced = await self._price_and_complete_levels(parsed)
        except Exception as exc:
            err = str(exc)
            signal = TelegramSignal(
                source="AGENT-WORLD",
                message_id=msg_id,
                message_time_utc=utc_now().isoformat(),
                received_at_utc=utc_now().isoformat(),
                symbol=sym,
                side=side,
                entry=parsed.get("entry", 0.0),
                stop_loss=parsed.get("stop_loss", 0.0),
                take_profit=parsed.get("take_profit", 0.0),
                leverage=lev,
                confidence=0,
                reason=err,
                raw_text=raw[:4000],
                parser_confidence=parsed.get("parser_confidence", 0) or 0,
            )
            review = {"approve": False, "confidence": 0, "reason": err, "world_extract": ext}
            self._post_signal_analytics(signal)
            self._append_signal(signal, review, "error", None)
            if self.notify_invalid_symbols or not err.startswith("Symbol not listed"):
                self._notify_signal(signal, review, "error", None)
            return
        await self._run_signal_core(
            "AGENT-WORLD",
            msg_id,
            utc_now(),
            raw[:4000],
            priced,
            force_no_execute=not self.agent_world_allow_auto_exec,
        )

    async def _drain_world_queue(self) -> None:
        if not self.agent_world_enabled:
            return
        path = self.agent_world_queue_path
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            WORLD_LOG.warning("read queue failed: %s", exc)
            return
        last = int(self.state.get("world_feed_last_line", 0) or 0)
        if last > len(lines):
            last = 0
        proc_raw = self.state.get("world_processed_ids", [])
        proc: set[str] = set(str(x) for x in proc_raw) if isinstance(proc_raw, list) else set()
        changed = False
        new_last = last
        for i in range(last, len(lines)):
            line = lines[i].strip()
            if not line:
                new_last = i + 1
                continue
            try:
                event = json.loads(line)
            except Exception:
                new_last = i + 1
                continue
            if not isinstance(event, dict):
                new_last = i + 1
                continue
            eid = str(event.get("id", "") or "")
            if not eid or eid in proc:
                new_last = i + 1
                continue
            try:
                await self._handle_world_event(event)
            except Exception as exc:
                WORLD_LOG.warning("handle event %s: %s", eid, exc)
            proc.add(eid)
            new_last = i + 1
            changed = True
        if changed or new_last != last:
            self.state["world_processed_ids"] = list(proc)[-9000:]
            self.state["world_feed_last_line"] = new_last
            self._save_state()

    async def _world_feed_loop(self) -> None:
        while True:
            try:
                await self._drain_world_queue()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                WORLD_LOG.warning("loop error: %s", exc)
            await asyncio.sleep(max(30.0, self.agent_world_poll_sec))

    def _notify_channel_auto_blocks(self, items: list[tuple[str, str]]) -> None:
        if not self.telegram_notify or not items:
            return
        token = os.getenv("TELEGRAM_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        lines = ["TELEGRAM SIGNAL AGENT — автоблок каналов (плохая статистика сигналов)", ""]
        for key, reason in items:
            lines.append(f"• {key}\n{reason}")
        text = "\n".join(lines)
        telegram_send(token, chat_id, text)

    def _notify_signal(self, signal: TelegramSignal, review: dict[str, Any], action: str, result: dict[str, Any] | None) -> None:
        if not self.telegram_notify:
            return
        if self.notify_only_approved and action not in {
            "executed",
            "approved_notify",
            "scanner_executed",
        }:
            return
        token = os.getenv("TELEGRAM_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        status = human_status(action, str(review.get("reason", "")))
        trusted = self._is_trusted_source(signal.source)
        rating_line = (
            "Рейтинг канала: — (свой источник в trusted_signal_sources)\n"
            if trusted
            else f"Рейтинг канала: {self._format_channel_rating(signal.source)}\n"
        )
        text = (
            "TELEGRAM SIGNAL AGENT\n"
            f"Статус: {status}\n"
            f"Источник: {signal.source}\n"
            f"{rating_line}"
            f"Режим рынка (оценка по klines): {signal.market_regime} "
            f"(множитель порога AI: ×{self._regime_confidence_factor(signal.market_regime):.2f})\n"
            f"Время сигнала: {self._format_signal_time(signal.message_time_utc)}\n"
            f"Сигнал: {signal.symbol} {signal.side} x{signal.leverage}\n"
            f"Entry: {signal.entry:.8g} SL: {signal.stop_loss:.8g} TP: {signal.take_profit:.8g}\n"
            f"Уверенность из текста: {signal.parser_confidence}%\n"
            f"AI: approve={review.get('approve')} conf={review.get('confidence')} "
            f"reason={review.get('reason')} min_ai≥{review.get('effective_min_ai', '—')}\n"
            f"Auto execute каналов (эффект.): {self._effective_channel_auto_execute()} YAML={self.auto_execute} "
            f"(пороги: текст>={self.auto_execute_min_parser_confidence}, "
            f"AI_exec>={self.auto_execute_min_ai_confidence}, жёсткий авто="
            f"{'on' if self.auto_execute_require_high_signal else 'off'}) "
            f"| MARKET SCANNER→Bybit (эффект.): {self._effective_market_scanner_auto_execute()}\n"
        )
        if review.get("levels_for_exec"):
            text += f"{review.get('levels_for_exec')}\n"
        if result:
            text += f"Bybit: success={result.get('success')} error={result.get('error', '')}\n"
        if not telegram_send(token, chat_id, text):
            LOG.warning("Notification skipped: %s %s %s", status, signal.source, signal.symbol)

    @staticmethod
    def _avg(values: list[float]) -> float:
        clean = [float(x) for x in values if math.isfinite(float(x))]
        return sum(clean) / len(clean) if clean else 0.0

    @staticmethod
    def _ticker_turnover_usdt(ticker: dict[str, Any]) -> float:
        turnover = safe_float(ticker.get("turnover24h"))
        if turnover > 0:
            return turnover
        return safe_float(ticker.get("volume24h")) * safe_float(ticker.get("lastPrice"))

    @staticmethod
    def _clamp_float(value: float, low: float, high: float) -> float:
        return max(low, min(high, float(value)))

    def _apply_market_scanner_adaptive_filters(self) -> None:
        if not self.market_scanner_learning_enabled:
            return
        filters = self.state.get("market_scanner_adaptive_filters", {})
        if not isinstance(filters, dict):
            return
        self.market_scanner_min_score_to_notify = int(
            self._clamp_float(
                safe_float(filters.get("min_score_to_notify"), self.market_scanner_min_score_to_notify),
                self.market_scanner_min_score_floor,
                self.market_scanner_min_score_ceiling,
            )
        )
        self.market_scanner_min_volume_ratio = self._clamp_float(
            safe_float(filters.get("min_volume_ratio"), self.market_scanner_min_volume_ratio),
            self.market_scanner_min_volume_ratio_floor,
            self.market_scanner_min_volume_ratio_ceiling,
        )
        self.market_scanner_max_range_pct = self._clamp_float(
            safe_float(filters.get("max_range_pct"), self.market_scanner_max_range_pct),
            self.market_scanner_max_range_pct_floor,
            self.market_scanner_max_range_pct_ceiling,
        )
        self.market_scanner_max_atr_pct = self._clamp_float(
            safe_float(filters.get("max_atr_pct"), self.market_scanner_max_atr_pct),
            self.market_scanner_max_atr_pct_floor,
            self.market_scanner_max_atr_pct_ceiling,
        )
        # Иначе адаптив мог поднять min_score_to_notify выше порога исполнения — тогда сеты 78–81
        # вообще не созируются, а exec остаётся без сделок.
        if int(self.market_scanner_min_score_to_notify) > int(self.market_scanner_execute_min_score):
            self.market_scanner_min_score_to_notify = int(self.market_scanner_execute_min_score)

    def _market_setup_id(self, setup: MarketSetup) -> str:
        checked = str(setup.checked_at_utc).replace("+00:00", "Z")
        return f"{setup.symbol}:{setup.scenario}:{checked}"

    def _track_market_setup_for_learning(self, setup: MarketSetup) -> None:
        if not self.market_scanner_learning_enabled:
            return
        pending = self.state.setdefault("pending_market_setups", [])
        if not isinstance(pending, list):
            pending = []
            self.state["pending_market_setups"] = pending
        setup_id = self._market_setup_id(setup)
        if any(isinstance(item, dict) and item.get("id") == setup_id for item in pending):
            return
        created_at = utc_now()
        pending.append(
            {
                "id": setup_id,
                "created_at": created_at.isoformat(),
                "expires_at": (created_at + timedelta(hours=self.market_scanner_learning_timeout_hours)).isoformat(),
                "setup": asdict(setup),
            }
        )
        del pending[:-max(1, self.market_scanner_learning_window * 3)]

    def _append_market_scanner_event(self, event: dict[str, Any]) -> None:
        path = self.out_dir / "market_scanner_events.jsonl"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _restore_market_scanner_learning_events_from_file(self) -> bool:
        if not self.market_scanner_learning_enabled:
            return False
        path = self.out_dir / "market_scanner_events.jsonl"
        if not path.exists():
            return False
        events = self.state.setdefault("market_scanner_learning_events", [])
        if not isinstance(events, list):
            events = []
            self.state["market_scanner_learning_events"] = events
        known_ids = {str(item.get("id", "")) for item in events if isinstance(item, dict)}
        imported: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return False
        for line in lines[-max(100, self.market_scanner_learning_window * 4) :]:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if not isinstance(event, dict) or event.get("outcome") not in {"win", "loss", "neutral"}:
                continue
            event_id = str(event.get("id", ""))
            if not event_id or event_id in known_ids:
                continue
            known_ids.add(event_id)
            imported.append(event)
        if not imported:
            return False
        events.extend(imported)
        self.state["market_scanner_learning_events"] = events[-max(50, self.market_scanner_learning_window * 4) :]
        LOG.info("Restored %s market learning events from file", len(imported))
        return True

    def _market_setup_outcome(self, setup: dict[str, Any], current_price: float, expired: bool) -> str:
        scenario = str(setup.get("scenario", "")).upper()
        target = safe_float(setup.get("target"))
        invalidation = safe_float(setup.get("invalidation"))
        if current_price <= 0 or target <= 0 or invalidation <= 0:
            return ""
        if scenario == "PUMP":
            if current_price >= target:
                return "win"
            if current_price <= invalidation:
                return "loss"
        elif scenario == "DUMP":
            if current_price <= target:
                return "win"
            if current_price >= invalidation:
                return "loss"
        return "neutral" if expired else ""

    def _recalculate_market_scanner_adaptive_filters(self) -> bool:
        events = self.state.get("market_scanner_learning_events", [])
        if not isinstance(events, list):
            self.state["market_scanner_learning_events"] = []
            return False
        window = [item for item in events[-max(1, self.market_scanner_learning_window) :] if isinstance(item, dict)]
        decided = [item for item in window if item.get("outcome") in {"win", "loss"}]
        if len(decided) < self.market_scanner_learning_min_events:
            return False

        wins = sum(1 for item in decided if item.get("outcome") == "win")
        losses = sum(1 for item in decided if item.get("outcome") == "loss")
        winrate = wins / max(1, wins + losses)
        window_signature = "|".join(
            str(
                item.get("id")
                or f"{item.get('checked_at')}:{item.get('symbol')}:{item.get('scenario')}:{item.get('outcome')}"
            )
            for item in decided
        )
        current_filters = self.state.get("market_scanner_adaptive_filters", {})
        if isinstance(current_filters, dict) and current_filters.get("learning_window_signature") == window_signature:
            return False
        old = {
            "min_score_to_notify": self.market_scanner_min_score_to_notify,
            "min_volume_ratio": self.market_scanner_min_volume_ratio,
            "max_range_pct": self.market_scanner_max_range_pct,
            "max_atr_pct": self.market_scanner_max_atr_pct,
        }
        new = dict(old)

        if winrate < 0.40 and losses >= 3:
            new["min_score_to_notify"] = min(self.market_scanner_min_score_ceiling, int(old["min_score_to_notify"]) + 3)
            new["min_volume_ratio"] = self._clamp_float(
                float(old["min_volume_ratio"]) + 0.10,
                self.market_scanner_min_volume_ratio_floor,
                self.market_scanner_min_volume_ratio_ceiling,
            )
            new["max_range_pct"] = self._clamp_float(
                float(old["max_range_pct"]) - 0.25,
                self.market_scanner_max_range_pct_floor,
                self.market_scanner_max_range_pct_ceiling,
            )
            new["max_atr_pct"] = self._clamp_float(
                float(old["max_atr_pct"]) - 0.04,
                self.market_scanner_max_atr_pct_floor,
                self.market_scanner_max_atr_pct_ceiling,
            )
            mode = "stricter"
        elif winrate > 0.62 and wins >= 4:
            new["min_score_to_notify"] = max(self.market_scanner_min_score_floor, int(old["min_score_to_notify"]) - 1)
            new["min_volume_ratio"] = self._clamp_float(
                float(old["min_volume_ratio"]) - 0.03,
                self.market_scanner_min_volume_ratio_floor,
                self.market_scanner_min_volume_ratio_ceiling,
            )
            new["max_range_pct"] = self._clamp_float(
                float(old["max_range_pct"]) + 0.10,
                self.market_scanner_max_range_pct_floor,
                self.market_scanner_max_range_pct_ceiling,
            )
            mode = "softer"
        else:
            return False

        changed = any(str(old.get(key)) != str(new.get(key)) for key in new)
        if not changed:
            return False
        new.update(
            {
                "updated_at": utc_now().isoformat(),
                "mode": mode,
                "sample": len(decided),
                "wins": wins,
                "losses": losses,
                "winrate": round(winrate, 4),
                "learning_window_signature": window_signature,
            }
        )
        self.state["market_scanner_adaptive_filters"] = new
        self.market_scanner_min_score_to_notify = int(new["min_score_to_notify"])
        self.market_scanner_min_volume_ratio = float(new["min_volume_ratio"])
        self.market_scanner_max_range_pct = float(new["max_range_pct"])
        self.market_scanner_max_atr_pct = float(new["max_atr_pct"])
        self._notify_market_scanner_learning_update(old, new)
        return True

    def _notify_market_scanner_learning_update(self, old: dict[str, Any], new: dict[str, Any]) -> None:
        if not self.telegram_notify:
            return
        token = os.getenv("TELEGRAM_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        text = (
            "MARKET SCANNER LEARNING\n"
            "Статус: ФИЛЬТРЫ СКАНЕРА ОБНОВЛЕНЫ\n"
            f"Режим: {new.get('mode')}\n"
            f"Выборка: {new.get('sample')} прогнозов, winrate={float(new.get('winrate', 0)) * 100:.1f}%\n"
            f"min_score: {old.get('min_score_to_notify')} -> {new.get('min_score_to_notify')}\n"
            f"min_volume_ratio: {float(old.get('min_volume_ratio', 0)):.2f} -> {float(new.get('min_volume_ratio', 0)):.2f}\n"
            f"max_range_pct: {float(old.get('max_range_pct', 0)):.2f} -> {float(new.get('max_range_pct', 0)):.2f}\n"
            f"max_atr_pct: {float(old.get('max_atr_pct', 0)):.2f} -> {float(new.get('max_atr_pct', 0)):.2f}\n"
            "Пороги уведомлений скана обновлены; это не PnL по счёту.\n"
            "Реальные ордера по сканеру — см. включение «MARKET SCANNER→Bybit» (/panel); "
            "уведомления «MARKET SCANNER → BYBIT» логируют попытку входа.\n"
            "\n"
            + TG_MARKET_SCANNER_LEARNING_FOOTER
        )
        telegram_send(token, chat_id, text, max_retries=1)

    def _market_scanner_on_cooldown(self, symbol: str, scenario: str) -> bool:
        if self.market_scanner_symbol_cooldown_sec <= 0:
            return False
        rows = self.state.setdefault("market_scanner_notified", {})
        if not isinstance(rows, dict):
            self.state["market_scanner_notified"] = {}
            return False
        # Кулдаун по символу (не по сценарию): один алерт на монету за период.
        key = str(symbol).upper()
        try:
            last = datetime.fromisoformat(str(rows.get(key, "")))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except Exception:
            return False
        age_sec = (utc_now() - last.astimezone(timezone.utc)).total_seconds()
        return age_sec < self.market_scanner_symbol_cooldown_sec

    def _mark_market_scanner_notified(self, setup: MarketSetup) -> None:
        rows = self.state.setdefault("market_scanner_notified", {})
        if isinstance(rows, dict):
            rows[str(setup.symbol).upper()] = utc_now().isoformat()
            cutoff = utc_now() - timedelta(seconds=max(self.market_scanner_symbol_cooldown_sec * 3, 86400))
            for key, value in list(rows.items()):
                try:
                    ts = datetime.fromisoformat(str(value))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < cutoff:
                        rows.pop(key, None)
                except Exception:
                    rows.pop(key, None)

    def _find_latest_fvg(self, klines: list[dict[str, Any]], scenario: str) -> tuple[float, float, str]:
        lookback = klines[-30:]
        for i in range(len(lookback) - 1, 1, -1):
            prev2 = lookback[i - 2]
            cur = lookback[i]
            if scenario == "PUMP":
                low = safe_float(prev2.get("high"))
                high = safe_float(cur.get("low"))
                if high > low > 0:
                    gap_pct = (high - low) / max(low, 1e-12) * 100.0
                    if gap_pct >= self.market_scanner_min_fvg_pct:
                        return low, high, f"bullish FVG {low:.8g}-{high:.8g}"
            elif scenario == "DUMP":
                low = safe_float(cur.get("high"))
                high = safe_float(prev2.get("low"))
                if high > low > 0:
                    gap_pct = (high - low) / max(high, 1e-12) * 100.0
                    if gap_pct >= self.market_scanner_min_fvg_pct:
                        return low, high, f"bearish FVG {low:.8g}-{high:.8g}"
        return 0.0, 0.0, ""

    async def _analyze_market_setup(self, ticker: dict[str, Any]) -> MarketSetup | None:
        symbol = str(ticker.get("symbol", "")).upper()
        if not symbol or symbol in self.market_scanner_blacklist:
            return None
        turnover = self._ticker_turnover_usdt(ticker)
        if turnover < self.market_scanner_min_24h_volume_usdt:
            return None

        assert self.bybit is not None
        bars = max(12, self.market_scanner_consolidation_bars)
        klines = await self.bybit.get_klines(
            symbol,
            interval=self.market_scanner_interval,
            limit=max(self.market_scanner_klines_limit, bars + 10),
        )
        if len(klines) < bars + 5:
            return None

        current = klines[-1]
        price = safe_float(current.get("close"))
        window = klines[-(bars + 1) : -1]
        range_high = max(safe_float(k.get("high")) for k in window)
        range_low = min(safe_float(k.get("low")) for k in window)
        if price <= 0 or range_high <= 0 or range_low <= 0 or range_high <= range_low:
            return None

        mid = (range_high + range_low) / 2.0
        range_pct = (range_high - range_low) / max(mid, 1e-12) * 100.0
        true_ranges: list[float] = []
        prev_close = safe_float(klines[-(bars + 2)].get("close")) if len(klines) > bars + 2 else 0.0
        for item in window:
            high = safe_float(item.get("high"))
            low = safe_float(item.get("low"))
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close)) if prev_close > 0 else high - low
            if tr > 0:
                true_ranges.append(tr)
            prev_close = safe_float(item.get("close"))
        atr_pct = self._avg(true_ranges) / max(price, 1e-12) * 100.0

        recent_vol = self._avg([safe_float(k.get("volume")) for k in klines[-3:]])
        base_vol = self._avg([safe_float(k.get("volume")) for k in window[:-3] or window])
        volume_ratio = recent_vol / max(base_vol, 1e-12)

        buffer = self.market_scanner_bos_buffer_pct / 100.0
        bos_up = price > range_high * (1.0 + buffer)
        bos_down = price < range_low * (1.0 - buffer)
        span = max(range_high - range_low, 1e-12)
        pos_in_range = (price - range_low) / span
        # Без подтверждённого BOS — только явная сторона диапазона (гистерезис),
        # иначе в узком боковике за 10 мин приходят и PUMP и DUMP на одну монету.
        if bos_up:
            scenario = "PUMP"
            bos_level = range_high
            invalidation = range_low
            target = max(range_high + (range_high - range_low) * 0.7, price * 1.012)
        elif bos_down:
            scenario = "DUMP"
            bos_level = range_low
            invalidation = range_high
            target = min(range_low - (range_high - range_low) * 0.7, price * 0.988)
        elif pos_in_range >= 0.62:
            scenario = "PUMP"
            bos_level = range_high
            invalidation = range_low
            target = max(range_high + (range_high - range_low) * 0.7, price * 1.012)
        elif pos_in_range <= 0.38:
            scenario = "DUMP"
            bos_level = range_low
            invalidation = range_high
            target = min(range_low - (range_high - range_low) * 0.7, price * 0.988)
        else:
            return None

        score = 0
        reasons: list[str] = []
        compact_range = range_pct <= self.market_scanner_max_range_pct
        compact_atr = atr_pct <= self.market_scanner_max_atr_pct
        if compact_range:
            score += 22
            reasons.append(f"консолидация {bars} свечей, диапазон {range_pct:.2f}%")
        elif range_pct <= self.market_scanner_max_range_pct * 1.4:
            score += 10
            reasons.append(f"умеренный диапазон {range_pct:.2f}%")
        if compact_atr:
            score += 14
            reasons.append(f"ATR сжат до {atr_pct:.2f}%")
        if bos_up or bos_down:
            score += 25
            reasons.append(f"BOS {'вверх' if scenario == 'PUMP' else 'вниз'} через {bos_level:.8g}")
        else:
            score += 12
            reasons.append(f"цена у границы диапазона {bos_level:.8g}, ждём подтверждение BOS")
        if volume_ratio >= self.market_scanner_min_volume_ratio:
            score += 15
            reasons.append(f"объём {volume_ratio:.2f}x к среднему")
        elif volume_ratio >= 1.1:
            score += 6
            reasons.append(f"объём слегка выше среднего: {volume_ratio:.2f}x")

        fvg_low, fvg_high, fvg_reason = self._find_latest_fvg(klines, scenario)
        if fvg_reason:
            score += 12
            reasons.append(fvg_reason)
        if turnover >= self.market_scanner_min_24h_volume_usdt * 3:
            score += 5
            reasons.append(f"оборот 24ч {turnover / 1_000_000:.1f}M USDT")

        score = max(0, min(100, int(round(score))))
        if score < self.market_scanner_min_score_to_notify:
            return None

        return MarketSetup(
            checked_at_utc=utc_now().isoformat(),
            symbol=symbol,
            scenario=scenario,
            score=score,
            price=price,
            turnover_24h=turnover,
            range_low=range_low,
            range_high=range_high,
            consolidation_bars=bars,
            range_pct=range_pct,
            atr_pct=atr_pct,
            volume_ratio=volume_ratio,
            bos_level=bos_level,
            fvg_low=fvg_low,
            fvg_high=fvg_high,
            invalidation=invalidation,
            target=target,
            reasons=reasons[:6],
            confirmed_bos=bool(bos_up or bos_down),
        )

    def _append_market_setup(self, setup: MarketSetup) -> None:
        path = self.out_dir / "market_scanner.jsonl"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(setup), ensure_ascii=False) + "\n")

    def _notify_market_setup(self, setup: MarketSetup) -> None:
        if not self.telegram_notify:
            return
        token = os.getenv("TELEGRAM_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        reasons = "\n".join(f"- {reason}" for reason in setup.reasons)
        direction_arrow = "⬆" if setup.scenario == "PUMP" else "⬇"
        target_label = "Цель вверх" if setup.scenario == "PUMP" else "Цель вниз"
        exec_on = self._effective_market_scanner_auto_execute()
        status_line = (
            f"наблюдение | если score≥{self.market_scanner_execute_min_score}: "
            f"{'попытка ордера Bybit возможна (сканер→Bybit вкл)' if exec_on else 'ордер по сканеру выкл (/panel)'}"
        )
        watch_note = ""
        if not bool(getattr(setup, "confirmed_bos", False)):
            watch_note = (
                "⚠️ Наблюдение у границы диапазона — это НЕ вход и НЕ гарантия движения. "
                "Ждём подтверждение BOS (пробой уровня).\n"
            )
        text = (
            "MARKET SCANNER\n"
            f"Статус: {status_line}\n"
            f"Монета: {setup.symbol}\n"
            f"Сценарий: {direction_arrow} возможный {setup.scenario}\n"
            f"{watch_note}"
            f"Уверенность (скорер наблюдения, не OpenRouter): {setup.score}/100\n"
            f"Оборот 24ч: {setup.turnover_24h / 1_000_000:.1f}M USDT\n"
            f"Цена: {setup.price:.8g}\n"
            f"Диапазон: {setup.range_low:.8g} - {setup.range_high:.8g}\n"
            f"BOS уровень: {setup.bos_level:.8g}\n"
            f"Отмена сценария: {setup.invalidation:.8g}\n"
            f"{target_label}: {direction_arrow} {setup.target:.8g}\n"
            f"Причины:\n{reasons}\n"
        )
        if setup.fvg_low > 0 and setup.fvg_high > 0:
            text += f"FVG зона: {setup.fvg_low:.8g} - {setup.fvg_high:.8g}\n"
        if exec_on and int(setup.score or 0) < int(self.market_scanner_execute_min_score):
            text += (
                f"\n— Автовход Bybit не выполняется: скор наблюдения {setup.score} "
                f"< порога входа сканера {self.market_scanner_execute_min_score} "
                "(config: market_scanner_execute_min_score). Уведомление ≠ ордер.\n"
            )
        elif (
            exec_on
            and self.market_scanner_execute_require_confirmed_bos
            and not bool(getattr(setup, "confirmed_bos", False))
            and int(setup.score or 0) >= int(self.market_scanner_execute_min_score)
        ):
            text += (
                "\n— Автовход не выполняется: требуется подтверждённый пробой (BOS), "
                "сейчас только подход к границе. См. market_scanner_execute_require_confirmed_bos.\n"
            )
        text += "\n" + self._market_scan_telegram_footer()
        if not telegram_send(token, chat_id, text, max_retries=1):
            LOG.warning("Market scanner notification skipped: %s", setup.symbol)

    async def _evaluate_pending_market_setups(self) -> None:
        if not self.market_scanner_learning_enabled:
            return
        pending = self.state.get("pending_market_setups", [])
        if not isinstance(pending, list) or not pending:
            return
        await self._ensure_execution()
        assert self.bybit is not None
        now = utc_now()
        keep: list[dict[str, Any]] = []
        events = self.state.setdefault("market_scanner_learning_events", [])
        if not isinstance(events, list):
            events = []
            self.state["market_scanner_learning_events"] = events
        changed = False

        for item in pending:
            if not isinstance(item, dict):
                changed = True
                continue
            setup = item.get("setup", {})
            if not isinstance(setup, dict):
                changed = True
                continue
            symbol = str(setup.get("symbol", "")).upper()
            if not symbol:
                changed = True
                continue
            valid_symbols = await self._get_valid_symbols()
            if symbol not in valid_symbols:
                LOG.info("Market learning skipped invalid symbol: %s", symbol)
                changed = True
                continue
            try:
                expires_at = datetime.fromisoformat(str(item.get("expires_at", "")))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except Exception:
                expires_at = now
            try:
                current_price = await self.bybit.get_price(symbol)
            except Exception as exc:
                LOG.warning("Market learning price check failed for %s: %s", symbol, exc)
                keep.append(item)
                continue
            expired = now >= expires_at
            outcome = self._market_setup_outcome(setup, current_price, expired)
            if not outcome:
                keep.append(item)
                continue
            event = {
                "checked_at": now.isoformat(),
                "id": item.get("id", ""),
                "symbol": symbol,
                "scenario": setup.get("scenario", ""),
                "entry_price": setup.get("price"),
                "current_price": current_price,
                "target": setup.get("target"),
                "invalidation": setup.get("invalidation"),
                "score": setup.get("score"),
                "range_pct": setup.get("range_pct"),
                "atr_pct": setup.get("atr_pct"),
                "volume_ratio": setup.get("volume_ratio"),
                "outcome": outcome,
                "filters": {
                    "min_score_to_notify": self.market_scanner_min_score_to_notify,
                    "min_volume_ratio": self.market_scanner_min_volume_ratio,
                    "max_range_pct": self.market_scanner_max_range_pct,
                    "max_atr_pct": self.market_scanner_max_atr_pct,
                },
            }
            events.append(event)
            self._append_market_scanner_event(event)
            changed = True

        if changed or len(keep) != len(pending):
            self.state["pending_market_setups"] = keep[-max(1, self.market_scanner_learning_window * 3) :]
            self.state["market_scanner_learning_events"] = events[-max(50, self.market_scanner_learning_window * 4) :]
            self._recalculate_market_scanner_adaptive_filters()
            self._save_state()

    async def run_market_scan_once(self) -> list[MarketSetup]:
        if not self.market_scanner_enabled:
            return []
        await self._ensure_execution()
        assert self.bybit is not None
        await self._evaluate_pending_market_setups()
        valid_symbols = await self._get_valid_symbols()
        tickers = await self.bybit.get_tickers()
        candidates = []
        for ticker in tickers:
            symbol = str(ticker.get("symbol", "")).upper()
            if (
                not symbol.endswith("USDT")
                or symbol not in valid_symbols
                or symbol in self.market_scanner_blacklist
                or not looks_like_trade_symbol(symbol)
            ):
                continue
            turnover = self._ticker_turnover_usdt(ticker)
            if turnover >= self.market_scanner_min_24h_volume_usdt:
                candidates.append((turnover, ticker))
        candidates.sort(key=lambda item: item[0], reverse=True)

        setups: list[MarketSetup] = []
        for _, ticker in candidates[: max(1, self.market_scanner_max_symbols)]:
            try:
                setup = await self._analyze_market_setup(ticker)
                if setup is None:
                    continue
                self._append_market_setup(setup)
                setups.append(setup)
            except Exception as exc:
                LOG.warning("Market scan failed for %s: %s", ticker.get("symbol"), exc)
        setups.sort(key=lambda item: item.score, reverse=True)

        notified = 0
        for setup in setups[: max(1, self.market_scanner_top_n)]:
            if self._market_scanner_on_cooldown(setup.symbol, setup.scenario):
                continue
            self._notify_market_setup(setup)
            self._mark_market_scanner_notified(setup)
            self._track_market_setup_for_learning(setup)
            notified += 1
            await self._try_execute_market_setup(setup)
        if setups or notified:
            self._save_state()
        if setups and notified == 0:
            LOG.info(
                "Market scan: %s сетапов, в Telegram 0 (cooldown %ss или уже видели)",
                len(setups),
                int(self.market_scanner_symbol_cooldown_sec),
            )
        LOG.info(
            "Market scan done: candidates=%s setups=%s notified=%s "
            "| active_thresholds score>=%s vol_ratio>=%.2f range%%<=%.2f atr%%<=%.2f "
            "| learning_events=%s | scanner_exec_eff=%s pause_all=%s",
            len(candidates),
            len(setups),
            notified,
            int(self.market_scanner_min_score_to_notify),
            float(self.market_scanner_min_volume_ratio),
            float(self.market_scanner_max_range_pct),
            float(self.market_scanner_max_atr_pct),
            len(self.state.get("market_scanner_learning_events", []) or [])
            if isinstance(self.state.get("market_scanner_learning_events"), list)
            else 0,
            self._effective_market_scanner_auto_execute(),
            bool(self._runtime_controls_dict().get("pause_all_execution")),
        )
        return setups

    async def _market_scanner_loop(self) -> None:
        while True:
            try:
                await self.run_market_scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("Market scanner error: %s", exc)
            await asyncio.sleep(max(60.0, self.market_scanner_interval_sec))

    @staticmethod
    def _chat_source_label(chat: Any) -> str:
        return str(
            getattr(chat, "title", "")
            or getattr(chat, "username", "")
            or chat
        )

    async def run_once(self, limit: int) -> None:
        await self._evaluate_pending_signal_reviews()
        await self._evaluate_pending_excursions()
        await self._drain_world_queue()
        await self.run_market_scan_once()
        client = await self._telegram_client()
        async with client:
            dialogs = self.allowed_chats or []
            if not dialogs:
                skipped = 0
                async for dialog in client.iter_dialogs():
                    if not getattr(dialog, "is_channel", False):
                        continue
                    ent = dialog.entity
                    if self._is_ignored_source(self._chat_source_label(ent)):
                        skipped += 1
                        continue
                    dialogs.append(ent)
                    if len(dialogs) >= self.max_chats_once:
                        break
                if skipped:
                    LOG.info("run_once: пропущено ignored каналов=%s, сканируем=%s", skipped, len(dialogs))
            for chat in dialogs:
                if self._is_ignored_source(self._chat_source_label(chat)):
                    continue
                async for msg in client.iter_messages(chat, limit=limit):
                    text = getattr(msg, "message", "") or ""
                    source = str(getattr(getattr(msg, "chat", None), "title", "") or getattr(chat, "username", "") or chat)
                    await self.process_message(
                        source,
                        int(msg.id),
                        msg.date,
                        text,
                        telethon_client=client,
                        telethon_message=msg,
                    )
                if self.scan_delay_sec > 0:
                    await asyncio.sleep(self.scan_delay_sec)
        await self._evaluate_pending_signal_reviews()
        await self._evaluate_pending_excursions()
        await self._drain_world_queue()

    async def _rating_monitor_loop(self) -> None:
        while True:
            try:
                if self.channel_rating_enabled:
                    await self._evaluate_pending_signal_reviews()
                await self._evaluate_pending_excursions()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("Rating monitor error: %s", exc)
            await asyncio.sleep(max(30.0, self.channel_rating_check_interval_sec))

    async def _new_message_source(self, event: Any) -> str:
        """Human-readable chat label without iter_dialogs (avoids Telethon SQLite lock storms)."""
        chat = getattr(event, "chat", None)
        if chat is not None:
            return str(getattr(chat, "title", "") or getattr(chat, "username", "") or event.chat_id)
        try:
            ent = await event.client.get_entity(event.chat_id)
            return str(getattr(ent, "title", "") or getattr(ent, "username", "") or event.chat_id)
        except Exception:
            return str(getattr(event, "chat_id", "") or "")

    @staticmethod
    def _new_message_peer_key(event: Any) -> int | None:
        """Стабильный peer id сообщения (для сравнения с allowed_chats)."""
        try:
            if telethon_utils is not None and getattr(event, "peer_id", None) is not None:
                return int(telethon_utils.get_peer_id(event.peer_id))
        except Exception:
            pass
        try:
            cid = getattr(event, "chat_id", None)
            return int(cid) if cid is not None else None
        except Exception:
            return None

    async def _resolve_allowed_peer_ids(self, client: Any) -> set[int] | None:
        """Множество peer id для allowed_chats. Пустой config → None (= все чаты, как у Telethon chats=None).

        Важно: не передаём username в events.NewMessage(chats=…): при апдейтах Telethon снова
        вызывает ResolveUsernameRequest и ловит UsernameInvalidError в фоне. Фильтруем в
        обработчике по числовому peer id.
        """
        raw = list(self.allowed_chats or [])
        if not raw:
            return None
        if telethon_utils is None:
            raise RuntimeError("telethon utils unavailable")
        out: set[int] = set()
        for item in raw:
            try:
                ent = await client.get_entity(item)
                out.add(int(telethon_utils.get_peer_id(ent)))
            except Exception as exc:
                LOG.error(
                    "allowed_chats: пропуск неверного peer %r — проверьте @username или id в config.yaml (%s)",
                    item,
                    exc,
                )
        if not out:
            raise RuntimeError(
                "telegram_signal_agent.allowed_chats: все записи неверны (например UsernameInvalidError). "
                "Удалите несуществующие username или временно задайте allowed_chats: []"
            )
        if len(out) < len(raw):
            LOG.warning(
                "allowed_chats: учтено %s из %s записей (остальные peer отброшены из-за ошибок)",
                len(out),
                len(raw),
            )
        return out

    async def run_forever(self) -> None:
        """Telethon живёт пока активна сеть; при разрыве `run_until_disconnected` завершается — без цикла
        процесс выходил с кодом 0 и systemd при `Restart=always` делал вид «перезапуск сам» каждый раз."""

        reconnect_after_sec = max(
            15.0, float(self.agent_cfg.get("telethon_reconnect_delay_sec", 15))
        )

        while True:
            rating_task: asyncio.Task | None = None
            scanner_task: asyncio.Task | None = None
            daily_task: asyncio.Task | None = None
            world_task: asyncio.Task | None = None
            panel_task: asyncio.Task | None = None
            client = await self._telegram_client()
            new_message_lock = asyncio.Lock()

            try:
                async with client:
                    allowed_peer_ids: set[int] | None
                    try:
                        allowed_peer_ids = await self._resolve_allowed_peer_ids(client)
                    except Exception:
                        LOG.exception(
                            "Telethon: не удалось разрешить allowed_chats; исправьте config.yaml "
                            "или задайте пустой список []."
                        )
                        raise

                    @client.on(events.NewMessage())
                    async def handler(event):  # type: ignore[no-untyped-def]
                        async with new_message_lock:
                            pk = self._new_message_peer_key(event)
                            if pk is not None and pk in self._ignored_peer_ids:
                                return
                            if allowed_peer_ids is not None:
                                if pk is None or pk not in allowed_peer_ids:
                                    return
                            try:
                                sender = await event.get_sender()
                                if sender is not None and bool(getattr(sender, "bot", False)):
                                    return
                            except Exception:
                                pass
                            text = getattr(event.message, "message", "") or ""
                            if is_bot_echo_notification_text(text):
                                return
                            try:
                                source = await self._new_message_source(event)
                            except Exception as exc:
                                LOG.warning("chat resolve failed: %s", exc)
                                source = str(getattr(event, "chat_id", "") or "")
                            await self.process_message(
                                source,
                                int(event.message.id),
                                event.message.date,
                                text,
                                telethon_client=event.client,
                                telethon_message=event.message,
                            )

                    if allowed_peer_ids is None:
                        filt_tag = "all_chats"
                    else:
                        filt_tag = f"peer_ids:{len(allowed_peer_ids)}"
                    LOG.info(
                        "started: ch_yaml=%s ch_eff=%s scan_yaml_default=%s scan_eff=%s market_scan_loop=%s "
                        "(interval_sec=%s) agent_world=%s control_panel=%s allowed_chats_filter=%s",
                        self.auto_execute,
                        self._effective_channel_auto_execute(),
                        bool(self.market_scanner_auto_execute_default),
                        self._effective_market_scanner_auto_execute(),
                        self.market_scanner_enabled,
                        self.market_scanner_interval_sec,
                        self.agent_world_enabled,
                        bool(
                            self.control_panel_enabled
                            and os.getenv("TELEGRAM_TOKEN", "").strip()
                            and os.getenv("TELEGRAM_CHAT_ID", "").strip()
                        ),
                        filt_tag,
                    )
                    if self.channel_rating_enabled or self.signal_excursion_cfg.enabled:
                        rating_task = asyncio.create_task(self._rating_monitor_loop())
                    if self.channel_daily_report_enabled:
                        daily_task = asyncio.create_task(self._daily_channel_report_loop())
                    mc_root = self.cfg.get("market_scanner") or {}
                    run_scan_loop = bool(mc_root.get("run_loop_in_signal_agent", True))
                    if self.market_scanner_enabled and run_scan_loop:
                        scanner_task = asyncio.create_task(self._market_scanner_loop())
                    elif self.market_scanner_enabled and not run_scan_loop:
                        LOG.info(
                            "Market scanner loop skipped (market_scanner.run_loop_in_signal_agent=false; "
                            "ожидается цикл в run_unified / trading_bot)"
                        )
                    if self.agent_world_enabled:
                        world_task = asyncio.create_task(self._world_feed_loop())
                    if self.control_panel_enabled and os.getenv("TELEGRAM_TOKEN", "").strip():
                        cid = os.getenv("TELEGRAM_CHAT_ID", "").strip()
                        if cid:
                            panel_task = start_control_panel_task(self)
                            LOG.info("Control panel (/start,/panel via Bot API) started for chat id=%s", cid)
                        else:
                            LOG.warning(
                                "control_panel_enabled but TELEGRAM_CHAT_ID missing; panel skipped"
                            )
                    try:
                        await client.run_until_disconnected()
                    finally:
                        for task in (rating_task, scanner_task, daily_task, world_task, panel_task):
                            if task is not None:
                                task.cancel()
                                try:
                                    await task
                                except asyncio.CancelledError:
                                    pass

                LOG.warning(
                    "Telethon: сессия завершилась (сеть/API/выход из run_until_disconnected); "
                    "повторное подключение через %.1f с (этот процесс не завершён — без лишних рестартов systemd)",
                    reconnect_after_sec,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception(
                    "Telethon: ошибка в цикле подключения; пауза %.1f с перед следующей попыткой",
                    reconnect_after_sec,
                )

            await asyncio.sleep(reconnect_after_sec)

    async def _telegram_client(self):
        if TelegramClient is None or events is None:
            raise RuntimeError("telethon is not installed. Run: pip install telethon")
        api_id = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
        api_hash = os.getenv("TELEGRAM_API_HASH", "")
        if api_id <= 0 or not api_hash:
            raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
        session_name = str(self.agent_cfg.get("session_name", "telegram_user_signal_agent"))
        session_path = self.repo_dir / session_name
        return TelegramClient(str(session_path), api_id, api_hash)

    async def close(self) -> None:
        if self.bybit is not None:
            await self.bybit.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Telegram channels for trading signals.")
    parser.add_argument("--repo-dir", default=str(ROOT), help="Bot repository directory")
    parser.add_argument("--once", action="store_true", help="Analyze recent messages and exit")
    parser.add_argument("--market-scan-once", action="store_true", help="Run one Bybit market scan and exit")
    parser.add_argument("--limit", type=int, default=30, help="Messages per channel in --once mode")
    parser.add_argument("--max-chats", type=int, default=None, help="Max channels/chats to scan in --once mode")
    return parser.parse_args()


async def amain() -> int:
    args = parse_args()
    repo = Path(args.repo_dir).resolve()
    agent: TelegramSignalAgent | None = None
    try:
        agent = TelegramSignalAgent(repo)
    except Exception:
        LOG.exception("FATAL: не удалось инициализировать TelegramSignalAgent (config/state/.env?)")
        return 1

    if not agent.enabled:
        LOG.info("disabled by config")
        return 0

    if args.market_scan_once:
        try:
            await agent.run_market_scan_once()
        except Exception:
            LOG.exception("FATAL: market scan failed")
            return 1
        finally:
            if agent is not None:
                try:
                    await agent.close()
                except Exception:
                    LOG.exception("agent.close() завершился ошибкой")
        return 0

    if TelegramClient is None or events is None:
        LOG.error("FATAL: пакет telethon не установлен в этом venv. Запустите: pip install telethon")
        return 1

    try:
        if args.once:
            if args.max_chats is not None:
                agent.max_chats_once = max(1, int(args.max_chats))
            await agent.run_once(args.limit)
        else:
            await agent.run_forever()
    except Exception:
        LOG.exception(
            "FATAL: падение в основном цикле (Telethon сессия/API, Bybit ключи, сеть). "
            "См. полный traceback в telegram_signal_agent.log и journalctl -u telegram-signal-agent"
        )
        return 1
    finally:
        if agent is not None:
            try:
                await agent.close()
            except Exception:
                LOG.exception("agent.close() завершился ошибкой")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
