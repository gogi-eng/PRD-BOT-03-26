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
from prd_agent.exchange.order_prep import prepare_market_order
from prd_agent.reporting.bi_hourly import BiHourlyReporter
from prd_agent.risk.guard import RiskGuard
from prd_agent.risk.quality_gate import QualityGate
from prd_agent.market.symbol_scanner import SymbolScanner
from prd_agent.positions.position_steward import PositionSteward
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
        self.trade_journal = TradeJournal(self.data_dir)
        self._min_analysis_conf = load_min_analysis_confidence(cfg)
        self._signal_cooldown = PerSymbolSignalCooldown(
            load_signal_notify_cooldown_sec(cfg)
        )
        self.reporter = BiHourlyReporter(cfg)
        self.reporter.high_conf = self._min_analysis_conf
        self.improver = SelfImprover(cfg, self.root, on_config_reload=self.reload_config)
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
        self._seen_closed_ids: Set[str] = set()
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
        )

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
        self.position_steward = PositionSteward(self.cfg)
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
        self.notifier._cfg = self.cfg
        logger.info("Config reloaded from disk")

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
        rows = await self.monitor.fetch_closed_pnl(self.exchange, hours=6)
        for r in rows:
            oid = str(r.get("orderId") or r.get("id") or "")
            if not oid or oid in self._seen_closed_ids:
                continue
            self._seen_closed_ids.add(oid)
            pnl = float(r.get("closedPnl", 0) or 0)
            self.risk.record_trade(pnl)
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
            if note.startswith("📌"):
                await self.notifier.send(note)
        await self._sync_closed_pnl_to_risk()

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
            await self.notifier.signal_received(
                sig.symbol,
                sig.side,
                sig.confidence,
                sig.source,
                sig.reason,
                raw=sig.raw,
            )
            await self._maybe_execute(sig, entry.id)

        now = datetime.now(timezone.utc).timestamp()
        if now - self._last_report_at >= self.report_interval_sec:
            await self._bi_hourly_report(positions)
            self._last_report_at = now
        if now - self._last_global_at >= self.global_interval_sec:
            await self._global_analysis()
            self._last_global_at = now

    async def _maybe_execute(self, sig: UnifiedSignal, ledger_id: str) -> None:
        if await self._skip_if_position_open(sig, ledger_id):
            return
        ok, reason = self.risk.can_trade(sig.symbol)
        if not ok:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            if not self._is_silent_skip(reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, reason)
            return
        if not self.exchange.uses_prd_client:
            self.ledger.update_status(ledger_id, SignalStatus.REJECTED, "нет BybitClient")
            await self.notifier.order_failed(sig.symbol, "BybitClient недоступен")
            return

        entry = sig.entry or await self.exchange.get_price(sig.symbol)
        sl = sig.stop_loss or (entry * 0.995 if sig.side == "Buy" else entry * 1.005)
        tp = sig.take_profit or (entry * 1.01 if sig.side == "Buy" else entry * 0.99)
        balance = await self.exchange.get_balance()
        available = await self.exchange.get_available_balance()
        qty = self.risk.calculate_position_size(balance, self.risk_pct, entry, sl, self.leverage)
        if qty <= 0:
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, "qty=0")
            return

        notional = (qty * entry) / max(self.leverage, 1)
        min_margin = notional * 1.05
        if available < min_margin:
            reason = (
                f"недостаточно свободной маржи: доступно {available:.2f} USDT, "
                f"нужно ~{min_margin:.2f} (retCode=110007)"
            )
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
            return

        qty, sl, tp, prep_err = await prepare_market_order(
            self.exchange._client,
            symbol=sig.symbol,
            leverage=self.leverage,
            qty=qty,
            stop_loss=sl,
            take_profit=tp,
        )
        if prep_err:
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, prep_err)
            if not self._is_silent_skip(prep_err):
                await self.notifier.signal_skipped(sig.symbol, sig.side, prep_err)
            return

        q_ok, q_reason = await self.quality_gate.check(
            sig, self.exchange, entry=entry, sl=sl, tp=tp
        )
        if not q_ok:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, q_reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, q_reason)
            if not self._is_silent_skip(q_reason):
                await self.notifier.signal_skipped(sig.symbol, sig.side, q_reason)
            return

        result = await self.exchange.place_order(
            symbol=sig.symbol,
            side=sig.side,
            qty=qty,
            stop_loss=sl,
            take_profit=tp,
        )
        if result.get("success"):
            oid = str(result.get("orderId", ""))
            logger.info("Order OK %s %s qty=%.6f id=%s", sig.symbol, sig.side, qty, oid)
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
            )
            self.position_steward.mark_bot_opened(sig.symbol)
            await self.notifier.order_placed(sig.symbol, sig.side, qty, oid)
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
        applied = self.improver.process_proposals(proposals + hints)
        if applied:
            code_changes = self.improver.recent_changes(hours=2)
            for ch in applied:
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
