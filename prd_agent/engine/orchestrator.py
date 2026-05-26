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
from prd_agent.analysis.trade_analytics import build_report as build_trade_stats_report
from prd_agent.analysis.trade_journal import TradeJournal
from prd_agent.analysis.trade_monitor import TradeMonitor
from prd_agent.config import load_config
from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.exchange.bybit_adapter import BybitAdapter
from prd_agent.exchange.order_prep import prepare_order
from prd_agent.risk.entry_guard import build_entry_execution_plan
from prd_agent.reporting.bi_hourly import BiHourlyReporter
from prd_agent.risk.closed_pnl_dedup import ClosedPnlDedup
from prd_agent.risk.guard import RiskGuard
from prd_agent.risk.quality_gate import QualityGate
from prd_agent.market.symbol_scanner import SymbolScanner
from prd_agent.positions.position_steward import PositionSteward
from prd_agent.positions.sr_sl_tp_adjust import adjust_sl_tp_with_sr_zones
from prd_agent.positions.trend_sl_buffer import TrendSlBufferConfig, apply_trend_sl_buffer
from prd_agent.supervisor.trade_supervisor import TradeSupervisor
from prd_agent.signals.confidence_filter import (
    PerSymbolSignalCooldown,
    filter_signal_dicts,
    load_min_analysis_confidence,
    load_signal_notify_cooldown_sec,
    passes_emit_gate,
)
from prd_agent.signals.router import SignalRouter
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
        self.supervisor = TradeSupervisor(cfg, self.data_dir / "supervisor", self.improver)
        self.notifier = TelegramNotifier(cfg)
        self.global_analyzer = GlobalAnalyzer(cfg, self.ledger, self.monitor)
        self.symbol_scanner = SymbolScanner(cfg)
        self.position_steward = PositionSteward(cfg)
        self.quality_gate = QualityGate(cfg)
        self.macro_ai = MacroAI(cfg)
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
        self._last_report_at = 0.0
        self._last_global_at = 0.0
        self._loop_sec = float(t.get("loop_interval_sec", 60))
        self._closed_pnl_dedup = ClosedPnlDedup(self.data_dir / "risk_seen_closed.json")
        self._last_upnl: Dict[str, float] = {}
        self._notify_loop: Optional[asyncio.AbstractEventLoop] = None
        self._block_notify_sent = False
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
        self._apply_sr_zones_config()

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
        self._trend_sl_cfg = TrendSlBufferConfig.from_cfg(self.cfg)

    async def _apply_trend_sl_buffer(
        self,
        sig: UnifiedSignal,
        entry: float,
        stop_loss: float,
        take_profit: float,
    ) -> tuple[float, float]:
        if not self._trend_sl_cfg.enabled:
            return stop_loss, take_profit
        try:
            klines = await self.exchange.get_klines(
                sig.symbol.upper(),
                interval=self._sr_interval,
                limit=self._sr_limit,
            )
            new_sl, new_tp, changed = apply_trend_sl_buffer(
                entry=entry,
                side=sig.side,
                stop_loss=stop_loss,
                take_profit=take_profit,
                klines=klines,
                cfg=self._trend_sl_cfg,
                signal_raw=sig.raw if isinstance(sig.raw, dict) else None,
                preserve_min_rr=self._sr_preserve_rr,
            )
            if changed:
                logger.info(
                    "Trend SL buffer %s %s: SL %.6g→%.6g TP %.6g→%.6g (стоп дальше по тренду)",
                    sig.symbol,
                    sig.side,
                    stop_loss,
                    new_sl,
                    take_profit,
                    new_tp,
                )
            return new_sl, new_tp
        except Exception as exc:
            logger.warning("Trend SL buffer %s: %s", sig.symbol, exc)
            return stop_loss, take_profit

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
        self._trend_sl_cfg = TrendSlBufferConfig.from_cfg(self.cfg)
        self.leverage = int(t.get("leverage", self.leverage))
        self.risk.max_positions = int(t.get("max_positions", self.risk.max_positions))
        self.supervisor = TradeSupervisor(self.cfg, self.data_dir / "supervisor", self.improver)
        self.notifier._cfg = self.cfg
        self.risk.apply_risk_config(self.cfg)
        logger.info("Config reloaded from disk")

    def reset_risk_guard(self) -> str:
        """Сброс дневного убытка, лимита сделок и AUTO-STOP в памяти."""
        self.risk.reset_daily_state(clear_stop=True)
        self._block_notify_sent = False
        return "Риск сброшен: дневной PnL=0, сделок сегодня=0, стоп снят."

    async def _plan_order_levels(
        self, sig: UnifiedSignal
    ) -> tuple[float, float, float]:
        """Цены входа/SL/TP как для реального ордера (включая SR-зоны)."""
        entry = float(sig.entry or 0) or await self.exchange.get_price(sig.symbol)
        sl = float(sig.stop_loss or 0) or (
            entry * 0.995 if sig.side == "Buy" else entry * 1.005
        )
        tp = float(sig.take_profit or 0) or (
            entry * 1.01 if sig.side == "Buy" else entry * 0.99
        )
        sl, tp = await self._apply_sr_zones_to_levels(sig.symbol, sig.side, entry, sl, tp)
        sl, tp = await self._apply_trend_sl_buffer(sig, entry, sl, tp)
        return entry, sl, tp

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
        self.risk.initial_balance = balance
        await self._refresh_symbols_if_due(force=True)
        mode = "TESTNET" if self.exchange.is_testnet else "LIVE"
        positions = await self.exchange.get_positions()
        table = await self.build_status_table(positions=positions, block_reason="")
        await self.notifier.send(f"🚀 <b>Unified Agent</b> запущен\n\n{table}")
        logger.info("Unified started balance=%.2f testnet=%s", balance, self.exchange.is_testnet)
        while self._running:
            try:
                await self._cycle()
            except Exception as exc:
                logger.exception("Cycle error: %s", exc)
                await self.notifier.send(f"⚠️ Ошибка цикла: {exc}")
            await asyncio.sleep(self._loop_sec)

    def stop(self) -> None:
        self._running = False

    async def close(self) -> None:
        self.stop()
        await self.exchange.close()

    async def _sync_closed_pnl_to_risk(self) -> None:
        """Только новые закрытия с биржи; дедуп на диске — иначе после рестарта убыток «удваивается»."""
        rows = await self.monitor.fetch_closed_pnl(self.exchange, hours=24)
        for r in rows:
            oid = str(r.get("orderId") or r.get("id") or "")
            if not oid or not self._closed_pnl_dedup.is_new(oid):
                continue
            pnl = float(r.get("closedPnl", 0) or 0)
            self.risk.record_trade(pnl)
            self._closed_pnl_dedup.mark(oid)
            origin = "bot" if str(r.get("symbol", "")).upper() in self.position_steward._bot_symbols else "manual"
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
        await self._refresh_symbols_if_due()
        positions = await self.exchange.get_positions()
        self.risk.open_positions_count = len(positions)
        await self._monitor_positions(positions)
        trail_notes = await self.position_steward.manage(self.exchange, positions)
        for note in trail_notes:
            if "time-stop" in note.lower():
                self.supervisor._log_note(note, event="exit_time_stop")
            if note.startswith("📌"):
                await self.notifier.send(note)
        await self._sync_closed_pnl_to_risk()
        await self.supervisor.run_cycle_tick(
            self.exchange, self.position_steward._bot_symbols
        )

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
            plan_entry, plan_sl, plan_tp = await self._plan_order_levels(sig)
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

        q_ok, q_reason = await self.quality_gate.check(
            sig, self.exchange, entry=entry, sl=sl, tp=tp
        )
        if not q_ok:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, q_reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, q_reason)
            self.supervisor.note_signal_outcome(ledger_id, "skipped", q_reason)
            if not self._is_silent_skip(q_reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, q_reason)
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
        leverage = lev_advice.leverage
        logger.info(
            "Supervisor leverage %s %s: %dx — %s",
            sig.symbol,
            sig.side,
            leverage,
            lev_advice.reason,
        )
        balance = await self.exchange.get_balance()
        available = await self.exchange.get_available_balance()
        qty = self.risk.calculate_position_size(balance, self.risk_pct, entry, sl, leverage)
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
            logger.info(
                "Order OK %s %s qty=%.6f lev=%dx conf=%.0f%% id=%s",
                sig.symbol,
                sig.side,
                qty,
                leverage,
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
                entry=entry,
                order_id=oid,
                stop_loss=sl,
                take_profit=tp,
                confidence=sig.confidence,
                leverage=leverage,
            )
            self.position_steward.mark_bot_opened(sig.symbol)
            self.supervisor.note_signal_outcome(ledger_id, "executed", oid)
            await self.notifier.order_placed(
                sig.symbol,
                sig.side,
                qty,
                oid,
                leverage=leverage,
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
        await self.reporter.publish_full_report(
            positions=positions,
            report_2h=report_2h,
            report_24h=report_24h,
            high_conf_signals=high_conf,
            code_changes=code_changes,
            risk_snapshot=self.risk.snapshot(),
            balance=balance,
            supervisor_summary=sup_summary,
        )

    async def _global_analysis(self) -> None:
        if not self.cfg.get("global_analysis", {}).get("enabled", True):
            return
        signals_24h = filter_signal_dicts(self.signals.recent_signals(hours=24), self.cfg)
        text = await self.global_analyzer.build_report(self.exchange, signals_24h, hours=24)
        await self.notifier.global_report(text)

    def get_trade_stats_report(self, hours: Optional[float] = None) -> str:
        h = float(hours if hours is not None else self._stats_hours)
        return build_trade_stats_report(self.trade_journal.path, h)

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
