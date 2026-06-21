#!/usr/bin/env python3
"""Точка входа PRD Unified Agent (оркестратор + Telegram-кнопки)."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config
from prd_agent.config_validate import validate_config_data
from prd_agent.engine.orchestrator import UnifiedOrchestrator
from prd_agent.ops.log_redact import (
    RedactSecretsFilter,
    apply_log_safety,
    redacting_formatter,
)
from prd_agent.telegram.control_bot import ControlBot


def setup_logging() -> None:
    log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    apply_log_safety()

    console = logging.StreamHandler()
    console.setFormatter(redacting_formatter(log_fmt))
    console.addFilter(RedactSecretsFilter())
    root.addHandler(console)

    log_path = ROOT / "bot.log"
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(redacting_formatter(log_fmt))
    file_handler.addFilter(RedactSecretsFilter())
    root.addHandler(file_handler)


async def async_main() -> None:
    setup_logging()
    log = logging.getLogger("prd_agent")
    cfg = load_config(ROOT / "config.yaml")
    ok, cfg_errors = validate_config_data(cfg)
    if not ok:
        for err in cfg_errors:
            log.error("config: %s", err)
        raise SystemExit("config.yaml не прошёл проверку — исправьте и перезапустите бота")
    orch = UnifiedOrchestrator(cfg)
    tg = ControlBot(cfg, orch)

    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()

    def _request_shutdown() -> None:
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            pass

    tg_poll = bool(cfg.get("telegram", {}).get("control_polling_enabled", True))
    poll_task = None
    if tg_poll:
        poll_fn = getattr(tg, "run_polling", None) or getattr(tg, "run_polling_sync", None)
        if poll_fn is None:
            log.error("ControlBot: нет run_polling / run_polling_sync")
        else:
            apply_log_safety()
            poll_task = asyncio.create_task(poll_fn())
            apply_log_safety()
    else:
        log.info(
            "Telegram polling кнопок отключён (control_polling_enabled=false). "
            "Торговля и уведомления работают."
        )
    if cfg.get("trading", {}).get("auto_start", True):
        asyncio.create_task(orch.start())
        log.info("Торговый цикл запущен автоматически (trading.auto_start=true)")
    try:
        await shutdown.wait()
    finally:
        orch.stop()
        if poll_task:
            await tg.stop()
            try:
                await asyncio.wait_for(poll_task, timeout=25.0)
            except asyncio.TimeoutError:
                log.warning("Telegram polling: таймаут остановки, cancel")
                poll_task.cancel()
                try:
                    await poll_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.warning("poll_task end: %s", exc)
        await orch.close()


if __name__ == "__main__":
    asyncio.run(async_main())
