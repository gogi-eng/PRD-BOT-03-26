"""
Агент-надсмотрщик: все сделки на бирже + виртуальные по сигналам + раз в 2ч подстройка config.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.supervisor.position_tracker import PositionTracker
from prd_agent.supervisor.virtual_trade_engine import VirtualTradeEngine

logger = logging.getLogger("prd_agent.supervisor")


class TradeSupervisor:
    def __init__(
        self,
        cfg: Dict[str, Any],
        store_dir: Path,
        improver: SelfImprover,
    ):
        self.cfg = cfg
        self.improver = improver
        sup = cfg.get("trade_supervisor", {})
        if not isinstance(sup, dict):
            sup = {}
        self.enabled = bool(sup.get("enabled", True))
        self.virtual_enabled = bool(sup.get("virtual_trades_enabled", True))
        self.interval_hours = float(sup.get("interval_hours", 2))
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.filters_path = self.store_dir / "dynamic_filters.yaml"
        self.notes_path = self.store_dir / "supervisor_notes.jsonl"
        self.positions = PositionTracker(store_dir / "positions")
        self.virtual = VirtualTradeEngine(
            store_dir / "virtual",
            max_open=int(sup.get("virtual_max_open", 40)),
            max_age_hours=float(sup.get("virtual_max_age_hours", 72)),
        )

    def _log_note(self, text: str, **extra: Any) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "text": text,
            **extra,
        }
        with self.notes_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def run_cycle_tick(self, exchange, bot_symbols: Set[str]) -> None:
        if not self.enabled:
            return
        snap = await self.positions.sync(exchange, bot_symbols)
        if snap.get("count", 0):
            logger.debug(
                "Supervisor positions: total=%s bot=%s manual=%s",
                snap["count"],
                snap.get("bot"),
                snap.get("manual"),
            )
        if self.virtual_enabled:
            closed = await self.virtual.tick(exchange)
            for vt in closed:
                self._log_note(
                    f"virtual {vt.symbol} {vt.close_reason} pnl={vt.pnl_pct:.2f}%",
                    symbol=vt.symbol,
                    event="virtual_close",
                )

    def register_virtual_signal(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        source: str,
        confidence: float,
        ledger_id: str = "",
    ) -> None:
        if not self.enabled or not self.virtual_enabled:
            return
        self.virtual.open_trade(
            symbol=symbol,
            side=side,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            source=source,
            confidence=confidence,
            ledger_id=ledger_id,
            real_status="received",
        )

    def note_signal_outcome(self, ledger_id: str, status: str, reason: str = "") -> None:
        if ledger_id:
            self.virtual.mark_real_status(ledger_id, status, reason)

    def _analyze_ledger_skips(self, ledger, hours: float = 2) -> Dict[str, Any]:
        entries = ledger.recent(hours) if hasattr(ledger, "recent") else []
        reasons = Counter()
        for e in entries:
            if str(e.get("status", "")) == "skipped":
                r = str(e.get("reason", "") or "unknown")[:120]
                key = r.split(":")[0] if ":" in r else r
                reasons[key] += 1
        return {"skipped_by_reason": dict(reasons.most_common(8)), "total": len(entries)}

    def _proposals_from_supervisor(
        self,
        *,
        report_2h: Dict[str, Any],
        report_24h: Dict[str, Any],
        virtual_2h: Dict[str, Any],
        virtual_24h: Dict[str, Any],
        skip_analysis: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        proposals: List[Dict[str, Any]] = []
        rr_skips = sum(
            v
            for k, v in skip_analysis.get("skipped_by_reason", {}).items()
            if "quality_gate" in k and "RR" in k
        )
        if rr_skips >= 3:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["quality_gate", "min_rr_ratio"],
                    "delta": -0.1,
                    "summary": "Супервизор: ослабить min_rr — много Skip по RR",
                    "justification": f"RR skips={rr_skips} за 2ч",
                }
            )
        v_wr = float(virtual_24h.get("win_rate_pct", 0))
        v_n = int(virtual_24h.get("closed", 0))
        real_wr = float(report_24h.get("win_rate_pct", 0))
        real_pnl = float(report_24h.get("pnl_usdt", 0))
        if v_n >= 8 and v_wr >= 52 and real_pnl < 0:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["signals", "min_analysis_confidence"],
                    "delta": -0.02,
                    "summary": "Супервизор: виртуальные в плюсе — чуть снизить порог входа",
                    "justification": f"virtual WR={v_wr}% real PnL={real_pnl}",
                }
            )
        if v_n >= 10 and v_wr < 38:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["trading", "min_signal_confidence"],
                    "delta": +0.02,
                    "summary": "Супервизор: виртуальные слабые — ужесточить conf",
                    "justification": f"virtual WR={v_wr}%",
                }
            )
        if real_wr < 35 and float(report_24h.get("closed_trades", 0)) >= 5:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["risk", "cooldown_after_loss_sec"],
                    "delta": +120,
                    "summary": "Супервизор: увеличить паузу после убытка",
                    "justification": f"real WR={real_wr}%",
                }
            )
        not_opened = int(report_2h.get("ledger_not_opened", 0))
        sig_total = max(int(report_2h.get("signals_total", 1)), 1)
        if not_opened > sig_total * 0.85 and rr_skips < 2:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["quality_gate", "min_rr_ratio"],
                    "delta": -0.05,
                    "summary": "Супервизор: много сигналов не доходит до ордера",
                    "justification": f"not_opened ratio high, skips={skip_analysis}",
                }
            )
        return proposals

    def _update_dynamic_filters(self, skip_analysis: Dict[str, Any]) -> List[str]:
        """Доп. фильтры в YAML (для ручного просмотра и будущих модулей)."""
        notes: List[str] = []
        filters: Dict[str, Any] = {}
        if self.filters_path.exists():
            try:
                filters = yaml.safe_load(self.filters_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                filters = {}
        filters.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        filters["skip_stats_2h"] = skip_analysis.get("skipped_by_reason", {})
        rr_heavy = sum(
            1
            for k in skip_analysis.get("skipped_by_reason", {})
            if "quality_gate" in k
        )
        if rr_heavy >= 2:
            filters["hint"] = "Рассмотреть min_rr_ratio 2.0 и проверку SR-зон"
            notes.append("dynamic_filters: hint RR")
        filters["require_sl_tp"] = True
        self.filters_path.write_text(
            yaml.safe_dump(filters, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        return notes

    async def run_bi_hourly_review(
        self,
        *,
        ledger,
        report_2h: Dict[str, Any],
        report_24h: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Раз в 2ч: статистика + предложения/применение правок config."""
        virtual_2h = self.virtual.stats(2) if self.virtual_enabled else {}
        virtual_24h = self.virtual.stats(24) if self.virtual_enabled else {}
        skip_analysis = self._analyze_ledger_skips(ledger, 2)
        filter_notes = self._update_dynamic_filters(skip_analysis)
        proposals = self._proposals_from_supervisor(
            report_2h=report_2h,
            report_24h=report_24h,
            virtual_2h=virtual_2h,
            virtual_24h=virtual_24h,
            skip_analysis=skip_analysis,
        )
        applied: List[Dict[str, Any]] = []
        if self.improver.enabled:
            applied = self.improver.process_proposals(proposals)
        summary = {
            "virtual_2h": virtual_2h,
            "virtual_24h": virtual_24h,
            "skip_analysis": skip_analysis,
            "proposals_count": len(proposals),
            "applied_count": len(applied),
            "filter_notes": filter_notes,
            "position_snapshot": {},
        }
        if self.positions.snapshot_path.exists():
            try:
                summary["position_snapshot"] = json.loads(
                    self.positions.snapshot_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError:
                pass
        self._log_note(
            "bi_hourly review",
            virtual_2h=virtual_2h,
            applied=len(applied),
        )
        logger.info(
            "Supervisor 2h: virtual_closed=%s applied_tunes=%s",
            virtual_2h.get("closed"),
            len(applied),
        )
        return summary

    @staticmethod
    def format_report_section(supervisor_summary: Dict[str, Any]) -> List[str]:
        if not supervisor_summary:
            return []
        v2 = supervisor_summary.get("virtual_2h") or {}
        v24 = supervisor_summary.get("virtual_24h") or {}
        snap = supervisor_summary.get("position_snapshot") or {}
        lines = [
            "",
            "<b>🧪 Виртуальные сделки (по сигналам бота)</b>",
            f"• За 2ч: закрыто {v2.get('closed', 0)} | WR {v2.get('win_rate_pct', 0)}% | "
            f"ср.PnL {v2.get('avg_pnl_pct', 0):+.3f}%",
            f"• За 24ч: закрыто {v24.get('closed', 0)} | WR {v24.get('win_rate_pct', 0)}% | "
            f"открыто сейчас {v24.get('open', 0)}",
            "",
            "<b>📌 Позиции на бирже (бот + ручные)</b>",
            f"• Всего: {snap.get('count', 0)} (бот {snap.get('bot', 0)}, ручные {snap.get('manual', 0)})",
        ]
        for p in (snap.get("positions") or [])[:6]:
            lines.append(
                f"• {p.get('symbol')} {p.get('side')} [{p.get('origin')}] "
                f"uPnL={float(p.get('upnl', 0)):+.2f}"
            )
        skips = supervisor_summary.get("skip_analysis", {}).get("skipped_by_reason", {})
        if skips:
            lines.append("")
            lines.append("<b>⏭ Пропуски сигналов (2ч)</b>")
            for reason, cnt in list(skips.items())[:5]:
                lines.append(f"• {cnt}× {reason[:60]}")
        if supervisor_summary.get("applied_count", 0):
            lines.append("")
            lines.append(
                f"<b>🛠 Супервизор применил правок config:</b> "
                f"{supervisor_summary.get('applied_count')}"
            )
        return lines
