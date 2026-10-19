# Shoreline Fruit GIS Dashboard

Operations dashboard for **Shoreline Fruit INC** — tart cherry production across Michigan. It combines satellite bloom detection, orchard-local weather, published phenology models, National Weather Service alerts, and deep links to MSU Enviroweather.

This started as a NASA Space Apps prototype (“Farm to Computer”). It is being rebuilt as a tool a farm manager can actually open during the season.

**This is decision support, not a spray recommendation and not a substitute for field scouting.**

## What works now

- **11 growing regions** covering the northern (Grand Traverse, Leelanau, Antrim, Benzie, Manistee) and southern/west-central (Oceana, Mason, Van Buren, Berrien) belts. Coordinates are public region centroids / nearest weather stations, not internal orchard polygons.
- **Weather** from Open-Meteo (hourly temp, RH, precip, wind), cached on disk, with Meteostat/NOAA airport fallback.
- **Chill** as simple 32–45°F hours and the Utah model (954 CU target for Montmorency).
- **GDD** with labeled models: Zavalloni base 4°C from 1 Mar, MSU-style base 42°F from 1 Jan, and post-chill base 41°F.
- **Real Enhanced Bloom Index** from the 50 PlanetScope 4-band scenes (Mar–Sep 2025), date selector, true-color + EBI, and a bloom-strength time series.
- **NWS active alerts** (frost/freeze and others) for the selected region.
- **Pest calendar** that links official Enviroweather models instead of inventing spray timing.
- CSV + PDF export.

## Run it

Python 3.11+ (tested on 3.13). From the repo root:

```
python -m venv .venv
.\.venv\Scripts\activate
pip install -r dashboard/requirements.txt
shiny run --launch-browser dashboard/app.py
```

On macOS/Linux, activate with `source .venv/bin/activate`.

The first weather fetch needs internet. After that, hourly series live in `data/weather/` and the app works offline for cached regions. Prefetch every region with:

```
python scripts/fetch_weather.py
```

PlanetScope files belong in `data/planetscope/` as `YYYYMMDD.tif` (50 scenes for 2025 are already in the repo).

## Data sources

| Source | Used for | Key? |
| --- | --- | --- |
| Open-Meteo | Hourly weather + short forecast at each region | No |
| Meteostat / NOAA airports | Fallback temperature | No |
| api.weather.gov | Active alerts | No |
| Local PlanetScope GeoTIFFs | Enhanced Bloom Index | No |
| MSU Enviroweather | Pest/disease models (linked, not scraped) | No |
| `config/farms.json` | Regions | — |
| `config/phenology.json` | Chill/GDD/stage tables | — |
| `data/reference/ground_truth.csv` | Observed bloom dates | — |

## Science notes

Enhanced Bloom Index (Chen, Jin & Brown 2019):

\[ {\rm EBI} = \frac{R+G+B}{(G/B)\,(R-B+\epsilon)} \]

\(\epsilon = 256\) for the 8-bit PlanetScope DN in this repo. Do **not** divide those files by 10,000 (that scaling is for Sentinel-2 L2A).

Montmorency stages follow Zavalloni et al. 2006 (GDD base 4°C from 1 March, side green at 120 GDD). MSU NW reports also cite ~360 GDD base 42°F for tart full bloom; both are shown and labeled.

## Tests

```
.\.venv\Scripts\python -m pytest -q
```

## License

GNU GPL v3. See `LICENSE`.
