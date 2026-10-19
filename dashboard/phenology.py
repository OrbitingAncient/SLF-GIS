"""Chill, GDD, and tart-cherry phenology helpers.

Temperatures in the weather frame are Celsius unless noted.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from shared import CONFIG_DIR


@lru_cache(maxsize=1)
def load_phenology_config() -> dict[str, Any]:
    path = CONFIG_DIR / "phenology.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def c_to_f(c: float | np.ndarray | pd.Series) -> float | np.ndarray | pd.Series:
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def utah_weight_c(temp_c: float) -> float:
    """Utah model hourly weight from air temperature in Celsius."""
    if np.isnan(temp_c):
        return 0.0
    if temp_c < 1.5:
        return 0.0
    if temp_c < 2.5:
        return 0.5
    if temp_c < 9.2:
        return 1.0
    if temp_c < 12.5:
        return 0.5
    if temp_c < 16.0:
        return 0.0
    if temp_c < 18.0:
        return -0.5
    return -1.0


def _hourly_temps(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty or "temp_c" not in df.columns:
        return pd.Series(dtype=float)
    s = pd.to_numeric(df["temp_c"], errors="coerce")
    s.index = pd.to_datetime(df.index)
    return s


def season_bounds(season: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Dormancy through harvest-ish window for a bloom year."""
    start = pd.Timestamp(year=season - 1, month=10, day=1)
    end = pd.Timestamp(year=season, month=9, day=30)
    return start, end


def slice_season(df: pd.DataFrame, season: int) -> pd.DataFrame:
    start, end = season_bounds(season)
    if df is None or df.empty:
        return pd.DataFrame(columns=["temp_c"])
    idx = pd.to_datetime(df.index)
    mask = (idx >= start) & (idx <= end)
    return df.loc[mask]


def simple_chill_hours(
    df: pd.DataFrame,
    season: int,
    t_min_c: float = 0.0,
    t_max_c: float = 7.22,
) -> int:
    """Count hours in the 32–45°F band from Oct 1 through Apr 30."""
    temps = _hourly_temps(slice_season(df, season))
    if temps.empty:
        return 0
    start = pd.Timestamp(year=season - 1, month=10, day=1)
    end = pd.Timestamp(year=season, month=4, day=30, hour=23)
    window = temps[(temps.index >= start) & (temps.index <= end)]
    return int(((window >= t_min_c) & (window < t_max_c)).sum())


def _utah_weights(temps: np.ndarray) -> np.ndarray:
    t = np.asarray(temps, dtype=np.float64)
    w = np.zeros(t.shape, dtype=np.float64)
    w[(t >= 1.5) & (t < 2.5)] = 0.5
    w[(t >= 2.5) & (t < 9.2)] = 1.0
    w[(t >= 9.2) & (t < 12.5)] = 0.5
    w[(t >= 16.0) & (t < 18.0)] = -0.5
    w[t >= 18.0] = -1.0
    w[np.isnan(t)] = 0.0
    return w


def _running_chill(weights: np.ndarray) -> np.ndarray:
    out = np.empty(len(weights), dtype=np.float64)
    cu = 0.0
    for i, x in enumerate(weights):
        cu = cu + x
        if cu < 0.0:
            cu = 0.0
        out[i] = cu
    return out


def utah_chill_units(df: pd.DataFrame, season: int) -> float:
    """Utah CU from Oct 1. Never drops below zero."""
    temps = _hourly_temps(slice_season(df, season))
    if temps.empty:
        return 0.0
    start = pd.Timestamp(year=season - 1, month=10, day=1)
    end = pd.Timestamp(year=season, month=4, day=30, hour=23)
    window = temps[(temps.index >= start) & (temps.index <= end)].to_numpy()
    if window.size == 0:
        return 0.0
    return round(float(_running_chill(_utah_weights(window))[-1]), 1)


def chill_complete_timestamp(
    df: pd.DataFrame,
    season: int,
    requirement_cu: float = 954.0,
) -> pd.Timestamp | None:
    temps = _hourly_temps(slice_season(df, season))
    if temps.empty:
        return None
    start = pd.Timestamp(year=season - 1, month=10, day=1)
    end = pd.Timestamp(year=season, month=5, day=31, hour=23)
    window = temps[(temps.index >= start) & (temps.index <= end)]
    if window.empty:
        return None
    run = _running_chill(_utah_weights(window.to_numpy()))
    hit = np.where(run >= requirement_cu)[0]
    if hit.size == 0:
        return None
    return pd.Timestamp(window.index[int(hit[0])])


def growing_degree_days(
    df: pd.DataFrame,
    *,
    base_c: float,
    start: pd.Timestamp,
    end: pd.Timestamp | None = None,
) -> float:
    """Hourly GDD: sum(max(T - base, 0)) / 24."""
    temps = _hourly_temps(df)
    if temps.empty:
        return 0.0
    end = end or temps.index.max()
    window = temps[(temps.index >= start) & (temps.index <= end)]
    if window.empty:
        return 0.0
    return float((window - base_c).clip(lower=0).sum() / 24.0)


def gdd_zavalloni(df: pd.DataFrame, season: int, as_of: pd.Timestamp | None = None) -> float:
    start = pd.Timestamp(year=season, month=3, day=1)
    return round(growing_degree_days(df, base_c=4.0, start=start, end=as_of), 1)


def gdd_msu_base42(df: pd.DataFrame, season: int, as_of: pd.Timestamp | None = None) -> float:
    start = pd.Timestamp(year=season, month=1, day=1)
    return round(growing_degree_days(df, base_c=5.56, start=start, end=as_of), 1)


def gdd_post_chill(df: pd.DataFrame, season: int, as_of: pd.Timestamp | None = None) -> tuple[float, pd.Timestamp | None]:
    done = chill_complete_timestamp(df, season)
    if done is None:
        return 0.0, None
    val = round(growing_degree_days(df, base_c=5.0, start=done, end=as_of), 1)
    return val, done


def current_stage(gdd_base4_from_mar1: float) -> dict[str, Any]:
    stages = load_phenology_config()["stages_zavalloni_from_mar1_base4c"]
    current = stages[0]
    next_stage = stages[0]
    for i, stage in enumerate(stages):
        if gdd_base4_from_mar1 >= stage["gdd"]:
            current = stage
            next_stage = stages[min(i + 1, len(stages) - 1)]
        else:
            next_stage = stage
            break
    return {"current": current, "next": next_stage}


def frost_status(min_temp_f: float, critical_temp_f: float) -> str:
    if np.isnan(min_temp_f):
        return "No temperature data"
    if min_temp_f <= critical_temp_f:
        return f"CRITICAL {min_temp_f:.1f} F (threshold {critical_temp_f:.0f} F)"
    if min_temp_f <= critical_temp_f + 4:
        return f"WATCH {min_temp_f:.1f} F (threshold {critical_temp_f:.0f} F)"
    return f"Clear {min_temp_f:.1f} F (threshold {critical_temp_f:.0f} F)"
