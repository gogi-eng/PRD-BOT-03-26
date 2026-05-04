# Trade Context Snapshot

- Generated UTC: `2026-05-03T06:10:01.779305+00:00`
- Lookback hours: `24.0`

## Recommendations
- Сохранить strict-mode: bot PnL отрицательный и winrate ниже 45%.
- Главная проблема окна: trend_exit PnL=-7.44.
- Проверить blacklist/cooldown для: PENGUUSDT, BIOUSDT, ORCAUSDT, SUIUSDT, XRPUSDT.
- Основные отказы сканера: orderflow_direction_mismatch=193, entry_too_extended_from_zone=161, already_in_position=96, score_below_threshold=88, volume_guard=78.

## Trade Stats
- `all_time`: trades=229 pnl=-106.13 winrate=39.74% wins=91 losses=135
- `last_24h_all`: trades=12 pnl=5.65 winrate=33.33% wins=4 losses=8
- `last_24h_bot`: trades=7 pnl=-8.22 winrate=28.57% wins=2 losses=5

## Bot By Reason
| name | trades | pnl | winrate | wins | losses |
| --- | --- | --- | --- | --- | --- |
| trend_exit | 3 | -7.44 | 0.0 | 0 | 3 |
| exchange_closed | 2 | -1.58 | 0.0 | 0 | 2 |
| basket_symbol_fall | 1 | 0.13 | 100.0 | 1 | 0 |
| early_exit | 1 | 0.67 | 100.0 | 1 | 0 |

## Bot Worst Symbols
| name | trades | pnl | winrate | wins | losses |
| --- | --- | --- | --- | --- | --- |
| PENGUUSDT | 1 | -3.51 | 0.0 | 0 | 1 |
| BIOUSDT | 1 | -2.78 | 0.0 | 0 | 1 |
| ORCAUSDT | 1 | -1.15 | 0.0 | 0 | 1 |
| SUIUSDT | 1 | -1.05 | 0.0 | 0 | 1 |
| XRPUSDT | 2 | -0.4 | 50.0 | 1 | 1 |
| MUSDT | 1 | 0.67 | 100.0 | 1 | 0 |

## Bot By Side
| name | trades | pnl | winrate | wins | losses |
| --- | --- | --- | --- | --- | --- |
| BUY | 6 | -8.89 | 16.67 | 1 | 5 |
| SELL | 1 | 0.67 | 100.0 | 1 | 0 |

## Log Summary
- scan_summaries=77 signals=10 errors=0
- top rejects: orderflow_direction_mismatch=193, entry_too_extended_from_zone=161, already_in_position=96, score_below_threshold=88, volume_guard=78, risk_blocked_max_trades_for_suiusdt:_1/1=77, risk_blocked_max_trades_for_taousdt:_2/1=77, risk_blocked_max_trades_for_xrpusdt:_2/1=77, funding_rate_high=66, smc_score_too_low=65

## Recent Scan Summaries
- `06:03:58 [BOT] SCAN SUMMARY: symbols=18 candidates=1 rejects[ai_rejected (55)=1, already_in_position=1, entry_hardgate_missing_bos=1, entry_too_extended_from_zone=2, orderflow_direction_mismatch (SELL but imb=+0.368 > -0.035)=1, orderflow_direction_mismatch (SELL but imb=+0.612 > -0.035)=1, orderflow_imbalance_too_low (0.003 < 0.035)=1, risk_blocked_max_trades_for_ethusdt:_1/1=1, risk_blocked_max_trades_for_suiusdt:_1/1=1, risk_blocked_max_trades_for_taousdt:_2/1=1, risk_blocked_max_trades_for_xrpusdt:_2/1=1, risk_blocked_symbol_streak_cooldown_biousdt:_76331s=1, risk_blocked_symbol_streak_cooldown_orcausdt:_81157s=1, risk_blocked_trend-exit_cooldown_penguusdt:_43167s=1, score_below_threshold (0.440 < 0.52)=1, score_below_threshold (0.508 < 0.52)=1]`
- `06:05:28 [BOT] SCAN SUMMARY: symbols=18 candidates=0 rejects[already_in_position=2, risk_blocked_max_trades_for_ethusdt:_1/1=1, risk_blocked_max_trades_for_suiusdt:_1/1=1, risk_blocked_max_trades_for_taousdt:_2/1=1, risk_blocked_max_trades_for_xrpusdt:_2/1=1, risk_blocked_symbol_streak_cooldown_biousdt:_76237s=1, risk_blocked_symbol_streak_cooldown_orcausdt:_81064s=1, risk_blocked_trend-exit_cooldown_penguusdt:_43073s=1, score_below_threshold (0.502 < 0.52)=1, sell_entry_threshold (0.610 < 0.640)=1, sell_entry_threshold (0.614 < 0.640)=2, volume_guard (vol=0 < avg20=320 * 0.06)=1, volume_guard (vol=1 < avg20=51 * 0.06)=1, volume_guard (vol=14398 < avg20=322707 * 0.06)=1, volume_guard (vol=37919 < avg20=2103125 * 0.06)=1, volume_guard (vol=97 < avg20=3675 * 0.06)=1]`
- `06:06:55 [BOT] SCAN SUMMARY: symbols=18 candidates=0 rejects[already_in_position=2, entry_too_extended_from_zone=3, price_momentum_against (3/3 candles oppose BUY)=2, risk_blocked_max_trades_for_ethusdt:_1/1=1, risk_blocked_max_trades_for_suiusdt:_1/1=1, risk_blocked_max_trades_for_taousdt:_2/1=1, risk_blocked_max_trades_for_xrpusdt:_2/1=1, risk_blocked_symbol_streak_cooldown_biousdt:_76151s=1, risk_blocked_symbol_streak_cooldown_orcausdt:_80978s=1, risk_blocked_trend-exit_cooldown_penguusdt:_42987s=1, score_below_threshold (0.467 < 0.52)=1, score_below_threshold (0.475 < 0.52)=1, score_below_threshold (0.505 < 0.52)=1, smc_score_too_low (0.574 < 0.600)=1]`
- `06:08:22 [BOT] SCAN SUMMARY: symbols=18 candidates=0 rejects[already_in_position=2, entry_hardgate_missing_bos=1, entry_too_extended_from_zone=3, orderflow_direction_mismatch (BUY but imb=-0.430 < +0.035)=1, orderflow_direction_mismatch (SELL but imb=+0.066 > -0.035)=1, orderflow_direction_mismatch (SELL but imb=+0.442 > -0.035)=1, risk_blocked_max_trades_for_ethusdt:_1/1=1, risk_blocked_max_trades_for_suiusdt:_1/1=1, risk_blocked_max_trades_for_taousdt:_2/1=1, risk_blocked_max_trades_for_xrpusdt:_2/1=1, risk_blocked_symbol_streak_cooldown_biousdt:_76064s=1, risk_blocked_symbol_streak_cooldown_orcausdt:_80890s=1, risk_blocked_trend-exit_cooldown_penguusdt:_42900s=1, score_below_threshold (0.475 < 0.52)=1, smc_score_too_low (0.574 < 0.600)=1]`
- `06:09:48 [BOT] SCAN SUMMARY: symbols=18 candidates=0 rejects[already_in_position=2, entry_hardgate_missing_bos=1, entry_too_extended_from_zone=3, orderflow_direction_mismatch (BUY but imb=-0.039 < +0.035)=1, orderflow_direction_mismatch (SELL but imb=+0.259 > -0.035)=1, risk_blocked_max_trades_for_ethusdt:_1/1=1, risk_blocked_max_trades_for_suiusdt:_1/1=1, risk_blocked_max_trades_for_taousdt:_2/1=1, risk_blocked_max_trades_for_xrpusdt:_2/1=1, risk_blocked_symbol_streak_cooldown_biousdt:_75977s=1, risk_blocked_symbol_streak_cooldown_orcausdt:_80804s=1, risk_blocked_trend-exit_cooldown_penguusdt:_42813s=1, smc_score_too_low (0.551 < 0.600)=1, smc_score_too_low (0.574 < 0.600)=1, smc_score_too_low (0.577 < 0.600)=1]`

## Recent Signals
- `04:17:48 [BOT] SIGNAL ORCAUSDT: BUY conf=79% smc=0.82 zone=fvg_bullish bos=none sweep=down 4H=BULL RR=3.0`
- `04:21:50 [BOT] SIGNAL HYPEUSDT: BUY conf=68% smc=0.61 zone=ob_bullish bos=none sweep=up 4H=FLAT RR=3.0`
- `04:24:53 [BOT] SIGNAL HYPEUSDT: BUY conf=68% smc=0.61 zone=ob_bullish bos=none sweep=up 4H=FLAT RR=3.0`
- `04:27:52 [BOT] SIGNAL HYPEUSDT: BUY conf=68% smc=0.61 zone=ob_bullish bos=none sweep=up 4H=FLAT RR=3.0`
- `05:01:55 [BOT] SIGNAL DOGEUSDT: BUY conf=68% smc=0.61 zone=fvg_bullish bos=none sweep=up 4H=FLAT RR=3.0`
- `05:04:59 [BOT] SIGNAL MUSDT: SELL conf=80% smc=0.75 zone=fvg_bearish bos=none sweep=up 4H=BEAR RR=3.0`
- `05:46:29 [BOT] SIGNAL DOGEUSDT: BUY conf=68% smc=0.61 zone=fvg_bullish bos=none sweep=down 4H=FLAT RR=3.0`
- `05:48:09 [BOT] SCALP SIGNAL KNCUSDT: SELL conf=93% RR=2.0 reason=SCALP DUMP: hour=03 UTC+3, impulse=-0.91%, confirm=-1.37%, vol=1.77x`
- `05:49:20 [BOT] SIGNAL DOGEUSDT: BUY conf=68% smc=0.61 zone=fvg_bullish bos=none sweep=down 4H=FLAT RR=3.0`
- `06:03:55 [BOT] SIGNAL TRXUSDT: BUY conf=79% smc=0.74 zone=fvg_bullish bos=none sweep=none 4H=BULL RR=3.0`

## Recent Closes
- `04:36:22 [BOT] CLOSED ORCAUSDT: pnl=$-1.15 reason=trend_exit`
- `05:09:50 [BOT] [EXCHANGE CLOSED DETAIL] ETHUSDT reason=exchange_closed side=BUY entry=2303.750000 exchange_exit=2296.370000 market_after_close=2302.720000 tracked_SL=2296.540000 tracked_TP=2407.580000 best=2337.630000 pnl=-0.417681 closedPnl=-0.41768056 avgExit=2296.37 execType=Trade stopOrderType= closeType= createType= orderType=Market`
- `05:09:50 [BOT] CLOSED ETHUSDT: pnl=$-0.42 reason=exchange_closed`

## Recent Errors
нет данных

## Recent Adaptive Recommendations
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] worst_reasons: trend_exit: n=3, PnL=-7.44, WR=0%; exchange_closed: n=2, PnL=-1.58, WR=0%; basket_symbol_fall: n=1, PnL=0.13, WR=100%; early_exit: n=1, PnL=0.67, WR=100%`
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] worst_symbols: PENGUUSDT: n=1, PnL=-3.51, WR=0%; BIOUSDT: n=1, PnL=-2.78, WR=0%; ORCAUSDT: n=1, PnL=-1.15, WR=0%; SUIUSDT: n=1, PnL=-1.05, WR=0%; XRPUSDT: n=2, PnL=-0.40, WR=50%; MUSDT: n=1, PnL=0.67, WR=100%`
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] worst_sides: BUY: n=6, PnL=-8.89, WR=17%; SELL: n=1, PnL=0.67, WR=100%`
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] recommendation 1: Включить/оставить strict-mode: снизить риск, max_positions, повысить confidence/SMC/orderflow.`
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] recommendation 2: Пауза новых входов или max_trades_per_day вниз до стабилизации серии.`
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] recommendation 3: trend_exit убыточен: быстрее закрывать смену тренда, но flip открывать только через полный entry-gate.`
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] recommendation 4: exchange_closed в минусе: расширить качество входа, не входить без зоны/BOS.`
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] recommendation 5: Кандидаты в blacklist/cooldown: PENGUUSDT, BIOUSDT, ORCAUSDT, SUIUSDT, XRPUSDT`
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] recommendation 6: Плохая сторона за окно: BUY(PnL=-8.89, WR=17%)`
- `04:37:58 [BOT] [ADAPTIVE RECOMMEND] mode=analyze_only: config.yaml не изменяется автоматически.`

## Config Snapshot
```json
{
  "trading": {
    "max_positions": 3,
    "margin_total_pct": 4.0,
    "risk_per_trade_pct": 0.35,
    "blacklist_symbols": [
      "WLFIUSDT",
      "AZTECUSDT",
      "MYXUSDT",
      "ORDIUSDT",
      "SOONUSDT",
      "MOVRUSDT",
      "MEGAUSDT",
      "BLENDUSDT",
      "XCNUSDT"
    ]
  },
  "entry": {
    "entry_threshold": 0.58,
    "entry_threshold_soft": 0.56,
    "min_orderflow_imbalance": 0.035,
    "min_rr_ratio": 3.0,
    "min_smc_score": 0.6,
    "missing_bos_min_confidence": 0.72
  },
  "quality_gate": {
    "enabled": true,
    "min_confidence": 0.7,
    "strong_signal_min_confidence": 0.76,
    "strong_signal_min_smc": 0.86,
    "min_expected_edge": 0.5,
    "anti_flat_min_adx": 14.0,
    "anti_flat_min_atr_pct": 0.08,
    "anti_flat_min_abs_imbalance": 0.07,
    "anti_flat_allow_chop": true,
    "chop_bypass_enabled": true,
    "chop_bypass_min_confidence": 0.72,
    "chop_bypass_min_abs_imbalance": 0.12,
    "chop_bypass_require_zone": true,
    "anti_flat_require_htf_trend": false,
    "countertrend_min_confidence": 0.78,
    "countertrend_min_abs_imbalance": 0.25,
    "no_zone_min_confidence": 0.74,
    "reject_no_zone_entries": true
  },
  "risk": {
    "max_daily_loss_usdt": 10,
    "max_trades_per_day": 40,
    "max_trades_per_symbol_24h": 2,
    "max_trades_per_symbol_after_loss_24h": 1,
    "max_trades_per_symbol_after_win_24h": 2,
    "cooldown_after_stop_hours": 4
  },
  "adaptive_recommendations": {
    "enabled": true,
    "telegram_enabled": true,
    "interval_sec": 900,
    "repeat_unchanged_sec": 21600,
    "lookback_hours": 24,
    "bot_only": true,
    "min_trades": 3,
    "max_symbols": 6,
    "trade_history_path": "trade_history.json",
    "state_path": "adaptive_recommendations_state.json"
  }
}
```
