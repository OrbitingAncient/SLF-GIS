"""Rank shake days and build a morning sheet."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from field_log import latest_for
from harvest import HarvestCall, daily_summary, score_ops_day
from phenology import c_to_f
from weather import daily_summary as wx_daily


def plan_days(weather: pd.DataFrame, start: date, n: int = 10, call: HarvestCall | None = None) -> pd.DataFrame:
    daily = wx_daily(weather)
    if daily.empty:
        return pd.DataFrame()
    rows = []
    for i in range(n):
        d = start + timedelta(days=i)
        match = daily[pd.to_datetime(daily.index).date == d]
        if match.empty:
            continue
        row = match.iloc[0]
        score, note = score_ops_day(row)
        in_window = ""
        if call:
            if call.recommended_start <= d <= call.recommended_end:
                in_window = "yes"
            else:
                in_window = "no"
        tmax = float(row["tmax_c"]) if pd.notna(row.get("tmax_c")) else float("nan")
        tmin = float(row["tmin_c"]) if pd.notna(row.get("tmin_c")) else float("nan")
        precip = float(row["precip_mm"]) if "precip_mm" in row and pd.notna(row["precip_mm"]) else 0.0
        rows.append(
            {
                "date": d,
                "tmin_f": None if tmin != tmin else round(float(c_to_f(tmin)), 1),
                "tmax_f": None if tmax != tmax else round(float(c_to_f(tmax)), 1),
                "precip_mm": round(precip, 1),
                "ops_score": round(score, 0),
                "in_window": in_window,
                "note": note,
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        win = out["in_window"].map({"yes": 0, "no": 1}).fillna(1)
        out = out.assign(_win=win).sort_values(["_win", "ops_score"], ascending=[True, False])
        out["rank"] = range(1, len(out) + 1)
        out = out.drop(columns=["_win"]).sort_values("date")
    return out


def shake_this_week(plan: pd.DataFrame, call: HarvestCall | None, today: date | None = None) -> str:
    today = today or date.today()
    if call is None:
        return "No harvest call yet."
    if today > call.recommended_end:
        return "Window closed. Not this week."
    if today < call.recommended_start - timedelta(days=7):
        return f"Too early. Window opens {call.recommended_start}."
    if plan is None or plan.empty:
        return f"Window {call.recommended_start} to {call.recommended_end}. Check weather."
    week = plan[(plan["date"] >= today) & (plan["date"] <= today + timedelta(days=6))]
    if week.empty:
        return f"Window {call.recommended_start} to {call.recommended_end}."
    in_win = week[week["in_window"] == "yes"]
    use = in_win if not in_win.empty else week
    best = use.sort_values("ops_score", ascending=False).iloc[0]
    return f"Best shake day {best['date']} (score {int(best['ops_score'])})."


FROST_CHECKLIST = [
    "Check tomorrow morning low vs stage killing temp",
    "Wind machines: fuel, oil, start test",
    "Irrigation / under-tree: lines charged if you use water",
    "Inversion: compare 5 ft vs tower if Enviroweather is loaded",
    "Crew call list for a 2 a.m. start",
    "Hydro-cool ready if you shake after a cold night",
]


def next_48h_min_f(weather: pd.DataFrame, as_of: date | None = None) -> tuple[float | None, date | None]:
    as_of = as_of or date.today()
    daily = wx_daily(weather)
    if daily.empty:
        return None, None
    end = as_of + timedelta(days=2)
    lows = []
    for d in (as_of, as_of + timedelta(days=1), end):
        match = daily[pd.to_datetime(daily.index).date == d]
        if match.empty:
            continue
        tmin = match.iloc[0].get("tmin_c")
        if tmin == tmin:
            lows.append((d, float(c_to_f(float(tmin)))))
    if not lows:
        return None, None
    d, val = min(lows, key=lambda x: x[1])
    return val, d


def field_adjust(call: HarvestCall) -> str:
    sample = latest_for(call.region_id)
    if not sample:
        return "No color/firmness sample. Calendar stands."
    color = sample.get("color")
    firm = sample.get("firmness")
    bits = [f"Last sample {sample.get('date')}: color {color}/5, firmness {firm}/5"]
    if color is not None and int(color) < 3:
        bits.append("Fruit still pale. Hold.")
    elif color is not None and int(color) >= 4:
        bits.append("Color is there. Weather decides the day.")
    return " ".join(str(b) for b in bits)
