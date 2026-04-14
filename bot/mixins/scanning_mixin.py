"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotScanningMixin:
    async def _scan_entries(self, symbols: list):
        candidates = []
        reject_counts: dict[str, int] = {}

        def mark_reject(reason: str):
            reject_counts[reason] = reject_counts.get(reason, 0) + 1

        for symbol in symbols:
            if self.position_manager.has(symbol):
                mark_reject("already_in_position")
                continue
            now_ts = time.monotonic()
            min_symbol_rescan_sec = max(0.0, float(getattr(self, "min_symbol_rescan_sec", 0.0) or 0.0))
            if min_symbol_rescan_sec > 0:
                last_scanned_at = float(getattr(self, "_last_symbol_scan_ts", {}).get(symbol, 0.0) or 0.0)
                if last_scanned_at > 0 and (now_ts - last_scanned_at) < min_symbol_rescan_sec:
                    mark_reject("symbol_rescan_throttle")
                    continue

            exchange_closed_wait = self._exchange_closed_reentry_remaining(symbol)
            if exchange_closed_wait > 0:
                mark_reject("exchange_closed_reentry_cooldown")
                continue

            quality_allowed, quality_reason, quality_stats = self.symbol_quality_filter.allow(
                symbol,
                is_whitelisted=symbol in self.whitelist,
            )
            if not quality_allowed:
                mark_reject(f"symbol_quality_{quality_reason}")
                continue

            allowed, risk_reason = self.risk_guard.can_trade(symbol)
            if not allowed:
                if risk_reason:
                    reason_key = str(risk_reason).strip().lower().replace(" ", "_")
                    mark_reject(f"risk_blocked_{reason_key}")
                else:
                    mark_reject("risk_blocked")
                continue
            try:
                self._last_symbol_scan_ts[symbol] = now_ts
                signal = await self._analyze_symbol(symbol)
                if signal.should_enter:
                    cooldown_left = self._same_side_cooldown_remaining(symbol, signal.side)
                    if cooldown_left > 0:
                        logger.info(f"SAME-SIDE COOLDOWN {symbol} {signal.side}: {cooldown_left}s left")
                        mark_reject("same_side_cooldown")
                        continue

                    if self.quality_gate_enabled:
                        gate_ok, gate_reason, gate_meta = self._passes_signal_quality_gate(symbol, signal)
                        if not gate_ok:
                            logger.info(f"QUALITY GATE REJECT {symbol}: {gate_reason}")
                            mark_reject(f"quality_gate_{gate_reason}")
                            continue
                        signal.metadata.update(gate_meta)

                    same_side_peers = self._same_side_peer_symbols(signal.side, candidates)
                    corr_ok, corr_reason = await self._passes_correlation_filter(symbol, same_side_peers)
                    if not corr_ok:
                        logger.info(f"CORRELATION REJECT {symbol}: {corr_reason}")
                        mark_reject("correlation_blocked")
                        continue

                    signal.metadata.update(
                        {
                            "symbol_quality_trades": quality_stats.get("trades", 0),
                            "symbol_quality_winrate": quality_stats.get("winrate", 0.0),
                            "symbol_quality_avg_pnl": quality_stats.get("avg_pnl", 0.0),
                        }
                    )

                    candidates.append(
                        {
                            "symbol": symbol,
                            "signal": signal,
                            "signal_strength": signal.capital_score or signal.confidence,
                            "liquidity": signal.metadata.get("liquidity", 0.0),
                            "volatility": signal.metadata.get("volatility", 0.0),
                            "spread": signal.metadata.get("spread_pct", 0.0),
                        }
                    )
                else:
                    mark_reject(signal.metadata.get("reject_reason", "entry_filters"))
            except Exception as exc:
                logger.error(f"Error analyzing {symbol}: {exc}")
                mark_reject("exception")
            await asyncio.sleep(0.8)

        ranked = self.allocator.allocate(candidates)
        self.controls.set_candidates(ranked)
        summary = ", ".join(f"{key}={value}" for key, value in sorted(reject_counts.items())) or "none"
        logger.info(f"SCAN SUMMARY: symbols={len(symbols)} candidates={len(ranked)} rejects[{summary}]")

        if self.signal_only:
            # Signal-only mode: send to Telegram, no execution (only if confidence above threshold)
            for item in ranked:
                signal = item["signal"]
                symbol = item["symbol"]
                conf_val = float(signal.confidence or 0.0)
                if conf_val <= self.signal_only_min_confidence:
                    logger.info(
                        f"SIGNAL-ONLY SKIP {symbol}: confidence={conf_val:.0%} "
                        f"(need >{self.signal_only_min_confidence:.0%})"
                    )
                    continue
                side = signal.side
                direction = "LONG" if side == "BUY" else "SHORT"
                sl = signal.stop_loss
                tp = signal.take_profit
                tp1 = signal.metadata.get("tp1_level", tp)
                entry = signal.entry_price
                rr = signal.rr_ratio
                zone = signal.metadata.get("entry_zone", "none")
                bos = signal.metadata.get("bos_direction", "none")
                sweep = signal.metadata.get("sweep_direction", "none")
                conf = signal.confidence
                expected_edge = float(signal.metadata.get("quality_expected_edge", 0.0) or 0.0)
                entry_range_low = float(signal.metadata.get("entry_range_low", entry) or entry)
                entry_range_high = float(signal.metadata.get("entry_range_high", entry) or entry)

                msg = (
                    f"<b>SIGNAL {direction} [{signal.grade}]</b>\n\n"
                    f"Монета: <code>{symbol}</code>\n"
                    f"Грейд: <b>{signal.grade}</b>\n"
                    f"Вход: <code>${entry:.4f}</code>\n"
                    f"Рекомендуемый вход: <code>${entry_range_low:.4f} - ${entry_range_high:.4f}</code>\n"
                    f"SL: <code>${sl:.4f}</code>\n"
                    f"TP1: <code>${tp1:.4f}</code>\n"
                    f"TP2: <code>${tp:.4f}</code>\n"
                    f"RR: <code>{rr:.1f}</code>\n"
                    f"Confidence: <code>{conf:.0%}</code>\n"
                    f"Expected Edge: <code>{expected_edge:.2f}R</code>\n"
                    f"Zone: <code>{zone}</code>\n"
                    f"BOS: <code>{bos}</code> | Sweep: <code>{sweep}</code>"
                )
                logger.info(f"SIGNAL-ONLY {symbol}: {direction} [{signal.grade}] entry=${entry:.4f} SL=${sl:.4f} TP=${tp:.4f} RR={rr:.1f}")
                self._register_signal_timestamp(symbol, side)
                self.signal_feedback.register_signal(symbol, signal)
                if self.tg:
                    await self.tg.send_message(msg)
            return

        available_slots = max(0, self.controls.max_positions - self.position_manager.count())
        executable = ranked[:available_slots]
        if executable:
            if self.entry_capital_weight_mode == "equal":
                # Equal split across executable signals to avoid tiny under-sized
                # entries on low-ranked candidates (user expected fixed margin cap).
                equal_weight = 1.0 / float(len(executable))
                for item in executable:
                    item["capital_weight"] = equal_weight
            else:
                # Renormalize capital weights to the actually executable set
                # (top-N by available slots). Otherwise each weight is diluted by
                # non-executed candidates and position size becomes too small.
                total_weight = sum(float(item.get("capital_weight", 0.0) or 0.0) for item in executable)
                if total_weight > 0:
                    for item in executable:
                        item["capital_weight"] = max(
                            0.0, float(item.get("capital_weight", 0.0) or 0.0) / total_weight
                        )
        for item in executable:
            await self._execute_entry(item["symbol"], item["signal"], item.get("capital_weight", 1.0))
