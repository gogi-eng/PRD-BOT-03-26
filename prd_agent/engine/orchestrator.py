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
from prd_agent.analysis.signal_ledger import SignalLedger, SignalStatus
from prd_agent.analysis.trade_monitor import TradeMonitor
from prd_agent.config import load_config
from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.exchange.bybit_adapter import BybitAdapter
from prd_agent.exchange.order_prep import prepare_market_order
from prd_agent.reporting.bi_hourly import BiHourlyReporter
from prd_agent.risk.guard import RiskGuard
from prd_agent.signals.router import SignalRouter, UnifiedSignal
from prd_agent.telegram.notifier import TelegramNotifier

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
        self.reporter = BiHourlyReporter(cfg)
        self.improver = SelfImprover(cfg, self.root, on_config_reload=self.reload_config)
        self.notifier = TelegramNotifier(cfg)
        self.global_analyzer = GlobalAnalyzer(cfg, self.ledger, self.monitor)

        t = cfg.get("trading", {})
        self.symbols: List[str] = list(t.get("symbols", ["BTCUSDT"]))
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

    def _risk_notify(self, msg: str) -> None:
        if self._notify_loop and self._notify_loop.is_running():
            asyncio.run_coroutine_threadsafe(self.notifier.risk_event(msg), self._notify_loop)

    def reload_config(self) -> None:
        path = Path(self.cfg.get("_config_path", self.root / "config.yaml"))
        self.cfg = load_config(path)
        t = self.cfg.get("trading", {})
        self.risk_pct = float(t.get("risk_pct_per_trade", self.risk_pct))
        self.signals._min_conf = float(t.get("min_signal_confidence", self.signals._min_conf))
        self.symbols = list(t.get("symbols", self.symbols))
        logger.info("Config reloaded from disk")

    async def start(self) -> None:
        self._running = True
        self._notify_loop = asyncio.get_running_loop()
        self.risk.set_notify_callback(self._risk_notify)
        balance = await self.exchange.get_balance()
        self.risk.initial_balance = balance
        mode = "TESTNET" if self.exchange.is_testnet else "LIVE"
        await self.notifier.send(
            f"🚀 <b>Unified Agent</b> запущен\n"
            f"Режим: {mode}\n"
            f"Баланс: {balance:.2f} USDT\n"
            f"Символы: {', '.join(self.symbols)}"
        )
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
        positions = await self.exchange.get_positions()
        self.risk.open_positions_count = len(positions)
        await self._monitor_positions(positions)
        await self._sync_closed_pnl_to_risk()

        all_signals = await self.signals.collect_all(self.exchange, self.symbols)
        if all_signals:
            logger.info(
                "Cycle: %d signal(s), open positions=%d",
                len(all_signals),
                len(positions),
            )
        for sig in all_signals:
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
            await self.notifier.signal_received(
                sig.symbol, sig.side, sig.confidence, sig.source, sig.reason
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
        ok, reason = self.risk.can_trade(sig.symbol)
        if not ok:
            logger.info("Skip %s %s: %s", sig.symbol, sig.side, reason)
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, reason)
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
        qty = self.risk.calculate_position_size(balance, self.risk_pct, entry, sl, self.leverage)
        if qty <= 0:
            self.ledger.update_status(ledger_id, SignalStatus.SKIPPED, "qty=0")
            await self.notifier.signal_skipped(sig.symbol, sig.side, "размер позиции = 0")
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
            await self.notifier.signal_skipped(sig.symbol, sig.side, prep_err)
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
            await self.notifier.order_placed(sig.symbol, sig.side, qty, oid)
        else:
            err = str(result.get("error", "unknown"))
            logger.warning("Order FAIL %s %s: %s", sig.symbol, sig.side, err)
            self.ledger.update_status(ledger_id, SignalStatus.REJECTED, err)
            await self.notifier.order_failed(sig.symbol, err)

    async def _bi_hourly_report(self, positions: List[Dict]) -> None:
        signals_2h = self.signals.recent_signals(hours=2)
        signals_24h = self.signals.recent_signals(hours=24)
        high_conf = [s for s in signals_2h if float(s.get("confidence", 0)) >= self.reporter.high_conf]
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
        signals_24h = self.signals.recent_signals(hours=24)
        text = await self.global_analyzer.build_report(self.exchange, signals_24h, hours=24)
        await self.notifier.global_report(text)
