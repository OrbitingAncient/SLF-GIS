"""Load Shoreline Fruit growing regions from config."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from shared import CONFIG_DIR


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    path = CONFIG_DIR / "farms.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def regions() -> list[dict[str, Any]]:
    return list(load_config()["regions"])


def region_choices() -> dict[str, str]:
    """Display name -> region id, for Shiny select inputs."""
    return {r["name"]: r["id"] for r in regions()}


def get_region(region_id: str) -> dict[str, Any]:
    for r in regions():
        if r["id"] == region_id:
            return r
    raise KeyError(f"Unknown region: {region_id}")


def get_region_by_name(name: str) -> dict[str, Any]:
    for r in regions():
        if r["name"] == name:
            return r
    raise KeyError(f"Unknown region name: {name}")


def default_region_id() -> str:
    return "williamsburg_gt"
