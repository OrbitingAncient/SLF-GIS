"""National Weather Service alerts and forecast (no API key)."""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

from shared import USER_AGENT, WEATHER_CACHE_DIR

logger = logging.getLogger(__name__)

ALERTS_URL = "https://api.weather.gov/alerts/active"
POINTS_URL = "https://api.weather.gov/points"


def _nws_get(url: str, timeout: int = 20) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _nws_cache_path(kind: str, lat: float, lon: float):
    WEATHER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return WEATHER_CACHE_DIR / f"nws_{kind}_{lat:.3f}_{lon:.3f}.json"


def _read_nws_cache(path, max_age_s: float = 10800) -> Any | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > max_age_s:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def fetch_forecast(lat: float, lon: float, timeout: int = 20) -> list[dict[str, Any]]:
    """NWS 7 day forecast. Cached on disk for 3 hours."""
    cache = _nws_cache_path("forecast", lat, lon)
    cached = _read_nws_cache(cache)
    if isinstance(cached, list):
        return cached
    try:
        pts = _nws_get(f"{POINTS_URL}/{lat:.4f},{lon:.4f}", timeout=timeout)
        forecast_url = (pts.get("properties") or {}).get("forecast")
        if not forecast_url:
            return []
        payload = _nws_get(forecast_url, timeout=timeout)
    except Exception as exc:
        logger.warning("NWS forecast failed: %s", exc)
        cached = _read_nws_cache(cache, max_age_s=7 * 24 * 3600)
        return cached if isinstance(cached, list) else []

    periods = []
    for p in (payload.get("properties") or {}).get("periods") or []:
        periods.append(
            {
                "name": p.get("name"),
                "start": p.get("startTime"),
                "is_day": p.get("isDaytime"),
                "temp_f": p.get("temperature"),
                "wind": p.get("windSpeed"),
                "precip_pct": (p.get("probabilityOfPrecipitation") or {}).get("value"),
                "short": p.get("shortForecast"),
                "detail": (p.get("detailedForecast") or "")[:280],
            }
        )
    try:
        cache.write_text(json.dumps(periods), encoding="utf-8")
    except Exception:
        pass
    return periods


def fetch_alerts(lat: float, lon: float, timeout: int = 20) -> list[dict[str, Any]]:
    cache = _nws_cache_path("alerts", lat, lon)
    cached = _read_nws_cache(cache, max_age_s=1800)
    if isinstance(cached, list):
        return cached
    url = f"{ALERTS_URL}?point={lat:.4f},{lon:.4f}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("NWS alerts failed: %s", exc)
        stale = _read_nws_cache(cache, max_age_s=7 * 24 * 3600)
        if isinstance(stale, list):
            return stale
        return []

    features = payload.get("features") or []
    alerts = []
    for feat in features:
        props = feat.get("properties") or {}
        event = props.get("event") or "Alert"
        alerts.append(
            {
                "event": event,
                "headline": props.get("headline") or event,
                "severity": props.get("severity") or "",
                "urgency": props.get("urgency") or "",
                "ends": props.get("ends") or "",
                "instruction": (props.get("instruction") or "")[:400],
                "is_frost": any(k in event.lower() for k in ("frost", "freeze", "hard freeze")),
            }
        )
    try:
        cache.write_text(json.dumps(alerts), encoding="utf-8")
    except Exception:
        pass
    return alerts
