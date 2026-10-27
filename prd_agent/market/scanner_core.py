"""
Выделенное ядро исполнения циклов MARKET SCANNER и SPIKE SCANNER (prd_agent/market/scanner_core.py).

Позволяет вызывать и тестировать верхнеуровневый алгоритм сканирования импульсов (15m SPIKE)
и структурных сетапов (MARKET SCANNER) в пакете prd_agent без дублирования логики.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, List

logger = logging.getLogger("prd_agent.market.scanner_core")


def looks_like_usdt_trade_symbol(symbol: str) -> bool:
    sym = str(symbol or "").upper()
    if not sym.endswith("USDT") or len(sym) < 6:
        return False
    base = sym[:-4]
    return base.isalnum()


async def execute_spike_scan_once(agent: Any) -> List[Any]:
    """Верхнеуровневый проход SPIKE SCANNER (15m скальп) с межпроцессным локом."""
    if not getattr(agent, "spike_scalp_enabled", False):
        return []
    lock_fn = getattr(agent, "_market_scan_cross_process_lock", None)
    if lock_fn is None and hasattr(agent, "run_spike_scan_once"):
        return list(await agent.run_spike_scan_once() or [])
    if lock_fn is None:
        return []
    with lock_fn(
        label="Spike scan",
        lock_filename=".spike_scan.lock",
    ) as acquired:
        if not acquired:
            return []
        return await execute_spike_scan_locked(agent)


async def execute_spike_scan_locked(agent: Any) -> List[Any]:
    """Внутренний цикл SPIKE SCANNER (под межпроцессным локом)."""
    await agent._ensure_execution()
    assert agent.bybit is not None
    await agent._timesfm_resolve_pending()
    await agent._evaluate_spike_pullback_watchlist()
    valid_symbols = await agent._get_valid_symbols()
    tickers = await agent.bybit.get_tickers()
    cfg = agent._spike_scalp_cfg
    candidates = []
    for ticker in tickers:
        symbol = str(ticker.get("symbol", "")).upper()
        if (
            not symbol.endswith("USDT")
            or symbol not in valid_symbols
            or symbol in agent.market_scanner_blacklist
            or not looks_like_usdt_trade_symbol(symbol)
        ):
            continue
        turnover = agent._ticker_turnover_usdt(ticker)
        if turnover >= cfg.min_24h_volume_usdt:
            candidates.append((turnover, ticker))
    candidates.sort(key=lambda item: item[0], reverse=True)

    setups: List[Any] = []
    for _, ticker in candidates[: max(1, cfg.max_symbols)]:
        try:
            setup = await agent._analyze_spike_setup(ticker)
            if setup is None:
                continue
            setups.append(setup)
        except Exception as exc:
            logger.warning("Spike scan failed for %s: %s", ticker.get("symbol"), exc)
    setups.sort(key=lambda item: (item.score, item.range_pct), reverse=True)

    notified = 0
    for setup in setups[: max(1, agent.spike_scalp_top_n)]:
        if agent._spike_scanner_on_cooldown(setup.symbol):
            continue
        agent._mark_spike_scanner_notified(setup)
        agent._save_state()
        agent._notify_spike_setup(setup)
        agent._append_market_setup(setup)
        notified += 1
        await agent._try_execute_market_setup(setup)
    if setups:
        agent._save_state()
    logger.info(
        "Spike scan done: candidates=%s setups=%s notified=%s spike_exec_eff=%s",
        len(candidates),
        len(setups),
        notified,
        agent._effective_market_scanner_auto_execute() and agent.spike_scalp_auto_execute,
    )
    return setups


async def evaluate_pending_market_setups(agent: Any) -> None:
    """Оценка отложенных сетапов MARKET SCANNER для адаптивного обучения порогов."""
    if not getattr(agent, "market_scanner_learning_enabled", False):
        return
    pending = agent.state.get("pending_market_setups", [])
    if not isinstance(pending, list) or not pending:
        return
    await agent._ensure_execution()
    assert agent.bybit is not None
    now = datetime.now(timezone.utc)
    keep: list[dict[str, Any]] = []
    events = agent.state.setdefault("market_scanner_learning_events", [])
    if not isinstance(events, list):
        events = []
        agent.state["market_scanner_learning_events"] = events
    changed = False

    for item in pending:
        if not isinstance(item, dict):
            changed = True
            continue
        setup = item.get("setup", {})
        if not isinstance(setup, dict):
            changed = True
            continue
        symbol = str(setup.get("symbol", "")).upper()
        if not symbol:
            changed = True
            continue
        valid_symbols = await agent._get_valid_symbols()
        if symbol not in valid_symbols:
            logger.info("Market learning skipped invalid symbol: %s", symbol)
            changed = True
            continue
        try:
            expires_at = datetime.fromisoformat(str(item.get("expires_at", "")))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except Exception:
            expires_at = now
        try:
            current_price = await agent.bybit.get_price(symbol)
        except Exception as exc:
            logger.warning("Market learning price check failed for %s: %s", symbol, exc)
            keep.append(item)
            continue
        expired = now >= expires_at
        outcome = agent._market_setup_outcome(setup, current_price, expired)
        if not outcome:
            keep.append(item)
            continue
        event = {
            "checked_at": now.isoformat(),
            "id": item.get("id", ""),
            "symbol": symbol,
            "scenario": setup.get("scenario", ""),
            "entry_price": setup.get("price"),
            "current_price": current_price,
            "target": setup.get("target"),
            "invalidation": setup.get("invalidation"),
            "score": setup.get("score"),
            "range_pct": setup.get("range_pct"),
            "atr_pct": setup.get("atr_pct"),
            "volume_ratio": setup.get("volume_ratio"),
            "outcome": outcome,
            "filters": {
                "min_score_to_notify": agent.market_scanner_min_score_to_notify,
                "min_volume_ratio": agent.market_scanner_min_volume_ratio,
                "max_range_pct": agent.market_scanner_max_range_pct,
                "max_atr_pct": agent.market_scanner_max_atr_pct,
            },
        }
        events.append(event)
        agent._append_market_scanner_event(event)
        changed = True

    if changed or len(keep) != len(pending):
        agent.state["pending_market_setups"] = keep[-max(1, agent.market_scanner_learning_window * 3) :]
        agent.state["market_scanner_learning_events"] = events[-max(50, agent.market_scanner_learning_window * 4) :]
        agent._recalculate_market_scanner_adaptive_filters()
        agent._save_state()


async def execute_market_scan_once(agent: Any) -> List[Any]:
    """Верхнеуровневый проход MARKET SCANNER с межпроцессным локом."""
    if not getattr(agent, "market_scanner_enabled", False):
        return []
    lock_fn = getattr(agent, "_market_scan_cross_process_lock", None)
    if lock_fn is None and hasattr(agent, "run_market_scan_once"):
        return list(await agent.run_market_scan_once() or [])
    if lock_fn is None:
        return []
    with lock_fn() as acquired:
        if not acquired:
            return []
        return await execute_market_scan_locked(agent)


async def execute_market_scan_locked(agent: Any) -> List[Any]:
    """Внутренний цикл MARKET SCANNER (под межпроцессным локом)."""
    agent._reload_market_scanner_notified_from_disk()
    await agent._ensure_execution()
    assert agent.bybit is not None
    await agent._timesfm_resolve_pending()
    await agent._evaluate_pending_market_setups()
    await agent._evaluate_bos_watchlist()
    valid_symbols = await agent._get_valid_symbols()
    tickers = await agent.bybit.get_tickers()
    candidates = []
    for ticker in tickers:
        symbol = str(ticker.get("symbol", "")).upper()
        if (
            not symbol.endswith("USDT")
            or symbol not in valid_symbols
            or symbol in agent.market_scanner_blacklist
            or not looks_like_usdt_trade_symbol(symbol)
        ):
            continue
        turnover = agent._ticker_turnover_usdt(ticker)
        if turnover >= agent.market_scanner_min_24h_volume_usdt:
            candidates.append((turnover, ticker))
    candidates.sort(key=lambda item: item[0], reverse=True)

    setups: List[Any] = []
    for _, ticker in candidates[: max(1, agent.market_scanner_max_symbols)]:
        try:
            setup = await agent._analyze_market_setup(ticker)
            if setup is None:
                continue
            agent._append_market_setup(setup)
            setups.append(setup)
        except Exception as exc:
            logger.warning("Market scan failed for %s: %s", ticker.get("symbol"), exc)
    setups.sort(key=lambda item: item.score, reverse=True)

    notified = 0
    for setup in setups[: max(1, agent.market_scanner_top_n)]:
        if agent._market_scanner_on_cooldown(setup.symbol, setup.scenario):
            continue
        agent._mark_market_scanner_notified(setup)
        agent._save_state()
        agent._notify_market_setup(setup)
        agent._track_market_setup_for_learning(setup)
        if agent._should_add_to_bos_watchlist(setup):
            agent._add_to_bos_watchlist(setup)
        notified += 1
        await agent._try_execute_market_setup(setup)
    await agent._check_scanner_reversals_against_open_positions(setups)
    if setups or notified:
        agent._save_state()
    if setups and notified == 0:
        logger.info(
            "Market scan: %s сетапов, в Telegram 0 (cooldown %ss или уже видели)",
            len(setups),
            int(agent.market_scanner_symbol_cooldown_sec),
        )
    logger.info(
        "Market scan done: candidates=%s setups=%s notified=%s "
        "| active_thresholds score>=%s vol_ratio>=%.2f range%%<=%.2f atr%%<=%.2f "
        "| learning_events=%s | scanner_exec_eff=%s pause_all=%s",
        len(candidates),
        len(setups),
        notified,
        agent.market_scanner_min_score_to_notify,
        agent.market_scanner_min_volume_ratio,
        agent.market_scanner_max_range_pct,
        agent.market_scanner_max_atr_pct,
        len(agent.state.get("market_scanner_learning_events", [])),
        agent._effective_market_scanner_auto_execute(),
        bool(agent._runtime_controls_dict().get("pause_all_execution")),
    )
    return setups
