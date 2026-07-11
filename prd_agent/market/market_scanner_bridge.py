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


def spike_scalp_raw(cfg: Dict[str, Any]) -> Dict[str, Any]:
    mc = cfg.get("market_scanner") if isinstance(cfg.get("market_scanner"), dict) else {}
    agent = cfg.get("telegram_signal_agent") if isinstance(cfg.get("telegram_signal_agent"), dict) else {}
    raw = mc.get("spike_scalp") if isinstance(mc.get("spike_scalp"), dict) else {}
    if not raw and isinstance(agent.get("spike_scalp"), dict):
        raw = agent["spike_scalp"]
    return dict(raw) if isinstance(raw, dict) else {}


def unified_should_run_market_scan(cfg: Dict[str, Any]) -> bool:
    mc = market_scanner_cfg(cfg)
    if not bool(mc.get("bos_scan_enabled", True)):
        return False
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
    try:
        if not agent.market_scanner_enabled:
            logger.debug("Market scanner: отключён в telegram_signal_agent.market_scanner_enabled")
            return []
        if not agent.telegram_notify:
            logger.warning(
                "Market scanner: telegram_notify=false — уведомления в Telegram не отправятся"
            )
        setups = await agent.run_market_scan_once()
        return setups or []
    finally:
        try:
            await agent.close()
        except Exception as exc:
            logger.warning("Market scanner: agent.close(): %s", exc)


def spike_scalp_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    from telegram_agent.pump_dump_spike_scan import SpikeScanConfig

    sc = SpikeScanConfig.from_cfg(cfg)
    return {
        "enabled": sc.enabled,
        "interval_sec": sc.interval_sec,
        "run_loop_in_unified_bot": sc.run_loop_in_unified_bot,
        "run_loop_in_signal_agent": sc.run_loop_in_signal_agent,
    }


def unified_should_run_spike_scan(cfg: Dict[str, Any]) -> bool:
    sc = spike_scalp_cfg(cfg)
    if not sc.get("enabled"):
        return False
    mc = market_scanner_cfg(cfg)
    if not bool(mc.get("enabled")):
        return False
    explicit = sc.get("run_loop_in_unified_bot")
    if explicit is not None:
        return bool(explicit)
    return bool(mc.get("run_loop_in_unified_bot", True))


def signal_agent_should_run_spike_scan(cfg: Dict[str, Any]) -> bool:
    sc = spike_scalp_cfg(cfg)
    if not sc.get("enabled"):
        return False
    mc = market_scanner_cfg(cfg)
    if not bool(mc.get("enabled")):
        return False
    return bool(sc.get("run_loop_in_signal_agent"))


async def run_spike_scan_once(repo: Path, cfg: Dict[str, Any] | None = None) -> List[Any]:
    """Быстрый проход: 15m импульс >= min_move_pct → скальп."""
    if cfg is not None and not unified_should_run_spike_scan(cfg):
        return []
    try:
        from scripts.telegram_signal_agent import TelegramSignalAgent
    except ImportError as exc:
        logger.warning("Spike scanner: не импортируется telegram_signal_agent: %s", exc)
        return []

    agent = TelegramSignalAgent(repo.resolve())
    try:
        if not agent.spike_scalp_enabled:
            return []
        setups = await agent.run_spike_scan_once()
        return setups or []
    finally:
        try:
            await agent.close()
        except Exception as exc:
            logger.warning("Spike scanner: agent.close(): %s", exc)
