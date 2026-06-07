"""
Мост к MARKET SCANNER (логика в scripts/telegram_signal_agent.py).
Нужен, когда работает только trading_bot (run_unified), без отдельного telegram_signal_agent.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("prd_agent.market_scanner")


def market_scanner_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    mc = dict(cfg.get("market_scanner") or {})
    agent = cfg.get("telegram_signal_agent") or {}
    if not isinstance(agent, dict):
        agent = {}
    mc.setdefault("enabled", bool(agent.get("market_scanner_enabled", True)))
    mc.setdefault("interval_sec", float(agent.get("market_scanner_interval_sec", 600)))
    mc.setdefault("run_loop_in_unified_bot", True)
    return mc


def unified_should_run_market_scan(cfg: Dict[str, Any]) -> bool:
    mc = market_scanner_cfg(cfg)
    return bool(mc.get("enabled")) and bool(mc.get("run_loop_in_unified_bot", True))


async def run_market_scan_once(repo: Path, cfg: Dict[str, Any] | None = None) -> List[Any]:
    """Один проход сканера → уведомления MARKET SCANNER в Telegram."""
    if cfg is not None and not unified_should_run_market_scan(cfg):
        return []
    try:
        from scripts.telegram_signal_agent import TelegramSignalAgent
    except ImportError as exc:
        logger.warning("Market scanner: не импортируется telegram_signal_agent: %s", exc)
        return []

    agent = TelegramSignalAgent(repo.resolve())
    if not agent.market_scanner_enabled:
        logger.debug("Market scanner: отключён в telegram_signal_agent.market_scanner_enabled")
        return []
    if not agent.telegram_notify:
        logger.warning(
            "Market scanner: telegram_notify=false — уведомления в Telegram не отправятся"
        )
    setups = await agent.run_market_scan_once()
    return setups or []
