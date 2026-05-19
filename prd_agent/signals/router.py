"""
Маршрутизатор: multi-agent (локально) + Telegram inbox + киты/новости.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from prd_agent.signals.telegram_inbox import TelegramInbox
from prd_agent.signals.whale_news_agent import WhaleNewsAgent, MacroSignal


@dataclass
class UnifiedSignal:
    symbol: str
    side: str
    confidence: float
    source: str
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "confidence": self.confidence,
            "source": self.source,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reason": self.reason,
            "created_at": self.created_at,
        }


SOURCE_WEIGHT = {
    "own_multi_agent": 1.0,
    "telegram": 0.95,
    "whale_liquidation": 0.85,
    "whale_oi": 0.75,
    "macro_news": 0.55,
}


class SignalRouter:
    def __init__(self, cfg: Dict[str, Any], store_dir: Path):
        self.cfg = cfg
        self.root = Path(cfg["_root"])
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._queue_file = self.store_dir / "signal_queue.jsonl"
        self._min_conf = float(cfg.get("trading", {}).get("min_signal_confidence", 0.62))
        self._multi_agent = None
        self._whale = WhaleNewsAgent(cfg) if cfg.get("signals", {}).get("whale_news_enabled", True) else None
        self._tg_inbox = (
            TelegramInbox(cfg, self.root)
            if cfg.get("signals", {}).get("telegram_inbox_enabled", True)
            else None
        )
        self._init_own_agents()

    def _init_own_agents(self) -> None:
        if not self.cfg.get("signals", {}).get("own_agents_enabled", True):
            return
        root_s = str(self.root.resolve())
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
        try:
            from agents.multi_agent_manager import MultiAgentManager  # type: ignore

            self._multi_agent = MultiAgentManager()
        except ImportError:
            self._multi_agent = None

    def _persist(self, sig: UnifiedSignal) -> None:
        with self._queue_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(sig.to_dict(), ensure_ascii=False) + "\n")

    async def collect_own_signals(self, exchange, symbols: List[str]) -> List[UnifiedSignal]:
        out: List[UnifiedSignal] = []
        if not self._multi_agent:
            return out
        for sym in symbols:
            klines = await exchange.get_klines(sym, interval="15", limit=120)
            if not klines:
                continue
            df = pd.DataFrame(klines)
            if df.empty or "close" not in df.columns:
                continue
            outputs = self._multi_agent.get_signals(df)
            score = self._multi_agent.aggregate(outputs)
            if abs(score) < 0.12:
                continue
            side = "Buy" if score > 0 else "Sell"
            conf = min(0.95, abs(score))
            if conf < self._min_conf:
                continue
            price = await exchange.get_price(sym)
            atr_pct = 0.005
            if score > 0:
                sl, tp = price * (1 - atr_pct), price * (1 + atr_pct * 2)
            else:
                sl, tp = price * (1 + atr_pct), price * (1 - atr_pct * 2)
            sig = UnifiedSignal(
                symbol=sym,
                side=side,
                confidence=conf,
                source="own_multi_agent",
                entry=price,
                stop_loss=sl,
                take_profit=tp,
                reason=f"multi-agent score={score:.3f}",
                raw={"agent_outputs": outputs, "aggregate": score},
            )
            out.append(sig)
            self._persist(sig)
        return out

    def collect_telegram_signals(self) -> List[UnifiedSignal]:
        out: List[UnifiedSignal] = []
        if not self._tg_inbox:
            return out
        for parsed in self._tg_inbox.poll():
            sym = str(parsed.get("symbol", "")).upper()
            side_raw = str(parsed.get("side", "")).upper()
            if side_raw in ("LONG", "BUY"):
                side = "Buy"
            elif side_raw in ("SHORT", "SELL"):
                side = "Sell"
            else:
                continue
            conf = float(parsed.get("confidence", parsed.get("score", 0.7)))
            if conf < self._min_conf:
                continue
            sig = UnifiedSignal(
                symbol=sym,
                side=side,
                confidence=conf,
                source="telegram",
                entry=float(parsed.get("entry", 0) or 0),
                stop_loss=float(parsed.get("stop_loss", parsed.get("sl", 0)) or 0),
                take_profit=float(parsed.get("take_profit", parsed.get("tp", 0)) or 0),
                reason=str(parsed.get("channel") or "telegram_inbox"),
                raw=parsed,
            )
            out.append(sig)
            self._persist(sig)
        return out

    async def collect_whale_news(self, exchange, symbols: List[str]) -> List[UnifiedSignal]:
        out: List[UnifiedSignal] = []
        if not self._whale:
            return out
        for m in await self._whale.collect(exchange, symbols):
            if m.confidence < self._min_conf:
                continue
            price = await exchange.get_price(m.symbol)
            sig = UnifiedSignal(
                symbol=m.symbol,
                side=m.side,
                confidence=m.confidence,
                source=m.source,
                entry=price,
                reason=m.reason,
                raw=m.raw,
            )
            out.append(sig)
            self._persist(sig)
        return out

    def merge_and_rank(self, signals: List[UnifiedSignal]) -> List[UnifiedSignal]:
        """Объединяет одинаковые symbol+side, усредняет confidence с весами."""
        buckets: Dict[tuple, List[UnifiedSignal]] = {}
        for s in signals:
            key = (s.symbol, s.side)
            buckets.setdefault(key, []).append(s)
        merged: List[UnifiedSignal] = []
        for (sym, side), group in buckets.items():
            w_sum = 0.0
            c_sum = 0.0
            sources: List[str] = []
            reasons: List[str] = []
            entry = sl = tp = 0.0
            for g in group:
                w = SOURCE_WEIGHT.get(g.source, 0.5)
                w_sum += w
                c_sum += g.confidence * w
                sources.append(g.source)
                if g.reason:
                    reasons.append(g.reason)
                if g.entry > 0:
                    entry = g.entry
                if g.stop_loss > 0:
                    sl = g.stop_loss
                if g.take_profit > 0:
                    tp = g.take_profit
            if w_sum <= 0:
                continue
            conf = c_sum / w_sum
            if conf < self._min_conf:
                continue
            merged.append(
                UnifiedSignal(
                    symbol=sym,
                    side=side,
                    confidence=min(0.98, conf),
                    source="hybrid" if len(sources) > 1 else sources[0],
                    entry=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    reason=" | ".join(reasons[:3]),
                    raw={"sources": sources, "count": len(group)},
                )
            )
        merged.sort(key=lambda x: x.confidence, reverse=True)
        return merged

    async def collect_all(self, exchange, symbols: List[str]) -> List[UnifiedSignal]:
        own = await self.collect_own_signals(exchange, symbols)
        tg = self.collect_telegram_signals()
        whale = await self.collect_whale_news(exchange, symbols)
        return self.merge_and_rank(own + tg + whale)

    def recent_signals(self, hours: float = 2) -> List[Dict]:
        if not self._queue_file.exists():
            return []
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        rows: List[Dict] = []
        for line in self._queue_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                if ts.timestamp() >= cutoff:
                    rows.append(row)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        return rows
