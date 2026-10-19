import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from phenology import (
    current_stage,
    frost_status,
    growing_degree_days,
    simple_chill_hours,
    utah_chill_units,
    utah_weight_c,
)


def test_utah_weights():
    assert utah_weight_c(0.0) == 0.0
    assert utah_weight_c(5.0) == 1.0
    assert utah_weight_c(17.0) == -0.5
    assert utah_weight_c(20.0) == -1.0


def _hourly(start: str, hours: int, temp_c: float) -> pd.DataFrame:
    idx = pd.date_range(start, periods=hours, freq="h")
    return pd.DataFrame({"temp_c": temp_c}, index=idx)


def test_simple_chill_hours_counts_band_only():
    # 10 hours in band during Nov 2024 (season 2025 window)
    df = _hourly("2024-11-01", 10, 4.0)
    assert simple_chill_hours(df, 2025) == 10
    df_hot = _hourly("2024-11-01", 10, 20.0)
    assert simple_chill_hours(df_hot, 2025) == 0


def test_utah_chill_does_not_go_negative():
    cold = _hourly("2024-11-01", 5, 5.0)  # +1 each
    hot = _hourly("2024-11-01 05:00", 20, 25.0)  # -1 each but floored at 0
    df = pd.concat([cold, hot])
    assert utah_chill_units(df, 2025) == 0.0


def test_gdd_hourly_conversion():
    # 24 hours at 14°C, base 4°C => 10 degree-days
    df = _hourly("2025-04-01", 24, 14.0)
    gdd = growing_degree_days(df, base_c=4.0, start=pd.Timestamp("2025-04-01"))
    assert abs(gdd - 10.0) < 1e-6


def test_stage_progression():
    assert current_stage(0)["current"]["id"] == "side_green"
    assert current_stage(243)["current"]["id"] == "full_bloom"
    assert current_stage(400)["current"]["id"] == "petal_fall"


def test_frost_status_uses_stage_threshold():
    assert "CRITICAL" in frost_status(26.0, 28.0)
    assert "WATCH" in frost_status(30.0, 28.0)
    assert "Clear" in frost_status(40.0, 28.0)
