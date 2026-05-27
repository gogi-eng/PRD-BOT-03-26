from pathlib import Path

from prd_agent.copy_mirror.pump_dump_agent import FeatureProfile, PumpDumpScout


class _DummyAdapter:
    async def get_tickers(self):
        return []


def _cfg(tmp_path: Path):
    return {
        "_root": str(tmp_path),
        "copy_mirror": {
            "pump_dump_agent": {
                "enabled": True,
                "state_file": "state.json",
            }
        },
    }


def test_signal_levels_long_short(tmp_path: Path):
    s = PumpDumpScout(_cfg(tmp_path), _DummyAdapter())
    sl_b, tp_b = s._signal_levels("Buy", 100.0, 1.0)
    sl_s, tp_s = s._signal_levels("Sell", 100.0, 1.0)
    assert sl_b < 100 < tp_b
    assert tp_s < 100 < sl_s


def test_impulse_filter(tmp_path: Path):
    s = PumpDumpScout(_cfg(tmp_path), _DummyAdapter())
    feat = {"vol_ratio": 3.0, "atr_pct": 1.0, "oi_delta": 5.0, "abs_funding": 0.2}
    assert s._impulse_ok("Buy", 3.0, feat)
    assert not s._impulse_ok("Buy", 1.0, feat)
    assert not s._impulse_ok("Buy", 3.0, {**feat, "oi_delta": 0.5})


def test_score_increases_on_stronger_features(tmp_path: Path):
    s = PumpDumpScout(_cfg(tmp_path), _DummyAdapter())
    p = FeatureProfile(
        side="Buy",
        vol_ratio_median=2.0,
        atr_pct_median=1.0,
        oi_delta_median=5.0,
        abs_funding_median=0.2,
        event_count=10,
    )
    weak = s._score({"vol_ratio": 1.0, "atr_pct": 0.5, "oi_delta": 2.0, "abs_funding": 0.1}, p)
    strong = s._score({"vol_ratio": 3.0, "atr_pct": 2.0, "oi_delta": 8.0, "abs_funding": 0.4}, p)
    assert strong > weak
