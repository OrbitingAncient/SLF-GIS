from datetime import date, timedelta

import pandas as pd

from harvest import (
    date_when_gdd_after_bloom_reaches,
    fruit_pct_final_diameter,
    harvest_belt,
    modeled_bloom_date,
    recommend_harvest,
    usda_calendar,
)


def test_belts():
    assert harvest_belt({"zone": "Northern Michigan"}) == "northwest"
    assert harvest_belt({"zone": "West Central Michigan"}) == "west_central"
    assert harvest_belt({"zone": "Southwest Michigan"}) == "southwest"


def test_usda_sw_is_earlier_than_nw():
    nw = usda_calendar({"zone": "Northern Michigan"}, 2026)
    sw = usda_calendar({"zone": "Southwest Michigan"}, 2026)
    assert sw["harvest_begin"] < nw["harvest_begin"]
    assert sw["peak_end"] < nw["peak_end"]


def test_fruit_fill_increases():
    assert fruit_pct_final_diameter(0) < 20
    assert fruit_pct_final_diameter(400) > fruit_pct_final_diameter(100)
    assert fruit_pct_final_diameter(1000) > 90


def _hourly_from(start: str, days: int, temp_c: float) -> pd.DataFrame:
    idx = pd.date_range(start, periods=days * 24, freq="h")
    return pd.DataFrame({"temp_c": temp_c, "precip_mm": 0.0}, index=idx)


def test_modeled_bloom_hits_243_gdd4():
    # 24°C for 13 days from Mar 1: (24-4)*13 = 260 GDD
    df = _hourly_from("2026-03-01", 20, 24.0)
    bloom = modeled_bloom_date(df, 2026)
    assert bloom is not None
    assert date(2026, 3, 12) <= bloom <= date(2026, 3, 16)


def test_recommend_window_around_65_dafb():
    bloom = date(2026, 5, 10)
    df = _hourly_from("2026-03-01", 150, 18.0)
    call = recommend_harvest(
        {"id": "williamsburg_gt", "name": "Williamsburg / Grand Traverse", "zone": "Northern Michigan"},
        2026,
        df,
        bloom,
        as_of=date(2026, 6, 1),
    )
    assert call.dafb_peak == bloom + timedelta(days=65)
    assert call.recommended_start <= call.recommended_peak <= call.recommended_end
    assert call.usda["harvest_begin"].month == 7


def test_gdd_target_date():
    bloom = date(2026, 5, 1)
    df = _hourly_from("2026-05-01", 80, 20.0)  # 16 GDD4/day → 950 in ~60 days
    hit = date_when_gdd_after_bloom_reaches(df, bloom, 950)
    assert hit is not None
    assert date(2026, 6, 25) <= hit <= date(2026, 7, 5)
