"""Download and cache season data for every region. Cap is 3.5 GB."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Callable

from farms import regions
from iem_asos import load_or_fetch_iem
from nws_alerts import fetch_alerts, fetch_forecast
from phenology import (
    c_to_f,
    chill_complete_timestamp,
    current_stage,
    frost_status,
    gdd_msu_base42,
    gdd_post_chill,
    gdd_zavalloni,
    simple_chill_hours,
    utah_chill_units,
)
from satellite import ebi_time_series
from shared import PLANETSCOPE_DIR, PROCESSED_DIR, WEATHER_CACHE_DIR
from weather import cache_path, load_or_fetch_weather

logger = logging.getLogger(__name__)

MAX_BYTES = int(3.5 * 1024 * 1024 * 1024)
ProgressFn = Callable[[int, int, str], None]


def dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def cache_bytes() -> int:
    return dir_bytes(WEATHER_CACHE_DIR) + dir_bytes(PROCESSED_DIR)


def cache_label() -> str:
    n = cache_bytes()
    if n > 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB on disk"
    return f"{n / 1024:.0f} KB on disk"


def metrics_path(region_id: str, season: int) -> Path:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    return PROCESSED_DIR / f"metrics_{region_id}_{season}.json"


def save_metrics(region_id: str, season: int, df) -> dict[str, Any]:
    if df is None or getattr(df, "empty", True) or "temp_c" not in df.columns:
        payload = {
            "simple": 0,
            "utah": 0,
            "gdd4": 0,
            "gdd42": 0,
            "gdd_pc": 0,
            "chill_done": None,
            "stage": current_stage(0),
            "min_f": float("nan"),
            "frost": "No temperature data",
        }
        metrics_path(region_id, season).write_text(json.dumps(payload, default=str), encoding="utf-8")
        return payload
    gdd4 = gdd_zavalloni(df, season)
    stage = current_stage(gdd4)
    recent = df.tail(72) if df is not None and not df.empty else df
    min_f = float(c_to_f(recent["temp_c"].min())) if recent is not None and not recent.empty else float("nan")
    gdd_pc, chill_done = gdd_post_chill(df, season)
    payload = {
        "simple": simple_chill_hours(df, season),
        "utah": utah_chill_units(df, season),
        "gdd4": gdd4,
        "gdd42": gdd_msu_base42(df, season),
        "gdd_pc": gdd_pc,
        "chill_done": None if chill_done is None else str(chill_done),
        "stage": stage,
        "min_f": min_f,
        "frost": frost_status(min_f, stage["current"].get("critical_temp_f", 28)),
    }
    metrics_path(region_id, season).write_text(json.dumps(payload, default=str), encoding="utf-8")
    return payload


def load_metrics(region_id: str, season: int) -> dict[str, Any] | None:
    path = metrics_path(region_id, season)
    wx = cache_path(region_id, season)
    if not path.exists():
        return None
    if wx.exists() and path.stat().st_mtime < wx.stat().st_mtime:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def prefetch_season(season: int, progress: ProgressFn | None = None, force: bool = False) -> str:
    """Fetch weather, airport, NWS, metrics, and EBI for one bloom season.

    Stops if cache would exceed 3.5 GB.
    """
    regs = regions()
    steps = []
    for r in regs:
        steps.append(("weather", r))
        steps.append(("iem", r))
        steps.append(("nws", r))
        steps.append(("metrics", r))
    steps.append(("ebi", None))
    total = len(steps)
    done = 0
    notes = []

    for kind, r in steps:
        done += 1
        if cache_bytes() > MAX_BYTES:
            notes.append("Stopped at 3.5 GB cap")
            break
        if kind == "weather":
            label = f"{r['name']} weather {season}"
            if progress:
                progress(done, total, label)
            df, status = load_or_fetch_weather(r, season, force=force)
            notes.append(f"{r['id']}: {status} ({len(df)} rows)")
        elif kind == "iem":
            label = f"{r['name']} airport"
            if progress:
                progress(done, total, label)
            try:
                load_or_fetch_iem(r, season, force=force)
            except Exception as exc:
                logger.warning("IEM skip %s: %s", r["id"], exc)
        elif kind == "nws":
            label = f"{r['name']} NWS"
            if progress:
                progress(done, total, label)
            try:
                fetch_forecast(r["lat"], r["lon"])
                fetch_alerts(r["lat"], r["lon"])
            except Exception as exc:
                logger.warning("NWS skip %s: %s", r["id"], exc)
        elif kind == "metrics":
            label = f"{r['name']} metrics"
            if progress:
                progress(done, total, label)
            df, _ = load_or_fetch_weather(r, season, force=False)
            if df is not None and not df.empty:
                save_metrics(r["id"], season, df)
        elif kind == "ebi":
            label = "Satellite bloom series"
            if progress:
                progress(done, total, label)
            try:
                ebi_time_series(force=False)
            except Exception as exc:
                logger.warning("EBI series skip: %s", exc)

    return f"Done. {cache_label()}. " + "; ".join(notes[:4])
