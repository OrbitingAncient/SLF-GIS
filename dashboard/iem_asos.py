"""Iowa Environmental Mesonet daily ASOS — NOAA airport observations for Michigan."""

from __future__ import annotations

import io
import logging
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

import pandas as pd

from shared import USER_AGENT, WEATHER_CACHE_DIR

logger = logging.getLogger(__name__)

IEM_DAILY = "https://mesonet.agron.iastate.edu/cgi-bin/request/daily.py"


def icao_to_iem(code: str) -> str:
    code = (code or "").upper()
    return code[1:] if code.startswith("K") and len(code) == 4 else code


def fetch_iem_daily(station: str, start: date, end: date, timeout: int = 30) -> pd.DataFrame:
    """Daily max/min/precip for a MI ASOS station (e.g. TVC, ACB)."""
    sid = icao_to_iem(station)
    params = {
        "network": "MI_ASOS",
        "stations": sid,
        "year1": start.year,
        "month1": start.month,
        "day1": start.day,
        "year2": end.year,
        "month2": end.month,
        "day2": end.day,
        "format": "csv",
        "na": "blank",
        "var": "max_temp_f,min_temp_f,precip_in,avg_rh,max_wind_gust_kts",
    }
    url = IEM_DAILY + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("IEM daily failed for %s: %s", sid, exc)
        return pd.DataFrame()

    if not text.strip() or "ERROR" in text[:200].upper():
        return pd.DataFrame()
    df = pd.read_csv(io.StringIO(text))
    if df.empty:
        return df
    date_col = "day" if "day" in df.columns else ("date" if "date" in df.columns else df.columns[0])
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).set_index(date_col).sort_index()
    df["source"] = f"iem:{sid}"
    return df


def load_or_fetch_iem(region: dict[str, Any], season: int, force: bool = False) -> tuple[pd.DataFrame, str]:
    airport = (region or {}).get("airport") or {}
    code = airport.get("id")
    if not code:
        return pd.DataFrame(), "No airport mapped"
    WEATHER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = WEATHER_CACHE_DIR / f"iem_{icao_to_iem(code)}_{season}.csv"
    if path.exists() and not force:
        df = pd.read_csv(path, parse_dates=[0], index_col=0)
        return df, f"Cached IEM {icao_to_iem(code)}"
    start = date(season, 3, 1)
    end = min(date.today(), date(season, 9, 30))
    df = fetch_iem_daily(code, start, end)
    if df.empty:
        return df, f"IEM {icao_to_iem(code)} unavailable"
    df.to_csv(path)
    return df, f"Live IEM {icao_to_iem(code)}"
