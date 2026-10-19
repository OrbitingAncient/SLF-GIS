"""Map live conditions and NWS text to ten sky types with photo backgrounds."""

from __future__ import annotations

from typing import Any

from phenology import c_to_f

WEATHER_TYPES = (
    "thunderstorm",
    "snow",
    "rain",
    "fog",
    "frost",
    "wind",
    "heat",
    "cloudy",
    "partly",
    "sunny",
)

BG_URL = {kind: f"weather/{kind}.jpg" for kind in WEATHER_TYPES}


def classify_forecast(short: str, temp_f: float | None = None, wind: str = "") -> str:
    t = f"{short or ''} {wind or ''}".lower()
    if any(k in t for k in ("thunder", "t-storm", "lightning", "severe")):
        return "thunderstorm"
    if any(k in t for k in ("snow", "sleet", "flurries", "ice")):
        return "snow"
    if any(k in t for k in ("rain", "shower", "drizzle")):
        return "rain"
    if any(k in t for k in ("fog", "mist", "haze")):
        return "fog"
    if any(k in t for k in ("frost", "freeze", "freezing")):
        return "frost"
    if any(k in t for k in ("windy", "breezy", "gust")):
        return "wind"
    if temp_f is not None:
        try:
            if float(temp_f) >= 88:
                return "heat"
        except (TypeError, ValueError):
            pass
        try:
            if float(temp_f) <= 32 and "clear" in t:
                return "frost"
        except (TypeError, ValueError):
            pass
    if any(k in t for k in ("overcast", "cloudy")) and "partly" not in t and "mostly sunny" not in t:
        return "cloudy"
    if any(k in t for k in ("partly", "mostly cloudy", "mostly sunny", "few clouds")):
        return "partly"
    if any(k in t for k in ("sunny", "clear", "fair")):
        return "sunny"
    return "partly"


def classify_obs(df) -> str:
    if df is None or getattr(df, "empty", True) or "temp_c" not in df.columns:
        return "partly"
    last = df.iloc[-1]
    temp_f = float(c_to_f(last["temp_c"])) if last["temp_c"] == last["temp_c"] else None
    precip = float(last["precip_mm"]) if "precip_mm" in df.columns and last.get("precip_mm") == last.get("precip_mm") else 0.0
    cloud = float(last["cloud_pct"]) if "cloud_pct" in df.columns and last.get("cloud_pct") == last.get("cloud_pct") else 50.0
    wind = float(last["wind_kmh"]) if "wind_kmh" in df.columns and last.get("wind_kmh") == last.get("wind_kmh") else 0.0
    if precip >= 1.5:
        return "rain"
    if precip >= 0.2:
        return "rain"
    if temp_f is not None and temp_f <= 32:
        return "frost"
    if temp_f is not None and temp_f >= 88:
        return "heat"
    if wind >= 40:
        return "wind"
    if cloud >= 85:
        return "cloudy"
    if cloud >= 45:
        return "partly"
    return "sunny"


def bg_url(kind: str) -> str:
    return BG_URL.get(kind, BG_URL["partly"])


def harvest_tone(status: str) -> str:
    s = (status or "").lower()
    if s.startswith("closed"):
        return "gray"
    if s.startswith("opens"):
        return "orange"
    if "window" in s:
        return "green"
    return "blue"


def score_tone(score: float) -> str:
    if score >= 90:
        return "green"
    if score >= 70:
        return "orange"
    return "red"
