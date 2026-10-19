"""Color and firmness samples. Field scouting overrides the calendar."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from shared import REFERENCE_DIR

LOG_PATH = REFERENCE_DIR / "color_firmness.csv"
COLUMNS = ["date", "region_id", "color", "firmness", "brix", "notes"]


def load_log() -> pd.DataFrame:
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(LOG_PATH)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df


def append_sample(
    *,
    when: date,
    region_id: str,
    color: int,
    firmness: int,
    brix: float | None,
    notes: str,
) -> pd.DataFrame:
    df = load_log()
    row = {
        "date": when,
        "region_id": region_id,
        "color": int(color),
        "firmness": int(firmness),
        "brix": brix if brix not in (None, "") else None,
        "notes": notes or "",
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(LOG_PATH, index=False)
    return df


def latest_for(region_id: str) -> dict | None:
    df = load_log()
    if df.empty:
        return None
    sub = df[df["region_id"] == region_id].dropna(subset=["date"])
    if sub.empty:
        return None
    rec = sub.sort_values("date").iloc[-1]
    return rec.to_dict()
