"""
Главный цикл unified-агента: все источники сигналов, журнал, риск, ордера, сопровождение, анализ.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from prd_agent.analysis.global_analyzer import GlobalAnalyzer
from prd_agent.analysis.macro_ai import MacroAI
from prd_agent.analysis.signal_ledger import SignalLedger, SignalStatus
from prd_agent.analysis.trade_analytics import (
    build_report as build_trade_stats_report,
    load_closed_trades,
    summarize_trades,
)
from prd_agent.analysis.trade_journal import TradeJournal
from prd_agent.analysis.trade_monitor import TradeMonitor
from prd_agent.config import load_config
from prd_agent.config_presets import ALLOWED_PRESETS, apply_risk_preset
from prd_agent.engine.adaptive_loop import compute_loop_interval_sec
from prd_agent.entry.entry_engine_bridge import EntryEngineBridge, should_apply_zone_entry
from prd_agent.entry.entry_pipeline import evaluate_entry_pipeline
from prd_agent.entry.retest_watchlist import RetestWatchlist
from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.exchange.bybit_adapter import BybitAdapter
from prd_agent.exchange.order_prep import prepare_order
from prd_agent.risk.entry_guard import build_entry_execution_plan
from prd_agent.risk.pullback_entry import check_pullback_entry
from prd_agent.signals.pump_dump_mode import is_agent_world_signal, is_pump_dump_signal
from prd_agent.reporting.bi_hourly import BiHourlyReporter
from prd_agent.risk.closed_pnl_dedup import ClosedPnlDedup
from prd_agent.risk.guard import GuardStatus, RiskGuard, StopKind
from prd_agent.risk.quality_gate import QualityGate
from prd_agent.market.market_scanner_bridge import (
    market_scanner_cfg,
    run_market_scan_once,
    unified_should_run_market_scan,
)
from prd_agent.ops.bot_manager import BotManagerAgent
from prd_agent.market.symbol_scanner import SymbolScanner
from prd_agent.positions.bot_position_registry import resolve_closed_origin
from prd_agent.positions.position_steward import PositionSteward
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
from prd_agent.strategies.router import StrategyRouter
from prd_agent.telemetry.skip_baseline import format_skip_baseline_text
from prd_agent.signals.types import UnifiedSignal
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
        self.quality_gate = QualityGate(cfg)
        self.macro_ai = MacroAI(cfg)
        self.bot_manager = BotManagerAgent(cfg)
        an = cfg.get("analytics", {})
        self._stats_hours = float(an.get("report_hours", 24))

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
        self._bot_manager_task: Optional[asyncio.Task] = None
        self._silent_skip_prefixes = (
            "Пауза после стопа",
            "Кулдаун после убытка",
            "Макс. позиций",
            "EMERGENCY",
            "на бирже уже открыта",
            "недостаточно свободной маржи",
            "недостаточно маржи",
            "quality_gate:",
            "entry_pipeline:",
            "retest_watch:",
            "entry_guard:",
            "Bybit circuit",
            "circuit open",
        )
        self.zone_entry = EntryEngineBridge(cfg)
        self.retest_watch = RetestWatchlist(cfg)
        self.strategy_router = StrategyRouter(cfg)
        _ze = cfg.get("zone_entry", {}) if isinstance(cfg.get("zone_entry"), dict) else {}
        self._zone_kline_interval = str(_ze.get("kline_interval", "15"))
        self._apply_sr_zones_config()
        self._last_api_cycle_snap: Dict[str, Any] = {}

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
        self._sr_min_tp_distance_pct = float(sr.get("min_tp_distance_pct", 1.0) or 1.0)

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
        self.quality_gate = QualityGate(self.cfg)
        self.macro_ai = MacroAI(self.cfg)
        an = self.cfg.get("analytics", {})
        self._stats_hours = float(an.get("report_hours", self._stats_hours))
        self.symbols = list(t.get("symbols", self.symbols))
        self.symbol_scanner = SymbolScanner(self.cfg)
        self._symbol_rescan_sec = float(t.get("symbol_rescan_interval_sec", self._symbol_rescan_sec))
        self._min_analysis_conf = load_min_analysis_confidence(self.cfg)
        self.reporter.high_conf = self._min_analysis_conf
        self._signal_cooldown = PerSymbolSignalCooldown(
            load_signal_notify_cooldown_sec(self.cfg)
        )
        self._apply_sr_zones_config()
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
        self.retest_watch = RetestWatchlist(self.cfg)
        self.strategy_router = StrategyRouter(self.cfg)
        ze = self.cfg.get("zone_entry", {}) if isinstance(self.cfg.get("zone_entry"), dict) else {}
        self._zone_kline_interval = str(ze.get("kline_interval", "15"))

    async def _plan_order_levels(
        self, sig: UnifiedSignal
    ) -> tuple[float, float, float, str]:
        """Цены входа/SL/TP: зона/BOS/ретест + SR-зоны для SL/TP."""
        entry = float(sig.entry or 0) or await self.exchange.get_price(sig.symbol)
        sl = float(sig.stop_loss or 0) or (
            entry * 0.995 if sig.side == "Buy" else entry * 1.005
        )
        tp = float(sig.take_profit or 0) or (
            entry * 1.01 if sig.side == "Buy" else entry * 0.99
        )
        block_reason = ""

        profile = self.strategy_router.profile
        zone_interval = (
            self._zone_kline_interval
            if profile.zone_entry_enabled
            else profile.kline_interval
        )
        if should_apply_zone_entry(sig, self.cfg) and profile.zone_entry_enabled:
            klines = await self.exchange.get_klines(
                sig.symbol,
                interval=zone_interval,
                limit=120,
            )
            htf = None
            if profile.require_htf and profile.htf_interval:
                htf = await self.exchange.get_klines(
                    sig.symbol, interval=profile.htf_interval, limit=120
                )
            plan = self.zone_entry.plan_levels(
                sig,
                klines=klines or [],
                htf_klines=htf,
                market_price=entry,
            )
            if not plan.ok:
                block_reason = plan.block_reason or "zone_entry: вход заблокирован"
            else:
                entry = float(plan.entry or entry)
                if plan.stop_loss > 0:
                    sl = float(plan.stop_loss)
                if plan.take_profit > 0:
                    tp = float(plan.take_profit)
                md = plan.metadata if isinstance(plan.metadata, dict) else {}
                if md.get("has_bos") and profile.impulse_retest_enabled:
                    self.retest_watch.register_breakout(
                        sig.symbol,
                        sig.side,
                        bos_level=float(md.get("bos_level", entry) or entry),
                    )

        sl, tp = await self._apply_sr_zones_to_levels(sig.symbol, sig.side, entry, sl, tp)
        return entry, sl, tp, block_reason

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
        if self.bot_manager.enabled:
            self._bot_manager_task = asyncio.create_task(self._bot_manager_loop())
            logger.info(
                "BOT MANAGER: цикл советов, интервал %.0f сек",
                self.bot_manager.interval_sec,
            )
        while self._running:
            try:
                await self._cycle()
            except Exception as exc:
                logger.exception("Cycle error: %s", exc)
                await self.notifier.send(f"⚠️ Ошибка цикла: {exc}")
            await asyncio.sleep(self._loop_sec)

    def stop(self) -> None:
        self._running = False
        if self._market_scan_task is not None:
            self._market_scan_task.cancel()
            self._market_scan_task = None
        if self._bot_manager_task is not None:
            self._bot_manager_task.cancel()
            self._bot_manager_task = None

    async def close(self) -> None:
        self.stop()
        if self._market_scan_task is not None:
            try:
                await self._market_scan_task
            except asyncio.CancelledError:
                pass
            self._market_scan_task = None
        if self._bot_manager_task is not None:
            try:
                await self._bot_manager_task
            except asyncio.CancelledError:
                pass
            self._bot_manager_task = None
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

    async def get_bot_manager_review(self) -> str:
        return await self.bot_manager.run_review(self)

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
            origin = resolve_closed_origin(
                self.root / "data",
                sym,
                order_id=oid,
                journal_path=journal_path,
                telegram_audit_path=audit_path if audit_path.exists() else None,
                bot_symbols=bot_symbols,
            )
            self.trade_journal.record_closed_from_exchange(r, origin=origin)

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

    async def _skip_if_position_open(
        self, sig: UnifiedSignal, ledger_id: Optional[str] = None, *, notify_telegram: bool = False
    ) -> bool:
        """True = пропустить (позиция на бирже уже есть)."""
        sym = sig.symbol.upper()
        try:
            if await self.exchange.has_open_position(sym):
                reason = f"на бирже уже открыта позиция {sym} — новый ордер не отправляем"
                logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
                if ledger_id:
                    self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
                elif notify_telegram is False:
                    self.ledger.record(
                        symbol=sig.symbol,
                        side=sig.side,
                        confidence=sig.confidence,
                        source=sig.source,
                        status=SignalStatus.SKIPPED,
                        reason=reason,
                        entry=sig.entry,
                        stop_loss=sig.stop_loss,
                        take_profit=sig.take_profit,
                        raw=sig.raw,
                    )
                if notify_telegram:
                    await self.notifier.signal_skipped(sig.symbol, sig.side, reason)
                return True
        except Exception as exc:
            logger.warning("has_open_position(%s) failed: %s", sym, exc)
        return False

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
        self._cycle_num += 1
        self.exchange.begin_api_cycle(self._cycle_num)
        self.strategy_router.refresh()
        await self.exchange.refresh_cycle_tickers()
        self.quality_gate.set_tickers_map(self.exchange.get_tickers_map())
        self.retest_watch.prune_expired()

        await self._refresh_symbols_if_due()
        positions = await self.exchange.get_positions()
        self.risk.open_positions_count = len(positions)
        await self._monitor_positions(positions)
        closed_24h = await self.monitor.fetch_closed_pnl(self.exchange, hours=24)
        await self._sync_closed_pnl_to_risk()
        trail_notes = await self.position_steward.manage(self.exchange, positions)
        for note in trail_notes:
            if note.startswith("📌") or note.startswith("⚠️"):
                await self.notifier.send(note)
        balance_now = await self.exchange.get_balance()
        self.risk.update_balance_reference(balance_now)
        self.risk.reconcile_from_closed_rows(closed_24h, balance=balance_now)
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

        open_symbols = self._symbols_with_open_positions(positions)
        can_trade, block_reason = self.risk.can_trade()
        if can_trade:
            self._block_notify_sent = False
        else:
            await self._notify_risk_block_once(block_reason, positions)

        all_signals = await self.signals.collect_all(self.exchange, self.symbols)
        if all_signals:
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
            if sym in open_symbols:
                await self._skip_if_position_open(sig)
                continue
            meta_ok, meta_reason = self.supervisor.can_enter(sym)
            if not meta_ok:
                self.ledger.record(
                    symbol=sig.symbol,
                    side=sig.side,
                    confidence=sig.confidence,
                    source=sig.source,
                    status=SignalStatus.SKIPPED,
                    reason=meta_reason,
                    entry=sig.entry,
                    stop_loss=sig.stop_loss,
                    take_profit=sig.take_profit,
                    raw=sig.raw,
                )
                logger.info("Skip %s %s: %s", sig.symbol, sig.side, meta_reason)
                continue
            if not can_trade:
                self.ledger.record(
                    symbol=sig.symbol,
                    side=sig.side,
                    confidence=sig.confidence,
                    source=sig.source,
                    status=SignalStatus.SKIPPED,
                    reason=block_reason,
                    entry=sig.entry,
                    stop_loss=sig.stop_loss,
                    take_profit=sig.take_profit,
                    raw=sig.raw,
                )
                continue
            entry = self.ledger.record(
                symbol=sig.symbol,
                side=sig.side,
                confidence=sig.confidence,
                source=sig.source,
                status=SignalStatus.RECEIVED,
                reason=sig.reason,
                entry=sig.entry,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                raw=sig.raw,
            )
            self._signal_cooldown.mark_handled(sym, sig.side)
            plan_entry, plan_sl, plan_tp, plan_block = await self._plan_order_levels(sig)
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
            await self._maybe_execute(sig, entry.id, plan_entry, plan_sl, plan_tp)

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
        self._last_api_cycle_snap = self.exchange.end_api_cycle()

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
    ) -> None:
        if await self._skip_if_position_open(sig, ledger_id):
            self.supervisor.note_signal_outcome(ledger_id, "skipped", "position_open")
            return
        meta_ok, meta_reason = self.supervisor.can_enter(sig.symbol)
        if not meta_ok:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, meta_reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, meta_reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", meta_reason)
            if not self._is_silent_skip(meta_reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, meta_reason)
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

        cb = self.exchange.api_circuit_snapshot()
        if cb.get("open"):
            reason = (
                f"Bybit circuit open — пауза API ~{cb.get('retry_in_sec', 0):.0f} сек"
            )
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", reason)
            return

        entry, sl, tp = plan_entry, plan_sl, plan_tp

        klines_entry = await self.exchange.get_klines(
            sig.symbol, interval=self._sr_interval, limit=self._sr_limit
        )
        pb_ok, pb_reason = True, ""
        profile = self.strategy_router.profile
        if should_apply_zone_entry(sig, self.cfg) and profile.impulse_retest_enabled:
            atr_v = 0.0
            if klines_entry and len(klines_entry) >= 3:
                from prd_agent.entry.entry_engine_bridge import atr_from_klines

                atr_v = atr_from_klines(klines_entry)
            ir_ok, ir_reason = self.retest_watch.evaluate(
                sig.symbol,
                sig.side,
                klines_entry or [],
                atr_v,
                confidence=float(sig.confidence),
            )
            if not ir_ok:
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

        meta_ok_sup, _ = self.supervisor.can_enter(sig.symbol)
        raw_meta = sig.raw if isinstance(sig.raw, dict) else {}
        zone_meta = raw_meta.get("zone_entry", {}) if isinstance(raw_meta.get("zone_entry"), dict) else {}
        atr_pct = 0.0
        if klines_entry and len(klines_entry) >= 5:
            from prd_agent.entry.entry_engine_bridge import atr_from_klines

            _atr = atr_from_klines(klines_entry)
            _px = float(klines_entry[-1].get("close", 0) or 0)
            if _px > 0 and _atr > 0:
                atr_pct = _atr / _px
        pipe = evaluate_entry_pipeline(
            sig,
            self.cfg,
            entry=entry,
            sl=sl,
            tp=tp,
            has_zone=bool(zone_meta.get("entry_zone") and zone_meta.get("entry_zone") != "no_zone"),
            has_bos=bool(zone_meta.get("has_bos")),
            supervisor_ok=meta_ok_sup,
            atr_pct=atr_pct,
        )
        if not pipe.passed:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, pipe.reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, pipe.reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", pipe.reason)
            if not self._is_silent_skip(pipe.reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, pipe.reason)
            return
        pipeline_size_mult = float(pipe.size_mult)

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
        eff_risk = self.supervisor.effective_risk_pct(self.risk_pct) * pipeline_size_mult
        qty = self.risk.calculate_position_size(balance, eff_risk, eff_entry, sl, leverage)
        if qty <= 0:
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, "qty=0")
            return

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
        ledger_7d = self.ledger.summary(168)
        report_2h["ledger_not_opened"] = ledger_sum.get("not_opened", 0)
        skip_bl = ledger_7d.get("skip_baseline") or {}
        if skip_bl:
            logger.info(format_skip_baseline_text(skip_bl))
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
            api_stats=self.exchange.api_stats_snapshot(),
            skip_baseline=skip_bl,
            active_strategy=self.strategy_router.profile.label,
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
