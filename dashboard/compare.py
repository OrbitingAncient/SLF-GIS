"""Southwest vs northwest vs west-central harvest board."""

from __future__ import annotations

from datetime import date

from farms import get_region, regions
from harvest import modeled_bloom_date, recommend_harvest, usda_calendar
from weather import load_cached


ANCHORS = {
    "northwest": "williamsburg_gt",
    "west_central": "oceana_hart",
    "southwest": "berrien",
}


def belt_row(belt: str, season: int, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    rid = ANCHORS[belt]
    region = get_region(rid)
    cached = load_cached(rid, season)
    usda = usda_calendar(region, season)
    if cached is None or cached.empty:
        return {
            "belt": belt.replace("_", " "),
            "region": region["name"],
            "bloom": usda.get("bloom_start"),
            "peak": usda["peak_start"],
            "window": f"{usda['harvest_begin']} to {usda['peak_end']}",
            "gdd": None,
            "ahead": None,
            "source": "USDA usual",
        }
    bloom = modeled_bloom_date(cached, season) or usda.get("bloom_start")
    call = recommend_harvest(region, season, cached, bloom, bloom_source="modeled")
    ahead = (call.recommended_peak - as_of).days
    return {
        "belt": belt.replace("_", " "),
        "region": region["name"],
        "bloom": call.bloom,
        "peak": call.recommended_peak,
        "window": f"{call.recommended_start} to {call.recommended_end}",
        "gdd": call.gdd4_after_bloom,
        "ahead": ahead,
        "source": "Cached weather",
    }


def comparison_table(season: int) -> list[dict]:
    return [belt_row(b, season) for b in ("southwest", "west_central", "northwest")]
