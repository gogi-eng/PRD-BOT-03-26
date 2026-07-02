"""
Маршрутизатор: multi-agent (локально) + Telegram inbox + киты/новости.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from prd_agent.entry.entry_engine_bridge import EntryEngineBridge, should_apply_zone_entry
from prd_agent.signals.telegram_inbox import TelegramInbox
from prd_agent.signals.types import UnifiedSignal
from prd_agent.signals.whale_news_agent import WhaleNewsAgent, MacroSignal


SOURCE_WEIGHT = {
    "own_multi_agent": 1.0,
    "ta_volatility": 0.92,
    "telegram": 0.95,
    "whale_liquidation": 0.85,
    "whale_oi": 0.75,
    "macro_news": 0.55,
    "coinugget_style": 0.78,
}


logger = logging.getLogger("prd_agent.signals")


class SignalRouter:
    def __init__(self, cfg: Dict[str, Any], store_dir: Path):
        self.cfg = cfg
        self.root = Path(cfg["_root"])
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._queue_file = self.store_dir / "signal_queue.jsonl"
        sig = cfg.get("signals", {}) if isinstance(cfg.get("signals"), dict) else {}
        t = cfg.get("trading", {})
        self._min_conf = float(t.get("min_signal_confidence", 0.62))
        self._min_own_conf = float(t.get("min_own_agent_confidence", 0.28))
        self._min_tg_conf = float(
            sig.get("min_telegram_confidence", t.get("min_telegram_confidence", self._min_conf))
        )
        self._multi_agent = None
        self._whale = WhaleNewsAgent(cfg) if cfg.get("signals", {}).get("whale_news_enabled", True) else None
        self._ta_vol = None
        if cfg.get("ta_scanner", {}).get("enabled", True):
            from prd_agent.signals.ta_volatility_agent import TAVolatilityAgent

            self._ta_vol = TAVolatilityAgent(cfg)
        self._tg_inbox = (
            TelegramInbox(cfg, self.root)
            if cfg.get("signals", {}).get("telegram_inbox_enabled", True)
            else None
        )
        self._zone_entry = EntryEngineBridge(cfg)
        self._external_sentiment = None
        if cfg.get("external_sentiment", {}).get("enabled", True):
            from prd_agent.signals.external_sentiment_agent import ExternalSentimentAgent

            self._external_sentiment = ExternalSentimentAgent(cfg)
        self._init_own_agents()

    @staticmethod
    def _own_agent_confidence(score: float, outputs: List[Dict[str, Any]]) -> float:
        """Уверенность по согласным агентам (не равна abs(score), иначе всё отсекается порогом 0.62)."""
        if not outputs:
            return min(0.95, abs(score))
        bullish = score > 0
        agreeing = [
            o for o in outputs
            if (float(o.get("signal", 0)) > 0) == bullish and abs(float(o.get("signal", 0))) >= 0.05
        ]
        pool = agreeing if len(agreeing) >= 2 else outputs
        confs = [float(o.get("confidence", 0)) for o in pool]
        avg_conf = sum(confs) / len(confs) if confs else abs(score)
        return min(0.95, max(abs(score), avg_conf))

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

    async def _fetch_klines_pair(
        self, exchange, sym: str, interval: str = "15", htf_interval: str = "240"
    ) -> tuple[str, list, list]:
        klines, htf = await asyncio.gather(
            exchange.get_klines(sym, interval=interval, limit=120),
            exchange.get_klines(sym, interval=htf_interval, limit=120),
        )
        return sym, list(klines or []), list(htf or [])

    async def collect_own_signals(self, exchange, symbols: List[str]) -> List[UnifiedSignal]:
        out: List[UnifiedSignal] = []
        if not self._multi_agent:
            return out

        zone_on = should_apply_zone_entry(
            UnifiedSignal(symbol="", side="Buy", confidence=0.5, source="own_multi_agent"),
            self.cfg,
        )
        # Параллельно: 15m для всех символов; 4h только если zone_entry активен глобально.
        if zone_on:
            pairs = await asyncio.gather(
                *[self._fetch_klines_pair(exchange, sym) for sym in symbols],
                return_exceptions=True,
            )
        else:
            raw = await asyncio.gather(
                *[exchange.get_klines(sym, interval="15", limit=120) for sym in symbols],
                return_exceptions=True,
            )
            pairs = []
            for sym, klines in zip(symbols, raw):
                if isinstance(klines, Exception):
                    pairs.append(klines)
                else:
                    pairs.append((sym, list(klines or []), []))

        for item in pairs:
            if isinstance(item, Exception):
                logger.warning("collect_own_signals klines failed: %s", item)
                continue
            sym, klines, htf = item
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
            conf = self._own_agent_confidence(score, outputs)
            if conf < self._min_own_conf:
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
            if zone_on:
                sig = await self._zone_entry.enrich_signal(
                    sig, exchange=exchange, klines=klines, htf_klines=htf
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
            if conf < self._min_tg_conf:
                continue
            raw_src = str(
                parsed.get("source") or parsed.get("channel") or "telegram"
            ).lower()
            if "agent-world" in raw_src or "agent_world" in raw_src:
                source = "agent_world"
                reason = str(parsed.get("reason") or parsed.get("channel") or source)
            elif "mirror_pump_dump" in raw_src or "pump_dump" in raw_src or "pumpdump" in raw_src:
                source = "mirror_pump_dump_agent"
                reason = str(parsed.get("reason") or parsed.get("channel") or source)
            else:
                source = "telegram"
                reason = str(parsed.get("channel") or "telegram_inbox")
            sig = UnifiedSignal(
                symbol=sym,
                side=side,
                confidence=conf,
                source=source,
                entry=float(parsed.get("entry", 0) or 0),
                stop_loss=float(parsed.get("stop_loss", parsed.get("sl", 0)) or 0),
                take_profit=float(parsed.get("take_profit", parsed.get("tp", 0)) or 0),
                reason=reason,
                raw=parsed,
            )
            out.append(sig)
            self._persist(sig)
        return out

    async def _enrich_with_zone(
        self, exchange, sig: UnifiedSignal
    ) -> UnifiedSignal:
        if not should_apply_zone_entry(sig, self.cfg):
            return sig
        sym, klines, htf = await self._fetch_klines_pair(exchange, sig.symbol)
        return await self._zone_entry.enrich_signal(
            sig, exchange=exchange, klines=klines or [], htf_klines=htf
        )

    async def collect_ta_volatility(self, exchange, symbols: List[str]) -> List[UnifiedSignal]:
        out: List[UnifiedSignal] = []
        if not self._ta_vol:
            return out
        candidates = await self._ta_vol.collect(exchange)
        enrich_tasks = []
        kept: List[UnifiedSignal] = []
        for sig in candidates:
            if sig.confidence < self._min_own_conf:
                continue
            kept.append(sig)
            if should_apply_zone_entry(sig, self.cfg):
                enrich_tasks.append(self._enrich_with_zone(exchange, sig))
            else:
                enrich_tasks.append(None)
        enriched: List[UnifiedSignal] = []
        pending = [t for t in enrich_tasks if t is not None]
        if pending:
            results = await asyncio.gather(*pending, return_exceptions=True)
            ri = 0
            for sig, task in zip(kept, enrich_tasks):
                if task is None:
                    enriched.append(sig)
                else:
                    res = results[ri]
                    ri += 1
                    if isinstance(res, Exception):
                        logger.warning("ta_vol zone enrich %s: %s", sig.symbol, res)
                        enriched.append(sig)
                    else:
                        enriched.append(res)
        else:
            enriched = kept
        for sig in enriched:
            out.append(sig)
            self._persist(sig)
        return out

    async def collect_external_sentiment(self, exchange, symbols: List[str]) -> List[UnifiedSignal]:
        out: List[UnifiedSignal] = []
        if not self._external_sentiment:
            return out
        candidates = await self._external_sentiment.collect(exchange, symbols)
        enrich_tasks = []
        kept: List[UnifiedSignal] = []
        for sig in candidates:
            if sig.confidence < self._min_own_conf:
                continue
            kept.append(sig)
            if should_apply_zone_entry(sig, self.cfg):
                enrich_tasks.append(self._enrich_with_zone(exchange, sig))
            else:
                enrich_tasks.append(None)
        enriched: List[UnifiedSignal] = []
        pending = [t for t in enrich_tasks if t is not None]
        if pending:
            results = await asyncio.gather(*pending, return_exceptions=True)
            ri = 0
            for sig, task in zip(kept, enrich_tasks):
                if task is None:
                    enriched.append(sig)
                else:
                    res = results[ri]
                    ri += 1
                    if isinstance(res, Exception):
                        logger.warning("ext_sentiment zone enrich %s: %s", sig.symbol, res)
                        enriched.append(sig)
                    else:
                        enriched.append(res)
        else:
            enriched = kept
        for sig in enriched:
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
            ma_score = 0.0
            for g in group:
                if g.source == "own_multi_agent":
                    agg = g.raw.get("aggregate") if isinstance(g.raw, dict) else None
                    if agg is not None:
                        ma_score = max(ma_score, abs(float(agg)))
            if sources and all(s in ("own_multi_agent", "ta_volatility") for s in sources):
                min_need = self._min_own_conf
            elif sources and all(s == "telegram" for s in sources):
                min_need = self._min_tg_conf
            else:
                min_need = self._min_conf
            if conf < min_need:
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
                    raw={
                        "sources": sources,
                        "count": len(group),
                        "multi_agent_score": ma_score,
                    },
                )
            )
        merged.sort(key=lambda x: x.confidence, reverse=True)
        return merged

    async def collect_all(self, exchange, symbols: List[str]) -> List[UnifiedSignal]:
        tg = self.collect_telegram_signals()
        gathered = await asyncio.gather(
            self.collect_own_signals(exchange, symbols),
            self.collect_ta_volatility(exchange, symbols),
            self.collect_whale_news(exchange, symbols),
            self.collect_external_sentiment(exchange, symbols),
            return_exceptions=True,
        )
        own: List[UnifiedSignal] = []
        ta: List[UnifiedSignal] = []
        whale: List[UnifiedSignal] = []
        ext: List[UnifiedSignal] = []
        labels = ("own", "ta_vol", "whale", "ext_sentiment")
        for label, res in zip(labels, gathered):
            if isinstance(res, Exception):
                logger.warning("collect_all %s failed: %s", label, res)
                continue
            if label == "own":
                own = res
            elif label == "ta_vol":
                ta = res
            elif label == "whale":
                whale = res
            else:
                ext = res
        from prd_agent.signals.confidence_filter import passes_emit_gate

        merged = self.merge_and_rank(own + ta + tg + whale + ext)
        merged = [s for s in merged if passes_emit_gate(s, self.cfg)]
        if own or ta or tg or whale or ext:
            logger.info(
                "Signals: own=%d ta_vol=%d telegram=%d whale=%d ext=%d → merged=%d",
                len(own),
                len(ta),
                len(tg),
                len(whale),
                len(ext),
                len(merged),
            )
        return merged

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
