"""PlanetScope scene discovery and Enhanced Bloom Index."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime


import numpy as np
import pandas as pd

from shared import DATA_DIR, PLANETSCOPE_DIR, PROCESSED_DIR

SENTINEL_DIR = DATA_DIR / "sentinel2"

SCENE_RE = re.compile(r"(20\d{6})")


@dataclass(frozen=True)
class Scene:
    date: date
    path: str
    name: str
    sensor: str = "planetscope"


def compute_ebi(
    blue: np.ndarray,
    green: np.ndarray,
    red: np.ndarray,
    eps: float | None = None,
) -> np.ndarray:
    """Chen, Jin & Brown (2019) Enhanced Bloom Index.

    EBI = (R + G + B) / ((G / B) * (R - B + ε))

    ε = 256 for 8-bit DN, ε = 1 for 0–1 reflectance.
    """
    blue = np.asarray(blue, dtype=np.float64)
    green = np.asarray(green, dtype=np.float64)
    red = np.asarray(red, dtype=np.float64)
    if eps is None:
        peak = np.nanmax([np.nanmax(blue), np.nanmax(green), np.nanmax(red)])
        eps = 256.0 if peak > 1.5 else 1.0
    safe_blue = np.where(blue <= 0, np.nan, blue)
    denom = (green / safe_blue) * (red - blue + eps)
    with np.errstate(divide="ignore", invalid="ignore"):
        ebi = (red + green + blue) / denom
    ebi[~np.isfinite(ebi)] = np.nan
    ebi[ebi < 0] = np.nan
    return ebi


def compute_ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    red = np.asarray(red, dtype=np.float64)
    nir = np.asarray(nir, dtype=np.float64)
    denom = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (nir - red) / denom
    ndvi[~np.isfinite(ndvi)] = np.nan
    return ndvi


def cloud_snow_mask(blue: np.ndarray, green: np.ndarray, red: np.ndarray) -> np.ndarray:
    """True where the pixel looks like cloud or snow (bright, low saturation)."""
    blue = np.asarray(blue, dtype=np.float64)
    green = np.asarray(green, dtype=np.float64)
    red = np.asarray(red, dtype=np.float64)
    rgb = (blue + green + red) / 3.0
    mx = np.maximum(np.maximum(blue, green), red)
    mn = np.minimum(np.minimum(blue, green), red)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    return (rgb > 185) & (sat < 0.18)


def soil_mask(green: np.ndarray, red: np.ndarray, blue: np.ndarray, nir: np.ndarray | None) -> np.ndarray:
    green = np.asarray(green, dtype=np.float64)
    red = np.asarray(red, dtype=np.float64)
    blue = np.asarray(blue, dtype=np.float64)
    rgb = (blue + green + red) / 3.0
    brown = (red > green) & (red > blue) & (rgb > 35) & (rgb < 170)
    if nir is None:
        return brown
    ndvi = compute_ndvi(red, nir)
    return brown | ((ndvi < 0.08) & (rgb > 25) & (rgb < 170))


def vegetation_mask(green: np.ndarray, red: np.ndarray, blue: np.ndarray, nir: np.ndarray | None = None) -> np.ndarray:
    green = np.asarray(green, dtype=np.float64)
    red = np.asarray(red, dtype=np.float64)
    blue = np.asarray(blue, dtype=np.float64)
    valid = (green + red + blue) > 0
    cloud = cloud_snow_mask(blue, green, red)
    soil = soil_mask(green, red, blue, nir)
    green_dom = (green > red) & (green > blue)
    if nir is None:
        return valid & green_dom & ~cloud & ~soil
    ndvi = compute_ndvi(red, nir)
    return valid & (green_dom | (ndvi > 0.12)) & ~cloud & ~soil


def parse_scene_date(name: str) -> date | None:
    m = SCENE_RE.search(name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%d").date()


def list_scenes() -> list[Scene]:
    found: dict[tuple[str, date], Scene] = {}
    for folder, sensor in ((PLANETSCOPE_DIR, "planetscope"), (SENTINEL_DIR, "sentinel2")):
        if not folder.exists():
            continue
        for path in folder.glob("*.tif"):
            d = parse_scene_date(path.name)
            if d is None:
                continue
            found[(sensor, d)] = Scene(date=d, path=str(path), name=path.name, sensor=sensor)
    return [found[k] for k in sorted(found, key=lambda x: (x[1], x[0]))]


def scene_dates() -> list[date]:
    return [s.date for s in list_scenes()]


def load_scene_bands(path: str) -> dict[str, np.ndarray | object]:
    import rasterio

    with rasterio.open(path) as src:
        count = src.count
        blue = src.read(1)
        green = src.read(2) if count >= 2 else blue
        red = src.read(3) if count >= 3 else blue
        nir = src.read(4) if count >= 4 else None
        return {
            "blue": blue,
            "green": green,
            "red": red,
            "nir": nir,
            "transform": src.transform,
            "crs": src.crs,
            "bounds": src.bounds,
        }


def scene_ebi_product(path: str) -> dict[str, np.ndarray | float | None]:
    bands = load_scene_bands(path)
    blue, green, red = bands["blue"], bands["green"], bands["red"]
    nir = bands["nir"]
    ebi = compute_ebi(blue, green, red)
    mask = vegetation_mask(green, red, blue, nir)
    masked = np.where(mask, ebi, np.nan)
    finite = masked[np.isfinite(masked)]
    green_safe = np.where(np.asarray(green, dtype=np.float64) <= 0, np.nan, green)
    rgi = np.asarray(red, dtype=np.float64) / green_safe
    rgi_veg = rgi[mask & np.isfinite(rgi)]
    cloud = cloud_snow_mask(blue, green, red)
    stats = {
        "ebi": ebi,
        "masked": masked,
        "mask": mask,
        "rgb": np.dstack((red, green, blue)),
        "ndvi": compute_ndvi(red, nir) if nir is not None else None,
        "mean": float(np.nanmean(finite)) if finite.size else None,
        "p90": float(np.nanpercentile(finite, 90)) if finite.size else None,
        "max": float(np.nanmax(finite)) if finite.size else None,
        "veg_frac": float(mask.mean()) if mask.size else 0.0,
        "rgi": float(np.nanmedian(rgi_veg)) if rgi_veg.size else None,
        "cloud_frac": float(cloud.mean()) if cloud.size else 0.0,
        "cloudy": bool(cloud.mean() > 0.35) if cloud.size else False,
    }
    return stats


def _series_cache_path():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    return PROCESSED_DIR / "ebi_series.json"


def ebi_time_series(force: bool = False) -> pd.DataFrame:
    cache = _series_cache_path()
    scenes = list_scenes()
    names = [s.name for s in scenes] + ["mask-v2"]
    if cache.exists() and not force:
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if payload.get("scenes") == names:
                df = pd.DataFrame(payload["rows"])
                if not df.empty:
                    df["date"] = pd.to_datetime(df["date"]).dt.date
                return df
        except Exception:
            pass

    rows = []
    for scene in scenes:
        try:
            stats = scene_ebi_product(scene.path)
            rows.append(
                {
                    "date": scene.date.isoformat(),
                    "file": scene.name,
                    "mean": stats["mean"],
                    "p90": stats["p90"],
                    "max": stats["max"],
                    "veg_frac": stats["veg_frac"],
                    "rgi": stats.get("rgi"),
                    "cloud_frac": stats.get("cloud_frac"),
                    "cloudy": stats.get("cloudy"),
                    "sensor": scene.sensor,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "date": scene.date.isoformat(),
                    "file": scene.name,
                    "mean": None,
                    "p90": None,
                    "max": None,
                    "veg_frac": None,
                    "rgi": None,
                    "error": str(exc),
                }
            )
    cache.write_text(json.dumps({"scenes": names, "rows": rows}, indent=2), encoding="utf-8")
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df
