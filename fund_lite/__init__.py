"""
Fund-lite / prop-desk reference layer.

Это НЕ второй бот. Точка входа в проде остаётся ``main.py`` → ``bot.trading_bot.TradingBot``.

Здесь — явная «сквозная» модель пайплайна + классы из спецификации (Hybrid PRO, dynamic risk, …),
чтобы сопоставить документацию с кодом в репозитории.

Карта (концепт → где в PRD-SCALP):
- Market data / OHLCV / orderbook → ``bot`` mixins + ``exchange.bybit_client``
- Feature Engineering PRO → ``analysis.feature_engineering.FeatureEngineer`` (+ ``auto_ml.feature_store`` для табличных ML-фич)
- XGB predictor → ``auto_ml.trainer`` / BPR → ``engine.bpr_ranker``
- LSTM / patterns → ``analysis.transformer_model.TransformerPriceModel`` (не LSTM, а seq-модель)
- Gemma → ``ai.gemma_engine`` / ``bot.ai.gemma_engine``
- Hybrid voting → ``engine.hybrid_voter.HybridVoter``; PRO-вариант с порогами 0.35/0.65 → ``fund_lite.hybrid_voting_pro``
- RL meta → ``engine.rl_meta_controller.RLMetaControllerFacade``
- Execution → ``engine.execution_engine.ExecutionEngine`` + ``engine.execution_ai.ExecutionAI``
- Risk / portfolio → ``engine.risk_manager.RiskGuard`` + ``core.live_controls.LiveControls``
- Learning / feedback → ``engine.signal_feedback_loop`` + ``scripts/feedback_retrain_once.py`` + ``auto_ml``
- Evolution / strategies → ``evolution`` + ``agents`` (multi-style сигналы)
- Backtest → ``backtester.py`` (корень), ``evolution.backtester`` (WF для геномов)
"""

from .dynamic_risk import DynamicRisk
from .hybrid_voting_pro import HybridVotingPro
from .portfolio_risk import PortfolioRisk
from .rl_meta_pro import RLMetaControllerPro

__all__ = [
    "DynamicRisk",
    "HybridVotingPro",
    "PortfolioRisk",
    "RLMetaControllerPro",
]
