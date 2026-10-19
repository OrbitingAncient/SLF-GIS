"""Parse MSU Enviroweather CSV exports. FTP fetch is optional later."""

from __future__ import annotations

import io
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from shared import DATA_DIR

EW_DIR = DATA_DIR / "enviroweather"

COLUMN_MAP = {
    r"date|time|timestamp": "time",
    r"air.?temp|atmp|temp.*f|tavg|tmean": "temp_f",
    r"temp.*c": "temp_c",
    r"rel.*hum|rh": "rh",
    r"precip|rain": "precip_in",
    r"leaf.?wet": "leaf_wet_hr",
    r"wind": "wind_mph",
    r"soil": "soil_f",
    r"gdd.*42|dd.*42|degree.?day.*42": "gdd42",
    r"gdd.*50|dd.*50": "gdd50",
    r"gdd|degree.?day": "gdd",
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def _match_col(name: str) -> str | None:
    n = _norm(name)
    for pat, dest in COLUMN_MAP.items():
        if re.search(pat, n):
            return dest
    return None


def parse_enviroweather_csv(raw: str | bytes, station_id: str = "unknown") -> pd.DataFrame:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    df = pd.read_csv(io.StringIO(text))
    rename = {}
    for col in df.columns:
        dest = _match_col(col)
        if dest and dest not in rename.values():
            rename[col] = dest
    df = df.rename(columns=rename)
    if "time" not in df.columns:
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(columns={df.columns[0]: "time"})
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).set_index("time").sort_index()
    if "temp_c" not in df.columns and "temp_f" in df.columns:
        df["temp_c"] = (pd.to_numeric(df["temp_f"], errors="coerce") - 32.0) * 5.0 / 9.0
    if "precip_mm" not in df.columns and "precip_in" in df.columns:
        df["precip_mm"] = pd.to_numeric(df["precip_in"], errors="coerce") * 25.4
    df["source"] = f"enviroweather:{station_id}"
    return df


def save_station(station_id: str, df: pd.DataFrame) -> Path:
    EW_DIR.mkdir(parents=True, exist_ok=True)
    path = EW_DIR / f"{station_id}.csv"
    out = df.copy()
    out.index.name = "time"
    out.to_csv(path)
    return path


def load_station(station_id: str) -> pd.DataFrame | None:
    path = EW_DIR / f"{station_id}.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, parse_dates=["time"], index_col="time")


def list_stations() -> list[str]:
    if not EW_DIR.exists():
        return []
    return sorted(p.stem for p in EW_DIR.glob("*.csv"))


def ftp_status() -> str:
    if os.environ.get("EW_FTP_HOST") and os.environ.get("EW_FTP_USER"):
        return "FTP credentials found in environment. Not pulling until you press Fetch."
    return "No FTP credentials. Drop a CSV export or set EW_FTP_HOST, EW_FTP_USER, EW_FTP_PASS later."


def fetch_ftp(dest_name: str = "ftp_pull.csv") -> tuple[bool, str]:
    host = os.environ.get("EW_FTP_HOST")
    user = os.environ.get("EW_FTP_USER")
    password = os.environ.get("EW_FTP_PASS", "")
    remote = os.environ.get("EW_FTP_PATH", "")
    if not host or not user:
        return False, ftp_status()
    try:
        from ftplib import FTP

        EW_DIR.mkdir(parents=True, exist_ok=True)
        dest = EW_DIR / dest_name
        with FTP(host, timeout=30) as ftp:
            ftp.login(user, password)
            with dest.open("wb") as fh:
                ftp.retrbinary(f"RETR {remote}", fh.write)
        return True, f"Saved {dest.name}"
    except Exception as exc:
        return False, f"FTP failed: {exc}"
