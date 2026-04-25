"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotFeedbackMixin:
    async def _process_signal_feedback_loop(self):
        outcomes = await self.signal_feedback.process_pending(self.client.get_price)
        if outcomes:
            wins = sum(1 for item in outcomes if item.record.get("result") == "win")
            losses = len(outcomes) - wins
            quality_labels = sum(1 for item in outcomes if self._is_quality_feedback_record(item.record))
            self.signal_feedback.add_quality_labels(quality_labels)
            if self.feedback_apply_to_risk_guard and (not self.signal_only):
                for item in outcomes:
                    symbol = str(item.record.get("symbol", ""))
                    pnl_proxy = 1.0 if item.record.get("result") == "win" else -1.0
                    self.risk_guard.record_trade(pnl_proxy, symbol=symbol, reason="signal_feedback")
            logger.info(
                f"[FEEDBACK] resolved={len(outcomes)} win={wins} loss={losses} "
                f"quality_labels={quality_labels} dataset={self.signal_feedback.dataset_path.name}"
            )
            if self.tg and self.feedback_notify_labeling:
                await self.tg.send_message(
                    "<b>FEEDBACK LOOP</b>\n"
                    f"Размечено сигналов: <code>{len(outcomes)}</code>\n"
                    f"Win: <code>{wins}</code> | Loss: <code>{losses}</code>\n"
                    f"Качественных меток: <code>{quality_labels}</code>\n"
                    f"Датасет: <code>{self.signal_feedback.dataset_path.name}</code>"
                )

        if self.signal_feedback.should_run_daily_retrain():
            if not bool(getattr(self, "feedback_retrain_in_process", True)):
                logger.info(
                    "[FEEDBACK] Daily in-process retrain skipped (retrain_in_process=false). "
                    "Run: bash scripts/run_feedback_retrain.sh from repo root (e.g. via cron)."
                )
                if self.tg:
                    try:
                        await self.tg.send_message(
                            "<b>FEEDBACK RETRAIN (off-line)</b>\n"
                            "In-process training disabled. Use cron, e.g.:\n"
                            "<code>0 2 * * * cd /path/to/PRD-SCALP && bash scripts/run_feedback_retrain.sh</code>"
                        )
                    except Exception as exc:
                        logger.warning(f"[FEEDBACK] retrain-skip Telegram failed: {exc}")
                self.signal_feedback.mark_retrain_attempt(False)
            else:
                await self._run_feedback_daily_retrain()


    async def _run_feedback_daily_retrain(self):
        logger.info("[FEEDBACK] Daily retrain started from signal-only labels")
        success = False
        try:
            from train_transformer import train as train_transformer_model

            output_path = str(self.entry_engine._resolve_weights_path())
            data_path = self._build_retrain_dataset()
            success = await asyncio.to_thread(
                train_transformer_model,
                data_path=str(data_path),
                epochs=self.feedback_train_epochs,
                lr=self.feedback_train_lr,
                batch_size=self.feedback_train_batch_size,
                output_path=output_path,
                val_ratio=self.feedback_train_val_ratio,
                decision_threshold=self.feedback_train_decision_threshold,
                seed=self.feedback_train_seed,
                augment_wins_factor=max(1, self.feedback_augment_wins_factor),
                augment_noise_std=max(0.0, self.feedback_augment_noise_std),
            )
            if success:
                self.entry_engine._load_trained_model()
                logger.info("[FEEDBACK] Daily retrain completed successfully")
                if self.tg:
                    await self.tg.send_message(
                        "<b>DAILY RETRAIN DONE</b>\n"
                        f"Файл весов: <code>{self.entry_engine._resolve_weights_path().name}</code>"
                    )
            else:
                logger.warning("[FEEDBACK] Daily retrain finished with failure status")
                if self.tg:
                    await self.tg.send_message(
                        "<b>DAILY RETRAIN FAILED</b>\n"
                        "Обучение завершилось без валидного улучшения/чекпоинта."
                    )
        except Exception as exc:
            logger.error(f"[FEEDBACK] Daily retrain error: {exc}")
            if self.tg:
                await self.tg.send_message(
                    "<b>DAILY RETRAIN ERROR</b>\n"
                    f"Ошибка: <code>{exc}</code>"
                )
        finally:
            self.signal_feedback.mark_retrain_attempt(success)


    def _is_quality_feedback_record(self, record: dict) -> bool:
        if record.get("source") != "signal_only_feedback":
            return False
        if record.get("exit_reason") not in {"stop_loss", "take_profit"}:
            return False
        abs_pnl = abs(float(record.get("pnl_pct", 0.0) or 0.0))
        if abs_pnl < self.feedback_min_label_abs_pnl_pct:
            return False
        entry_dt = self._parse_iso_dt(record.get("entry_time"))
        exit_dt = self._parse_iso_dt(record.get("exit_time"))
        if not entry_dt or not exit_dt:
            return False
        hold_minutes = (exit_dt - entry_dt).total_seconds() / 60.0
        return hold_minutes >= self.feedback_min_label_hold_minutes


    def _build_retrain_dataset(self) -> Path:
        if not self.feedback_use_merged_dataset_for_retrain:
            return self.signal_feedback.dataset_path

        base_rows = []
        feedback_rows = []
        if self.feedback_base_dataset_path.exists():
            try:
                with open(self.feedback_base_dataset_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, list):
                        base_rows = [row for row in loaded if row.get("result") in {"win", "loss"}]
            except Exception as exc:
                logger.warning(f"Failed to read base dataset for retrain: {exc}")

        if self.signal_feedback.dataset_path.exists():
            try:
                with open(self.signal_feedback.dataset_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, list):
                        feedback_rows = [row for row in loaded if self._is_quality_feedback_record(row)]
            except Exception as exc:
                logger.warning(f"Failed to read feedback dataset for retrain: {exc}")

        merged = base_rows + feedback_rows
        output_path = resolve_bot_dir() / "training_data_merged.json"
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)

        logger.info(
            f"[FEEDBACK] Retrain dataset prepared: base={len(base_rows)} "
            f"quality_feedback={len(feedback_rows)} total={len(merged)}"
        )
        return output_path
