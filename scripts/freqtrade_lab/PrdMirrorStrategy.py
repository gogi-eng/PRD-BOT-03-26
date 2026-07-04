# PRD-BOT mirror strategy for Freqtrade dry-run / backtest lab.
# Не для live — только сравнение параметров с config PRD-BOT.
from pandas import DataFrame
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
import talib.abstract as ta


class PrdMirrorStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = True
    stoploss = -0.025
    minimal_roi = {"0": 0.06, "60": 0.03, "180": 0.01}
    trailing_stop = True
    trailing_stop_positive = 0.012
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    buy_rsi_max = IntParameter(28, 42, default=35, space="buy")
    sell_rsi_min = IntParameter(58, 72, default=65, space="sell")
    stoploss_param = DecimalParameter(-0.04, -0.015, default=-0.025, decimals=3, space="sell")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=50)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["rsi"] < self.buy_rsi_max.value)
                & (dataframe["ema_fast"] > dataframe["ema_slow"])
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        dataframe.loc[
            (
                (dataframe["rsi"] > self.sell_rsi_min.value)
                & (dataframe["ema_fast"] < dataframe["ema_slow"])
                & (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe["rsi"] > 70), "exit_long"] = 1
        dataframe.loc[(dataframe["rsi"] < 30), "exit_short"] = 1
        return dataframe

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        return float(self.stoploss_param.value)
