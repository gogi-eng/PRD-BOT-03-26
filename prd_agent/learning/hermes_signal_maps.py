"""
Карты исполнения сигналов для Hermes (Analise_Hermes).

Собирает по каждому сигналу из signal_ledger:
- параметры входа (raw, entry_context, индикаторы, стакан)
- виртуальный исход SL/TP (skipped_backtest, virtual_engine)
- симуляцию трейлинга по свечам (если есть entry_candles)
- реальное закрытие (trade_history)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SIGNAL_MAPS_JSONL = "hermes_signal_maps.jsonl"
SIGNAL_MAPS_MD = "HERMES_SIGNAL_MAPS.md"


def _parse_ts(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _flatten_indicators(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    keys = (
        "atr_pct",
        "adx",
        "rsi",
        "regime",
        "trend",
        "htf_trend",
        "volatility",
        "entry_zone",
        "normalized_imbalance",
        "spread_pct",
        "volume_24h_usdt",
        "local_hour",
        "soft_score",
        "confidence",
    )
    out: Dict[str, Any] = {}
    for k in keys:
        if k in ctx:
            out[k] = ctx[k]
    return out


def _orderbook_from(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    ob: Dict[str, Any] = {}
    for k in ("normalized_imbalance", "spread_pct"):
        if k in ctx:
            ob[k] = ctx[k]
    filters = ctx.get("filters")
    if isinstance(filters, dict):
        raw = filters.get("signal_raw")
        if isinstance(raw, dict):
            for k in ("normalized_imbalance", "spread_pct", "bid_ask_imbalance"):
                if k in raw and k not in ob:
                    ob[k] = raw[k]
    return ob


def _mfe_mae_pct(side: str, entry: float, candles: Sequence[Mapping[str, Any]]) -> Tuple[float, float]:
    if entry <= 0 or not candles:
        return 0.0, 0.0
    side_u = str(side or "").upper()
    is_buy = side_u in ("BUY", "LONG")
    mfe = 0.0
    mae = 0.0
    for c in candles:
        try:
            high = float(c.get("h", c.get("high", 0)) or 0)
            low = float(c.get("l", c.get("low", 0)) or 0)
        except (TypeError, ValueError):
            continue
        if high <= 0 or low <= 0:
            continue
        if is_buy:
            mfe = max(mfe, (high - entry) / entry * 100.0)
            mae = min(mae, (low - entry) / entry * 100.0)
        else:
            mfe = max(mfe, (entry - low) / entry * 100.0)
            mae = min(mae, (entry - high) / entry * 100.0)
    return round(mfe, 4), round(mae, 4)


def simulate_trailing_on_candles(
    *,
    side: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    candles: Sequence[Mapping[str, Any]],
    activation_pct: float = 1.8,
    distance_pct: float = 0.8,
) -> Dict[str, Any]:
    """Упрощённая симуляция трейлинга по OHLC (как если бы сделка была открыта)."""
    if entry <= 0 or not candles or stop_loss <= 0:
        return {"simulated": False, "reason": "no_candles_or_entry"}

    side_u = str(side or "").upper()
    is_buy = side_u in ("BUY", "LONG")
    trail_sl = float(stop_loss)
    activated = False
    best_price = entry
    milestones: List[Dict[str, Any]] = []

    for i, c in enumerate(candles):
        try:
            high = float(c.get("h", c.get("high", 0)) or 0)
            low = float(c.get("l", c.get("low", 0)) or 0)
            close = float(c.get("c", c.get("close", 0)) or 0)
            ts = c.get("t", c.get("timestamp", i))
        except (TypeError, ValueError):
            continue
        if high <= 0 or low <= 0:
            continue

        if is_buy:
            profit_pct = (high - entry) / entry * 100.0
            if profit_pct >= activation_pct:
                activated = True
            if activated:
                best_price = max(best_price, high)
                trail_sl = max(trail_sl, best_price * (1.0 - distance_pct / 100.0))
            if low <= trail_sl:
                pnl = (trail_sl - entry) / entry * 100.0
                return {
                    "simulated": True,
                    "would_exit": "trailing_stop" if activated else "stop_loss",
                    "exit_pnl_pct": round(pnl, 4),
                    "activated": activated,
                    "activation_pct": activation_pct,
                    "distance_pct": distance_pct,
                    "milestones": milestones[-8:],
                    "bars": i + 1,
                }
            if take_profit > 0 and high >= take_profit:
                pnl = (take_profit - entry) / entry * 100.0
                return {
                    "simulated": True,
                    "would_exit": "take_profit",
                    "exit_pnl_pct": round(pnl, 4),
                    "activated": activated,
                    "activation_pct": activation_pct,
                    "distance_pct": distance_pct,
                    "milestones": milestones[-8:],
                    "bars": i + 1,
                }
        else:
            profit_pct = (entry - low) / entry * 100.0
            if profit_pct >= activation_pct:
                activated = True
            if activated:
                best_price = min(best_price, low)
                trail_sl = min(trail_sl, best_price * (1.0 + distance_pct / 100.0))
            if high >= trail_sl:
                pnl = (entry - trail_sl) / entry * 100.0
                return {
                    "simulated": True,
                    "would_exit": "trailing_stop" if activated else "stop_loss",
                    "exit_pnl_pct": round(pnl, 4),
                    "activated": activated,
                    "activation_pct": activation_pct,
                    "distance_pct": distance_pct,
                    "milestones": milestones[-8:],
                    "bars": i + 1,
                }
            if take_profit > 0 and low <= take_profit:
                pnl = (entry - take_profit) / entry * 100.0
                return {
                    "simulated": True,
                    "would_exit": "take_profit",
                    "exit_pnl_pct": round(pnl, 4),
                    "activated": activated,
                    "activation_pct": activation_pct,
                    "distance_pct": distance_pct,
                    "milestones": milestones[-8:],
                    "bars": i + 1,
                }

        if i % max(1, len(candles) // 5) == 0:
            milestones.append(
                {
                    "bar": i,
                    "t": ts,
                    "close": round(close, 8),
                    "trail_sl": round(trail_sl, 8),
                    "activated": activated,
                }
            )

    last = candles[-1]
    close = float(last.get("c", last.get("close", entry)) or entry)
    if is_buy:
        pnl = (close - entry) / entry * 100.0
    else:
        pnl = (entry - close) / entry * 100.0
    return {
        "simulated": True,
        "would_exit": "still_open",
        "exit_pnl_pct": round(pnl, 4),
        "activated": activated,
        "activation_pct": activation_pct,
        "distance_pct": distance_pct,
        "milestones": milestones[-8:],
        "bars": len(candles),
    }


@dataclass
class SignalExecutionMap:
    ledger_id: str
    symbol: str
    side: str
    status: str
    skip_reason: str = ""
    signal_at: str = ""
    source: str = ""
    confidence: float = 0.0
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    entry_params: Dict[str, Any] = field(default_factory=dict)
    price_movement: Dict[str, Any] = field(default_factory=dict)
    virtual_sl_tp: Dict[str, Any] = field(default_factory=dict)
    virtual_trailing: Dict[str, Any] = field(default_factory=dict)
    real_trade: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HermesSignalMapBuilder:
    def __init__(
        self,
        data_dir: Path,
        *,
        trailing_activation_pct: float = 1.8,
        trailing_distance_pct: float = 0.8,
    ):
        self.data_dir = Path(data_dir)
        self.trailing_activation_pct = trailing_activation_pct
        self.trailing_distance_pct = trailing_distance_pct

    def _paths(self) -> Dict[str, Path]:
        d = self.data_dir
        return {
            "ledger": d / "ledger" / "signal_ledger.jsonl",
            "trades": d / "trades" / "trade_history.jsonl",
            "skipped_bt": d / "supervisor" / "skipped_backtest" / "results.jsonl",
            "virtual_closed": d / "supervisor" / "virtual" / "virtual_closed.jsonl",
        }

    @staticmethod
    def _index_by_ledger(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            lid = str(row.get("ledger_id", "") or "")
            if lid:
                out[lid] = dict(row)
        return out

    @staticmethod
    def _match_trade(
        ledger_row: Mapping[str, Any], trades: Sequence[Mapping[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        lid = str(ledger_row.get("id", "") or "")
        sym = str(ledger_row.get("symbol", "")).upper()
        side = str(ledger_row.get("side", "")).upper()
        sig_ts = _parse_ts(ledger_row.get("created_at"))

        for t in reversed(list(trades)):
            if str(t.get("ledger_id", "") or "") == lid and lid:
                return dict(t)
        for t in reversed(list(trades)):
            if str(t.get("event", "")).lower() != "entered":
                continue
            if str(t.get("symbol", "")).upper() != sym:
                continue
            if str(t.get("side", "")).upper() != side:
                continue
            if sig_ts > 0 and abs(_parse_ts(t.get("ts")) - sig_ts) > 900:
                continue
            return dict(t)

        closed_by_sym: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            if str(t.get("event", "")).lower() == "closed":
                key = f"{t.get('symbol','')}_{t.get('side','')}"
                closed_by_sym[key] = dict(t)

        ent = None
        for t in reversed(list(trades)):
            if str(t.get("event", "")).lower() != "entered":
                continue
            if str(t.get("symbol", "")).upper() != sym:
                continue
            if str(t.get("side", "")).upper() != side:
                continue
            if sig_ts > 0 and abs(_parse_ts(t.get("ts")) - sig_ts) > 900:
                continue
            ent = dict(t)
            break
        if not ent:
            return None
        c = closed_by_sym.get(f"{sym}_{side}")
        if c:
            merged = {**ent, **c, "matched_closed": True}
            return merged
        return ent

    def build_maps(self, *, hours: float = 72.0) -> List[SignalExecutionMap]:
        paths = self._paths()
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600.0

        ledger_rows = [
            r
            for r in _read_jsonl(paths["ledger"])
            if _parse_ts(r.get("created_at")) >= cutoff
        ]
        trades = _read_jsonl(paths["trades"])
        skipped_idx = self._index_by_ledger(_read_jsonl(paths["skipped_bt"]))
        virtual_idx = self._index_by_ledger(_read_jsonl(paths["virtual_closed"]))

        maps: List[SignalExecutionMap] = []
        for row in ledger_rows:
            lid = str(row.get("id", "") or "")
            raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
            ledger_snap = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
            trade = self._match_trade(row, trades)
            ctx: Dict[str, Any] = {}
            candles: List[Dict[str, Any]] = []
            if trade:
                ec = trade.get("entry_context")
                if isinstance(ec, dict):
                    ctx = ec
                ecandles = trade.get("entry_candles")
                if isinstance(ecandles, list):
                    candles = [c for c in ecandles if isinstance(c, dict)]

            entry_params: Dict[str, Any] = {
                "indicators": _flatten_indicators(ledger_snap)
                or _flatten_indicators(ctx)
                or _flatten_indicators(raw),
                "orderbook": _orderbook_from(ledger_snap)
                or _orderbook_from(ctx)
                or _orderbook_from(raw),
                "ledger_snapshot": ledger_snap,
                "signal_raw_keys": sorted(raw.keys())[:40] if raw else [],
            }
            if ctx.get("filters"):
                entry_params["filters"] = ctx.get("filters")

            mfe, mae = _mfe_mae_pct(
                str(row.get("side", "")),
                float(row.get("entry", 0) or 0),
                candles,
            )
            price_movement: Dict[str, Any] = {
                "mfe_pct": mfe,
                "mae_pct": mae,
                "candles_count": len(candles),
            }
            if candles:
                price_movement["candles_tail"] = candles[-12:]

            virtual_sl_tp: Dict[str, Any] = {"source": "none"}
            if lid in skipped_idx:
                sb = skipped_idx[lid]
                virtual_sl_tp = {
                    "source": "skipped_backtest",
                    "outcome": sb.get("outcome"),
                    "pnl_pct_net": sb.get("pnl_pct_net", sb.get("pnl_pct")),
                    "exit_price": sb.get("exit_price"),
                    "candles_used": sb.get("candles_used"),
                }
            elif lid in virtual_idx:
                vt = virtual_idx[lid]
                virtual_sl_tp = {
                    "source": "virtual_engine",
                    "outcome": vt.get("close_reason") or vt.get("status"),
                    "pnl_pct": vt.get("pnl_pct"),
                    "mfe_pct": vt.get("mfe_pct"),
                    "mae_pct": vt.get("mae_pct"),
                    "exit_price": vt.get("exit_price"),
                }
                if not mfe and vt.get("mfe_pct"):
                    price_movement["mfe_pct"] = vt.get("mfe_pct")
                if not mae and vt.get("mae_pct"):
                    price_movement["mae_pct"] = vt.get("mae_pct")

            virtual_trailing = simulate_trailing_on_candles(
                side=str(row.get("side", "")),
                entry=float(row.get("entry", 0) or 0),
                stop_loss=float(row.get("stop_loss", 0) or 0),
                take_profit=float(row.get("take_profit", 0) or 0),
                candles=candles,
                activation_pct=self.trailing_activation_pct,
                distance_pct=self.trailing_distance_pct,
            )

            real_trade: Dict[str, Any] = {"matched": False}
            if trade:
                real_trade["matched"] = True
                real_trade["order_id"] = trade.get("order_id", "")
                real_trade["ledger_id"] = trade.get("ledger_id", "")
                if str(trade.get("event", "")).lower() == "closed" or trade.get("matched_closed"):
                    real_trade.update(
                        {
                            "pnl_usdt": trade.get("pnl_usdt", trade.get("pnl")),
                            "pnl_pct": trade.get("pnl_pct"),
                            "close_reason": trade.get("close_reason", trade.get("reason")),
                            "closed_at": trade.get("ts"),
                            "outcome": (
                                "profit"
                                if float(trade.get("pnl_usdt", trade.get("pnl", 0)) or 0) > 0
                                else (
                                    "loss"
                                    if float(trade.get("pnl_usdt", trade.get("pnl", 0)) or 0) < 0
                                    else "neutral"
                                )
                            ),
                        }
                    )
                else:
                    real_trade["state"] = "open_or_entered_only"

            status = str(row.get("status", "")).lower()
            reason = str(row.get("reason", "") or "")
            maps.append(
                SignalExecutionMap(
                    ledger_id=lid,
                    symbol=str(row.get("symbol", "")).upper(),
                    side=str(row.get("side", "")).upper(),
                    status=status,
                    skip_reason=reason if status in ("skipped", "rejected") else "",
                    signal_at=str(row.get("created_at", "")),
                    source=str(row.get("source", "")),
                    confidence=float(row.get("confidence", 0) or 0),
                    entry=float(row.get("entry", 0) or 0),
                    stop_loss=float(row.get("stop_loss", 0) or 0),
                    take_profit=float(row.get("take_profit", 0) or 0),
                    entry_params=entry_params,
                    price_movement=price_movement,
                    virtual_sl_tp=virtual_sl_tp,
                    virtual_trailing=virtual_trailing,
                    real_trade=real_trade,
                )
            )
        return maps


def build_signal_maps_markdown(
    maps: Sequence[SignalExecutionMap],
    *,
    source_label: str,
    hours: float,
    generated_at: str,
) -> str:
    total = len(maps)
    by_status: Dict[str, int] = {}
    executed = 0
    virt_tp = 0
    virt_sl = 0
    for m in maps:
        by_status[m.status] = by_status.get(m.status, 0) + 1
        if m.real_trade.get("matched"):
            executed += 1
        vo = str(m.virtual_sl_tp.get("outcome", "")).lower()
        if "profit" in vo or vo == "take_profit":
            virt_tp += 1
        if "loss" in vo or vo == "stop_loss":
            virt_sl += 1

    lines = [
        "---",
        "hermes_feed: true",
        f"generated_at: {generated_at}",
        f"source: {source_label}",
        f"lookback_hours: {hours}",
        "---",
        "",
        "# HERMES — карты сигналов (исполнение)",
        "",
        "> **Для Hermes / Cursor:** полные карты — `hermes_signal_maps.jsonl` (одна строка = один сигнал).",
        "> Бот пишет **все** сигналы в `signal_ledger`; виртуальный исход — для пропущенных; реальный PnL — для открытых.",
        "",
        f"**Обновлено:** {generated_at} | окно **{hours:.0f} ч** | сигналов: **{total}**",
        "",
        "## Сводка",
        "",
        f"| Метрика | Значение |",
        f"|---------|----------|",
        f"| Всего сигналов | {total} |",
    ]
    for st, cnt in sorted(by_status.items(), key=lambda x: -x[1]):
        lines.append(f"| status `{st}` | {cnt} |")
    lines.extend(
        [
            f"| Сопоставлено с реальной сделкой | {executed} |",
            f"| Вирт. TP (skipped/virtual) | {virt_tp} |",
            f"| Вирт. SL (skipped/virtual) | {virt_sl} |",
            "",
            "## Структура карты (JSONL)",
            "",
            "Поля: `entry_params` (индикаторы, стакан), `price_movement` (MFE/MAE, свечи),",
            "`virtual_sl_tp`, `virtual_trailing` (если бы трейлинг работал), `real_trade` (+/−).",
            "",
            "## Последние 15 сигналов",
            "",
        ]
    )
    for m in list(maps)[-15:]:
        pnl = m.real_trade.get("pnl_usdt", "")
        virt = m.virtual_sl_tp.get("outcome", "—")
        trail = m.virtual_trailing.get("would_exit", "—")
        lines.append(
            f"- `{m.ledger_id}` **{m.symbol}** {m.side} `{m.status}` "
            f"conf={m.confidence:.2f} virt={virt} trail={trail} real_pnl={pnl}"
        )
    lines.append("")
    return "\n".join(lines)


def write_signal_maps_artifacts(
    maps: Sequence[SignalExecutionMap],
    out_dir: Path,
    *,
    source_label: str,
    hours: float,
) -> Tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    jsonl_path = out_dir / SIGNAL_MAPS_JSONL
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for m in maps:
            row = m.to_dict()
            row["generated_at"] = generated_at
            row["source_label"] = source_label
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    md_path = out_dir / SIGNAL_MAPS_MD
    md_path.write_text(
        build_signal_maps_markdown(
            maps, source_label=source_label, hours=hours, generated_at=generated_at
        ),
        encoding="utf-8",
    )
    return jsonl_path, md_path
