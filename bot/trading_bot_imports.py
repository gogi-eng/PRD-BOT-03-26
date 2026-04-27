"""Imports and path setup shared by TradingBot mixins (mirrors legacy main.py top)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BOT_DIR = ROOT


def resolve_bot_dir() -> Path:
    """Prefer ``main.BOT_DIR`` when main is loaded (tests monkeypatch this)."""
    main_mod = sys.modules.get("main")
    if main_mod is not None:
        d = getattr(main_mod, "BOT_DIR", None)
        if d is not None:
            return Path(d)
    return ROOT


from analysis.ai_analyzer import AITradeAnalyzer
from analysis.advisor import LocalTradingAdvisor
from analysis.correlation_filter import CorrelationFilter
from analysis.feature_engineering import FeatureEngineer
from analysis.liquidation_clusters import LiquidationCluster, LiquidationClusterDetector, LiquidationAnalysis
from analysis.liquidity_heatmap import LiquidityHeatmap
from analysis.market_analyzer import MarketAnalyzer
from analysis.market_regime_ai import MarketRegimeAI
from analysis.market_structure import MarketStructureEngine
from analysis.orderflow_analyzer import OrderflowAnalyzer
from analysis.structure_zones import StructureZoneAnalyzer
from analysis.transformer_model import TransformerPriceModel
from core.config import BotConfig
from core.live_controls import LiveControls
from core.main_refactor_helpers import (
    classify_exchange_closed_reason,
    filter_recent_closed_pnl,
    interval_to_seconds,
    last_closed_kline_ts,
    parse_iso_dt,
)
from core.security import SecureStore
from engine.ai_decision import AIDecisionEngine
from engine.capital_allocator import MultiSymbolCapitalAllocator
from engine.entry_engine import EntryEngine, EntrySignal
from engine.execution_ai import ExecutionAI
from engine.execution_engine import ExecutionEngine
from engine.exit_engine import ExitEngine, ExitReason
from engine.position_manager import Position, PositionManager
from engine.risk_manager import RiskGuard
from engine.rl_position_agent import RLAction, RLPositionAgent
from engine.signal_feedback_loop import SignalFeedbackLoop
from engine.symbol_quality_filter import SymbolQualityFilter
from exchange.bybit_client import BybitClient
from portfolio_profit_lock import PortfolioProfitLock
from strategy import ScalpSessionStrategy
from tg.controller import TelegramController
from utils import ATRCalculator

from bot.state import BasketProfitState

_LOG_FMT = logging.Formatter("%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")


def _configure_logging() -> None:
    """Console (systemd/journal) + ``bot.log`` in project root — same lines to both."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    log_path = (resolve_bot_dir() / "bot.log").resolve()

    def _has_stderr() -> bool:
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr:
                return True
        return False

    def _has_bot_log_file() -> bool:
        for h in root.handlers:
            if isinstance(h, logging.FileHandler):
                try:
                    if Path(h.baseFilename).resolve() == log_path:
                        return True
                except (OSError, ValueError):
                    pass
        return False

    if not _has_stderr():
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(_LOG_FMT)
        root.addHandler(sh)
    if not _has_bot_log_file():
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(_LOG_FMT)
        root.addHandler(fh)


_configure_logging()
logger = logging.getLogger("BOT")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
