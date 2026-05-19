"""Post-signal MFE/MAE checkpoints at fixed horizons (extra Bybit kline reads)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from exchange.bybit_client import BybitClient


@dataclass
class SignalExcursionConfig:
    enabled: bool = False
    horizons_hours: list[float] = field(default_factory=lambda: [4.0, 24.0])
    kline_interval: str = "15"
    kline_max_limit: int = 500
    max_pending: int = 500


def signal_excursion_from_agent_cfg(raw: dict[str, Any] | None) -> SignalExcursionConfig:
    if not isinstance(raw, dict):
        return SignalExcursionConfig(enabled=False)
    hrs = raw.get("horizons_hours") or [4, 24]
    parsed: list[float] = []
    for h in hrs if isinstance(hrs, (list, tuple)) else [4, 24]:
        try:
            parsed.append(float(h))
        except (TypeError, ValueError):
            continue
    if not parsed:
        parsed = [4.0, 24.0]
    return SignalExcursionConfig(
        enabled=bool(raw.get("enabled", False)),
        horizons_hours=parsed,
        kline_interval=str(raw.get("kline_interval", "15") or "15"),
        kline_max_limit=max(50, int(raw.get("kline_max_limit", 500) or 500)),
        max_pending=max(10, int(raw.get("max_pending", 500) or 500)),
    )


def _horizon_key(h: float) -> str:
    if abs(h - int(h)) < 1e-9:
        return str(int(h))
    return f"{h:g}"


def _parse_dt(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def excursion_track_id(source_key: str, message_id: int, symbol: str, side: str) -> str:
    return f"{str(source_key).strip().lower()}:{int(message_id)}:{str(symbol).upper()}:{str(side).upper()}"


def mfe_mae_pct(entry: float, side: str, high: float, low: float) -> tuple[float, float]:
    """MFE/MAE as percent of entry; magnitudes >= 0 (favorable / adverse)."""
    e = max(float(entry), 1e-12)
    side_u = str(side).upper()
    hi, lo = float(high), float(low)
    if side_u == "BUY":
        mfe = max(0.0, (hi - e) / e * 100.0)
        mae = max(0.0, (e - lo) / e * 100.0)
    elif side_u == "SELL":
        mfe = max(0.0, (e - lo) / e * 100.0)
        mae = max(0.0, (hi - e) / e * 100.0)
    else:
        return 0.0, 0.0
    return mfe, mae


def enqueue_signal_excursion(
    state: dict[str, Any],
    *,
    cfg: SignalExcursionConfig,
    track_id: str,
    source: str,
    source_key: str,
    message_id: int,
    symbol: str,
    side: str,
    entry: float,
    created_at: datetime,
    market_regime: str,
) -> None:
    if not cfg.enabled:
        return
    if float(entry) <= 0 or str(side).upper() not in {"BUY", "SELL"} or not str(symbol).strip():
        return
    pending = state.setdefault("pending_signal_excursions", [])
    if not isinstance(pending, list):
        pending = []
        state["pending_signal_excursions"] = pending
    if any(isinstance(x, dict) and str(x.get("id", "")) == track_id for x in pending):
        return
    horizons_meta: dict[str, Any] = {}
    for h in cfg.horizons_hours:
        if h <= 0:
            continue
        due = created_at + timedelta(hours=float(h))
        horizons_meta[_horizon_key(h)] = {"due": due.isoformat(), "done": False}
    if not horizons_meta:
        return
    pending.append(
        {
            "id": track_id,
            "source": source,
            "source_key": str(source_key).strip().lower(),
            "message_id": int(message_id),
            "symbol": str(symbol).upper(),
            "side": str(side).upper(),
            "entry": float(entry),
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "market_regime": str(market_regime or "unknown"),
            "horizons": horizons_meta,
        }
    )
    del pending[:-max(1, cfg.max_pending)]


async def evaluate_signal_excursions(
    state: dict[str, Any],
    *,
    cfg: SignalExcursionConfig,
    bybit: BybitClient,
    valid_symbols: set[str],
    now: datetime,
    jsonl_path: Path,
) -> bool:
    """Evaluate due horizons; append rows to jsonl. Returns True if state changed."""
    if not cfg.enabled:
        return False
    pending = state.get("pending_signal_excursions", [])
    if not isinstance(pending, list) or not pending:
        return False
    now = now.astimezone(timezone.utc)
    keep: list[dict[str, Any]] = []
    changed = False
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    for item in pending:
        if not isinstance(item, dict):
            changed = True
            continue
        symbol = str(item.get("symbol", "")).upper()
        side = str(item.get("side", "")).upper()
        entry = float(item.get("entry", 0) or 0)
        created = _parse_dt(str(item.get("created_at", "")))
        horizons = item.get("horizons", {})
        if not symbol or side not in {"BUY", "SELL"} or entry <= 0 or created is None or not isinstance(horizons, dict):
            changed = True
            continue
        if symbol not in valid_symbols:
            keep.append(item)
            continue

        for h_key, meta in list(horizons.items()):
            if not isinstance(meta, dict) or meta.get("done"):
                continue
            try:
                h = float(h_key)
            except (TypeError, ValueError):
                meta["done"] = True
                changed = True
                continue
            due = _parse_dt(str(meta.get("due", "")))
            if due is None:
                due = created + timedelta(hours=h)
            if now < due:
                continue

            start_ms = int(created.timestamp() * 1000)
            end_ms = int(due.timestamp() * 1000)
            try:
                bars = await bybit.get_klines(
                    symbol,
                    interval=cfg.kline_interval,
                    limit=cfg.kline_max_limit,
                    start=start_ms,
                    end=end_ms,
                )
            except Exception:
                continue
            if not bars:
                continue
            hi = max(float(b.get("high", 0) or 0) for b in bars)
            lo = min(float(b.get("low", 0) or 0) for b in bars)
            if hi <= 0 or lo <= 0:
                continue
            mfe, mae = mfe_mae_pct(entry, side, hi, lo)
            row = {
                "evaluated_at": now.isoformat(),
                "track_id": str(item.get("id", "")),
                "source": str(item.get("source", "")),
                "source_key": str(item.get("source_key", "")),
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "horizon_hours": h,
                "window_start": created.isoformat(),
                "window_end": due.isoformat(),
                "high": hi,
                "low": lo,
                "mfe_pct": round(mfe, 6),
                "mae_pct": round(mae, 6),
                "market_regime": str(item.get("market_regime", "unknown")),
                "bars_used": len(bars),
            }
            with open(jsonl_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            meta["done"] = True
            changed = True

        pending_horizons = [m for m in horizons.values() if isinstance(m, dict)]
        if pending_horizons and all(m.get("done") for m in pending_horizons):
            changed = True
            continue
        keep.append(item)

    new_list = keep[-max(1, cfg.max_pending) :]
    if changed or len(new_list) != len(pending):
        state["pending_signal_excursions"] = new_list
        return True
    return False


def aggregate_excursions_by_channel(jsonl_path: Path, *, max_lines: int = 4000) -> dict[str, dict[str, Any]]:
    """Lightweight tail scan for daily report (avg MFE/MAE per source_key per horizon)."""
    if not jsonl_path.exists():
        return {}
    try:
        lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}
    agg: dict[str, dict[str, Any]] = {}
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        sk = str(row.get("source_key", "") or "").lower()
        if not sk:
            continue
        h = row.get("horizon_hours")
        try:
            hf = float(h)
        except (TypeError, ValueError):
            continue
        hk = _horizon_key(hf)
        node = agg.setdefault(sk, {})
        bucket = node.setdefault(hk, {"n": 0, "mfe": 0.0, "mae": 0.0})
        bucket["n"] = int(bucket["n"]) + 1
        bucket["mfe"] = float(bucket["mfe"]) + float(row.get("mfe_pct", 0) or 0)
        bucket["mae"] = float(bucket["mae"]) + float(row.get("mae_pct", 0) or 0)
    for sk, node in agg.items():
        for hk, b in node.items():
            n = max(1, int(b.get("n", 1)))
            b["avg_mfe_pct"] = round(float(b["mfe"]) / n, 4)
            b["avg_mae_pct"] = round(float(b["mae"]) / n, 4)
            b.pop("mfe", None)
            b.pop("mae", None)
    return agg
