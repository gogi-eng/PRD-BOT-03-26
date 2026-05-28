"""Pump/Dump scout: daily feature learning + 15m execution-only signal feed."""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("prd_agent.copy_mirror.pump_dump")


@dataclass
class FeatureProfile:
    side: str
    vol_ratio_median: float
    atr_pct_median: float
    oi_delta_median: float
    abs_funding_median: float
    event_count: int


class PumpDumpScout:
    """Learns from >=X% moves and emits compact inbox signals every 15m."""

    def __init__(self, cfg: Dict[str, Any], market_adapter):
        self.cfg = cfg
        self.market = market_adapter
        p = cfg.get("copy_mirror", {}).get("pump_dump_agent", {})

        self.enabled = bool(p.get("enabled", True))
        self.daily_rebuild_hour_utc = int(p.get("daily_rebuild_hour_utc", 0))
        self.scan_every_minutes = int(p.get("scan_every_minutes", 15))
        self.move_threshold_pct = float(p.get("move_threshold_pct", 5.0))
        self.lookback_candles = int(p.get("lookback_candles", 4))  # 4x15m=1h
        self.symbol_limit = int(p.get("symbol_limit", 120))
        self.min_turnover_usdt = float(p.get("min_turnover_usdt", 1_000_000))
        self.score_threshold = float(p.get("score_threshold", 0.60))
        self.cooldown_minutes = int(p.get("signal_cooldown_minutes", 180))
        self.inbox_path = Path(
            str(
                p.get(
                    "target_inbox_path",
                    "/root/PRD-BOT-ALL/reports/telegram_signals/signals_inbox.jsonl",
                )
            )
        )
        state_path = p.get("state_file", "data/copy_mirror/pump_dump_state.json")
        self.state_path = Path(cfg["_root"]) / state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        self._profile_up: Optional[FeatureProfile] = None
        self._profile_down: Optional[FeatureProfile] = None
        self._last_scan_ts = 0.0
        self._last_rebuild_day = ""
        self._last_signal_ts: Dict[str, float] = {}
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._last_rebuild_day = str(raw.get("last_rebuild_day", ""))
            self._last_signal_ts = {
                str(k): float(v) for k, v in (raw.get("last_signal_ts") or {}).items()
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            self._last_rebuild_day = ""
            self._last_signal_ts = {}

    def _save_state(self) -> None:
        payload = {
            "last_rebuild_day": self._last_rebuild_day,
            "last_signal_ts": self._last_signal_ts,
            "profile_up": asdict(self._profile_up) if self._profile_up else None,
            "profile_down": asdict(self._profile_down) if self._profile_down else None,
            "updated_ts": time.time(),
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _ema(vals: List[float], n: int) -> List[float]:
        if not vals:
            return []
        k = 2.0 / (n + 1)
        out = [vals[0]]
        for v in vals[1:]:
            out.append(v * k + out[-1] * (1 - k))
        return out

    @staticmethod
    def _atr_pct(c: List[float], h: List[float], l: List[float], n: int = 14) -> List[float]:
        tr: List[float] = []
        for i in range(len(c)):
            if i == 0:
                tr.append(h[i] - l[i])
            else:
                tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
        out = [0.0] * len(c)
        if len(c) < n:
            return out
        a = sum(tr[:n]) / n
        out[n - 1] = a / c[n - 1] * 100 if c[n - 1] > 0 else 0.0
        for i in range(n, len(c)):
            a = (a * (n - 1) + tr[i]) / n
            out[i] = a / c[i] * 100 if c[i] > 0 else 0.0
        return out

    async def _symbol_universe(self) -> List[str]:
        tickers = await self.market.get_tickers()
        rows = []
        for t in tickers:
            sym = str(t.get("symbol", "")).upper()
            if not sym.endswith("USDT"):
                continue
            turnover = float(t.get("turnover24h", 0) or 0)
            if turnover < self.min_turnover_usdt:
                continue
            rows.append((sym, turnover))
        rows.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in rows[: self.symbol_limit]]

    async def _kline_15m(self, symbol: str, limit: int = 200) -> List[Dict[str, float]]:
        kl = await self.market.get_klines(symbol, interval="15", limit=limit)
        out: List[Dict[str, float]] = []
        for r in kl:
            ts = int(float(r.get("start", r.get("timestamp", 0)) or 0))
            out.append(
                {
                    "ts": ts,
                    "o": float(r.get("open", 0) or 0),
                    "h": float(r.get("high", 0) or 0),
                    "l": float(r.get("low", 0) or 0),
                    "c": float(r.get("close", 0) or 0),
                    "v": float(r.get("volume", 0) or 0),
                }
            )
        out.sort(key=lambda x: x["ts"])
        return out

    async def _oi_delta_pct(self, symbol: str) -> float:
        rows = await self.market.get_open_interest_history(symbol, interval="15min", limit=8)
        pts: List[Tuple[int, float]] = []
        for r in rows:
            try:
                pts.append((int(r.get("timestamp", 0) or 0), float(r.get("openInterest", 0) or 0)))
            except (TypeError, ValueError):
                continue
        pts.sort()
        if len(pts) < 4 or pts[0][1] <= 0:
            return 0.0
        prev = pts[-4][1]
        cur = pts[-1][1]
        if prev <= 0:
            return 0.0
        return (cur / prev - 1.0) * 100

    async def _funding_abs(self, symbol: str) -> float:
        fr = await self.market.get_funding_rate(symbol)
        if not fr:
            return 0.0
        return abs(float(fr.get("funding_rate", 0) or 0)) * 100

    def _move_pct(self, c: List[float], i0: int, i1: int) -> float:
        if i0 < 0 or i1 >= len(c) or c[i0] <= 0:
            return 0.0
        return (c[i1] / c[i0] - 1.0) * 100.0

    async def rebuild_daily_profile(self) -> None:
        syms = await self._symbol_universe()
        up_feats: List[Tuple[float, float, float, float]] = []
        dn_feats: List[Tuple[float, float, float, float]] = []

        for sym in syms:
            kl = await self._kline_15m(sym, limit=220)
            if len(kl) < 80:
                continue
            c = [x["c"] for x in kl]
            h = [x["h"] for x in kl]
            l = [x["l"] for x in kl]
            v = [x["v"] for x in kl]
            atrp = self._atr_pct(c, h, l, n=14)
            oi_d = await self._oi_delta_pct(sym)
            abs_f = await self._funding_abs(sym)

            for i in range(20, len(kl) - self.lookback_candles):
                mv = self._move_pct(c, i, i + self.lookback_candles)
                if abs(mv) < self.move_threshold_pct:
                    continue
                v_prev = v[max(0, i - 8) : i]
                if not v_prev:
                    continue
                v_ratio = v[i] / max(sum(v_prev) / len(v_prev), 1e-9)
                feat = (v_ratio, atrp[i], oi_d, abs_f)
                if mv > 0:
                    up_feats.append(feat)
                else:
                    dn_feats.append(feat)

        def mk(side: str, feats: List[Tuple[float, float, float, float]]) -> Optional[FeatureProfile]:
            if len(feats) < 3:
                return None
            return FeatureProfile(
                side=side,
                vol_ratio_median=median(x[0] for x in feats),
                atr_pct_median=median(x[1] for x in feats),
                oi_delta_median=median(x[2] for x in feats),
                abs_funding_median=median(x[3] for x in feats),
                event_count=len(feats),
            )

        self._profile_up = mk("Buy", up_feats)
        self._profile_down = mk("Sell", dn_feats)
        self._last_rebuild_day = datetime.now(timezone.utc).date().isoformat()
        self._save_state()
        logger.info(
            "pump/dump profile rebuilt: up=%s down=%s symbols=%d",
            self._profile_up.event_count if self._profile_up else 0,
            self._profile_down.event_count if self._profile_down else 0,
            len(syms),
        )

    def _score(self, feat: Dict[str, float], p: FeatureProfile) -> float:
        parts = []
        parts.append(min(feat["vol_ratio"] / max(p.vol_ratio_median, 1e-6), 2.0))
        parts.append(min(feat["atr_pct"] / max(p.atr_pct_median, 1e-6), 2.0))
        if p.oi_delta_median != 0:
            parts.append(min(max(feat["oi_delta"] / p.oi_delta_median, 0.0), 2.0))
        else:
            parts.append(0.5)
        parts.append(min(feat["abs_funding"] / max(p.abs_funding_median, 1e-6), 2.0))
        return max(0.0, min(sum(parts) / (2.0 * len(parts)), 1.0))

    def _signal_levels(self, side: str, price: float, atr_pct: float) -> Tuple[float, float]:
        dist = max(price * max(atr_pct / 100.0, 0.004), price * 0.004)
        if side == "Buy":
            return price - dist, price + dist * 2.1
        return price + dist, price - dist * 2.1

    def _cooldown_ok(self, symbol: str, side: str) -> bool:
        key = f"{symbol}:{side}"
        last = self._last_signal_ts.get(key, 0.0)
        return (time.time() - last) >= self.cooldown_minutes * 60

    def _mark_signal(self, symbol: str, side: str) -> None:
        self._last_signal_ts[f"{symbol}:{side}"] = time.time()
        self._save_state()

    def _write_inbox_signal(
        self, symbol: str, side: str, confidence: float, entry: float, stop_loss: float, take_profit: float, score: float
    ) -> None:
        self.inbox_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "id": f"pumpdump-{symbol}-{side}-{int(time.time())}",
            "symbol": symbol,
            "side": side,
            "confidence": round(confidence, 3),
            "entry": round(entry, 8),
            "stop_loss": round(stop_loss, 8),
            "take_profit": round(take_profit, 8),
            "channel": "mirror_pump_dump_agent",
            "reason": f"pattern_score={score:.2f} fast-exec",
            "source": "mirror_pump_dump_agent",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.inbox_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def _scan_once(self) -> None:
        syms = await self._symbol_universe()
        for sym in syms:
            kl = await self._kline_15m(sym, limit=80)
            if len(kl) < 30:
                continue
            c = [x["c"] for x in kl]
            h = [x["h"] for x in kl]
            l = [x["l"] for x in kl]
            v = [x["v"] for x in kl]
            atrp = self._atr_pct(c, h, l, n=14)

            i = len(kl) - 2  # closed candle
            if i < 20:
                continue
            v_prev = v[max(0, i - 8) : i]
            if not v_prev:
                continue
            feat = {
                "vol_ratio": v[i] / max(sum(v_prev) / len(v_prev), 1e-9),
                "atr_pct": atrp[i],
                "oi_delta": await self._oi_delta_pct(sym),
                "abs_funding": await self._funding_abs(sym),
            }
            move = self._move_pct(c, max(0, i - self.lookback_candles), i)
            side = "Buy" if move >= 0 else "Sell"
            profile = self._profile_up if side == "Buy" else self._profile_down
            if profile is None:
                continue
            if not self._cooldown_ok(sym, side):
                continue
            score = self._score(feat, profile)
            if score < self.score_threshold:
                continue
            entry = c[i]
            sl, tp = self._signal_levels(side, entry, feat["atr_pct"])
            conf = min(0.92, max(0.70, 0.62 + score * 0.35))
            self._write_inbox_signal(sym, side, conf, entry, sl, tp, score)
            self._mark_signal(sym, side)
            logger.info(
                "pump/dump signal -> inbox %s %s score=%.2f move=%.2f%%",
                sym,
                side,
                score,
                move,
            )

    async def tick(self) -> None:
        if not self.enabled:
            return
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        if (
            self._last_rebuild_day != day
            and now.hour >= self.daily_rebuild_hour_utc
        ) or (self._profile_up is None and self._profile_down is None):
            await self.rebuild_daily_profile()

        if (time.time() - self._last_scan_ts) < self.scan_every_minutes * 60:
            return
        self._last_scan_ts = time.time()
        await self._scan_once()
