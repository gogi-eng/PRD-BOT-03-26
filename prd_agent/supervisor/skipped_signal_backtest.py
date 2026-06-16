"""
Бэктест сигналов из ledger, по которым сделка не открылась (skipped/rejected).
Симуляция SL/TP по историческим свечам после момента сигнала.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from exchange.bybit_fees import BybitFeeConfig, apply_fees_to_pnl_pct

logger = logging.getLogger("prd_agent.supervisor.skipped_bt")


@dataclass
class SkippedBacktestResult:
    ledger_id: str
    symbol: str
    side: str
    source: str
    skip_reason: str
    entry: float
    stop_loss: float
    take_profit: float
    outcome: str
    pnl_pct: float
    exit_price: float
    signal_at: str
    backtested_at: str
    pnl_pct_gross: float = 0.0
    pnl_pct_net: float = 0.0
    fee_pct_round_trip: float = 0.0
    candles_used: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _kline_ts_ms(k: Dict[str, Any]) -> int:
    raw = k.get("startTime") or k.get("timestamp") or k.get("open_time") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _fill_missing_levels(
    entry: float, side: str, stop_loss: float, take_profit: float
) -> tuple[float, float, float]:
    if entry <= 0:
        return entry, stop_loss, take_profit
    side_u = str(side or "").upper()
    sl = float(stop_loss or 0)
    tp = float(take_profit or 0)
    if sl <= 0:
        sl = entry * 0.995 if side_u == "BUY" else entry * 1.005
    if tp <= 0:
        tp = entry * 1.01 if side_u == "BUY" else entry * 0.99
    return entry, sl, tp


def _row_pnl_pct(row: Dict[str, Any]) -> float:
    if "pnl_pct_net" in row:
        return float(row.get("pnl_pct_net", 0) or 0)
    return float(row.get("pnl_pct", 0) or 0)


def simulate_skipped_signal(
    *,
    side: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    klines: List[Dict[str, Any]],
    entry_ts_ms: int,
    fee_cfg: Optional[BybitFeeConfig] = None,
) -> Dict[str, Any]:
    """
    Проход по свечам после сигнала. На одной свече SL проверяется раньше TP (консервативно).
    """
    entry, sl, tp = _fill_missing_levels(entry, side, stop_loss, take_profit)
    if entry <= 0 or sl <= 0 or tp <= 0 or not klines:
        empty = apply_fees_to_pnl_pct(0.0, fee_cfg)
        return {
            "outcome": "invalid",
            "pnl_pct": empty["pnl_pct_net"],
            "pnl_pct_gross": empty["pnl_pct_gross"],
            "pnl_pct_net": empty["pnl_pct_net"],
            "fee_pct_round_trip": empty["fee_pct_round_trip"],
            "exit_price": 0.0,
            "candles_used": 0,
        }

    def _finish(outcome: str, gross_pnl: float, exit_px: float, used_n: int) -> Dict[str, Any]:
        fees = apply_fees_to_pnl_pct(gross_pnl, fee_cfg)
        return {
            "outcome": outcome,
            "pnl_pct": fees["pnl_pct_net"],
            "pnl_pct_gross": fees["pnl_pct_gross"],
            "pnl_pct_net": fees["pnl_pct_net"],
            "fee_pct_round_trip": fees["fee_pct_round_trip"],
            "exit_price": exit_px,
            "candles_used": used_n,
        }

    side_u = str(side or "").upper()
    is_buy = side_u in ("BUY", "LONG")
    used = 0
    last_close = entry

    for k in klines:
        ts = _kline_ts_ms(k)
        if ts and ts < entry_ts_ms:
            continue
        try:
            high = float(k.get("high", 0) or 0)
            low = float(k.get("low", 0) or 0)
            last_close = float(k.get("close", 0) or last_close)
        except (TypeError, ValueError):
            continue
        if high <= 0 or low <= 0:
            continue
        used += 1
        if is_buy:
            if low <= sl:
                gross = (sl - entry) / entry * 100.0
                return _finish("stop_loss", gross, sl, used)
            if high >= tp:
                gross = (tp - entry) / entry * 100.0
                return _finish("take_profit", gross, tp, used)
        else:
            if high >= sl:
                gross = (entry - sl) / entry * 100.0
                return _finish("stop_loss", gross, sl, used)
            if low <= tp:
                gross = (entry - tp) / entry * 100.0
                return _finish("take_profit", gross, tp, used)

    if last_close > 0:
        if is_buy:
            gross = (last_close - entry) / entry * 100.0
        else:
            gross = (entry - last_close) / entry * 100.0
        return _finish("still_open", gross, last_close, used)
    return _finish("no_data", 0.0, 0.0, used)


class SkippedSignalBacktester:
    def __init__(self, store_dir: Path, *, cfg: Optional[Dict[str, Any]] = None):
        sb = (cfg or {}).get("skipped_signal_backtest", {})
        if not isinstance(sb, dict):
            sb = {}
        self.enabled = bool(sb.get("enabled", True))
        self.lookback_hours = float(sb.get("lookback_hours", 72))
        self.max_per_run = int(sb.get("max_per_run", 40))
        self.min_age_minutes = int(sb.get("min_age_minutes", 30))
        self.kline_interval = str(sb.get("kline_interval", "15"))
        self.max_kline_limit = int(sb.get("max_kline_limit", 200))
        self.fee_cfg = BybitFeeConfig.from_cfg(cfg)
        self.store_dir = store_dir / "skipped_backtest"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.results_path = self.store_dir / "results.jsonl"
        self._done_ids: Set[str] = self._load_done_ids()

    def _load_done_ids(self) -> Set[str]:
        ids: Set[str] = set()
        if not self.results_path.exists():
            return ids
        for line in self.results_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                lid = str(row.get("ledger_id", "") or "")
                if lid:
                    ids.add(lid)
            except json.JSONDecodeError:
                pass
        return ids

    def _append_result(self, result: SkippedBacktestResult) -> None:
        with self.results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        self._done_ids.add(result.ledger_id)

    def _pick_candidates(self, ledger_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        min_age_sec = self.min_age_minutes * 60
        out: List[Dict[str, Any]] = []
        for row in ledger_rows:
            status = str(row.get("status", "")).lower()
            if status not in ("skipped", "rejected"):
                continue
            lid = str(row.get("id", "") or "")
            if not lid or lid in self._done_ids:
                continue
            try:
                ts = datetime.fromisoformat(
                    str(row.get("created_at", "")).replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if (now - ts).total_seconds() < min_age_sec:
                continue
            sym = str(row.get("symbol", "") or "").upper()
            if not sym:
                continue
            out.append(row)
        out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return out[: max(1, self.max_per_run)]

    def stats(self, hours: float = 24) -> Dict[str, Any]:
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        rows: List[Dict[str, Any]] = []
        if not self.results_path.exists():
            return {"hours": hours, "n": 0}
        for line in self.results_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(
                    str(row.get("backtested_at", "")).replace("Z", "+00:00")
                )
                if ts.timestamp() >= cutoff:
                    rows.append(row)
            except (json.JSONDecodeError, ValueError, KeyError):
                pass
        if not rows:
            return {"hours": hours, "n": 0, "win_rate_pct": 0.0, "tp_hits": 0, "sl_hits": 0}
        tp_hits = sum(1 for r in rows if r.get("outcome") == "take_profit")
        sl_hits = sum(1 for r in rows if r.get("outcome") == "stop_loss")
        wins = sum(1 for r in rows if _row_pnl_pct(r) > 0)
        by_reason: Dict[str, int] = {}
        for r in rows:
            key = str(r.get("skip_reason", "") or "?")[:50]
            by_reason[key] = by_reason.get(key, 0) + 1
        return {
            "hours": hours,
            "n": len(rows),
            "tp_hits": tp_hits,
            "sl_hits": sl_hits,
            "still_open": sum(1 for r in rows if r.get("outcome") == "still_open"),
            "win_rate_pct": round(wins / len(rows) * 100, 1),
            "avg_pnl_pct": round(sum(_row_pnl_pct(r) for r in rows) / len(rows), 3),
            "avg_pnl_gross_pct": round(
                sum(float(r.get("pnl_pct_gross", r.get("pnl_pct", 0))) for r in rows)
                / len(rows),
                3,
            ),
            "avg_fee_pct": round(
                sum(float(r.get("fee_pct_round_trip", 0)) for r in rows) / len(rows), 4
            ),
            "top_skip_reasons": dict(
                sorted(by_reason.items(), key=lambda x: -x[1])[:5]
            ),
        }

    @staticmethod
    def _reason_bucket(reason: str) -> str:
        r = str(reason or "").strip().lower()
        if not r:
            return "unknown"
        if "quality_gate" in r:
            if "rr" in r:
                return "quality_gate_rr"
            if "confidence" in r:
                return "quality_gate_conf"
            return "quality_gate"
        if "entry_guard" in r:
            return "entry_guard"
        if "pullback" in r:
            return "pullback_entry"
        if "supervisor_v4" in r or "meta_v3" in r:
            return "supervisor_block"
        if ":" in r:
            return r.split(":", 1)[0].strip()
        return r[:60]

    def stats_by_reason(self, hours: float = 24) -> Dict[str, Dict[str, Any]]:
        """WR/TP/SL по категории причины пропуска (для подстройки фильтров)."""
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        groups: Dict[str, List[Dict[str, Any]]] = {}
        if not self.results_path.exists():
            return {}
        for line in self.results_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(
                    str(row.get("backtested_at", "")).replace("Z", "+00:00")
                )
                if ts.timestamp() < cutoff:
                    continue
                bucket = self._reason_bucket(str(row.get("skip_reason", "")))
                groups.setdefault(bucket, []).append(row)
            except (json.JSONDecodeError, ValueError, KeyError):
                pass
        out: Dict[str, Dict[str, Any]] = {}
        for bucket, rows in groups.items():
            n = len(rows)
            wins = sum(1 for r in rows if _row_pnl_pct(r) > 0)
            tp_hits = sum(1 for r in rows if r.get("outcome") == "take_profit")
            sl_hits = sum(1 for r in rows if r.get("outcome") == "stop_loss")
            out[bucket] = {
                "n": n,
                "win_rate_pct": round(wins / n * 100, 1) if n else 0.0,
                "avg_pnl_pct": round(sum(_row_pnl_pct(r) for r in rows) / max(n, 1), 3),
                "avg_pnl_gross_pct": round(
                    sum(float(r.get("pnl_pct_gross", r.get("pnl_pct", 0))) for r in rows)
                    / max(n, 1),
                    3,
                ),
                "tp_hits": tp_hits,
                "sl_hits": sl_hits,
            }
        return out

    async def run_batch(self, ledger, exchange) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "tested": 0}
        if not hasattr(ledger, "recent"):
            return {"enabled": True, "tested": 0, "error": "no_ledger"}
        rows = ledger.recent(self.lookback_hours)
        candidates = self._pick_candidates(rows)
        if not candidates:
            return {"enabled": True, "tested": 0, "candidates": 0}

        tested = 0
        outcomes: Dict[str, int] = {}
        errors = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        for row in candidates:
            sym = str(row.get("symbol", "")).upper()
            try:
                ts = datetime.fromisoformat(
                    str(row.get("created_at", "")).replace("Z", "+00:00")
                )
                entry_ts_ms = int(ts.timestamp() * 1000)
            except ValueError:
                errors += 1
                continue
            age_h = max(
                1.0,
                (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0,
            )
            limit = min(
                self.max_kline_limit,
                max(24, int(age_h * 4) + 12),
            )
            try:
                klines = await exchange.get_klines(
                    sym, interval=self.kline_interval, limit=limit
                )
            except Exception as exc:
                logger.warning("skipped_bt klines %s: %s", sym, exc)
                errors += 1
                continue
            if not klines:
                errors += 1
                continue

            entry = float(row.get("entry", 0) or 0)
            if entry <= 0 and klines:
                for k in klines:
                    if _kline_ts_ms(k) >= entry_ts_ms:
                        entry = float(k.get("close", 0) or 0)
                        break
            sim = simulate_skipped_signal(
                side=str(row.get("side", "Buy")),
                entry=entry,
                stop_loss=float(row.get("stop_loss", 0) or 0),
                take_profit=float(row.get("take_profit", 0) or 0),
                klines=klines,
                entry_ts_ms=entry_ts_ms,
                fee_cfg=self.fee_cfg,
            )
            if sim.get("outcome") == "invalid":
                errors += 1
                continue

            entry, sl, tp = _fill_missing_levels(
                entry,
                str(row.get("side", "Buy")),
                float(row.get("stop_loss", 0) or 0),
                float(row.get("take_profit", 0) or 0),
            )
            result = SkippedBacktestResult(
                ledger_id=str(row.get("id", "")),
                symbol=sym,
                side=str(row.get("side", "")),
                source=str(row.get("source", "")),
                skip_reason=str(row.get("reason", ""))[:200],
                entry=entry,
                stop_loss=sl,
                take_profit=tp,
                outcome=str(sim.get("outcome", "")),
                pnl_pct=float(sim.get("pnl_pct_net", sim.get("pnl_pct", 0))),
                pnl_pct_gross=float(sim.get("pnl_pct_gross", sim.get("pnl_pct", 0))),
                pnl_pct_net=float(sim.get("pnl_pct_net", sim.get("pnl_pct", 0))),
                fee_pct_round_trip=float(sim.get("fee_pct_round_trip", 0)),
                exit_price=float(sim.get("exit_price", 0)),
                signal_at=str(row.get("created_at", "")),
                backtested_at=now_iso,
                candles_used=int(sim.get("candles_used", 0)),
            )
            self._append_result(result)
            tested += 1
            oc = result.outcome
            outcomes[oc] = outcomes.get(oc, 0) + 1
            logger.info(
                "SKIPPED_BT %s %s %s → %s pnl_net=%.2f%% gross=%.2f%% fee=%.3f%% (skip: %s)",
                sym,
                row.get("side"),
                row.get("id"),
                result.outcome,
                result.pnl_pct_net,
                result.pnl_pct_gross,
                result.fee_pct_round_trip,
                str(row.get("reason", ""))[:60],
            )

        summary = {
            "enabled": True,
            "tested": tested,
            "candidates": len(candidates),
            "errors": errors,
            "outcomes": outcomes,
            "stats_24h": self.stats(24),
        }
        return summary
