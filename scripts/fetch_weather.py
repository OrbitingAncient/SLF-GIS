"""Prefetch Open-Meteo hourly weather for every configured region."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from farms import regions  # noqa: E402
from weather import load_or_fetch_weather  # noqa: E402


def main() -> None:
    seasons = [2025, 2026]
    for region in regions():
        for season in seasons:
            df, status = load_or_fetch_weather(region, season, force=True)
            print(f"{region['id']:20} {season}  rows={len(df):5}  {status}")


if __name__ == "__main__":
    main()
