"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotExchangeClosedMixin:
    def _should_finalize_exchange_closed(self, symbol: str) -> bool:
        required = max(1, int(self.exchange_closed_confirm_cycles))
        seen = int(self._missing_exchange_cycles.get(symbol, 0)) + 1
        self._missing_exchange_cycles[symbol] = seen
        if seen < required:
            logger.info(
                f"[POSITION_SYNC] {symbol} missing on exchange ({seen}/{required}) — waiting confirm"
            )
            return False
        return True


    @staticmethod
    def _filter_recent_closed_pnl(closed_records: list | None, max_age_sec: int = 300) -> list:
        return filter_recent_closed_pnl(closed_records, max_age_sec=max_age_sec)


    @staticmethod
    def _classify_exchange_closed_reason(closed_records: list | None) -> str:
        return classify_exchange_closed_reason(closed_records)


    def _set_exchange_close_meta(self, symbol: str, closed_records: list | None):
        """Persist compact closed-pnl metadata for the next finalized trade record."""
        if not symbol:
            return
        if not closed_records:
            self._last_exchange_close_meta[symbol] = {}
            return
        record = closed_records[0] or {}
        meta = {
            "execType": record.get("execType", ""),
            "stopOrderType": record.get("stopOrderType", ""),
            "orderType": record.get("orderType", ""),
            "createType": record.get("createType", ""),
            "closeType": record.get("closeType", ""),
            "orderFilter": record.get("orderFilter", ""),
            "orderLinkId": record.get("orderLinkId", ""),
            "updatedTime": record.get("updatedTime", ""),
            "createdTime": record.get("createdTime", ""),
        }
        self._last_exchange_close_meta[symbol] = meta


    def _pop_exchange_close_meta(self, symbol: str) -> dict:
        if not symbol:
            return {}
        return self._last_exchange_close_meta.pop(symbol, {}) or {}


    def _can_finalize_exchange_closed(self, missing_cycles: int, closed_records_count: int) -> bool:
        if not self.exchange_closed_require_closed_pnl:
            return True
        if closed_records_count > 0:
            return True
        return missing_cycles >= max(1, int(self.exchange_closed_force_cycles))


    def _set_exchange_closed_reentry_block(self, symbol: str):
        cooldown = max(0, int(self.exchange_closed_reentry_cooldown_sec))
        if cooldown <= 0:
            return
        self._exchange_closed_reentry_until[symbol] = time.time() + cooldown


    def _exchange_closed_reentry_remaining(self, symbol: str) -> int:
        until = self._exchange_closed_reentry_until.get(symbol)
        if not until:
            return 0
        remaining = int((until - time.time()) + 0.999)
        if remaining <= 0:
            self._exchange_closed_reentry_until.pop(symbol, None)
            return 0
        return remaining


    def _exchange_closed_sync_pause_remaining(self) -> int:
        cooldown = max(0, int(self.exchange_closed_pause_after_rate_limit_sec))
        if cooldown <= 0:
            return 0
        last_at = float(getattr(self.client, "last_rate_limit_at_monotonic", 0.0) or 0.0)
        if last_at <= 0:
            return 0
        remaining = int((last_at + cooldown - time.monotonic()) + 0.999)
        return remaining if remaining > 0 else 0
