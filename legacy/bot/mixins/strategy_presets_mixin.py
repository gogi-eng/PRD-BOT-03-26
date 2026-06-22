"""Impulse vs defensive entry overlays (config: strategy_presets)."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403


class TradingBotStrategyPresetsMixin:
    def _orderflow_set_from_cfg_scalar(self, of_cfg: float) -> None:
        """Match engine/entry_engine.py: ratio >1 => normalized, else use as [0,1] threshold."""
        if of_cfg > 1.0:
            self.entry_engine.min_orderflow_imbalance = (of_cfg - 1.0) / (of_cfg + 1.0)
        else:
            self.entry_engine.min_orderflow_imbalance = max(float(of_cfg), 0.0)

    async def _apply_strategy_presets(self) -> None:
        if not getattr(self, "strategy_presets_enabled", False):
            return

        mode = str(getattr(self, "strategy_presets_mode", "defensive") or "defensive").lower()
        if mode == "impulse":
            name = "impulse"
        elif mode == "defensive":
            name = "defensive"
        elif mode == "auto":
            hours = {
                int(x) % 24
                for x in (self.cfg.get("strategy_presets", "auto_impulse_local_hours", default=[]) or [])
                if str(x).strip() != ""
            }
            off = int(self.cfg.get("strategy_presets", "auto_timezone_offset", default=3) or 3)
            now_utc = datetime.now(timezone.utc)
            local_h = (now_utc.hour + off) % 24
            name = "impulse" if local_h in hours else "defensive"
        else:
            name = "defensive"

        impulse = self.cfg.get("strategy_presets", "impulse", default={}) or {}
        defensive = self.cfg.get("strategy_presets", "defensive", default={}) or {}
        if not isinstance(impulse, dict):
            impulse = {}
        if not isinstance(defensive, dict):
            defensive = {}
        overlay = impulse if name == "impulse" else defensive

        prev = getattr(self, "_active_strategy_preset_label", None)

        # Restore from snapshot, then apply overlay (same as base trading.* + entry.*)
        self.entry_engine.entry_threshold = float(getattr(self, "_base_entry_threshold", 0.70))
        self._orderflow_set_from_cfg_scalar(float(getattr(self, "_base_min_orderflow_cfg", 0.0)))
        self.block_entry_utc_hours = set(getattr(self, "_base_block_entry_utc_hours", set()) or set())

        if overlay:
            if "entry_threshold" in overlay:
                self.entry_engine.entry_threshold = float(overlay["entry_threshold"])
            if "min_orderflow_imbalance" in overlay:
                self._orderflow_set_from_cfg_scalar(float(overlay["min_orderflow_imbalance"]))
            if "block_entry_utc_hours" in overlay and overlay["block_entry_utc_hours"] is not None:
                self.block_entry_utc_hours = {
                    int(h) % 24
                    for h in (overlay["block_entry_utc_hours"] or [])
                    if str(h).strip() != ""
                }

        self._active_strategy_preset_label = name
        if name != prev:
            of_disp = float(self.entry_engine.min_orderflow_imbalance)
            logger.info(
                f"[STRATEGY PRESET] active={name} (mode={mode}) entry_th={self.entry_engine.entry_threshold:.2f} "
                f"|imb|>={of_disp:.3f} block_hours={sorted(self.block_entry_utc_hours)}"
            )
            if self.tg and bool(getattr(self, "strategy_presets_notify_telegram", False)):
                try:
                    await self.tg.send_message(
                        f"<b>STRATEGY PRESET</b>\n"
                        f"Режим: <code>{name}</code> (mode=<code>{mode}</code>)\n"
                        f"entry_threshold=<code>{self.entry_engine.entry_threshold:.2f}</code>\n"
                        f"|orderflow| min=<code>{of_disp:.3f}</code>\n"
                        f"block_entry UTC: <code>{sorted(self.block_entry_utc_hours)}</code>"
                    )
                except Exception as exc:
                    logger.warning(f"[STRATEGY PRESET] Telegram notify failed: {exc}")
