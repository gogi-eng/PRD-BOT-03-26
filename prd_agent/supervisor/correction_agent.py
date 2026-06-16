from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Set, Tuple

logger = logging.getLogger("prd_agent.supervisor.correction_agent")


class CorrectionAgent:
    """Простой корректирующий агент-плагин.

    Этот класс предоставляет точки подключения: async `on_tick` и sync `on_can_enter`.
    Настройка читается из `cfg['correction_agent']` и агент по умолчанию выключен.
    """

    def __init__(self, cfg: Dict[str, Any], data_dir, improver=None) -> None:
        cfg_block = cfg.get("correction_agent") or {}
        self.enabled = bool(cfg_block.get("enabled", False))
        self.cfg = cfg_block
        self.data_dir = data_dir
        self.improver = improver
        # training / persistence
        self._store_dir = Path(self.data_dir) / "correction_agent"
        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            logger.exception("Failed to create correction_agent store dir")
        self.model_path = self._store_dir / "model.json"
        self.train_log = self._store_dir / "train_log.jsonl"
        self._model: Dict[str, Any] = self._load_model()
        self._tick_counter = 0
        self._train_every = int(cfg_block.get("train_every_ticks", 20))

    async def on_tick(self, meta_snap: Dict[str, Any], exchange, bot_symbols: Set[str], cycle_num: int) -> None:
        """Вызывается асинхронно каждый tick после обновления meta.

        По умолчанию ничего не делает — расширяемая точка для логики коррекции.
        """
        if not self.enabled:
            return
        # Пример: можно тут вставить вызов внешнего корректорного сервиса
        logger.debug("CorrectionAgent.on_tick cycle=%s enabled", cycle_num)
        try:
            sample = self._sample_market(exchange, bot_symbols, meta_snap, cycle_num)
            if sample is not None:
                # append sample to training log (JSONL)
                try:
                    with self.train_log.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                except Exception:
                    logger.exception("Failed to write train sample")
            # periodic lightweight training
            self._tick_counter += 1
            if self._tick_counter >= max(1, self._train_every):
                self._tick_counter = 0
                try:
                    self._train_step()
                except Exception:
                    logger.exception("CorrectionAgent training step failed")
        except Exception:
            logger.exception("CorrectionAgent on_tick failed")

    def on_can_enter(self, symbol: str, default_ok: bool, default_reason: str) -> Tuple[bool, str]:
        """Вызывается синхронно при проверке входа `can_enter`.

        Возвращает (ok, reason). По умолчанию возвращает переданные значения.
        """
        if not self.enabled:
            return default_ok, default_reason
        try:
            # Здесь можно поместить локальную логику быстрых проверок
            logger.debug("CorrectionAgent.on_can_enter %s => %s", symbol, default_ok)
            sym = str(symbol or "").upper()
            # consult lightweight model: if symbol has high risk score, veto entries
            try:
                score = float(self._model.get("symbols", {}).get(sym, {}).get("risk_score", 0.0))
            except Exception:
                score = 0.0
            threshold = float(self.cfg.get("veto_risk_threshold", 0.8))
            if default_ok and score >= threshold:
                reason = f"correction_agent: высокий риск по историке (score={score:.2f})"
                return False, reason
            # if default denied, agent may only clarify reason
            return default_ok, default_reason
        except Exception:
            logger.exception("CorrectionAgent on_can_enter failed")
            return default_ok, default_reason

    def _load_model(self) -> Dict[str, Any]:
        if not self.model_path.exists():
            return {"symbols": {}}
        try:
            return json.loads(self.model_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load correction_agent model")
            return {"symbols": {}}

    def _save_model(self) -> None:
        try:
            self.model_path.write_text(json.dumps(self._model, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("Failed to save correction_agent model")

    def _sample_market(self, exchange, bot_symbols: Set[str], meta_snap: Dict[str, Any], cycle_num: int) -> Dict[str, Any]:
        """Собирает лёгкие признаки по рынку/позициям — не блокирует исполнение при ошибках."""
        ts = int(time.time())
        sample: Dict[str, Any] = {"ts": ts, "cycle": cycle_num, "symbols": {}, "meta": {"mode": meta_snap.get("mode")}}
        symbols = list(bot_symbols or [])
        # fallback: try to get exchange.symbols
        if not symbols:
            try:
                symbols = list(getattr(exchange, "symbols", []) or [])
            except Exception:
                symbols = []
        # limit samples to a reasonable number
        max_symbols = int(self.cfg.get("sample_max_symbols", 40))
        symbols = symbols[:max_symbols]
        for s in symbols:
            sym = str(s).upper()
            try:
                ticker = None
                if hasattr(exchange, "fetch_ticker"):
                    try:
                        ticker = exchange.fetch_ticker(sym)
                    except Exception:
                        ticker = None
                if not ticker and hasattr(exchange, "fetch_tickers"):
                    try:
                        allt = exchange.fetch_tickers([sym])
                        ticker = allt.get(sym)
                    except Exception:
                        ticker = None
                bid = float(ticker.get("bid") or 0) if ticker else 0.0
                ask = float(ticker.get("ask") or 0) if ticker else 0.0
                last = float(ticker.get("last") or 0) if ticker else 0.0
                vol = float(ticker.get("baseVolume") or ticker.get("volume") or 0) if ticker else 0.0
                spread_pct = ((ask - bid) / ask) * 100 if ask and bid else 0.0
                sample["symbols"][sym] = {"bid": bid, "ask": ask, "last": last, "vol": vol, "spread_pct": spread_pct}
            except Exception:
                logger.debug("correction_agent: failed sample for %s", s)
                continue
        # try to get open positions/orders counts
        try:
            opens = 0
            if hasattr(exchange, "fetch_open_orders"):
                try:
                    opens = len(exchange.fetch_open_orders())
                except Exception:
                    opens = 0
            sample["open_orders_count"] = opens
        except Exception:
            sample["open_orders_count"] = 0
        return sample

    def _train_step(self) -> None:
        """Простейшая агрегирующая 'тренировка': считает средний spread/vol и формирует risk_score per symbol."""
        if not self.train_log.exists():
            return
        per_sym: Dict[str, list] = {}
        try:
            with self.train_log.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    symbols = row.get("symbols", {}) or {}
                    for sym, data in symbols.items():
                        per_sym.setdefault(sym, []).append(data)
        except Exception:
            logger.exception("Failed to read train log")
            return
        model_symbols: Dict[str, Any] = {}
        for sym, records in per_sym.items():
            try:
                spreads = [float(r.get("spread_pct") or 0.0) for r in records]
                vols = [float(r.get("vol") or 0.0) for r in records]
                avg_spread = mean(spreads) if spreads else 0.0
                avg_vol = mean(vols) if vols else 0.0
                n = len(records)
                # simple heuristic: high spread and low volume → higher risk
                norm_spread = min(1.0, avg_spread / max(0.0001, float(self.cfg.get("spread_norm", 0.5))))
                norm_vol = 1.0 - min(1.0, avg_vol / max(0.0001, float(self.cfg.get("vol_norm", 1000.0))))
                risk = min(1.0, max(0.0, (0.6 * norm_spread + 0.4 * norm_vol)))
                model_symbols[sym] = {"risk_score": round(risk, 3), "n": n, "avg_spread": avg_spread, "avg_vol": avg_vol}
            except Exception:
                continue
        self._model = {"symbols": model_symbols, "updated_at": int(time.time())}
        self._save_model()
