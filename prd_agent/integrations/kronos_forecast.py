"""Kronos OHLCV forecast (Bybit klines → HuggingFace model)."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"

_INTERVAL_MS = {
    "1": 60_000,
    "3": 3 * 60_000,
    "5": 5 * 60_000,
    "15": 15 * 60_000,
    "30": 30 * 60_000,
    "60": 60 * 60_000,
    "240": 4 * 60 * 60_000,
    "D": 24 * 60 * 60_000,
}


def default_kronos_home() -> Path:
    return Path(__file__).resolve().parents[2].parent / "Kronos-master"


def resolve_kronos_home(cfg: dict[str, Any] | None = None) -> Path:
    k = (cfg or {}).get("kronos") or {}
    raw = k.get("kronos_home") or k.get("home")
    if raw:
        return Path(str(raw)).expanduser().resolve()
    env = __import__("os").environ.get("KRONOS_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return default_kronos_home()


def _import_kronos(kronos_home: Path):
    home = kronos_home.resolve()
    if not home.is_dir():
        raise FileNotFoundError(f"Kronos не найден: {home}")
    if str(home) not in sys.path:
        sys.path.insert(0, str(home))
    from model import Kronos, KronosPredictor, KronosTokenizer

    return Kronos, KronosTokenizer, KronosPredictor


def fetch_bybit_klines(
    symbol: str,
    *,
    interval: str = "15",
    limit: int = 400,
    category: str = "linear",
) -> pd.DataFrame:
    """Публичный API Bybit — ключи не нужны."""
    r = requests.get(
        BYBIT_KLINE_URL,
        params={
            "category": category,
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1000),
        },
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("retCode") != 0:
        raise RuntimeError(f"Bybit kline: {body.get('retMsg')}")
    rows = body.get("result", {}).get("list") or []
    if not rows:
        raise RuntimeError(f"Пустые свечи для {symbol}")
    # Bybit: newest first
    rows = list(reversed(rows))
    records = []
    for row in rows:
        ts_ms = int(row[0])
        records.append(
            {
                "timestamps": pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_localize(None),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "amount": float(row[6]) if len(row) > 6 else float(row[5]) * float(row[4]),
            }
        )
    return pd.DataFrame(records)


def _future_timestamps(last_ts: pd.Timestamp, pred_len: int, interval: str) -> pd.Series:
    step_ms = _INTERVAL_MS.get(str(interval), 15 * 60_000)
    delta = timedelta(milliseconds=step_ms)
    out = []
    t = last_ts
    for _ in range(pred_len):
        t = t + delta
        out.append(t)
    return pd.Series(out)


def forecast_symbol(
    symbol: str,
    cfg: dict[str, Any],
    *,
    kronos_home: Path | None = None,
) -> dict[str, Any]:
    k = cfg.get("kronos") or {}
    home = kronos_home or resolve_kronos_home(cfg)
    Kronos, KronosTokenizer, KronosPredictor = _import_kronos(home)

    model_id = k.get("model_id") or "NeoQuasar/Kronos-mini"
    tokenizer_id = k.get("tokenizer_id") or "NeoQuasar/Kronos-Tokenizer-2k"
    interval = str(k.get("interval") or "15")
    lookback = int(k.get("lookback") or 400)
    pred_len = int(k.get("pred_len") or 16)
    max_context = int(k.get("max_context") or 512)
    device = str(k.get("device") or "cpu")

    df = fetch_bybit_klines(
        symbol,
        interval=interval,
        limit=max(lookback + pred_len + 5, lookback),
        category=str((cfg.get("bybit") or {}).get("category") or "linear"),
    )
    if len(df) < lookback:
        raise RuntimeError(f"{symbol}: мало свечей ({len(df)} < {lookback})")

    x_df = df.iloc[-lookback:].reset_index(drop=True)
    x_timestamp = x_df["timestamps"]
    y_timestamp = _future_timestamps(x_timestamp.iloc[-1], pred_len, interval)

    cols = ["open", "high", "low", "close", "volume", "amount"]
    x_input = x_df[cols]

    tokenizer = KronosTokenizer.from_pretrained(tokenizer_id)
    model = Kronos.from_pretrained(model_id)
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=max_context)

    pred_df = predictor.predict(
        df=x_input,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=pred_len,
        T=float(k.get("temperature") or 1.0),
        top_p=float(k.get("top_p") or 0.9),
        sample_count=int(k.get("sample_count") or 1),
        verbose=bool(k.get("verbose", False)),
    )

    last_close = float(x_df["close"].iloc[-1])
    pred_close = float(pred_df["close"].iloc[-1])
    change_pct = (pred_close - last_close) / last_close * 100 if last_close else 0.0
    direction = "up" if change_pct > 0.15 else "down" if change_pct < -0.15 else "flat"

    return {
        "symbol": symbol,
        "interval": interval,
        "last_close": last_close,
        "pred_close_end": pred_close,
        "change_pct": change_pct,
        "direction": direction,
        "pred_len": pred_len,
        "model_id": model_id,
        "pred_df": pred_df,
    }


def format_telegram_summary(result: dict[str, Any]) -> str:
    sym = result["symbol"].replace("USDT", "")
    arrow = {"up": "📈", "down": "📉", "flat": "➡️"}.get(result["direction"], "➡️")
    return (
        f"{arrow} <b>Kronos</b> {sym} ({result['interval']}m)\n"
        f"сейчас: <code>{result['last_close']:.4f}</code>\n"
        f"прогноз +{result['pred_len']} св.: <code>{result['pred_close_end']:.4f}</code>\n"
        f"Δ ≈ <b>{result['change_pct']:+.2f}%</b> ({result['direction']})"
    )
