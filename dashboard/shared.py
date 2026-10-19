"""Paths and shared constants for the Shoreline Fruit dashboard."""

from __future__ import annotations

from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
PLANETSCOPE_DIR = DATA_DIR / "planetscope"
WEATHER_CACHE_DIR = DATA_DIR / "weather"
PROCESSED_DIR = DATA_DIR / "processed"
REFERENCE_DIR = DATA_DIR / "reference"

# Back-compat for the original import
app_dir = APP_DIR

USER_AGENT = "SLF-GIS/1.0 (Shoreline Fruit orchard dashboard; local operations tool)"
