"""
AI-менеджер PRD-BOT: анализирует состояние бота и даёт рекомендации по управлению.
Не торгует на бирже — только наблюдение, советы и runtime-флаги через Telegram.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from prd_agent.ai.llm_gateway import chat_async, load_llm_settings
from prd_agent.ops.runtime_controls import load_runtime_controls, runtime_controls_status_text

if TYPE_CHECKING:
    from prd_agent.engine.orchestrator import UnifiedOrchestrator

logger = logging.getLogger("prd_agent.bot_manager")


class BotManagerAgent:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.root = Path(cfg["_root"])
        bm = cfg.get("bot_manager", {}) or {}
        self.enabled = bool(bm.get("enabled", True))
        self.interval_sec = float(bm.get("interval_sec", 3600))
        self.log_tail_lines = int(bm.get("log_tail_lines", 35))
        self._llm = load_llm_settings(cfg)
        self._last_review_at = 0.0

    def _tail_log(self) -> List[str]:
        log_path = self.root / "bot.log"
        if not log_path.exists():
            return []
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return []
        interesting = [
            ln
            for ln in lines[-max(80, self.log_tail_lines * 3) :]
            if any(
                token in ln
                for token in (
                    "ERROR",
                    "Cycle error",
                    "AUTO-STOP",
                    "MARKET SCANNER",
                    "skipped",
                    "Макс. позиций",
                    "agent_world",
                )
            )
        ]
        return interesting[-self.log_tail_lines :] or lines[-self.log_tail_lines :]

    def _supervisor_hint(self) -> str:
        path = self.root / "data" / "supervisor" / "skip_stats.json"
        if not path.exists():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                top = sorted(
                    data.get("skipped_by_reason", {}).items(),
                    key=lambda x: -int(x[1]),
                )[:5]
                if top:
                    return "; ".join(f"{k[:40]}={v}" for k, v in top)
        except Exception:
            pass
        return ""

    async def collect_snapshot(self, orch: "UnifiedOrchestrator") -> Dict[str, Any]:
        balance = await orch.exchange.get_balance()
        positions = await orch.exchange.get_positions()
        risk = orch.risk.snapshot()
        rtc = load_runtime_controls(self.root)
        upnl = sum(float(p.get("unrealisedPnl", 0) or 0) for p in positions)
        return {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "balance_usdt": round(balance, 2),
            "open_positions": len(positions),
            "upnl_usdt": round(upnl, 2),
            "risk": risk,
            "runtime_controls": rtc,
            "runtime_text": runtime_controls_status_text(self.root),
            "log_tail": self._tail_log(),
            "supervisor_skips": self._supervisor_hint(),
            "symbols_watch": list(orch.symbols[:12]),
        }

    def _build_prompt(self, snap: Dict[str, Any]) -> str:
        risk = snap.get("risk") or {}
        log_lines = snap.get("log_tail") or []
        return f"""Ты AI-менеджер торгового бота PRD-BOT на Bybit (не трейдер на бирже).
Задача: проанализировать состояние и дать 4–6 коротких рекомендаций по УПРАВЛЕНИЮ БОТОМ.

Данные:
- Баланс: {snap.get('balance_usdt')} USDT
- Открытых позиций: {snap.get('open_positions')}
- Суммарный uPnL: {snap.get('upnl_usdt')} USDT
- Риск: status={risk.get('status')} blocked={risk.get('blocked')} PnL сегодня UTC={risk.get('pnl_today_usdt')} USDT
- Позиции лимит: {risk.get('open_positions', '?')}/{risk.get('max_positions', '?')}
- Runtime-флаги (Telegram панель):
{snap.get('runtime_text', '')}
- Символы в скане: {', '.join(snap.get('symbols_watch') or [])}
- Топ пропусков супервизора: {snap.get('supervisor_skips') or 'нет данных'}

Последние строки bot.log:
{chr(10).join(log_lines[-20:]) if log_lines else 'лог пуст'}

Формат ответа (русский, HTML допустим <b>):
1) <b>Статус</b> — одно предложение
2) <b>Риски</b> — 1–2 пункта
3) <b>Рекомендации</b> — 2–4 пункта (config/runtime/пауза/лимиты; без «купи монету X»)
4) <b>Кнопки</b> — что нажать в Telegram (/panel): пауза, сканер, отчёт

Запрещено: обещать прибыль, советовать all-in, открывать сделки вручную на бирже."""

    async def run_review(self, orch: "UnifiedOrchestrator") -> str:
        if not self._llm.uses_fcc and not self._llm.openrouter_api_key:
            snap = await self.collect_snapshot(orch)
            return (
                "<b>🤖 Bot Manager</b>\n\n"
                f"{snap.get('runtime_text', '')}\n\n"
                "<i>AI не настроен (OPENROUTER_API_KEY). "
                "Используйте кнопки панели /panel.</i>"
            )
        snap = await self.collect_snapshot(orch)
        prompt = self._build_prompt(snap)
        try:
            text, err = await chat_async(
                self._llm,
                system=(
                    "Ты операционный менеджер алгоритмического бота. "
                    "Только управление ботом: риск, config, пауза, лимиты. Не даёшь торговых сигналов."
                ),
                user=prompt,
                max_tokens=900,
            )
            if err:
                return f"<b>🤖 Bot Manager</b>\n\n⚠️ AI: {err}"
            body = (text or "").strip()
            if not body:
                return "<b>🤖 Bot Manager</b>\n\nПустой ответ AI."
            return f"<b>🤖 Bot Manager</b> ({snap.get('ts_utc', '')[:16]} UTC)\n\n{body}"
        except Exception as exc:
            logger.warning("bot_manager review: %s", exc)
            return f"<b>🤖 Bot Manager</b>\n\n⚠️ Ошибка: {exc}"

    async def maybe_scheduled_review(self, orch: "UnifiedOrchestrator") -> Optional[str]:
        if not self.enabled:
            return None
        now = datetime.now(timezone.utc).timestamp()
        if now - self._last_review_at < self.interval_sec:
            return None
        self._last_review_at = now
        return await self.run_review(orch)
