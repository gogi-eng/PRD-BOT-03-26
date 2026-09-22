#!/usr/bin/env python3
"""
Эксперимент: TimesFM 2.5 vs сигналы бота / skipped_lab на одном символе Bybit.

Не торгует и не меняет config — только отчёт для лаборатории.

=== Установка (один раз) ===
  pip install "timesfm[torch]==2.0.2"

=== На ПК (когда Bybit открыт) ===
  cd PRD-BOT-ALL
  python scripts/timesfm_bybit_compare.py --symbol BTCUSDT --max-signals 20

  # Сначала сохранить свечи в кэш (если потом Bybit снова закроется):
  python scripts/timesfm_bybit_compare.py --symbol BTCUSDT --fetch-only \\
    --save-klines-cache ..\\.vscode\\dumps\\timesfm_experiment\\BTCUSDT_15m.json

  # Прогон из кэша (без Bybit):
  python scripts/timesfm_bybit_compare.py --symbol BTCUSDT \\
    --klines-cache ..\\.vscode\\dumps\\timesfm_experiment\\BTCUSDT_15m.json

=== skipped_lab с сервера (FileZilla / scp) ===
  Файлы с AGENT-WORLD:
    data/supervisor/skipped_backtest/results.jsonl
    data/ledger/signal_ledger.jsonl  (опционально)

  python scripts/timesfm_bybit_compare.py --symbol BTCUSDT \\
    --skipped-file C:\\Users\\Labuh\\.vscode\\dumps\\skipped_results_aw.jsonl \\
    --max-signals 30

=== На сервере (Bybit там работает) ===
  bash scripts/run_timesfm_experiment_server.sh BTCUSDT
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
DEFAULT_OUT_DIR = ROOT.parent / "dumps" / "timesfm_experiment"
DEFAULT_SKIPPED_AW = ROOT / "data" / "supervisor" / "skipped_backtest" / "results.jsonl"
DEFAULT_LEDGER_AW = ROOT / "data" / "ledger" / "signal_ledger.jsonl"


@dataclass
class SignalRow:
    signal_id: str
    symbol: str
    side: str
    entry: float
    signal_at_ms: int
    source: str
    skip_reason: str = ""
    outcome: str = ""
    pnl_pct: float = 0.0
    origin: str = "unknown"


@dataclass
class CompareRow:
    signal_id: str
    signal_at: str
    side: str
    source: str
    skip_reason: str
    entry: float
    last_close: float
    tfm_direction: str
    tfm_change_pct: float
    signal_direction: str
    direction_match: bool
    actual_move_pct: float
    actual_direction: str
    outcome: str
    pnl_pct: float
    tfm_agrees_with_profit: Optional[bool] = None


@dataclass
class ExperimentReport:
    symbol: str
    interval: str
    horizon: int
    context_len: int
    model: str
    generated_at: str
    klines_count: int
    signals_total: int
    signals_used: int
    direction_match_pct: float
    tfm_direction_accuracy_pct: float
    skipped_would_win: int
    skipped_tfm_agree_when_win: int
    rows: List[CompareRow] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _parse_iso_ms(value: str) -> int:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def side_to_direction(side: str) -> str:
    s = str(side or "").upper()
    if s in ("BUY", "LONG"):
        return "up"
    if s in ("SELL", "SHORT"):
        return "down"
    return "flat"


def direction_from_delta(delta_pct: float, eps: float = 0.02) -> str:
    if delta_pct > eps:
        return "up"
    if delta_pct < -eps:
        return "down"
    return "flat"


def fetch_bybit_klines(
    symbol: str,
    *,
    interval: str = "15",
    limit: int = 1000,
    end_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Публичный REST Bybit — ключи не нужны."""
    params: Dict[str, str] = {
        "category": "linear",
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": str(min(max(limit, 1), 1000)),
    }
    if end_ms is not None:
        params["end"] = str(int(end_ms))
    url = BYBIT_KLINE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PRD-BOT-TimesFM/1.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if int(payload.get("retCode", 0)) != 0:
        raise urllib.error.URLError(
            f"Bybit retCode={payload.get('retCode')} msg={payload.get('retMsg')}"
        )
    rows = (payload.get("result") or {}).get("list") or []
    klines: List[Dict[str, Any]] = []
    for k in reversed(rows):
        try:
            klines.append(
                {
                    "timestamp": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    return klines


def fetch_klines_history(
    symbol: str,
    *,
    interval: str,
    min_bars: int,
) -> List[Dict[str, Any]]:
    """Догружаем свечи пачками до min_bars (Bybit max 1000 за запрос)."""
    merged: Dict[int, Dict[str, Any]] = {}
    end_ms: Optional[int] = None
    while len(merged) < min_bars:
        batch = fetch_bybit_klines(
            symbol, interval=interval, limit=1000, end_ms=end_ms
        )
        if not batch:
            break
        for k in batch:
            merged[int(k["timestamp"])] = k
        oldest = batch[0]["timestamp"]
        end_ms = int(oldest) - 1
        if len(batch) < 1000:
            break
    out = [merged[t] for t in sorted(merged.keys())]
    return out[-min_bars:] if len(out) > min_bars else out


def save_klines_cache(
    path: Path,
    *,
    symbol: str,
    interval: str,
    klines: List[Dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": symbol.upper(),
        "interval": interval,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "count": len(klines),
        "klines": klines,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_klines_cache(path: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    klines = raw.get("klines") or []
    meta = {k: raw.get(k) for k in ("symbol", "interval", "saved_at", "count")}
    return klines, meta


def load_skipped_results(path: Path, symbol: str, limit: int) -> List[SignalRow]:
    if not path.exists():
        return []
    sym = symbol.upper()
    out: List[SignalRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("symbol", "")).upper() != sym:
            continue
        ts_ms = _parse_iso_ms(str(row.get("signal_at", "")))
        if ts_ms <= 0:
            continue
        out.append(
            SignalRow(
                signal_id=str(row.get("ledger_id", "") or row.get("id", "")),
                symbol=sym,
                side=str(row.get("side", "")),
                entry=float(row.get("entry", 0) or 0),
                signal_at_ms=ts_ms,
                source=str(row.get("source", "skipped_backtest")),
                skip_reason=str(row.get("skip_reason", "")),
                outcome=str(row.get("outcome", "")),
                pnl_pct=float(row.get("pnl_pct_net", row.get("pnl_pct", 0)) or 0),
                origin="skipped_backtest",
            )
        )
    out.sort(key=lambda r: r.signal_at_ms, reverse=True)
    return out[:limit]


def load_ledger_skipped(path: Path, symbol: str, limit: int) -> List[SignalRow]:
    """Пропущенные из signal_ledger (если results.jsonl ещё не содержит символ)."""
    if not path.exists():
        return []
    sym = symbol.upper()
    out: List[SignalRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("symbol", "")).upper() != sym:
            continue
        status = str(row.get("status", "")).lower()
        if status not in ("skipped", "rejected"):
            continue
        ts_ms = _parse_iso_ms(str(row.get("created_at", "")))
        if ts_ms <= 0:
            continue
        out.append(
            SignalRow(
                signal_id=str(row.get("id", "")),
                symbol=sym,
                side=str(row.get("side", "")),
                entry=float(row.get("entry", 0) or 0),
                signal_at_ms=ts_ms,
                source=str(row.get("source", "ledger")),
                skip_reason=str(row.get("reason", "")),
                outcome=status,
                pnl_pct=0.0,
                origin="signal_ledger",
            )
        )
    out.sort(key=lambda r: r.signal_at_ms, reverse=True)
    return out[:limit]


def load_trade_history_entries(path: Path, symbol: str, limit: int) -> List[SignalRow]:
    """Fallback: реальные входы из trade_history (если skipped_lab локально нет)."""
    if not path.exists():
        return []
    sym = symbol.upper()
    out: List[SignalRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "entered":
            continue
        if str(row.get("symbol", "")).upper() != sym:
            continue
        ts_ms = _parse_iso_ms(str(row.get("ts", "")))
        if ts_ms <= 0:
            continue
        out.append(
            SignalRow(
                signal_id=str(row.get("order_id", "") or "")[:12],
                symbol=sym,
                side=str(row.get("side", "")),
                entry=float(row.get("entry", 0) or 0),
                signal_at_ms=ts_ms,
                source=str(row.get("source", "trade_history")),
                skip_reason="",
                outcome="executed",
                pnl_pct=0.0,
                origin="trade_history",
            )
        )
    out.sort(key=lambda r: r.signal_at_ms, reverse=True)
    return out[:limit]


def resolve_signals(
    *,
    symbol: str,
    limit: int,
    skipped_file: Optional[Path],
    ledger_file: Optional[Path],
    trade_history: Path,
) -> Tuple[List[SignalRow], str]:
    """Приоритет: skipped_backtest → signal_ledger → trade_history."""
    if skipped_file and skipped_file.exists():
        rows = load_skipped_results(skipped_file, symbol, limit)
        if rows:
            return rows, f"skipped_backtest ({skipped_file})"
    if ledger_file and ledger_file.exists():
        rows = load_ledger_skipped(ledger_file, symbol, limit)
        if rows:
            return rows, f"signal_ledger ({ledger_file})"
    rows = load_trade_history_entries(trade_history, symbol, limit)
    return rows, f"trade_history ({trade_history})"


def find_kline_index(klines: Sequence[Dict[str, Any]], ts_ms: int) -> int:
    idx = -1
    for i, k in enumerate(klines):
        if int(k["timestamp"]) <= ts_ms:
            idx = i
        else:
            break
    return idx


def build_context_closes(klines: Sequence[Dict[str, Any]], idx: int, context_len: int) -> np.ndarray:
    start = max(0, idx + 1 - context_len)
    closes = [float(klines[i]["close"]) for i in range(start, idx + 1)]
    return np.asarray(closes, dtype=np.float64)


def actual_move_pct(closes: Sequence[float], horizon: int) -> float:
    if len(closes) < horizon + 1:
        return 0.0
    base = float(closes[0])
    future = float(closes[horizon])
    if base <= 0:
        return 0.0
    return (future - base) / base * 100.0


def load_timesfm_model(context_len: int, horizon: int):
    import torch
    import timesfm

    torch.set_float32_matmul_precision("high")
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
        "google/timesfm-2.5-200m-pytorch",
        force_download=False,
    )
    model.compile(
        timesfm.ForecastConfig(
            max_context=max(context_len, 512),
            max_horizon=max(horizon, 16),
            normalize_inputs=True,
            use_continuous_quantile_head=True,
            force_flip_invariance=True,
            infer_is_positive=True,
            fix_quantile_crossing=True,
            per_core_batch_size=1,
        )
    )
    return model


def forecast_direction(
    model,
    context: np.ndarray,
    *,
    horizon: int,
) -> Tuple[str, float]:
    point, _quant = model.forecast(horizon=horizon, inputs=[context])
    last = float(context[-1])
    pred = float(point[0, horizon - 1])
    if last <= 0:
        return "flat", 0.0
    delta_pct = (pred - last) / last * 100.0
    return direction_from_delta(delta_pct), round(delta_pct, 4)


def compare_signals(
    *,
    symbol: str,
    interval: str,
    horizon: int,
    context_len: int,
    klines: List[Dict[str, Any]],
    signals: List[SignalRow],
    model=None,
    dry_run: bool = False,
) -> ExperimentReport:
    notes: List[str] = []
    rows: List[CompareRow] = []
    used = 0
    dir_match = 0
    tfm_correct = 0
    skipped_wins = 0
    tfm_agree_win = 0

    closes_all = [float(k["close"]) for k in klines]

    for sig in signals:
        idx = find_kline_index(klines, sig.signal_at_ms)
        if idx < 32:
            continue
        if idx + horizon >= len(klines):
            continue
        ctx = build_context_closes(klines, idx, context_len)
        if len(ctx) < 32:
            continue

        last_close = float(ctx[-1])
        sig_dir = side_to_direction(sig.side)

        if dry_run or model is None:
            tfm_dir = "n/a"
            tfm_delta = 0.0
        else:
            tfm_dir, tfm_delta = forecast_direction(model, ctx, horizon=horizon)

        fut_slice = closes_all[idx : idx + horizon + 1]
        act_pct = actual_move_pct(fut_slice, horizon)
        act_dir = direction_from_delta(act_pct)

        match = tfm_dir == sig_dir if tfm_dir != "n/a" else False
        if tfm_dir != "n/a":
            used += 1
            if match:
                dir_match += 1
            if tfm_dir == act_dir and act_dir != "flat":
                tfm_correct += 1

        would_win = sig.pnl_pct > 0 or sig.outcome == "take_profit"
        agree_profit: Optional[bool] = None
        if sig.origin == "skipped_backtest" and would_win and tfm_dir != "n/a":
            skipped_wins += 1
            agree_profit = tfm_dir == sig_dir
            if agree_profit:
                tfm_agree_win += 1

        rows.append(
            CompareRow(
                signal_id=sig.signal_id,
                signal_at=datetime.fromtimestamp(
                    sig.signal_at_ms / 1000, tz=timezone.utc
                ).isoformat(),
                side=sig.side,
                source=sig.source,
                skip_reason=sig.skip_reason[:80],
                entry=sig.entry,
                last_close=round(last_close, 4),
                tfm_direction=tfm_dir,
                tfm_change_pct=tfm_delta,
                signal_direction=sig_dir,
                direction_match=match,
                actual_move_pct=round(act_pct, 4),
                actual_direction=act_dir,
                outcome=sig.outcome,
                pnl_pct=round(sig.pnl_pct, 4),
                tfm_agrees_with_profit=agree_profit,
            )
        )

    if not signals:
        notes.append(
            "Нет сигналов для символа. Скопируйте skipped_lab с сервера "
            "или укажите --skipped-file / --ledger-file."
        )
    if dry_run:
        notes.append("dry-run: TimesFM не загружался — только свечи и сигналы.")
    if used == 0 and signals and not dry_run:
        notes.append(
            "Сигналы не попали в окно свечей. Увеличьте --klines-bars "
            "или обновите --klines-cache."
        )

    return ExperimentReport(
        symbol=symbol.upper(),
        interval=interval,
        horizon=horizon,
        context_len=context_len,
        model="google/timesfm-2.5-200m-pytorch" if not dry_run else "dry-run",
        generated_at=datetime.now(timezone.utc).isoformat(),
        klines_count=len(klines),
        signals_total=len(signals),
        signals_used=used,
        direction_match_pct=round(dir_match / used * 100, 1) if used else 0.0,
        tfm_direction_accuracy_pct=round(tfm_correct / used * 100, 1) if used else 0.0,
        skipped_would_win=skipped_wins,
        skipped_tfm_agree_when_win=tfm_agree_win,
        rows=rows,
        notes=notes,
    )


def format_markdown(report: ExperimentReport) -> str:
    lines = [
        f"# TimesFM 2.5 × {report.symbol} ({report.interval}m)",
        "",
        f"- Сгенерировано: {report.generated_at}",
        f"- Модель: `{report.model}`",
        f"- Свечей: {report.klines_count}, горизонт: {report.horizon} баров, контекст: {report.context_len}",
        f"- Сигналов: {report.signals_total}, использовано TimesFM: {report.signals_used}",
        f"- Совпадение направления TimesFM ↔ сигнал: **{report.direction_match_pct}%**",
        f"- Точность направления TimesFM ↔ факт движения: **{report.tfm_direction_accuracy_pct}%**",
    ]
    if report.skipped_would_win:
        pct = round(report.skipped_tfm_agree_when_win / report.skipped_would_win * 100, 1)
        lines.append(
            f"- Пропуски skipped_lab с виртуальным плюсом: {report.skipped_would_win}; "
            f"TimesFM согласен с направлением: **{pct}%**"
        )
    if report.notes:
        lines.extend(["", "## Заметки", ""])
        for n in report.notes:
            lines.append(f"- {n}")
    lines.extend(["", "## Сравнения", ""])
    lines.append(
        "| UTC | side | source | TFM | signal | match | move% | outcome | pnl% |"
    )
    lines.append("|---|---|---|---|---|---|---:|---|---:|")
    for r in report.rows[:30]:
        lines.append(
            f"| {r.signal_at[:19]} | {r.side} | {r.source[:12]} | {r.tfm_direction} "
            f"| {r.signal_direction} | {'✓' if r.direction_match else '·'} "
            f"| {r.actual_move_pct:.2f} | {r.outcome or '-'} | {r.pnl_pct:.2f} |"
        )
    return "\n".join(lines) + "\n"


def load_klines_for_run(
    *,
    symbol: str,
    interval: str,
    klines_bars: int,
    klines_cache: Optional[Path],
    save_klines_cache: Optional[Path],
    fetch_only: bool,
) -> Tuple[Optional[List[Dict[str, Any]]], int]:
    """Возвращает (klines, exit_code). exit_code=0 fetch-only ok, None = продолжать."""
    if klines_cache and klines_cache.exists():
        klines, meta = load_klines_cache(klines_cache)
        print(
            f"Свечи из кэша: {klines_cache} "
            f"({meta.get('count')} bars, saved {meta.get('saved_at', '?')})"
        )
        if fetch_only:
            print("fetch-only + кэш: новые свечи не запрашивались.")
            return None, 0
        return klines, 0

    print(f"Загрузка свечей {symbol} interval={interval}m с Bybit …")
    try:
        klines = fetch_klines_history(
            symbol, interval=interval, min_bars=klines_bars
        )
    except urllib.error.HTTPError as exc:
        print(
            f"Bybit HTTP {exc.code}: {exc.reason}. "
            "Если CloudFront блокирует страну — запустите на сервере "
            "или используйте --klines-cache после успешного --fetch-only.",
            file=sys.stderr,
        )
        return None, 1
    except urllib.error.URLError as exc:
        print(f"Bybit недоступен: {exc}", file=sys.stderr)
        return None, 1

    print(f"Свечей получено: {len(klines)}")
    out_path = save_klines_cache or (
        DEFAULT_OUT_DIR / f"{symbol.upper()}_{interval}m.json" if fetch_only else None
    )
    if out_path and klines:
        save_klines_cache_path(out_path, symbol=symbol, interval=interval, klines=klines)
        print(f"Кэш свечей сохранён: {out_path}")

    if fetch_only:
        return None, 0
    return klines, 0


def save_klines_cache_path(
    path: Path, *, symbol: str, interval: str, klines: List[Dict[str, Any]]
) -> None:
    save_klines_cache(path, symbol=symbol, interval=interval, klines=klines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="TimesFM 2.5 vs bot signals (Bybit closes, лаборатория)"
    )
    ap.add_argument("--symbol", default="BTCUSDT", help="Символ Bybit linear")
    ap.add_argument("--interval", default="15", help="Интервал свечей Bybit (мин)")
    ap.add_argument("--horizon", type=int, default=12, help="Горизонт прогноза (бars, 12×15m=3ч)")
    ap.add_argument("--context-len", type=int, default=512, help="История для TimesFM")
    ap.add_argument("--max-signals", type=int, default=20, help="Макс. сигналов за прогон")
    ap.add_argument("--klines-bars", type=int, default=1500, help="Сколько свечей загрузить")
    ap.add_argument(
        "--skipped-file",
        type=Path,
        default=None,
        help="skipped_backtest/results.jsonl (локально или с сервера)",
    )
    ap.add_argument(
        "--ledger-file",
        type=Path,
        default=None,
        help="signal_ledger.jsonl — пропущенные без results",
    )
    ap.add_argument(
        "--trade-history",
        type=Path,
        default=ROOT.parent / "dumps" / "trade_history_aw.jsonl",
        help="Fallback: реальные входы",
    )
    ap.add_argument(
        "--klines-cache",
        type=Path,
        default=None,
        help="JSON-кэш свечей (если Bybit с ПК недоступен)",
    )
    ap.add_argument(
        "--save-klines-cache",
        type=Path,
        default=None,
        help="Сохранить загруженные свечи в файл",
    )
    ap.add_argument(
        "--fetch-only",
        action="store_true",
        help="Только скачать свечи в кэш, без TimesFM",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Куда писать отчёт",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Без TimesFM — проверка свечей и сигналов",
    )
    args = ap.parse_args()

    skipped = args.skipped_file
    if skipped is None and DEFAULT_SKIPPED_AW.exists():
        skipped = DEFAULT_SKIPPED_AW
    ledger = args.ledger_file
    if ledger is None and DEFAULT_LEDGER_AW.exists():
        ledger = DEFAULT_LEDGER_AW

    klines, kline_rc = load_klines_for_run(
        symbol=args.symbol,
        interval=args.interval,
        klines_bars=args.klines_bars,
        klines_cache=args.klines_cache,
        save_klines_cache=args.save_klines_cache,
        fetch_only=args.fetch_only,
    )
    if kline_rc != 0:
        return kline_rc
    if args.fetch_only:
        return 0
    assert klines is not None

    if len(klines) < args.horizon + 64:
        print(f"Мало свечей: {len(klines)}", file=sys.stderr)
        return 1

    signals, sig_src = resolve_signals(
        symbol=args.symbol,
        limit=args.max_signals,
        skipped_file=skipped,
        ledger_file=ledger,
        trade_history=args.trade_history,
    )
    print(f"Сигналы: {len(signals)} из {sig_src}")

    model = None
    if not args.dry_run:
        print("Загрузка TimesFM 2.5 (первый раз — ~400 МБ с Hugging Face)…")
        try:
            model = load_timesfm_model(args.context_len, args.horizon)
        except ImportError:
            print(
                'Нет пакета timesfm. Установите: pip install "timesfm[torch]==2.0.2"',
                file=sys.stderr,
            )
            return 1
        print("Модель готова.")

    report = compare_signals(
        symbol=args.symbol,
        interval=args.interval,
        horizon=args.horizon,
        context_len=args.context_len,
        klines=klines,
        signals=signals,
        model=model,
        dry_run=args.dry_run,
    )
    report.notes.insert(0, f"Источник сигналов: {sig_src}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = args.out_dir / f"timesfm_{args.symbol}_{stamp}.json"
    md_path = args.out_dir / f"timesfm_{args.symbol}_{stamp}.md"
    json_path.write_text(
        json.dumps(
            {
                **{k: v for k, v in asdict(report).items() if k != "rows"},
                "rows": [asdict(r) for r in report.rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    md_path.write_text(format_markdown(report), encoding="utf-8")

    print("")
    print(format_markdown(report))
    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
