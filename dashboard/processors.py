"""Processor color, brix, and delivery windows."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from field_log import latest_for
from harvest import harvest_belt
from shared import CONFIG_DIR


def load_plants() -> list[dict[str, Any]]:
    path = CONFIG_DIR / "processors.json"
    with path.open(encoding="utf-8") as f:
        return list(json.load(f)["plants"])


def _md(season: int, mmdd: str) -> date:
    m, d = mmdd.split("-")
    return date(season, int(m), int(d))


def plant_status(plant: dict[str, Any], region: dict[str, Any], season: int, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    start = _md(season, plant["window_start"])
    end = _md(season, plant["window_end"])
    sample = latest_for(region["id"]) if region.get("id") == plant.get("region_id") else latest_for(plant["region_id"])
    flags = []
    if as_of > end:
        flags.append("Delivery window closed")
    elif as_of < start:
        flags.append(f"Opens {start.isoformat()}")
    else:
        flags.append("In delivery window")
    color = None
    brix = None
    if sample:
        color = sample.get("color")
        brix = sample.get("brix")
        if color is not None and int(color) < int(plant["color_min"]):
            flags.append(f"Color {color} below spec {plant['color_min']}")
        if brix not in (None, "") and float(brix) < float(plant["brix_min"]):
            flags.append(f"Brix {brix} below spec {plant['brix_min']}")
        if as_of >= end - timedelta(days=5) and color is not None and int(color) < int(plant["color_min"]):
            flags.append("Risk of missing the slot")
    else:
        flags.append("No field sample yet")
    miss = any("below spec" in f or "missing" in f.lower() for f in flags)
    return {
        "plant": plant,
        "start": start,
        "end": end,
        "sample": sample,
        "flags": flags,
        "miss": miss,
        "belt": harvest_belt(region) if region else "",
    }
