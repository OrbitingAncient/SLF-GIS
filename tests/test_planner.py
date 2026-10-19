from datetime import date, timedelta

import pandas as pd

from harvest import recommend_harvest
from planner import plan_days, shake_this_week


def _hourly(start: str, days: int, temp_c: float) -> pd.DataFrame:
    idx = pd.date_range(start, periods=days * 24, freq="h")
    return pd.DataFrame({"temp_c": temp_c, "precip_mm": 0.0}, index=idx)


def test_shake_this_week_closed():
    bloom = date(2026, 5, 10)
    df = _hourly("2026-03-01", 150, 18.0)
    call = recommend_harvest(
        {"id": "williamsburg_gt", "name": "Williamsburg / Grand Traverse", "zone": "Northern Michigan"},
        2026,
        df,
        bloom,
        as_of=date(2026, 9, 1),
    )
    plan = plan_days(df, date(2026, 9, 1), n=5, call=call)
    msg = shake_this_week(plan, call, today=date(2026, 9, 1))
    assert "closed" in msg.lower()
