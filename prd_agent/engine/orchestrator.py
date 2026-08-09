"""
Главный цикл unified-агента: все источники сигналов, журнал, риск, ордера, сопровождение, анализ.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from prd_agent.analysis.bybit_monitor import BybitMonitorAgent
from prd_agent.analysis.entry_snapshot import build_entry_snapshot, build_light_signal_snapshot
from prd_agent.analysis.wallet_flow_agent import WalletFlowAgent
from prd_agent.analysis.global_analyzer import GlobalAnalyzer
from prd_agent.analysis.macro_ai import MacroAI
from prd_agent.analysis.signal_ledger import LedgerEntry, SignalLedger, SignalStatus
from prd_agent.analysis.trade_analytics import (
    build_daily_pnl_report,
    build_portfolio_quality_report,
    build_report as build_trade_stats_report,
    load_closed_trades,
    summarize_trades,
)
from prd_agent.analysis.trade_journal import TradeJournal
from prd_agent.analysis.trade_lifecycle import TradeLifecycleTracker
from prd_agent.analysis.trade_monitor import TradeMonitor
from prd_agent.config import load_config
from prd_agent.config_presets import ALLOWED_PRESETS, apply_risk_preset
from prd_agent.engine.adaptive_loop import compute_loop_interval_sec
from prd_agent.entry.derivatives_entry_guard import DerivativesEntryGuard
from prd_agent.entry.entry_engine_bridge import EntryEngineBridge, should_apply_zone_entry
from prd_agent.entry.entry_pipeline import evaluate_entry_pipeline
from prd_agent.entry.entry_soft_rules import compute_soft_score
from prd_agent.entry.retest_watchlist import RetestWatchlist
from prd_agent.entry.rule_weight_tracker import RuleWeightTracker
from prd_agent.entry.zone_corridor_play import (
    evaluate_zone_corridor_play,
    zone_corridor_enabled,
)
from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.exchange.bybit_adapter import BybitAdapter
from prd_agent.exchange.order_prep import prepare_order
from prd_agent.risk.entry_guard import build_entry_execution_plan
from prd_agent.risk.pullback_entry import check_pullback_entry
from prd_agent.risk.volatility_regime_sizing import (
    evaluate_volatility_regime_sizing,
    log_volatility_regime_startup,
)
from prd_agent.signals.pump_dump_mode import is_agent_world_signal, is_pump_dump_signal
from prd_agent.reporting.bi_hourly import BiHourlyReporter
from prd_agent.risk.closed_pnl_dedup import ClosedPnlDedup
from prd_agent.risk.guard import GuardStatus, RiskGuard, StopKind
from prd_agent.risk.rr_enforce import enforce_min_rr_levels, rr_ratio
from prd_agent.risk.quality_gate import QualityGate
from prd_agent.market.market_scanner_bridge import (
    market_scanner_cfg,
    run_market_scan_once,
    run_spike_scan_once,
    spike_scalp_cfg,
    unified_should_run_market_scan,
    unified_should_run_spike_scan,
)
from prd_agent.ops.bot_manager import BotManagerAgent
from prd_agent.ops.runtime_controls import is_signal_only_active, load_runtime_controls
from prd_agent.market.symbol_scanner import SymbolScanner
from prd_agent.positions.bot_position_registry import resolve_closed_origin
from prd_agent.positions.opposite_signal_policy import (
    lookup_open_entry_meta,
    should_block_opposite_exit_for_weak_or_young,
    should_skip_opposite_exit_for_spike_own,
)
from prd_agent.positions.scanner_reversal_sl import position_age_minutes
from prd_agent.positions.position_steward import PositionSteward
from prd_agent.positions.trade_companion import TradeCompanionAgent
from prd_agent.positions.sr_sl_tp_adjust import adjust_sl_tp_with_sr_zones
from prd_agent.supervisor.supervisor_v4 import SupervisorV4
from prd_agent.signals.confidence_filter import (
    PerSymbolSignalCooldown,
    filter_signal_dicts,
    load_min_analysis_confidence,
    load_signal_notify_cooldown_sec,
    passes_emit_gate,
)
from prd_agent.signals.router import SignalRouter
from prd_agent.signals.side_utils import normalize_trade_side, trade_sides_opposite
from prd_agent.signals.types import UnifiedSignal
from prd_agent.strategies.router import StrategyRouter
from prd_agent.telemetry.skip_baseline import format_skip_baseline_text, skip_baseline_report
from prd_agent.telegram.notifier import TelegramNotifier
from prd_agent.telegram.status_table import format_status_table

logger = logging.getLogger("prd_agent")


class UnifiedOrchestrator:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.root = Path(cfg["_root"])
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.exchange = BybitAdapter(cfg)
        self.risk = RiskGuard(cfg)
        self.signals = SignalRouter(cfg, self.data_dir / "signals")
        self.ledger = SignalLedger(self.data_dir / "ledger")
        self.monitor = TradeMonitor(self.data_dir / "trades")
        self.trade_journal = TradeJournal(self.data_dir, cfg)
        self.trade_lifecycle = TradeLifecycleTracker(self.data_dir, cfg)
        self._min_analysis_conf = load_min_analysis_confidence(cfg)
        self._signal_cooldown = PerSymbolSignalCooldown(
            load_signal_notify_cooldown_sec(cfg)
        )
        self.reporter = BiHourlyReporter(cfg)
        self.reporter.high_conf = self._min_analysis_conf
        self.improver = SelfImprover(cfg, self.root, on_config_reload=self.reload_config)
        self.supervisor = SupervisorV4(cfg, self.data_dir, self.improver)
        self.notifier = TelegramNotifier(cfg)
        self.global_analyzer = GlobalAnalyzer(cfg, self.ledger, self.monitor)
        self.symbol_scanner = SymbolScanner(cfg)
        self.position_steward = PositionSteward(cfg)
        self.trade_companion = TradeCompanionAgent(cfg)
        self.quality_gate = QualityGate(cfg)
        self.derivatives_guard = DerivativesEntryGuard(cfg)
        log_volatility_regime_startup(cfg)
        self.macro_ai = MacroAI(cfg)
        self.bybit_monitor = BybitMonitorAgent(cfg)
        self.wallet_tracker = WalletFlowAgent(cfg, self.data_dir)
        self.bot_manager = BotManagerAgent(cfg)
        an = cfg.get("analytics", {})
        self._stats_hours = float(an.get("report_hours", 24))
        self._portfolio_quality_hours = float(an.get("portfolio_quality_hours", 168))
        self._daily_pnl_days = int(an.get("daily_pnl_days", 7))
        self._daily_pnl_split_origin = bool(an.get("daily_pnl_split_origin", True))
        self._daily_pnl_exclude_manual = bool(an.get("exclude_manual", False))
        self._skipped_lab_hours = float(an.get("skipped_lab_hours", 168))

        t = cfg.get("trading", {})
        self.symbols: List[str] = list(t.get("symbols", ["BTCUSDT"]))
        self._symbol_rescan_sec = float(t.get("symbol_rescan_interval_sec", 1800))
        self._last_symbol_scan_at = 0.0
        self.leverage = int(t.get("leverage", 5))
        self.risk_pct = float(t.get("risk_pct_per_trade", 0.5))
        self.report_interval_sec = float(cfg.get("reporter", {}).get("interval_hours", 2)) * 3600
        ga = cfg.get("global_analysis", {})
        self.global_interval_sec = float(ga.get("interval_hours", 6)) * 3600
        self._running = False
        _boot_ts = datetime.now(timezone.utc).timestamp()
        self._last_report_at = _boot_ts
        self._last_global_at = _boot_ts
        self._loop_sec = float(t.get("loop_interval_sec", 60))
        self._closed_pnl_dedup = ClosedPnlDedup(self.data_dir / "risk_seen_closed.json")
        self._last_upnl: Dict[str, float] = {}
        self._notify_loop: Optional[asyncio.AbstractEventLoop] = None
        self._block_notify_sent = False
        self._cycle_num = 0
        self._last_market_activity_at = _boot_ts
        self._last_loop_interval_logged = 0.0
        _mc = market_scanner_cfg(cfg)
        self._market_scan_interval_sec = float(_mc.get("interval_sec", 600))
        self._market_scan_task: Optional[asyncio.Task] = None
        _sp = spike_scalp_cfg(cfg)
        self._spike_scan_interval_sec = float(_sp.get("interval_sec", 90))
        self._spike_scan_task: Optional[asyncio.Task] = None
        self._bot_manager_task: Optional[asyncio.Task] = None
        self._bybit_monitor_task: Optional[asyncio.Task] = None
        self._wallet_tracker_task: Optional[asyncio.Task] = None
        self._silent_skip_prefixes = (
            "Пауза после стопа",
            "Кулдаун после убытка",
            "Макс. позиций",
            "EMERGENCY",
            "на бирже уже открыта",
            "недостаточно свободной маржи",
            "недостаточно маржи",
            "quality_gate:",
            "entry_guard:",
            "Bybit circuit",
            "circuit open",
        )
        self.zone_entry = EntryEngineBridge(cfg)
        self.retest_watch = RetestWatchlist(cfg)
        self.strategy_router = StrategyRouter(cfg)
        self._last_api_cycle_snap: Dict[str, Any] = {}
        self._light_snapshot_cache: Dict[str, Dict[str, Any]] = {}
        _ze = cfg.get("zone_entry", {}) if isinstance(cfg.get("zone_entry"), dict) else {}
        self._zone_kline_interval = str(_ze.get("kline_interval", "15"))
        self.rule_weight_tracker = RuleWeightTracker(self.data_dir, cfg)
        self._apply_sr_zones_config()
        self._apply_open_position_policy()

    def _apply_open_position_policy(self) -> None:
        p = self.cfg.get("positions", {}) if isinstance(self.cfg.get("positions"), dict) else {}
        opp = p.get("opposite_signal_exit") if isinstance(p.get("opposite_signal_exit"), dict) else {}
        # Новый ключ opposite_signal_exit.enabled; старый reverse_signal_close_enabled — совместимость.
        if "enabled" in opp:
            self._reverse_signal_close = bool(opp.get("enabled"))
        else:
            self._reverse_signal_close = bool(p.get("reverse_signal_close_enabled", True))
        # SPIKE + own opposite → не закрывать (DEXE-кейс). Default ON.
        self._skip_spike_on_own_opposite = bool(opp.get("skip_spike_on_own_signal", True))

    def _apply_sr_zones_config(self) -> None:
        sr = self.cfg.get("execution_sr_zones", {})
        if not isinstance(sr, dict):
            sr = {}
        self._sr_enabled = bool(sr.get("enabled", True))
        self._sr_interval = str(sr.get("kline_interval", "15"))
        self._sr_limit = max(20, int(sr.get("kline_limit", 120) or 120))
        self._sr_sl_atr = float(sr.get("sl_extra_buffer_atr", 0.1))
        self._sr_tp_atr = float(sr.get("tp_extra_buffer_atr", 0.08))
        preserve = float(sr.get("preserve_min_rr", 0) or 0)
        if preserve <= 0:
            q = self.cfg.get("quality_gate", {})
            if isinstance(q, dict):
                preserve = float(q.get("min_rr_ratio", 2.0))
        self._sr_preserve_rr = preserve
        self._sr_sl_level_index = max(0, int(sr.get("sl_sr_level_index", 1) or 1))
        self._sr_tp_level_index = max(1, int(sr.get("tp_sr_level_index", 1) or 1))
        self._sr_prefer_far_tp = bool(sr.get("prefer_far_tp", True))
        self._sr_target_initial_tp_rr = float(sr.get("target_initial_tp_rr", 0) or 0)
        self._sr_min_tp_distance_pct = float(sr.get("min_tp_distance_pct", 1.0) or 1.0)

    def _effective_min_rr(self, min_rr: float) -> float:
        target = float(getattr(self, "_sr_target_initial_tp_rr", 0) or 0)
        base = float(min_rr or 0)
        if target > 0:
            return max(base, target)
        return base

    async def _apply_sr_zones_to_levels(
        self,
        symbol: str,
        side: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
    ) -> tuple[float, float]:
        if not self._sr_enabled:
            return stop_loss, take_profit
        try:
            klines = await self.exchange.get_klines(
                symbol.upper(),
                interval=self._sr_interval,
                limit=self._sr_limit,
            )
            new_sl, new_tp, changed = adjust_sl_tp_with_sr_zones(
                entry=entry,
                side=side,
                stop_loss=stop_loss,
                take_profit=take_profit,
                klines=klines,
                sl_extra_atr=self._sr_sl_atr,
                tp_extra_atr=self._sr_tp_atr,
                preserve_min_rr=self._sr_preserve_rr,
                sl_sr_level_index=self._sr_sl_level_index,
                min_tp_distance_pct=self._sr_min_tp_distance_pct,
                prefer_far_tp=self._sr_prefer_far_tp,
                tp_sr_level_index=self._sr_tp_level_index,
                target_initial_tp_rr=self._sr_target_initial_tp_rr,
            )
            if changed:
                logger.info(
                    "SR zones %s %s: SL %.6g→%.6g TP %.6g→%.6g (поддержка/сопротивление)",
                    symbol,
                    side,
                    stop_loss,
                    new_sl,
                    take_profit,
                    new_tp,
                )
            return new_sl, new_tp
        except Exception as exc:
            logger.warning("SR zones %s: %s", symbol, exc)
            return stop_loss, take_profit

    def _risk_notify(self, msg: str) -> None:
        # AUTO-STOP дублируется таблицей в _notify_risk_block_once — не спамим в Telegram.
        if str(msg).startswith("AUTO-STOP"):
            return
        if self._notify_loop and self._notify_loop.is_running():
            asyncio.run_coroutine_threadsafe(self.notifier.risk_event(msg), self._notify_loop)

    def reload_config(self) -> None:
        path = Path(self.cfg.get("_config_path", self.root / "config.yaml"))
        self.cfg = load_config(path)
        sig = self.cfg.get("signals", {}) if isinstance(self.cfg.get("signals"), dict) else {}
        t = self.cfg.get("trading", {})
        self.risk_pct = float(t.get("risk_pct_per_trade", self.risk_pct))
        self.signals._min_conf = float(t.get("min_signal_confidence", self.signals._min_conf))
        self.signals._min_own_conf = float(
            t.get("min_own_agent_confidence", getattr(self.signals, "_min_own_conf", 0.28))
        )
        self.signals._min_tg_conf = float(
            sig.get(
                "min_telegram_confidence",
                t.get("min_telegram_confidence", getattr(self.signals, "_min_tg_conf", self.signals._min_conf)),
            )
        )
        # Не пересоздаём steward — иначе теряется _tracked и в Telegram снова «Подхвачена позиция».
        self.position_steward.apply_config(self.cfg)
        self.trade_companion.apply_config(self.cfg)
        self.trade_lifecycle.apply_config(self.cfg)
        self.quality_gate = QualityGate(self.cfg)
        self.derivatives_guard = DerivativesEntryGuard(self.cfg)
        self.macro_ai = MacroAI(self.cfg)
        an = self.cfg.get("analytics", {})
        self._stats_hours = float(an.get("report_hours", self._stats_hours))
        self._portfolio_quality_hours = float(
            an.get("portfolio_quality_hours", self._portfolio_quality_hours)
        )
        self._daily_pnl_days = int(an.get("daily_pnl_days", self._daily_pnl_days))
        self._daily_pnl_split_origin = bool(
            an.get("daily_pnl_split_origin", self._daily_pnl_split_origin)
        )
        self._daily_pnl_exclude_manual = bool(
            an.get("exclude_manual", self._daily_pnl_exclude_manual)
        )
        self._skipped_lab_hours = float(
            an.get("skipped_lab_hours", self._skipped_lab_hours)
        )
        ext_on = bool(self.cfg.get("external_sentiment", {}).get("enabled", True))
        if ext_on:
            from prd_agent.signals.external_sentiment_agent import ExternalSentimentAgent

            self.signals._external_sentiment = ExternalSentimentAgent(self.cfg)
        else:
            self.signals._external_sentiment = None
        self.symbols = list(t.get("symbols", self.symbols))
        self.symbol_scanner = SymbolScanner(self.cfg)
        self._symbol_rescan_sec = float(t.get("symbol_rescan_interval_sec", self._symbol_rescan_sec))
        self._min_analysis_conf = load_min_analysis_confidence(self.cfg)
        self.reporter.high_conf = self._min_analysis_conf
        self._signal_cooldown = PerSymbolSignalCooldown(
            load_signal_notify_cooldown_sec(self.cfg)
        )
        self._apply_sr_zones_config()
        self._apply_open_position_policy()
        t = self.cfg.get("trading", {})
        self._loop_sec = float(t.get("loop_interval_sec", self._loop_sec))
        self.leverage = int(t.get("leverage", self.leverage))
        self.risk.max_positions = int(t.get("max_positions", self.risk.max_positions))
        prev_skipped_bt_at = float(
            getattr(self.supervisor, "_last_skipped_bt_at", 0.0) or 0.0
        )
        self.supervisor = SupervisorV4(self.cfg, self.data_dir, self.improver)
        if prev_skipped_bt_at > 0:
            self.supervisor._last_skipped_bt_at = prev_skipped_bt_at
        self.notifier._cfg = self.cfg
        self.zone_entry = EntryEngineBridge(self.cfg)
        self.retest_watch.cfg = self.cfg
        self.strategy_router.cfg = self.cfg
        ze = self.cfg.get("zone_entry", {}) if isinstance(self.cfg.get("zone_entry"), dict) else {}
        self._zone_kline_interval = str(ze.get("kline_interval", "15"))

    async def _plan_order_levels(
        self, sig: UnifiedSignal
    ) -> tuple[float, float, float, str, Dict[str, Any]]:
        """Цены входа/SL/TP: зона/BOS/ретест + SR-зоны для SL/TP."""
        entry = float(sig.entry or 0) or await self.exchange.get_price(sig.symbol)
        sl = float(sig.stop_loss or 0) or (
            entry * 0.995 if sig.side == "Buy" else entry * 1.005
        )
        tp = float(sig.take_profit or 0) or (
            entry * 1.01 if sig.side == "Buy" else entry * 0.99
        )
        block_reason = ""
        zone_meta: Dict[str, Any] = {}

        if should_apply_zone_entry(sig, self.cfg):
            klines, htf = await asyncio.gather(
                self.exchange.get_klines(
                    sig.symbol,
                    interval=self._zone_kline_interval,
                    limit=120,
                ),
                self.exchange.get_klines(sig.symbol, interval="240", limit=120),
            )
            plan = await self.zone_entry.plan_levels(
                sig,
                klines=klines or [],
                htf_klines=htf,
                market_price=entry,
                exchange=self.exchange,
            )
            if isinstance(plan.metadata, dict):
                zone_meta = dict(plan.metadata)
            if not plan.ok:
                block_reason = plan.block_reason or "zone_entry: вход заблокирован"
            else:
                entry = float(plan.entry or entry)
                if plan.stop_loss > 0:
                    sl = float(plan.stop_loss)
                if plan.take_profit > 0:
                    tp = float(plan.take_profit)

        sl, tp = await self._apply_sr_zones_to_levels(sig.symbol, sig.side, entry, sl, tp)
        min_rr = self.quality_gate._min_rr_for_signal(sig)
        effective_rr = self._effective_min_rr(min_rr)
        sl, tp, rr_ok = enforce_min_rr_levels(
            side=sig.side,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            min_rr=effective_rr,
        )
        if not rr_ok:
            block_reason = (
                block_reason
                or f"plan: RR {rr_ratio(entry, sl, tp, sig.side):.2f} < {effective_rr:.2f} (SL/TP)"
            )
        return entry, sl, tp, block_reason, zone_meta

    def _maybe_register_retest_watch(
        self,
        sig: UnifiedSignal,
        reason: str,
        zone_meta: Dict[str, Any],
    ) -> None:
        if not self.retest_watch.enabled():
            return
        r = str(reason or "").lower()
        if "нет свечи ретеста" not in r and "нет свечи подтверждения" not in r:
            return
        bos = float(zone_meta.get("bos_level", 0) or 0)
        self.retest_watch.register_breakout(
            sig.symbol,
            sig.side,
            bos_level=bos,
            zone_low=float(zone_meta.get("zone_low", 0) or 0),
            zone_high=float(zone_meta.get("zone_high", 0) or 0),
        )

    def set_trailing_enabled(self, enabled: bool) -> str:
        """Вкл/выкл трейлинг SL; сохраняет positions.trailing_enabled в config.yaml."""
        import shutil

        import yaml

        path = Path(self.cfg.get("_config_path", self.root / "config.yaml"))
        if path.exists():
            backup = (
                self.improver.sandbox_dir
                / f"config_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.yaml"
            )
            shutil.copy2(path, backup)
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            data.setdefault("positions", {})["trailing_enabled"] = bool(enabled)
            with path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
            self.reload_config()
            backup_note = f"Резервная копия: {backup.name}"
        else:
            self.position_steward.enabled = bool(enabled)
            backup_note = "config.yaml не найден — только до перезапуска"
        state = "ВКЛ" if self.position_steward.enabled else "ВЫКЛ"
        logger.info("Trailing %s via Telegram", state)
        return f"Трейлинг позиций: <b>{state}</b>\n{backup_note}"

    def reset_daily_loss(self) -> str:
        """Сброс дневного PnL, блокировки по лимиту и протокола Supervisor."""
        msg = self.risk.reset_daily_loss_counter()
        self.risk.reset_streak_counters()
        sup_msg = self.supervisor.clear_recovery_protocol()
        self._block_notify_sent = False
        logger.info("Daily loss + supervisor recovery reset via Telegram")
        return f"{msg}\n{sup_msg}"

    def reset_risk_stops(self) -> str:
        """Сброс risk-стопов и протокола восстановления Supervisor (кнопка «Сброс риска»)."""
        self.risk.status = GuardStatus.ACTIVE
        self.risk.stop_reason = ""
        self.risk.stop_kind = StopKind.NONE
        self.risk.auto_stop_time = None
        self.risk.reset_streak_counters()
        sup_msg = self.supervisor.clear_recovery_protocol()
        self._block_notify_sent = False
        logger.info("Risk stops + supervisor recovery reset via Telegram")
        return f"Риск-стоп сброшен (пауза/серия). {sup_msg}"

    async def _refresh_symbols_if_due(self, *, force: bool = False) -> None:
        if not self.symbol_scanner.enabled():
            return
        now = datetime.now(timezone.utc).timestamp()
        if not force and (now - self._last_symbol_scan_at) < self._symbol_rescan_sec:
            return
        self.symbols = await self.symbol_scanner.scan(self.exchange)
        self._last_symbol_scan_at = now

    async def _sync_orderbook_ws(self, positions: List[Dict]) -> None:
        if not hasattr(self.exchange, "set_orderbook_symbols"):
            return
        open_syms = [
            str(p.get("symbol", "") or "").upper()
            for p in positions
            if p.get("symbol")
        ]
        merged: List[str] = []
        seen: set = set()
        for sym in list(self.symbols) + open_syms:
            s = str(sym or "").upper()
            if s and s not in seen:
                seen.add(s)
                merged.append(s)
        await self.exchange.set_orderbook_symbols(merged[:30])

    async def start(self) -> None:
        if self._running:
            logger.debug("Trading loop already running, skip duplicate start()")
            return
        self._running = True
        self._notify_loop = asyncio.get_running_loop()
        self.risk.set_notify_callback(self._risk_notify)
        balance = await self.exchange.get_balance()
        self.risk.update_balance_reference(balance)
        await self._refresh_symbols_if_due(force=True)
        mode = "TESTNET" if self.exchange.is_testnet else "LIVE"
        positions = await self.exchange.get_positions()
        table = await self.build_status_table(positions=positions, block_reason="")
        await self.notifier.send(f"🚀 <b>Unified Agent</b> запущен\n\n{table}")
        logger.info("Unified started balance=%.2f testnet=%s", balance, self.exchange.is_testnet)
        if unified_should_run_market_scan(self.cfg):
            self._market_scan_task = asyncio.create_task(self._market_scanner_loop())
            logger.info(
                "MARKET SCANNER: цикл в unified-боте, интервал %.0f сек",
                self._market_scan_interval_sec,
            )
        if unified_should_run_spike_scan(self.cfg):
            self._spike_scan_task = asyncio.create_task(self._spike_scanner_loop())
            logger.info(
                "SPIKE SCANNER: 15m импульс, интервал %.0f сек",
                self._spike_scan_interval_sec,
            )
        if self.bot_manager.enabled:
            self._bot_manager_task = asyncio.create_task(self._bot_manager_loop())
            logger.info(
                "BOT MANAGER: цикл советов, интервал %.0f сек",
                self.bot_manager.interval_sec,
            )
        if self.bybit_monitor.enabled and self.bybit_monitor.notify_telegram:
            self._bybit_monitor_task = asyncio.create_task(self._bybit_monitor_loop())
            logger.info(
                "BYBIT MONITOR: фоновые алерты, интервал %.0f сек",
                self.bybit_monitor.interval_sec,
            )
        if self.wallet_tracker.should_run_loop():
            self._wallet_tracker_task = asyncio.create_task(self._wallet_tracker_loop())
            logger.info(
                "Wallet tracker advisory: цикл опроса, интервал %.0f сек",
                self.wallet_tracker.poll_interval_sec,
            )
        if self.trade_companion.enabled:
            logger.info("TRADE COMPANION: сопровождение открытых сделок включено")
        if self.trade_lifecycle.enabled:
            logger.info("TRADE LIFECYCLE: сбор статистики по сделкам включён")
        while self._running:
            try:
                await self._cycle()
            except Exception as exc:
                logger.exception("Cycle error: %s", exc)
                err_text = f"{type(exc).__name__}: {exc}"
                await self.notifier.send(f"⚠️ Ошибка цикла: {err_text[:350]}")
            await asyncio.sleep(self._loop_sec)

    def stop(self) -> None:
        self._running = False
        if self._market_scan_task is not None:
            self._market_scan_task.cancel()
            self._market_scan_task = None
        if self._spike_scan_task is not None:
            self._spike_scan_task.cancel()
            self._spike_scan_task = None
        if self._bot_manager_task is not None:
            self._bot_manager_task.cancel()
            self._bot_manager_task = None
        if self._bybit_monitor_task is not None:
            self._bybit_monitor_task.cancel()
            self._bybit_monitor_task = None
        if self._wallet_tracker_task is not None:
            self._wallet_tracker_task.cancel()
            self._wallet_tracker_task = None

    async def close(self) -> None:
        self.stop()
        if self._market_scan_task is not None:
            try:
                await self._market_scan_task
            except asyncio.CancelledError:
                pass
            self._market_scan_task = None
        if self._spike_scan_task is not None:
            try:
                await self._spike_scan_task
            except asyncio.CancelledError:
                pass
            self._spike_scan_task = None
        if self._bot_manager_task is not None:
            try:
                await self._bot_manager_task
            except asyncio.CancelledError:
                pass
            self._bot_manager_task = None
        if self._bybit_monitor_task is not None:
            try:
                await self._bybit_monitor_task
            except asyncio.CancelledError:
                pass
            self._bybit_monitor_task = None
        if self._wallet_tracker_task is not None:
            try:
                await self._wallet_tracker_task
            except asyncio.CancelledError:
                pass
            self._wallet_tracker_task = None
        if self.bybit_monitor._read_exchange is not None:
            await self.bybit_monitor._read_exchange.close()
        await self.exchange.close()

    async def _market_scanner_loop(self) -> None:
        """Уведомления MARKET SCANNER в Telegram (PUMP/DUMP наблюдения)."""
        await asyncio.sleep(15)
        while self._running:
            try:
                setups = await run_market_scan_once(self.root, self.cfg)
                if setups:
                    logger.info("MARKET SCANNER: отправлено/найдено сетапов: %s", len(setups))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MARKET SCANNER: %s", exc)
            await asyncio.sleep(max(120.0, self._market_scan_interval_sec))

    async def _spike_scanner_loop(self) -> None:
        """Быстрый скан 15m импульсов (памп/дамп скальп)."""
        await asyncio.sleep(25)
        while self._running:
            try:
                setups = await run_spike_scan_once(self.root, self.cfg)
                if setups:
                    logger.info("SPIKE SCANNER: сетапов: %s", len(setups))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("SPIKE SCANNER: %s", exc)
            await asyncio.sleep(max(45.0, self._spike_scan_interval_sec))

    async def _bot_manager_loop(self) -> None:
        """Периодический AI-обзор состояния бота (без торговли)."""
        await asyncio.sleep(90)
        bm_cfg = self.cfg.get("bot_manager", {}) or {}
        notify = bool(bm_cfg.get("notify_telegram", True))
        while self._running:
            try:
                text = await self.bot_manager.maybe_scheduled_review(self)
                if text and notify:
                    await self.notifier.send(text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("BOT MANAGER: %s", exc)
            await asyncio.sleep(max(300.0, self.bot_manager.interval_sec))

    async def _bybit_monitor_loop(self) -> None:
        """Фоновый read-only мониторинг позиций (уведомления в Telegram)."""
        await asyncio.sleep(120)
        while self._running:
            try:
                text = await self.bybit_monitor.maybe_scheduled_alert(self)
                if text:
                    await self.notifier.send(text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("BYBIT MONITOR: %s", exc)
            await asyncio.sleep(max(120.0, self.bybit_monitor.interval_sec))

    async def get_bot_manager_review(self) -> str:
        return await self.bot_manager.run_review(self)

    async def get_bybit_monitor_report(self) -> str:
        return await self.bybit_monitor.build_report(self)

    def get_wallet_tracker_report(self) -> str:
        """Текстовый отчёт wallet tracker (без новой кнопки Telegram в v1)."""
        return self.wallet_tracker.build_report()

    async def _wallet_tracker_loop(self) -> None:
        """Фоновый опрос watch-кошельков → advisory рекомендации (без ордеров)."""
        await asyncio.sleep(75)
        while self._running:
            try:
                await self.wallet_tracker.poll_and_recommend(notifier=self.notifier)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Wallet tracker: %s", exc)
            await asyncio.sleep(max(60.0, self.wallet_tracker.poll_interval_sec))

    async def _sync_closed_pnl_to_risk(self) -> None:
        """Только новые закрытия с биржи; дедуп на диске — иначе после рестарта убыток «удваивается»."""
        rows = await self.monitor.fetch_closed_pnl(self.exchange, hours=24)
        journal_path = self.trade_journal.path
        audit_path = self.position_steward._telegram_audit_path()
        bot_symbols = set(self.position_steward._bot_symbols)
        for r in rows:
            oid = str(r.get("orderId") or r.get("id") or "")
            if not oid or not self._closed_pnl_dedup.is_new(oid):
                continue
            pnl = float(r.get("closedPnl", 0) or 0)
            self.risk.record_trade(pnl)
            self._closed_pnl_dedup.mark(oid)
            sym = str(r.get("symbol", "")).upper()
            side_raw = str(r.get("side", "")).upper()
            side = "Buy" if side_raw in ("BUY", "LONG") else "Sell" if side_raw in ("SELL", "SHORT") else side_raw
            exit_p = float(r.get("avgExitPrice", 0) or 0)
            origin = resolve_closed_origin(
                self.root / "data",
                sym,
                order_id=oid,
                journal_path=journal_path,
                telegram_audit_path=audit_path if audit_path.exists() else None,
                bot_symbols=bot_symbols,
            )
            pending = self.trade_journal.peek_pending(sym, side=side, order_id=oid)
            exit_ctx = self.trade_lifecycle.pop_exit_context(
                sym,
                side,
                exit_price=exit_p,
                pnl_usdt=pnl,
                reason="exchange_closed",
                pending=pending,
            )
            self.trade_journal.record_closed_from_exchange(
                r,
                origin=origin,
                exit_context=exit_ctx,
            )
        self.rule_weight_tracker.refresh_if_due(journal_path, force=False)

    @staticmethod
    def _dedupe_signals_for_report(signals: List[Dict], *, limit: int = 8) -> List[Dict]:
        """В отчёте — по одному последнему сигналу на символ (без повторов BTC×8)."""
        skip_reasons = ("позиция уже открыта",)
        latest: Dict[str, Dict] = {}
        for s in sorted(signals, key=lambda x: str(x.get("created_at", "")), reverse=True):
            reason = str(s.get("reason", "") or "")
            if any(x in reason for x in skip_reasons):
                continue
            sym = str(s.get("symbol", "")).upper()
            if sym and sym not in latest:
                latest[sym] = s
        return list(latest.values())[:limit]

    @staticmethod
    def _position_size(p: Dict) -> float:
        for key in ("size", "qty", "positionQty"):
            val = float(p.get(key, 0) or 0)
            if val > 0:
                return val
        avg = float(p.get("avgPrice", 0) or p.get("entryPrice", 0) or 0)
        pval = float(p.get("positionValue", 0) or 0)
        if pval > 0 and avg > 0:
            return pval / avg
        return 0.0

    @classmethod
    def _symbols_with_open_positions(cls, positions: List[Dict]) -> Set[str]:
        out: Set[str] = set()
        for p in positions:
            sym = str(p.get("symbol", "")).upper()
            if sym and cls._position_size(p) > 0:
                out.add(sym)
        return out

    @classmethod
    def _positions_by_symbol(cls, positions: List[Dict]) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        for row in positions:
            sym = str(row.get("symbol", "")).upper()
            if sym and cls._position_size(row) > 0:
                out[sym] = row
        return out

    async def _handle_signal_with_open_position(
        self, sig: UnifiedSignal, pos_row: Dict
    ) -> None:
        """
        Позиция по символу уже открыта:
        - тот же side → тихо игнор (без ledger / Telegram);
        - обратный side → срочное закрытие на бирже (если включено в config);
        - SPIKE + own-сигнал → не закрывать (skip_spike_on_own_signal).
        """
        sym = sig.symbol.upper()
        pos_side = str(pos_row.get("side", "") or "")
        if trade_sides_opposite(sig.side, pos_side):
            if self._reverse_signal_close:
                pos_cfg = (
                    self.cfg.get("positions", {})
                    if isinstance(self.cfg.get("positions"), dict)
                    else {}
                )
                tracked = self.position_steward._tracked.get(sym)
                if (
                    tracked is not None
                    and str(getattr(tracked, "origin", "") or "").lower() == "manual"
                    and not bool(pos_cfg.get("manual_auto_close", False))
                ):
                    logger.info(
                        "Opposite signal EXIT skipped %s origin=manual "
                        "(manual_auto_close=false)",
                        sym,
                    )
                    return
                open_src, open_pd = lookup_open_entry_meta(self.data_dir, sym)
                if should_skip_opposite_exit_for_spike_own(
                    position_source=open_src,
                    position_pump_dump=open_pd,
                    signal_source=str(sig.source or ""),
                    positions_cfg=pos_cfg,
                ):
                    logger.info(
                        "Opposite signal EXIT skipped SPIKE %s open=%s signal=%s "
                        "pos_src=%s sig_src=%s (skip_spike_on_own_signal)",
                        sym,
                        pos_side,
                        sig.side,
                        open_src or "?",
                        sig.source,
                    )
                    return
                pos_age = position_age_minutes(pos_row)
                blocked, block_why = should_block_opposite_exit_for_weak_or_young(
                    confidence=sig.confidence,
                    position_age_min=pos_age,
                    positions_cfg=pos_cfg,
                )
                if blocked:
                    logger.info(
                        "Opposite signal EXIT skipped %s open=%s signal=%s: %s",
                        sym,
                        pos_side,
                        sig.side,
                        block_why,
                    )
                    return
                await self._close_on_reverse_signal(sig, pos_row)
        else:
            logger.debug(
                "Open position %s %s — ignore same-side signal %s (no skip log)",
                sym,
                pos_side,
                sig.side,
            )

    async def _close_on_reverse_signal(self, sig: UnifiedSignal, pos_row: Dict) -> None:
        sym = sig.symbol.upper()
        pos_side = str(pos_row.get("side", "") or "")
        qty = self._position_size(pos_row)
        pidx = int(pos_row.get("positionIdx", 0) or 0)
        conf = float(sig.confidence or 0)
        conf_pct = conf * 100 if conf <= 1 else conf
        logger.warning(
            "Opposite signal EXIT %s open=%s signal=%s conf=%.0f%% src=%s",
            sym,
            pos_side,
            sig.side,
            conf_pct,
            sig.source,
        )
        if not hasattr(self.exchange, "close_position"):
            logger.error("Opposite signal EXIT %s: exchange.close_position unavailable", sym)
            return
        try:
            res = await self.exchange.close_position(
                sym, pos_side, qty=qty if qty > 0 else None, position_idx=pidx
            )
        except Exception as exc:
            logger.error(
                "Opposite signal EXIT %s failed: %s", sym, exc, exc_info=True
            )
            await self.notifier.send(
                f"⚠️ Не удалось закрыть {sym} по обратному сигналу: {exc}"
            )
            return
        if isinstance(res, dict) and (res.get("success") or res.get("orderId")):
            msg = (
                f"🔄 <b>Обратный сигнал — закрыто</b>\n"
                f"{sym}: было {pos_side} → сигнал {normalize_trade_side(sig.side)}\n"
                f"Источник: {sig.source}"
            )
            await self.notifier.send(msg)
            self.position_steward._tracked.pop(sym, None)
            self.position_steward._bot_symbols.discard(sym)
        else:
            err = str((res or {}).get("error", "unknown"))[:160]
            logger.error("Opposite signal EXIT failed %s: %s", sym, err)
            await self.notifier.send(
                f"⚠️ Не удалось закрыть {sym} по обратному сигналу: {err}"
            )

    async def _open_position_blocks_entry(self, sig: UnifiedSignal) -> bool:
        """True = вход запрещён (позиция открыта). Обратный сигнал закрывает, повторный — тихий пропуск."""
        sym = sig.symbol.upper()
        try:
            if not await self.exchange.has_open_position(sym):
                return False
            rows = await self.exchange.get_positions(sym)
            pos_row = None
            for row in rows:
                if str(row.get("symbol", "")).upper() == sym and self._position_size(row) > 0:
                    pos_row = row
                    break
            if pos_row:
                await self._handle_signal_with_open_position(sig, pos_row)
            return True
        except Exception as exc:
            logger.warning("open_position_blocks_entry(%s) failed: %s", sym, exc, exc_info=True)
        return False

    async def _skip_if_position_open(
        self, sig: UnifiedSignal, ledger_id: Optional[str] = None, *, notify_telegram: bool = False
    ) -> bool:
        """Устаревший путь — используйте _open_position_blocks_entry."""
        return await self._open_position_blocks_entry(sig)

    def _supervisor_can_enter(
        self, sig: UnifiedSignal, *, atr_pct_frac: float = 0.0
    ) -> Tuple[bool, str]:
        # Hermes bypass отключён — только обычный supervisor
        return self.supervisor.can_enter(sig.symbol.upper())

    def _build_soft_score_context(
        self, sig: UnifiedSignal, *, atr_pct_frac: float = 0.0
    ) -> Dict[str, Any]:
        tz = int(self.cfg.get("timezone_offset", 3) or 3)
        ctx: Dict[str, Any] = {
            "local_hour": (datetime.now(timezone.utc).hour + tz) % 24,
            "side": str(sig.side or "").upper(),
        }
        if atr_pct_frac > 0:
            ctx["atr_pct"] = round(atr_pct_frac * 100.0, 4)
        raw = sig.raw if isinstance(sig.raw, dict) else {}
        for key in (
            "htf_trend",
            "regime",
            "adx",
            "atr_pct",
            "rsi",
            "normalized_imbalance",
            "spread_pct",
            "volume_24h_usdt",
        ):
            if key in raw:
                ctx[key] = raw[key]
        return ctx

    def _check_entry_soft_gate(
        self, sig: UnifiedSignal, *, atr_pct_frac: float = 0.0
    ) -> Tuple[bool, str]:
        rwl = self.cfg.get("rule_weight_learning", {})
        if not isinstance(rwl, dict):
            return True, ""
        min_score = float(rwl.get("min_score_to_enter", 0) or 0)
        if min_score <= 0:
            return True, ""
        ctx = self._build_soft_score_context(sig, atr_pct_frac=atr_pct_frac)
        soft = compute_soft_score(
            ctx,
            side=sig.side,
            cfg=self.cfg,
            rule_weights=self.rule_weight_tracker.get_weights(),
        )
        if soft.score + 1e-9 < min_score:
            return (
                False,
                f"soft_score {soft.score:.1f} < {min_score:.1f} (label={soft.label})",
            )
        return True, ""

    def _signal_trade_priority(self, sig: UnifiedSignal) -> float:
        """Приоритет сделки: soft score + confidence (validated правила усиливают soft score)."""
        ctx = self._build_soft_score_context(sig)
        soft = compute_soft_score(
            ctx,
            side=sig.side,
            cfg=self.cfg,
            rule_weights=self.rule_weight_tracker.get_weights(),
        )
        return float(soft.score) + float(sig.confidence or 0) * 20.0

    async def _light_snapshot_for_signal(self, sig: UnifiedSignal) -> Dict[str, Any]:
        sym = sig.symbol.upper()
        cached = self._light_snapshot_cache.get(sym)
        if cached is not None:
            return dict(cached)
        snap = await build_light_signal_snapshot(
            exchange=self.exchange,
            cfg=self.cfg,
            symbol=sym,
            side=sig.side,
            entry=float(sig.entry or 0),
            sig_raw=sig.raw if isinstance(sig.raw, dict) else None,
        )
        self._light_snapshot_cache[sym] = snap
        return snap

    async def _record_ledger_signal(
        self,
        sig: UnifiedSignal,
        *,
        status: SignalStatus,
        reason: str = "",
        entry_id: Optional[str] = None,
    ) -> LedgerEntry:
        snapshot = await self._light_snapshot_for_signal(sig)
        return self.ledger.record(
            symbol=sig.symbol,
            side=sig.side,
            confidence=sig.confidence,
            source=sig.source,
            status=status,
            reason=reason or sig.reason,
            entry=sig.entry,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            raw=sig.raw,
            snapshot=snapshot,
            entry_id=entry_id,
        )

    def _is_silent_skip(self, reason: str) -> bool:
        r = reason or ""
        return any(r.startswith(p) or p in r for p in self._silent_skip_prefixes)

    async def build_status_table(
        self, *, positions: Optional[List[Dict]] = None, block_reason: str = ""
    ) -> str:
        positions = positions if positions is not None else await self.exchange.get_positions()
        balance = await self.exchange.get_balance()
        available = await self.exchange.get_available_balance()
        mode = "TESTNET" if self.exchange.is_testnet else "LIVE"
        return format_status_table(
            balance=balance,
            available=available,
            positions=positions,
            watch_symbols=self.symbols,
            risk_snapshot=self.risk.snapshot(),
            block_reason=block_reason,
            mode=mode,
            trailing_enabled=self.position_steward.enabled,
        )

    async def _notify_risk_block_once(self, block_reason: str, positions: List[Dict]) -> None:
        if self._block_notify_sent:
            return
        self._block_notify_sent = True
        table = await self.build_status_table(positions=positions, block_reason=block_reason)
        await self.notifier.send(table)

    async def _monitor_positions(self, positions: List[Dict]) -> None:
        for p in positions:
            sym = p.get("symbol", "")
            upnl = float(p.get("unrealisedPnl", 0) or 0)
            size = float(p.get("size", 0) or 0)
            side = p.get("side", "")
            prev = self._last_upnl.get(sym, 0.0)
            if abs(upnl - prev) >= 15:
                await self.notifier.position_update(sym, side, upnl, size)
            self._last_upnl[sym] = upnl

    async def _cycle(self) -> None:
        self._light_snapshot_cache.clear()
        self.exchange.api_journal.begin_cycle(self._cycle_num + 1)
        await self._refresh_symbols_if_due()
        positions = await self.exchange.get_positions()
        await self._sync_orderbook_ws(positions)
        self.risk.open_positions_count = len(positions)
        await self._monitor_positions(positions)
        self.trade_lifecycle.update_mark_prices(
            positions,
            self.position_steward._tracked,
            bot_symbols=self.position_steward._bot_symbols,
        )
        closed_24h = await self.monitor.fetch_closed_pnl(self.exchange, hours=24)
        await self._sync_closed_pnl_to_risk()
        trail_notes = await self.position_steward.manage(self.exchange, positions)
        companion_notes = await self.trade_companion.manage_cycle(
            self.exchange, positions, self.position_steward
        )
        await self.trade_lifecycle.maybe_sample(
            self.exchange,
            positions,
            self.position_steward._tracked,
            bot_symbols=self.position_steward._bot_symbols,
        )
        for note in trail_notes + companion_notes:
            if note.startswith("📌") or note.startswith("⚠️") or note.startswith("🤖") or note.startswith("🎯") or note.startswith("🔒") or note.startswith("🛡"):
                await self.notifier.send(note)
        balance_now = await self.exchange.get_balance()
        self.risk.update_balance_reference(balance_now)
        self.risk.reconcile_from_closed_rows(closed_24h, balance=balance_now)
        self._cycle_num += 1
        self.strategy_router.refresh()
        risk_snap = self.risk.snapshot()
        recent = load_closed_trades(self.trade_journal.path, hours=24)
        recent_sum = summarize_trades(recent)
        await self.supervisor.run_cycle_tick(
            self.exchange,
            self.position_steward._bot_symbols,
            cycle_num=self._cycle_num,
            day_pnl_usdt=float(risk_snap.get("pnl_today_usdt", 0)),
            consecutive_losses=int(risk_snap.get("consecutive_losses", 0)),
            recent_wr_pct=float(recent_sum.get("winrate", 0)),
            recent_trades=int(recent_sum.get("n", 0)),
        )
        await self.supervisor.run_skipped_backtests_if_due(self.ledger, self.exchange)
        self.rule_weight_tracker.refresh_if_due(self.trade_journal.path, force=False)

        open_by_sym = self._positions_by_symbol(positions)
        can_trade, block_reason = self.risk.can_trade()
        if can_trade:
            self._block_notify_sent = False
        else:
            await self._notify_risk_block_once(block_reason, positions)

        all_signals = await self.signals.collect_all(self.exchange, self.symbols)
        if all_signals:
            all_signals = sorted(
                all_signals,
                key=lambda s: self._signal_trade_priority(s),
                reverse=True,
            )
            strong = sum(1 for s in all_signals if passes_emit_gate(s, self.cfg))
            logger.info(
                "Cycle: %d signal(s) (conf>=%.0f%%: %d), open=%d, scan=%d symbols, trade_ok=%s",
                len(all_signals),
                self._min_analysis_conf * 100,
                strong,
                len(positions),
                len(self.symbols),
                can_trade,
            )
        for sig in all_signals:
            sym = sig.symbol.upper()
            if not passes_emit_gate(sig, self.cfg):
                continue
            if self._signal_cooldown.is_on_cooldown(sym, sig.side):
                continue
            if sym in open_by_sym:
                await self._handle_signal_with_open_position(sig, open_by_sym[sym])
                continue
            meta_ok, meta_reason = self._supervisor_can_enter(sig)
            if not meta_ok:
                await self._record_ledger_signal(
                    sig,
                    status=SignalStatus.SKIPPED,
                    reason=meta_reason,
                )
                logger.info("Skip %s %s: %s", sig.symbol, sig.side, meta_reason)
                continue
            if not can_trade:
                await self._record_ledger_signal(
                    sig,
                    status=SignalStatus.SKIPPED,
                    reason=block_reason,
                )
                continue
            entry = await self._record_ledger_signal(
                sig,
                status=SignalStatus.RECEIVED,
            )
            self._signal_cooldown.mark_handled(sym, sig.side)
            plan_entry, plan_sl, plan_tp, plan_block, zone_meta = await self._plan_order_levels(sig)
            if plan_block:
                self.ledger.update_status(entry.id, SignalStatus.SKIPPED, plan_block)
                self.supervisor.note_signal_outcome(entry.id, "skipped", plan_block)
                logger.info("Skip %s %s: %s", sig.symbol, sig.side, plan_block)
                if not self._is_silent_skip(plan_block):
                    await self.notifier.signal_skipped(sig.symbol, sig.side, plan_block)
                continue
            self.supervisor.register_virtual_signal(
                symbol=sig.symbol,
                side=sig.side,
                entry=plan_entry,
                stop_loss=plan_sl,
                take_profit=plan_tp,
                source=sig.source,
                confidence=sig.confidence,
                ledger_id=entry.id,
            )
            await self._maybe_execute(
                sig, entry.id, plan_entry, plan_sl, plan_tp, zone_meta=zone_meta
            )

        now = datetime.now(timezone.utc).timestamp()
        if now - self._last_report_at >= self.report_interval_sec:
            await self._bi_hourly_report(positions)
            self._last_report_at = now
        if now - self._last_global_at >= self.global_interval_sec:
            await self._global_analysis()
            self._last_global_at = now
        if self.improver.flush_reload():
            logger.info("Config reloaded from disk (end of cycle)")

        emit_count = sum(1 for s in all_signals if passes_emit_gate(s, self.cfg))
        if len(positions) > 0 or emit_count > 0:
            self._last_market_activity_at = now
        new_loop = compute_loop_interval_sec(
            self.cfg,
            open_positions=len(positions),
            signals_this_cycle=emit_count,
            seconds_since_activity=max(0.0, now - self._last_market_activity_at),
        )
        if abs(new_loop - self._loop_sec) >= 5:
            logger.info(
                "Adaptive loop: %.0fs → %.0fs (open=%d signals=%d)",
                self._loop_sec,
                new_loop,
                len(positions),
                emit_count,
            )
        self._loop_sec = new_loop
        self._last_api_cycle_snap = self.exchange.api_journal.end_cycle()

    def apply_risk_preset(self, name: str) -> str:
        if name not in ALLOWED_PRESETS:
            return f"Неизвестный пресет: {name}"
        path = Path(self.cfg.get("_config_path", self.root / "config.yaml"))
        try:
            changes, backup = apply_risk_preset(path, self.cfg, name)
            self.reload_config()
            lines = ", ".join(changes[:6]) if changes else "без изменений"
            labels = {
                "conservative": "Консервативный",
                "normal": "Нормальный",
                "aggressive": "Агрессивный",
            }
            return (
                f"Пресет: <b>{labels.get(name, name)}</b>\n"
                f"Изменено: {lines}\n"
                f"Резервная копия: {Path(backup).name}"
            )
        except ValueError as exc:
            return f"⚠️ {exc}"

    async def _maybe_execute(
        self,
        sig: UnifiedSignal,
        ledger_id: str,
        plan_entry: float,
        plan_sl: float,
        plan_tp: float,
        *,
        zone_meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if await self._open_position_blocks_entry(sig):
            self.supervisor.note_signal_outcome(ledger_id, "skipped", "position_open")
            return
        ok, reason = self.risk.can_trade(sig.symbol)
        if not ok:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", reason)
            if not self._is_silent_skip(reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, reason)
            return
        if not self.exchange.uses_prd_client:
            self.ledger.update_status(ledger_id, SignalStatus.REJECTED, "нет BybitClient")
            self.supervisor.note_signal_outcome(ledger_id, "rejected", "no client")
            await self.notifier.order_failed(sig.symbol, "BybitClient недоступен")
            return

        rtc = load_runtime_controls(self.root)
        if bool(rtc.get("pause_all_execution", False)):
            reason = "runtime: пауза всех входов (Telegram)"
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", reason)
            return

        cb = self.exchange.api_circuit_snapshot()
        if cb.get("open"):
            reason = (
                f"Bybit circuit open — пауза API ~{cb.get('retry_in_sec', 0):.0f} сек"
            )
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", reason)
            return

        # Soft advisory: совпадение с wallet_flow (не блокирует вход)
        if self.wallet_tracker.enabled:
            wf_rec = self.wallet_tracker.recommendation_for_symbol(sig.symbol)
            if wf_rec is not None:
                logger.info(
                    "Wallet tracker soft match %s signal=%s bias=%s conf=%.2f (advisory only)",
                    sig.symbol,
                    sig.side,
                    wf_rec.bias,
                    wf_rec.confidence,
                )

        entry, sl, tp = plan_entry, plan_sl, plan_tp
        zone_meta = zone_meta or {}
        pipeline_size_mult = 1.0

        klines_entry = await self.exchange.get_klines(
            sig.symbol, interval=self._sr_interval, limit=self._sr_limit
        )
        atr_v = 0.0
        if klines_entry and len(klines_entry) >= 3:
            from prd_agent.entry.entry_engine_bridge import atr_from_klines

            atr_v = atr_from_klines(klines_entry)
        pb_ok, pb_reason = True, ""
        if should_apply_zone_entry(sig, self.cfg):
            ir_ok, ir_reason = self.retest_watch.evaluate(
                sig.symbol,
                sig.side,
                klines_entry or [],
                atr_value=atr_v,
                confidence=float(sig.confidence),
            )
            if not ir_ok:
                self._maybe_register_retest_watch(sig, ir_reason, zone_meta)
                logger.info("Skip %s %s: %s", sig.symbol, sig.side, ir_reason)
                self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, ir_reason)
                self.supervisor.note_signal_outcome(ledger_id, "skipped", ir_reason)
                if not self._is_silent_skip(ir_reason):
                    await self.notifier.signal_skipped(sig.symbol, sig.side, ir_reason)
                return
            ze = self.cfg.get("zone_entry", {}) if isinstance(self.cfg.get("zone_entry"), dict) else {}
            if bool(ze.get("skip_pullback_when_retest_ok", True)):
                pb_reason = ir_reason
            else:
                pb_ok, pb_reason = check_pullback_entry(sig, klines_entry or [], self.cfg)
        else:
            pb_ok, pb_reason = check_pullback_entry(sig, klines_entry or [], self.cfg)
        if is_pump_dump_signal(sig) and pb_ok and "pump_dump" in pb_reason:
            logger.info(
                "Pump/dump %s %s: вход без отката (быстрый режим)",
                sig.symbol,
                sig.side,
            )
        if is_agent_world_signal(sig) and pb_ok and "agent_world" in pb_reason:
            logger.info(
                "AGENT-WORLD %s %s: вход по новости без отката",
                sig.symbol,
                sig.side,
            )
        if not pb_ok:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, pb_reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, pb_reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", pb_reason)
            if not self._is_silent_skip(pb_reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, pb_reason)
            return

        exec_plan = await build_entry_execution_plan(
            sig, plan_entry=entry, exchange=self.exchange, cfg=self.cfg
        )
        if not exec_plan.allowed:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, exec_plan.reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, exec_plan.reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", exec_plan.reason)
            if not self._is_silent_skip(exec_plan.reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, exec_plan.reason)
            return

        order_type = exec_plan.order_type
        limit_price = exec_plan.limit_price if order_type == "Limit" else None
        if order_type == "Limit" and limit_price and limit_price > 0:
            eff_entry = float(limit_price)
        elif exec_plan.market_price > 0:
            eff_entry = float(exec_plan.market_price)
        else:
            eff_entry = entry

        min_rr = self.quality_gate._min_rr_for_signal(sig)
        effective_rr = self._effective_min_rr(min_rr)
        sl, tp, rr_ok = enforce_min_rr_levels(
            side=sig.side,
            entry=eff_entry,
            stop_loss=sl,
            take_profit=tp,
            min_rr=effective_rr,
        )
        if not rr_ok:
            reason = (
                f"RR {rr_ratio(eff_entry, sl, tp, sig.side):.2f} < {effective_rr:.2f} "
                f"после цены входа {eff_entry:.6g}"
            )
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", reason)
            if not self._is_silent_skip(reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, reason)
            return


        atr_pct_frac_pre = atr_v / eff_entry if eff_entry > 0 and atr_v > 0 else 0.0
        meta_ok, meta_reason = self._supervisor_can_enter(sig, atr_pct_frac=atr_pct_frac_pre)
        if not meta_ok:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, meta_reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, meta_reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", meta_reason)
            if not self._is_silent_skip(meta_reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, meta_reason)
            return
        raw = sig.raw if isinstance(sig.raw, dict) else {}
        market_regime = str(raw.get("regime", "chop") or "chop").lower()

        dg_ok, dg_reason = await self.derivatives_guard.check(
            self.exchange, sig.symbol, sig.side
        )
        if not dg_ok:
            reason = dg_reason or "derivatives_entry_guard"
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", reason)
            if not self._is_silent_skip(reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, reason)
            return

        raw_for_corridor = sig.raw if isinstance(sig.raw, dict) else {}
        try:
            corridor_score = float(
                raw_for_corridor.get("score", getattr(sig, "confidence", 0) or 0) or 0
            )
        except (TypeError, ValueError):
            corridor_score = 0.0
        # confidence often 0..1 for non-SPIKE; SPIKE scanner uses 0..100-ish
        if 0 < corridor_score <= 1.0 and str(sig.source or "").upper().find("SPIKE") >= 0:
            corridor_score *= 100.0
        try:
            corridor_move = float(
                raw_for_corridor.get(
                    "range_pct",
                    raw_for_corridor.get("move_pct", raw_for_corridor.get("move", 0)),
                )
                or 0
            )
        except (TypeError, ValueError):
            corridor_move = 0.0
        corridor = evaluate_zone_corridor_play(
            side=sig.side,
            price=float(eff_entry or 0),
            klines=klines_entry or [],
            cfg=self.cfg,
            source=str(sig.source or ""),
            has_bos=bool(zone_meta.get("has_bos")),
            atr=float(atr_v or 0),
            score=corridor_score,
            move_pct=corridor_move,
        )
        if zone_corridor_enabled(self.cfg) and corridor.reason:
            logger.info(
                "Zone corridor %s %s: play=%s allowed=%s %s",
                sig.symbol,
                sig.side,
                corridor.play,
                corridor.allowed,
                corridor.reason,
            )
        if not corridor.allowed:
            reason = corridor.reason or "zone_corridor: block"
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", reason)
            if not self._is_silent_skip(reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, reason)
            return

        pipe = evaluate_entry_pipeline(
            sig,
            self.cfg,
            entry=eff_entry,
            sl=sl,
            tp=tp,
            has_zone=bool(
                zone_meta.get("has_bos")
                or str(zone_meta.get("entry_zone", "")).lower() not in ("", "no_zone")
                or corridor.play in ("bounce", "breakout")
            ),
            has_bos=bool(zone_meta.get("has_bos") or corridor.play == "breakout"),
            supervisor_ok=meta_ok,
            atr_pct=(atr_v / eff_entry if eff_entry > 0 and atr_v > 0 else 0.0),
            market_regime=market_regime,
            zone_play=corridor.play if corridor.play in ("bounce", "breakout") else "",
            zone_play_bonus=float(corridor.score_bonus or 0.0),
        )
        if not pipe.passed:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, pipe.reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, pipe.reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", pipe.reason)
            if not self._is_silent_skip(pipe.reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, pipe.reason)
            return
        pipeline_size_mult = float(pipe.size_mult or 1.0)
        if pipe.reason:
            logger.info("Entry pipeline %s %s: %s", sig.symbol, sig.side, pipe.reason)

        atr_pct_frac = atr_v / eff_entry if eff_entry > 0 and atr_v > 0 else 0.0
        soft_ok, soft_reason = self._check_entry_soft_gate(sig, atr_pct_frac=atr_pct_frac)
        if not soft_ok:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, soft_reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, soft_reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", soft_reason)
            if not self._is_silent_skip(soft_reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, soft_reason)
            return

        q_ok, q_reason = await self.quality_gate.check(
            sig, self.exchange, entry=eff_entry, sl=sl, tp=tp
        )
        if not q_ok:
            logger.info(
                "Skip %s %s: %s (entry_plan=%.6g entry_eff=%.6g sl=%.6g tp=%.6g)",
                sig.symbol,
                sig.side,
                q_reason,
                entry,
                eff_entry,
                sl,
                tp,
            )
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, q_reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", q_reason)
            if not self._is_silent_skip(q_reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, q_reason)
            return

        await self.notifier.signal_received(
            sig.symbol,
            sig.side,
            sig.confidence,
            sig.source,
            f"{sig.reason} | {exec_plan.reason}"[:400],
            raw=sig.raw,
        )

        lev_advice = self.supervisor.recommend_leverage(
            sig, entry=entry, stop_loss=sl, take_profit=tp
        )
        leverage_requested = lev_advice.leverage
        lev_apply = await self.exchange.apply_trade_leverage(
            sig.symbol, leverage_requested
        )
        leverage = max(1, int(lev_apply.applied or lev_apply.target or leverage_requested))
        if lev_apply.mismatch or leverage_requested != leverage:
            logger.warning(
                "Leverage %s %s: supervisor=%dx exchange=%dx (max_inst=%dx) %s",
                sig.symbol,
                sig.side,
                leverage_requested,
                leverage,
                lev_apply.max_instrument,
                lev_apply.error or "",
            )
        else:
            logger.info(
                "Leverage %s %s: %dx on exchange — %s",
                sig.symbol,
                sig.side,
                leverage,
                lev_advice.reason,
            )
        balance = await self.exchange.get_balance()
        available = await self.exchange.get_available_balance()
        eff_risk = self.supervisor.effective_risk_pct(self.risk_pct)
        qty = self.risk.calculate_position_size(balance, eff_risk, eff_entry, sl, leverage)
        if qty <= 0:
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, "qty=0")
            return

        entry_context: Dict[str, Any] = {}
        entry_candles: List[Dict[str, Any]] = []
        soft_size_mult = 1.0
        try:
            entry_context, entry_candles = await build_entry_snapshot(
                exchange=self.exchange,
                cfg=self.cfg,
                symbol=sig.symbol,
                side=sig.side,
                entry=eff_entry,
                stop_loss=float(sl or 0),
                take_profit=float(tp or 0),
                sig=sig,
                klines=klines_entry or [],
                extra_filters={
                    "pullback_reason": pb_reason,
                    "exec_order_type": order_type,
                    "exec_plan_reason": exec_plan.reason,
                    "min_rr_gate": min_rr,
                    "leverage_requested": leverage_requested,
                    "leverage_applied": leverage,
                },
            )
            soft = compute_soft_score(
                entry_context,
                side=sig.side,
                cfg=self.cfg,
                rule_weights=self.rule_weight_tracker.get_weights(),
            )
            entry_context["soft_score"] = soft.score
            entry_context["soft_label"] = soft.label
            entry_context["active_rules"] = soft.active_rules
            entry_context["rule_breakdown"] = soft.breakdown
            entry_context["validated_rule_weights"] = {
                k: self.rule_weight_tracker.get_weights().get(k, 1.0)
                for k in soft.active_rules
            }
            soft_size_mult = float(soft.size_mult or 1.0) * pipeline_size_mult
            logger.info(
                "Soft score %s %s: %.0f (%s) rules=%s size_mult=%.3f validated=%s",
                sig.symbol,
                sig.side,
                soft.score,
                soft.label,
                ",".join(soft.active_rules[:6]) or "-",
                soft_size_mult,
                ",".join(self.rule_weight_tracker.get_validated_rules()) or "-",
            )
        except Exception as exc:
            logger.warning("entry_snapshot/soft_score failed %s: %s", sig.symbol, exc)

        # Учитывать и boost (>1), и caution/weak cut (<1). Раньше cut игнорировался.
        if abs(soft_size_mult - 1.0) > 1e-9:
            qty = qty * soft_size_mult

        try:
            vol_reg = await evaluate_volatility_regime_sizing(
                exchange=self.exchange,
                symbol=sig.symbol,
                cfg=self.cfg,
                side=sig.side,
                source=str(getattr(sig, "source", "") or ""),
                klines=klines_entry or [],
            )
            if vol_reg.block_entry:
                reason = f"volatility_regime_storm: {vol_reg.reason}"
                logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
                self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
                return
            if vol_reg.apply and vol_reg.size_mult > 0:
                qty = qty * float(vol_reg.size_mult)
        except Exception as exc:
            logger.warning("volatility_regime_sizing failed %s: %s", sig.symbol, exc)

        notional = (qty * entry) / max(leverage, 1)
        min_margin = notional * 1.05
        if available < min_margin:
            reason = (
                f"недостаточно свободной маржи: доступно {available:.2f} USDT, "
                f"нужно ~{min_margin:.2f} (плечо {leverage}x, retCode=110007)"
            )
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            return

        qty, sl, tp, prep_err = await prepare_order(
            self.exchange._client,
            symbol=sig.symbol,
            leverage=leverage,
            qty=qty,
            stop_loss=sl,
            take_profit=tp,
            limit_price=limit_price,
        )
        if prep_err:
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, prep_err)
            if not self._is_silent_skip(prep_err):
                await self.notifier.signal_skipped(sig.symbol, sig.side, prep_err)
            return

        sl_f = float(sl or 0)
        tp_f = float(tp or 0)
        effective_rr = self._effective_min_rr(min_rr)
        sl_f, tp_f, rr_ok = enforce_min_rr_levels(
            side=sig.side,
            entry=eff_entry,
            stop_loss=sl_f,
            take_profit=tp_f,
            min_rr=effective_rr,
        )
        if not rr_ok:
            reason = (
                f"RR {rr_ratio(eff_entry, sl_f, tp_f, sig.side):.2f} < {effective_rr:.2f} "
                "после округления SL/TP"
            )
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", reason)
            if not self._is_silent_skip(reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, reason)
            return
        sl, tp = sl_f, tp_f

        q_ok2, q_reason2 = await self.quality_gate.check(
            sig, self.exchange, entry=eff_entry, sl=float(sl or 0), tp=float(tp or 0)
        )
        if not q_ok2:
            logger.info(
                "Skip %s %s: %s после округления SL/TP (eff_entry=%.6g sl=%.6g tp=%.6g)",
                sig.symbol,
                sig.side,
                q_reason2,
                eff_entry,
                sl,
                tp,
            )
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, q_reason2)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", q_reason2)
            if not self._is_silent_skip(q_reason2):
                await self.notifier.signal_skipped(sig.symbol, sig.side, q_reason2)
            return

        if is_signal_only_active(self.cfg, self.root):
            reason = (
                f"signal_only: {sig.symbol} {sig.side} entry≈{eff_entry:.6g} "
                f"sl={float(sl or 0):.6g} tp={float(tp or 0):.6g} lev={leverage}x "
                f"size_mult={pipeline_size_mult:.2f}"
            )
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", "signal_only")
            await self.notifier.signal_only_preview(
                sig.symbol,
                sig.side,
                sig.confidence,
                eff_entry,
                float(sl or 0),
                float(tp or 0),
                leverage,
                reason,
            )
            return

        result = await self.exchange.place_order(
            symbol=sig.symbol,
            side=sig.side,
            qty=qty,
            stop_loss=sl,
            take_profit=tp,
            order_type=order_type,
            price=limit_price,
        )
        if result.get("success"):
            oid = str(result.get("orderId", ""))
            exchange_lev = await self.exchange.get_symbol_leverage(sig.symbol)
            if exchange_lev > 0:
                leverage = exchange_lev
            logger.info(
                "Order OK %s %s qty=%.6f lev=%dx (req %dx) conf=%.0f%% id=%s",
                sig.symbol,
                sig.side,
                float(qty or 0),
                leverage,
                leverage_requested,
                sig.confidence * 100,
                oid,
            )
            self.ledger.update_status(ledger_id, SignalStatus.EXECUTED, "ok", order_id=oid)
            self.monitor.record_execution(sig.symbol, sig.side, qty, sig.source, oid)
            self.trade_journal.log_entered(
                symbol=sig.symbol,
                side=sig.side,
                source=sig.source,
                qty=qty,
                entry=eff_entry,
                order_id=oid,
                stop_loss=sl,
                take_profit=tp,
                confidence=sig.confidence,
                leverage=leverage,
                origin="bot",
                entry_context=entry_context,
                entry_candles=entry_candles,
                ledger_id=ledger_id,
            )
            self.position_steward.mark_bot_opened(
                sig.symbol,
                take_profit=tp,
                stop_loss=sl,
                pump_dump=is_pump_dump_signal(sig),
            )
            self.supervisor.note_signal_outcome(ledger_id, "executed", oid)
            await self.notifier.order_placed(
                sig.symbol,
                sig.side,
                qty,
                oid,
                leverage=leverage,
                leverage_requested=leverage_requested,
                advisor_reason=f"{order_type} | {lev_advice.reason}",
            )
        else:
            err = str(result.get("error", "unknown"))
            logger.warning("Order FAIL %s %s: %s", sig.symbol, sig.side, err)
            if "110007" in err or "not enough" in err.lower():
                self.ledger.update_status(
                    ledger_id,
                    SignalStatus.SKIPPED,
                    "недостаточно маржи — позиция уже занята или баланс в сделках",
                )
                return
            self.ledger.update_status(ledger_id, SignalStatus.REJECTED, err)
            await self.notifier.order_failed(sig.symbol, err)

    async def _bi_hourly_report(self, positions: List[Dict]) -> None:
        min_c = self._min_analysis_conf
        signals_2h = filter_signal_dicts(self.signals.recent_signals(hours=2), self.cfg)
        signals_24h = filter_signal_dicts(self.signals.recent_signals(hours=24), self.cfg)
        high_conf = self._dedupe_signals_for_report(signals_2h)
        report_2h = await self.monitor.period_report(
            self.exchange, signals_2h, 2, self.reporter.high_conf
        )
        report_24h = await self.monitor.period_report(
            self.exchange, signals_24h, 24, self.reporter.high_conf
        )
        ledger_sum = self.ledger.summary(24)
        report_2h["ledger_not_opened"] = ledger_sum.get("not_opened", 0)
        code_changes = self.improver.recent_changes(hours=2)
        proposals = self.improver.propose_from_performance(report_2h, report_24h)
        hints = self.global_analyzer.improvement_hints(ledger_sum, report_24h.get("win_rate_pct", 50))
        applied_main = self.improver.process_proposals(proposals + hints)
        sup_summary = await self.supervisor.run_bi_hourly_review(
            ledger=self.ledger,
            report_2h=report_2h,
            report_24h=report_24h,
        )
        code_changes = self.improver.recent_changes(hours=2)
        for ch in applied_main:
            await self.notifier.config_change(ch.get("summary", ""), ch.get("justification", ""))
        balance = await self.exchange.get_balance()
        closed_today = await self.monitor.fetch_closed_pnl(self.exchange, hours=24)
        self.risk.update_balance_reference(balance)
        self.risk.reconcile_from_closed_rows(closed_today, balance=balance)
        risk_snap = self.risk.snapshot()
        skip_report = skip_baseline_report(self.ledger, hours=2)
        logger.info(format_skip_baseline_text(skip_report))
        await self.reporter.publish_full_report(
            positions=positions,
            report_2h=report_2h,
            report_24h=report_24h,
            high_conf_signals=high_conf,
            code_changes=code_changes,
            risk_snapshot=risk_snap,
            balance=balance,
            exchange_pnl_today_usdt=risk_snap.get("pnl_today_usdt", 0),
            exchange_pnl_today_pct=risk_snap.get("pnl_today_pct", 0),
            supervisor_summary=sup_summary,
            trade_journal_path=self.trade_journal.path,
            api_stats_snapshot=self._last_api_cycle_snap or self.exchange.api_journal.snapshot(),
            skip_baseline=skip_report,
            active_strategy=self.strategy_router.profile.name,
        )

    async def _global_analysis(self) -> None:
        if not self.cfg.get("global_analysis", {}).get("enabled", True):
            return
        signals_24h = filter_signal_dicts(self.signals.recent_signals(hours=24), self.cfg)
        text = await self.global_analyzer.build_report(self.exchange, signals_24h, hours=24)
        await self.notifier.global_report(text)

    def get_trade_stats_report(self, hours: Optional[float] = None) -> str:
        h = float(hours if hours is not None else self._stats_hours)
        audit = self.position_steward._telegram_audit_path()
        return build_trade_stats_report(
            self.trade_journal.path,
            h,
            data_dir=self.root / "data",
            telegram_audit_path=audit if audit.exists() else None,
        )

    def get_portfolio_quality_report(self, hours: Optional[float] = None) -> str:
        h = float(hours if hours is not None else self._portfolio_quality_hours)
        return build_portfolio_quality_report(self.trade_journal.path, h)

    def get_daily_pnl_report(self, days: Optional[int] = None) -> str:
        d = int(days if days is not None else self._daily_pnl_days)
        tz = int(self.cfg.get("timezone_offset", 3))
        return build_daily_pnl_report(
            self.trade_journal.path,
            days=d,
            timezone_offset=tz,
            split_origin=self._daily_pnl_split_origin,
            exclude_manual=self._daily_pnl_exclude_manual,
        )

    def get_skipped_lab_report(self, hours: Optional[float] = None) -> str:
        h = float(hours if hours is not None else self._skipped_lab_hours)
        last = getattr(self.supervisor, "_last_skipped_bt_summary", None) or {}
        return self.supervisor.skipped_bt.build_telegram_report(h, last_run=last)

    def get_hermes_briefing(self) -> str:
        hermes = self.cfg.get("hermes", {}) if isinstance(self.cfg.get("hermes"), dict) else {}
        if not bool(hermes.get("enabled", False)):
            return (
                "<b>📊 Hermes отключён</b>\n\n"
                "Модуль советов Hermes выключен в config (`hermes.enabled: false`).\n"
                "Используйте <b>📅 По дням</b> и <b>🧪 Лаборатория</b>."
            )
        from prd_agent.analysis.hermes_briefing import build_hermes_telegram_briefing

        return build_hermes_telegram_briefing(self.root)

    async def get_liquidation_safety_report(self) -> str:
        from prd_agent.positions.liquidation_guard import (
            LiquidationGuardConfig,
            distance_to_liq_pct,
            protective_level,
        )

        cfg = LiquidationGuardConfig.from_cfg(self.cfg)
        positions = await self.exchange.get_positions()
        lines = [
            "<b>🛡 Защита от ликвидации</b>",
            f"Статус: <code>{'ВКЛ' if cfg.enabled else 'ВЫКЛ'}</code> | "
            f"буфер <code>{cfg.buffer_pct:.2f}%</code> от цены ликв.",
            f"Ручные позиции: <code>{'не трогаем' if cfg.skip_manual else 'под защитой'}</code>",
            "",
        ]
        if not positions:
            lines.append("Открытых позиций нет.")
            return "\n".join(lines)
        for row in positions:
            sym = str(row.get("symbol", "")).upper()
            side = str(row.get("side", ""))
            mark = float(row.get("markPrice") or 0)
            liq = float(row.get("liqPrice") or 0)
            dist = distance_to_liq_pct(side, mark, liq)
            guard = protective_level(liq, side, cfg.buffer_pct)
            origin = (
                "bot"
                if sym in getattr(self.position_steward, "_bot_symbols", set())
                else "manual"
            )
            lines.append(
                f"<b>{sym}</b> {side} ({origin})\n"
                f"  mark={mark:.6g} | liq={liq:.6g} | до ликв. ≈{dist:.2f}%\n"
                f"  ранний выход ≤ <code>{guard:.6g}</code>"
            )
        lines.append(
            "\n<i>Бот закрывает позицию до биржевой ликвидации, если цена "
            "достигает защитного уровня.</i>"
        )
        return "\n".join(lines)

    async def get_macro_briefing(self) -> str:
        positions: List[Dict] = []
        try:
            positions = await self.exchange.get_positions()
        except Exception as exc:
            logger.warning("macro positions: %s", exc)
        return await self.macro_ai.build_briefing(
            positions=positions,
            watch_symbols=self.symbols,
        )

    def ta_cache_age_sec(self) -> float:
        ta = self.signals._ta_vol
        if not ta:
            return 9999.0
        return ta.cache_age_sec()

    async def get_ta_scan_report(
        self, *, prefer_cache: bool = True, force: bool = False
    ) -> str:
        ta = self.signals._ta_vol
        if not ta:
            return (
                "<b>📉 TA-скан</b>\n\nМодуль отключён: "
                "<code>ta_scanner.enabled: false</code>"
            )
        try:
            return await ta.get_telegram_report(
                self.exchange, prefer_cache=prefer_cache, force=force
            )
        except Exception as exc:
            logger.exception("ta_scan: %s", exc)
            return f"<b>📉 TA-скан</b>\n\nОшибка: {exc}"
