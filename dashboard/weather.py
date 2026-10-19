"""Weather from Open-Meteo (primary) and Meteostat airports (optional overlay)."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from shared import USER_AGENT, WEATHER_CACHE_DIR

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARS = "temperature_2m,relative_humidity_2m,precipitation,windspeed_10m,cloudcover"


def _http_get_json(url: str, timeout: int = 45) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _hourly_frame(payload: dict[str, Any]) -> pd.DataFrame:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return pd.DataFrame(columns=["temp_c", "rh", "precip_mm", "wind_kmh", "cloud_pct", "source"])
    df = pd.DataFrame(
        {
            "temp_c": hourly.get("temperature_2m"),
            "rh": hourly.get("relative_humidity_2m"),
            "precip_mm": hourly.get("precipitation"),
            "wind_kmh": hourly.get("windspeed_10m"),
            "cloud_pct": hourly.get("cloudcover"),
        },
        index=pd.to_datetime(times),
    )
    df.index.name = "time"
    df["source"] = "open-meteo"
    return df


def fetch_open_meteo(lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    """Historical archive plus forecast, concatenated and de-duplicated."""
    today = datetime.now(timezone.utc).date()
    frames: list[pd.DataFrame] = []

    hist_end = min(end, today)
    if start <= hist_end:
        params = {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "start_date": start.isoformat(),
            "end_date": hist_end.isoformat(),
            "hourly": HOURLY_VARS,
            "timezone": "America/Detroit",
        }
        url = OPEN_METEO_ARCHIVE + "?" + urllib.parse.urlencode(params)
        logger.info("Open-Meteo archive %s", url)
        frames.append(_hourly_frame(_http_get_json(url)))

    if end >= today:
        forecast_days = min(16, max(1, (end - today).days + 1))
        params = {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "hourly": HOURLY_VARS,
            "forecast_days": str(forecast_days),
            "past_days": "2",
            "timezone": "America/Detroit",
        }
        url = OPEN_METEO_FORECAST + "?" + urllib.parse.urlencode(params)
        logger.info("Open-Meteo forecast %s", url)
        frames.append(_hourly_frame(_http_get_json(url)))

    if not frames:
        return pd.DataFrame(columns=["temp_c", "rh", "precip_mm", "wind_kmh", "cloud_pct", "source"])

    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def fetch_meteostat_airport(station_code: str, lat: float, lon: float, start: date, end: date) -> pd.DataFrame:
    """Optional NOAA/Meteostat airport series. Returns empty frame if unavailable."""
    try:
        import meteostat as ms
    except Exception as exc:
        logger.warning("meteostat not available: %s", exc)
        return pd.DataFrame(columns=["temp_c"])

    try:
        station = ms.Station(station_code, lat, lon)
        start_dt = datetime(start.year, start.month, start.day)
        end_dt = datetime(end.year, end.month, end.day, 23, 59)
        raw = ms.hourly(station, start_dt, end_dt).fetch()
        if raw is None or raw.empty or "temp" not in raw.columns:
            return pd.DataFrame(columns=["temp_c"])
        out = pd.DataFrame({"temp_c": raw["temp"]})
        out["source"] = f"meteostat:{station_code}"
        return out
    except Exception as exc:
        logger.warning("Meteostat fetch failed for %s: %s", station_code, exc)
        return pd.DataFrame(columns=["temp_c"])


def cache_path(region_id: str, season: int):
    WEATHER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return WEATHER_CACHE_DIR / f"{region_id}_{season}.csv"


def cache_meta_path(region_id: str, season: int):
    return WEATHER_CACHE_DIR / f"{region_id}_{season}.meta.json"


def load_cached(region_id: str, season: int) -> pd.DataFrame | None:
    path = cache_path(region_id, season)
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["time"], index_col="time")
    return df


def save_cache(region_id: str, season: int, df: pd.DataFrame, source: str) -> None:
    path = cache_path(region_id, season)
    out = df.copy()
    out.index.name = "time"
    out.to_csv(path)
    meta = {
        "region_id": region_id,
        "season": season,
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(out)),
    }
    cache_meta_path(region_id, season).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def cache_age_hours(region_id: str, season: int) -> float | None:
    meta_path = cache_meta_path(region_id, season)
    if not meta_path.exists():
        path = cache_path(region_id, season)
        if not path.exists():
            return None
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        return age / 3600.0
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(meta["fetched_at"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0
    except Exception:
        return None


def load_or_fetch_weather(
    region: dict[str, Any],
    season: int,
    *,
    force: bool = False,
    max_age_hours: float = 24.0,
) -> tuple[pd.DataFrame, str]:
    """Return (hourly frame, status message). Cache wins unless Refresh is pressed."""
    start = date(season - 1, 10, 1)
    end = min(date.today() + timedelta(days=7), date(season, 9, 30))
    if end < start:
        end = date.today() + timedelta(days=7)

    cached = load_cached(region["id"], season)
    age = cache_age_hours(region["id"], season)
    if cached is not None and not force:
        if age is None:
            return cached, "Cached weather"
        return cached, f"Cached weather ({age:.1f} h old)"

    try:
        df = fetch_open_meteo(region["lat"], region["lon"], start, end)
        if df.empty:
            raise RuntimeError("Open-Meteo returned no rows")
        save_cache(region["id"], season, df, "open-meteo")
        return df, "Live Open-Meteo (just fetched)"
    except Exception as exc:
        logger.warning("Open-Meteo failed: %s", exc)
        if cached is not None:
            return cached, f"Open-Meteo failed ({exc}); using cache"
        airport = region.get("airport") or {}
        if airport.get("id"):
            try:
                df = fetch_meteostat_airport(
                    airport["id"],
                    float(airport["lat"]),
                    float(airport["lon"]),
                    start,
                    end,
                )
                if not df.empty:
                    save_cache(region["id"], season, df, f"meteostat:{airport['id']}")
                    return df, f"Fallback Meteostat {airport['id']}"
            except Exception as exc2:
                logger.warning("Meteostat fallback failed: %s", exc2)
        return pd.DataFrame(columns=["temp_c"]), f"No weather available: {exc}"


def daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "temp_c" not in df.columns:
        return pd.DataFrame(columns=["tmin_c", "tmax_c", "tmean_c", "precip_mm"])
    g = df.copy()
    g["date"] = pd.to_datetime(g.index).tz_localize(None).normalize()
    agg = {"temp_c": ["min", "max", "mean"]}
    if "precip_mm" in g.columns:
        agg["precip_mm"] = "sum"
    out = g.groupby("date").agg(agg)
    out.columns = ["_".join(c).strip("_") if isinstance(c, tuple) else c for c in out.columns]
    rename = {
        "temp_c_min": "tmin_c",
        "temp_c_max": "tmax_c",
        "temp_c_mean": "tmean_c",
        "precip_mm_sum": "precip_mm",
    }
    out = out.rename(columns=rename)
    return out
