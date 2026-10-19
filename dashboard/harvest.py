"""Harvest-window engine for Michigan Montmorency tart cherries.

Combines days-after-full-bloom, heat units, USDA usual dates, fruit-growth
stage, satellite color, and a dry-weather operations score.

This is decision support. Processor specs, fruit color, and firmness win.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from phenology import c_to_f, growing_degree_days, gdd_msu_base42, gdd_zavalloni
from shared import CONFIG_DIR, REFERENCE_DIR
from weather import daily_summary


@lru_cache(maxsize=1)
def load_harvest_config() -> dict[str, Any]:
    with (CONFIG_DIR / "harvest.json").open(encoding="utf-8") as f:
        return json.load(f)


def harvest_belt(region: dict[str, Any]) -> str:
    zone = (region or {}).get("zone") or ""
    if "Southwest" in zone:
        return "southwest"
    if "West Central" in zone:
        return "west_central"
    return "northwest"


def _md(season: int, mmdd: str) -> date:
    m, d = mmdd.split("-")
    return date(season, int(m), int(d))


def usda_calendar(region: dict[str, Any], season: int) -> dict[str, date]:
    cfg = load_harvest_config()["usda_rma_usual"]
    block = cfg[harvest_belt(region)]
    return {k: _md(season, v) for k, v in block.items() if k != "source"}


def modeled_bloom_date(df: pd.DataFrame, season: int) -> date | None:
    """First date Zavalloni GDD base 4°C from 1 Mar reaches full bloom (243)."""
    if df is None or df.empty or "temp_c" not in df.columns:
        return None
    start = pd.Timestamp(year=season, month=3, day=1)
    temps = pd.to_numeric(df["temp_c"], errors="coerce")
    temps.index = pd.to_datetime(df.index)
    window = temps[temps.index >= start]
    if window.empty:
        return None
    gdd = (window - 4.0).clip(lower=0).cumsum() / 24.0
    hit = gdd[gdd >= 243.0]
    if hit.empty:
        return None
    return pd.Timestamp(hit.index[0]).date()


def fruit_pct_final_diameter(gdd4_after_bloom: float) -> float:
    """Approximate double-sigmoid fruit fill after bloom.

    Shape is inspired by Zavalloni et al. 2006 (R² 0.936 on % final diameter)
    but the published coefficients are not reproduced here. Labeled as analog.
    """
    x = max(0.0, float(gdd4_after_bloom))
    stage_i = 55.0 / (1.0 + np.exp(-(x - 180.0) / 55.0))
    stage_iii = 45.0 / (1.0 + np.exp(-(x - 700.0) / 80.0))
    return float(min(100.0, stage_i + stage_iii))


def gdd_after_bloom(df: pd.DataFrame, bloom: date, as_of: date | None = None) -> float:
    start = pd.Timestamp(bloom)
    end = pd.Timestamp(as_of) if as_of else None
    return round(growing_degree_days(df, base_c=4.0, start=start, end=end), 1)


def date_when_gdd_after_bloom_reaches(df: pd.DataFrame, bloom: date, target: float) -> date | None:
    if df is None or df.empty or "temp_c" not in df.columns:
        return None
    temps = pd.to_numeric(df["temp_c"], errors="coerce")
    temps.index = pd.to_datetime(df.index)
    window = temps[temps.index >= pd.Timestamp(bloom)]
    if window.empty:
        return None
    gdd = (window - 4.0).clip(lower=0).cumsum() / 24.0
    hit = gdd[gdd >= target]
    if not hit.empty:
        return pd.Timestamp(hit.index[0]).date()
    # Project with last 7 observed days of heat
    daily = gdd.diff().dropna()
    if daily.empty:
        return None
    recent = daily.tail(24 * 7)
    per_day = float(recent.sum() / max(1.0, len(recent) / 24.0))
    if per_day <= 0.2:
        return None
    remaining = target - float(gdd.iloc[-1])
    days = int(np.ceil(remaining / per_day))
    last = pd.Timestamp(gdd.index[-1]).date()
    return last + timedelta(days=days)


def score_ops_day(row: pd.Series, cfg: dict[str, Any] | None = None) -> tuple[float, str]:
    """0–100 operations score for a calendar day. Higher = better shake day."""
    ops = (cfg or load_harvest_config())["ops"]
    precip = float(row["precip_mm"]) if "precip_mm" in row and pd.notna(row["precip_mm"]) else 0.0
    tmax_c = float(row["tmax_c"]) if "tmax_c" in row and pd.notna(row["tmax_c"]) else np.nan
    tmax_f = float(c_to_f(tmax_c)) if tmax_c == tmax_c else np.nan
    score = 100.0
    notes = []
    if precip >= ops["max_precip_mm_48h"]:
        score -= min(50.0, precip * 8)
        notes.append(f"{precip:.1f} mm rain")
    if tmax_f == tmax_f and tmax_f >= ops["heat_hard_f"]:
        score -= 25
        notes.append(f"hot {tmax_f:.0f}°F")
    elif tmax_f == tmax_f and tmax_f >= ops["heat_soft_f"]:
        score -= 10
        notes.append(f"warm {tmax_f:.0f}°F")
    if precip < 0.2 and (tmax_f != tmax_f or tmax_f < ops["heat_soft_f"]):
        notes.append("dry / moderate")
    return max(0.0, min(100.0, score)), "; ".join(notes) if notes else "ok"


def best_ops_window(daily: pd.DataFrame, start: date, end: date, width: int = 4) -> tuple[date, date, float] | None:
    if daily is None or daily.empty:
        return None
    idx = pd.to_datetime(daily.index).date
    mask = np.array([start <= d <= end for d in idx])
    if not mask.any():
        return None
    sub = daily.loc[mask].copy()
    scores = []
    for _, row in sub.iterrows():
        s, _ = score_ops_day(row)
        scores.append(s)
    sub["ops"] = scores
    if len(sub) < width:
        d0 = pd.Timestamp(sub.index[0]).date()
        d1 = pd.Timestamp(sub.index[-1]).date()
        return d0, d1, float(sub["ops"].mean())
    roll = sub["ops"].rolling(width, min_periods=width).mean().dropna()
    if roll.empty:
        d0 = pd.Timestamp(sub.index[0]).date()
        return d0, d0, float(sub["ops"].iloc[0])
    best_end = roll.idxmax()
    loc = list(sub.index).index(best_end)
    start_loc = max(0, loc - width + 1)
    d1 = pd.Timestamp(sub.index[start_loc]).date()
    d2 = pd.Timestamp(best_end).date()
    return d1, d2, float(roll.max())


def satellite_color_date(rgi_series: pd.DataFrame | None, threshold: float | None = None) -> date | None:
    if rgi_series is None or rgi_series.empty:
        return None
    thr = threshold if threshold is not None else load_harvest_config()["satellite"]["rgi_color_threshold"]
    df = rgi_series.dropna(subset=["rgi"])
    if df.empty:
        return None
    # Look at June–August only
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    summer = df[(df["date"].dt.month >= 6) & (df["date"].dt.month <= 8)]
    if summer.empty:
        return None
    baseline = summer["rgi"].iloc[: max(1, min(3, len(summer)))]
    base = float(baseline.mean())
    for _, rec in summer.iterrows():
        if rec["rgi"] >= max(thr, base * 1.08):
            return rec["date"].date()
    return None


@dataclass
class HarvestCall:
    region_id: str
    region_name: str
    belt: str
    season: int
    bloom: date
    bloom_source: str
    modeled_bloom: date | None
    dafb_today: int
    dafb_start: date
    dafb_peak: date
    dafb_end: date
    gdd4_after_bloom: float
    gdd_peak_date: date | None
    gdd42_from_mar1: float
    fruit_pct: float
    usda: dict[str, date]
    recommended_start: date
    recommended_peak: date
    recommended_end: date
    status: str
    confidence: str
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    ops_window: tuple[date, date, float] | None = None
    swd_watch: str = ""
    satellite_note: str = ""
    as_of: date = field(default_factory=date.today)


def _median_date(dates: list[date]) -> date:
    ordinals = sorted(d.toordinal() for d in dates)
    return date.fromordinal(ordinals[len(ordinals) // 2])


def recommend_harvest(
    region: dict[str, Any],
    season: int,
    weather: pd.DataFrame,
    bloom: date,
    *,
    bloom_source: str = "user biofix",
    as_of: date | None = None,
    rgi_series: pd.DataFrame | None = None,
) -> HarvestCall:
    cfg = load_harvest_config()
    as_of = as_of or date.today()
    belt = harvest_belt(region)
    usda = usda_calendar(region, season)
    dafb = cfg["dafb"]
    gcfg = cfg["gdd_after_bloom"]

    modeled = modeled_bloom_date(weather, season)
    dafb_start = bloom + timedelta(days=int(dafb["start"]))
    dafb_peak = bloom + timedelta(days=int(dafb["peak"]))
    dafb_end = bloom + timedelta(days=int(dafb["end"]))

    gdd4 = gdd_after_bloom(weather, bloom, as_of)
    gdd_peak = date_when_gdd_after_bloom_reaches(weather, bloom, float(gcfg["harvest_peak"]))
    gdd42 = gdd_msu_base42(weather, season, as_of=pd.Timestamp(as_of))
    fruit = fruit_pct_final_diameter(gdd4)

    votes = [dafb_peak, usda["peak_start"]]
    if gdd_peak is not None:
        votes.append(gdd_peak)
    color_day = satellite_color_date(rgi_series)
    if color_day is not None:
        votes.append(color_day + timedelta(days=8))

    peak = _median_date(votes)
    start = min(dafb_start, usda["harvest_begin"], peak - timedelta(days=5))
    end = max(dafb_end, usda["peak_end"], peak + timedelta(days=7))
    # Keep the window from running away
    start = max(start, bloom + timedelta(days=55))
    end = min(end, bloom + timedelta(days=80), usda["harvest_end"])
    if start > end:
        start, end = dafb_start, dafb_end

    daily = daily_summary(weather)
    ops = best_ops_window(daily, start, end, width=4)

    spread = (max(votes) - min(votes)).days
    if spread <= 5:
        confidence = "High. Models agree within 5 days."
    elif spread <= 10:
        confidence = "Moderate. Models differ by about a week."
    else:
        confidence = "Low. Models disagree. Scout color and firmness."

    today_dafb = (as_of - bloom).days
    if as_of < start:
        status = f"Opens {start.isoformat()}"
    elif as_of > end:
        status = f"Closed {end.isoformat()}"
    elif ops and ops[0] <= as_of <= ops[1]:
        status = "In window"
    else:
        status = "In window. Wait for a dry stretch."

    reasons = [
        f"DAFB peak {dafb_peak.isoformat()} ({dafb['peak']} days after bloom {bloom.isoformat()})",
        f"GDD peak {gdd_peak.isoformat() if gdd_peak else 'not reached'} (target {gcfg['harvest_peak']}, now {gdd4:.0f})",
        f"USDA {belt.replace('_', ' ')} active {usda['peak_start']} to {usda['peak_end']}",
        f"Fruit fill {fruit:.0f}% of final diameter",
    ]
    if color_day:
        reasons.append(f"Satellite color rise {color_day.isoformat()}")
    if ops:
        reasons.append(f"Best dry stretch {ops[0]} to {ops[1]}")

    caveats = [
        "Shake on fruit color and firmness, not calendar alone.",
        "Scouting and Enviroweather override this page.",
    ]
    if modeled and abs((modeled - bloom).days) > 7:
        caveats.append(
            f"Modeled bloom {modeled.isoformat()} vs biofix {bloom.isoformat()}."
        )

    swd_cfg = cfg["swd"]
    gdd4_mar = gdd_zavalloni(weather, season, as_of=pd.Timestamp(as_of))
    if today_dafb >= int(swd_cfg["color_risk_dafb"]) and gdd4_mar >= float(swd_cfg["green_safe_gdd4_from_mar1"]):
        swd = "SWD watch. Fruit is likely coloring."
    elif gdd4_mar < float(swd_cfg["green_safe_gdd4_from_mar1"]):
        swd = "Fruit still in the green GDD range."
    else:
        swd = "Approaching color."

    sat_note = ""
    if color_day:
        sat_note = f"Satellite color rise {color_day.isoformat()}."

    return HarvestCall(
        region_id=region["id"],
        region_name=region["name"],
        belt=belt,
        season=season,
        bloom=bloom,
        bloom_source=bloom_source,
        modeled_bloom=modeled,
        dafb_today=today_dafb,
        dafb_start=dafb_start,
        dafb_peak=dafb_peak,
        dafb_end=dafb_end,
        gdd4_after_bloom=gdd4,
        gdd_peak_date=gdd_peak,
        gdd42_from_mar1=gdd42,
        fruit_pct=fruit,
        usda=usda,
        recommended_start=start,
        recommended_peak=peak,
        recommended_end=end,
        status=status,
        confidence=confidence,
        reasons=reasons,
        caveats=caveats,
        ops_window=ops,
        swd_watch=swd,
        satellite_note=sat_note,
        as_of=as_of,
    )


def ops_calendar(weather: pd.DataFrame, start: date, days: int = 16) -> pd.DataFrame:
    daily = daily_summary(weather)
    if daily.empty:
        return pd.DataFrame()
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        ts = pd.Timestamp(d)
        if ts not in daily.index:
            # try normalized
            match = daily[pd.to_datetime(daily.index).date == d]
            if match.empty:
                continue
            row = match.iloc[0]
        else:
            row = daily.loc[ts]
        score, note = score_ops_day(row)
        tmax = float(row["tmax_c"]) if pd.notna(row.get("tmax_c")) else np.nan
        tmin = float(row["tmin_c"]) if pd.notna(row.get("tmin_c")) else np.nan
        precip = float(row["precip_mm"]) if "precip_mm" in row and pd.notna(row["precip_mm"]) else 0.0
        rows.append(
            {
                "date": d,
                "tmin_f": None if tmin != tmin else round(float(c_to_f(tmin)), 1),
                "tmax_f": None if tmax != tmax else round(float(c_to_f(tmax)), 1),
                "precip_mm": round(precip, 1),
                "ops_score": round(score, 0),
                "note": note,
            }
        )
    return pd.DataFrame(rows)
