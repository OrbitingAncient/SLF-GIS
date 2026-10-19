# SLF-GIS / Farm to Computer — Project Analysis & Improvement Roadmap

**Date:** 2026 analysis  
**Repo:** SLF-GIS (Shoreline Fruit GIS / NASA Space Apps "Farm to Computer")  
**Core idea:** Use PlanetScope satellite imagery + weather data to detect cherry blossom timing (via Enhanced Bloom Index) and correlate with temperature/chill/GDD drivers for Michigan cherry production risk.

---

## Executive Summary

This is a strong **prototype** from a NASA Space Apps Challenge. It has:
- Real scientific grounding (Chen et al. 2019 EBI paper for similar blooms).
- Actual PlanetScope 4-band imagery for the key 2025 season over Shoreline Fruit (largest MI cherry orchard).
- A working pyShiny interactive dashboard skeleton.
- Live weather interpolation + basic phenology indicators.

**However, the current dashboard does not deliver on its core promise.** The "EBI" plot is not EBI (it renders raw band 1 of the most-recently-modified TIF). The date slider is completely dead code. Chill/GDD calculations accumulate over the entire fetched weather window instead of proper seasonal windows. As a result, the app is visually appealing but scientifically misleading or non-functional for the stated goal.

The experimental notebook contains the EBI logic but was never integrated.

**Verdict:** High potential. With focused fixes to data integration + phenology modeling + reactivity, it can become a credible, reusable decision-support tool for growers or researchers. Many improvements are high-leverage and low-to-medium effort.

---

## Current State Assessment

### Strengths
- Motivating README with economic context, chill/GDD science, and paper citation.
- 50 small (311x427, 4-band uint8) PlanetScope scenes spanning Mar–Sep 2025 — excellent temporal coverage around the reported ~Apr 29 2025 bloom.
- Multi-farm selector + inverse-distance temperature interpolation from two airports.
- Value boxes, folium map, PDF/CSV export, basic theming.
- Small total data size (~18 MB) — easy to keep in repo.
- Used real tools (rasterio, meteostat, shiny, folium, reportlab).

### Critical Defects (Impact: High)
1. **No actual EBI computation in the running app** (`dashboard/app.py:89`).
   - `load_ebi_raster()` does `sorted(..., key=mtime)[-most recent]` then `src.read(1)` + viridis.
   - The TIFs are raw 4-band PlanetScope (Blue=1, Green=2, Red=3, NIR=4 per notebook). Band 1 shown as "EBI".
   - Notebook has EBI code (multiple slightly inconsistent versions); never saved as products or called from dashboard.
2. **Dead UI element**: `date_slider` ("5-Day Window Start") is rendered but **never read** in any reactive or output.
3. **Broken phenology metrics** (`chill_points`, `gdd_value`):
   - Run over the entire `load_weather_data()` range (current season + 60 days future).
   - Chill: simple boolean count in 0–7.22 °C band (no seasonal bounds, no warm-temperature resets).
   - GDD: base ~5 °C summed over whole period /24. No "after chill satisfied" logic. README correctly describes the science; code does not implement the windows.
4. **Global side effects on import**: `df_tvc, df_acb = load_weather_data()` at module level. Slow, brittle (API), fails to empty DF silently, non-reproducible.
5. **Brittle data discovery**: `Path("data")` relative + mtime sort. Depends on cwd when running `shiny run dashboard/app.py`. `dashboard/data/` dir is empty.
6. **Missing .gitignore**: `__pycache__` was present; easy to accidentally commit large data or `.env`.
7. **No temporal linking**: Imagery dates (from filenames) are not associated with weather windows or EBI time series.

### Other Issues
- PDF export is crude text-only (reportlab canvas, no figures).
- Long-term plot is just a rolling average of the same data as the "5-day" plot.
- Notebook is Colab-only (Drive mounts, `!pip`, hard-coded `/content` paths).
- Requirements unpinned; no lockfile.
- Hard-coded everything (farms, stations, thresholds).
- Title says "2026" while data + README are 2025 retrospective.
- Limited error UX and no offline mode.

---

## Prioritized Improvement Recommendations

### Tier 1 — Core Correctness (Do These First, 1–4 days total)
These turn the prototype from "demo that lies" into "tool that informs".

| Priority | Improvement | Effort | Impact | Notes |
|----------|-------------|--------|--------|-------|
| 1 | Implement real EBI calculation (port + correct formula from notebook). Support the uint8 data here (ε=256, no /10000 scaling). Add optional vegetation mask using green-dominant or simple NIR threshold. | Low-Med | Critical | Formula per README: `(R+G+B) / ((G/B)*(R-B+ε))`. Create `compute_ebi(blue, green, red)` pure function. |
| 2 | Date-aware imagery selection. Parse `YYYYMMDD` from filenames into a sorted list of available scenes. Replace or activate the date slider (or add a `input_select("image_date")`). Make `ebi_plot` show **EBI for the chosen date**, with correct title + transform. | Low | Critical | This makes the existing date_slider concept finally useful. |
| 3 | Add **EBI / Bloom Strength time series** plot. For every available date, compute a scalar (max, mean, or % pixels above threshold) on masked EBI. Plot as line chart with markers. Overlay temperature or GDD markers. | Med | High | This is the "killer feature" that was intended: see bloom onset vs weather. |
| 4 | Fix weather + phenology calculations. | Med | High | - Add season/year selector or "Analyze 2025 bloom event" mode.<br>- Ship or cache a 2025 weather CSV for reproducibility/offline.<br>- Proper chill accumulation window (e.g. 1 Oct prior year → 30 Apr).<br>- Track chill completion date, then start GDD from there.<br>- Optional: implement simple Utah-style penalty for warm hours >60 °F / ~15.5 °C. |
| 5 | Robust data paths + move or document data location. Prefer `app_dir.parent / "data" / "planetscope"`. Or copy/symlink the 50 TIFs under `dashboard/data/planetscope`. Add clear error + instructions in UI when no scenes found. | Low | High | |

### Tier 2 — Scientific Value & Features (Next 1–2 weeks)
- Proper seasonal chill/GDD with configurable start dates and model choice (Simple / Utah / note Dynamic).
- Correlate: auto-detect EBI rise dates and show preceding 5–14 day temp/GDD summary.
- Improve frost alert: hours below threshold during/after bloom window, not just recent min.
- Use the 4th (NIR) band for better tree/vegetation masking (NDVI-style or NIR > threshold) before EBI aggregation.
- Multiple indices: compute NDVI/EVI alongside EBI for comparison (canopy green-up vs bloom).
- Better PDF report: embed the current EBI image + time series plot + key stats.
- Add "known bloom window" annotations (Apr 26–May 10 2025 per README) as reference lines.
- Farm-specific notes or future yield impact text.

### Tier 3 — Architecture, Code Quality, Maintainability
- Refactor:
  - `dashboard/shared.py` or new `dashboard/data.py` / `phenology.py` / `satellite.py`.
  - Pure functions for `compute_ebi`, `accumulate_chill`, `compute_gdd`, `interpolate_temps`.
  - Reactive values: `selected_date`, `current_ebi_raster`, `season_weather`.
- Remove global import-time weather fetch. Use `@reactive.calc` + caching (shiny has `reactive.file_reader` or just memo + disk cache).
- Add a `scripts/` or `dashboard/scripts/compute_ebi.py` that can batch-process the raw TIFs into `data/ebi/YYYYMMDD_ebi.tif` (or store as single-band + metadata). This decouples heavy lifting.
- Clean notebook or convert key cells into the new scripts + a "reproduce 2025 analysis" notebook that runs locally against `../data/planetscope`.
- Type hints + docstrings on the math functions.
- Unit tests (pytest) for phenology math and EBI (easy to test pure funcs with synthetic arrays).
- Pin dependencies (`requirements.txt` with `==` or switch to `uv` / `pip-tools` + `requirements.lock`).
- Add `.gitignore` (standard Python + `*.tif` if you decide data is too big, or keep the small set; `__pycache__`, `.env`, etc.).

### Tier 4 — UX / Polish / Dashboard Power
- Make all plots react to the selected image date (highlight the 5-day pre-image window on the temperature plot, show "days since chill complete", etc.).
- Replace the vague "5-Day Window Start" slider with either a proper date picker or an image selector + "days before" control.
- Improve layout: use shiny's `ui.nav_panel`, accordions, or `ui.layout_sidebar` refinements. Add a "Methodology" expandable card explaining EBI + chill/GDD.
- Better empty / error states and loading indicators.
- Theming: expand `styles.css` (current is minimal but decent).
- Map upgrades: show weather station markers + lines to farm, or simple raster overlay if you rasterize EBI (folium + raster is possible but heavy).
- Export: include a small table of recent EBI values + weather summary in the PDF.

### Tier 5 — DevOps, Collaboration, Extensibility
- Add GitHub Actions: lint (ruff), test (pytest), perhaps a "build dashboard image" check.
- Docker / `shiny run` container for easy sharing.
- Deployment docs: shinyapps.io (Python support?), Hugging Face Spaces, or a cheap VPS + systemd.
- Config-driven farms + data roots (YAML/JSON) so others can add orchards without code changes.
- Optional: support user-uploaded scenes (fileInput in shiny) for ad-hoc EBI on new dates.
- Generalization hooks for other crops (apples per the README "What's next?").
- Consider STAC / planetary-computer or official Planet API for fresh data in future seasons (requires keys + quota).

### Tier 6 — Longer-term / Higher Ambition
- Time-series modeling: predict bloom date from cumulative GDD + EBI trend.
- Validation: hard-code or ingest reported bloom dates (news, farm reports) and show EBI vs ground truth.
- Alerts prototype: simple threshold rules → console or (later) email.
- Scale to full Sentinel-2 tiles or larger Planet scenes + proper AOI clipping / zonal stats.
- Yield proxy: if historical county or farm yield data can be found, simple regression.
- Move some heavy compute to a small FastAPI backend if dashboard gets slow, but unnecessary for current data sizes.

---

## Quick Wins (Can Do in < 1 Day, High Visible Impact)
1. Add a proper `.gitignore`.
2. Wire `input.date_slider()` or (better) create an `input_select` populated from the 50 scene dates. Update `ebi_plot` title + show something date-specific.
3. Extract `def compute_ebi(blue, green, red, eps=256): ...` and call it in `load_ebi...` (or precompute).
4. Fix the two value boxes that say "Chill Hours" and "GDD Accumulated" to at least scope to a reasonable pre-bloom window (hard-code 2025 season dates for now).
5. Update the README "To run" section with a note about needing the `data/planetscope/` TIFs and what the current limitations are.
6. Change the app title to reflect 2025 retrospective analysis (or make the year a config).

---

## Risks & Open Questions
- **Data availability**: PlanetScope scenes here were apparently downloaded for the challenge. Future seasons or other orchards will need new downloads (API access, cost, or public alternatives like Sentinel-2 with lower res).
- **Ground truth gap**: Without actual bloom observation dates or yield numbers tied to this orchard, EBI is still a proxy.
- **Model fidelity**: The simple chill/GDD implementations are educational but real farm decisions use more sophisticated models (Dynamic model mentioned in README is complex). Be transparent in UI.
- **Reproducibility**: Live meteostat calls + mtime-based loading make runs non-deterministic today.
- **Scope creep**: The "apple farmers" question in README is open-ended. Decide whether to stay laser-focused on tart cherries + EBI or generalize.

---

## Suggested Implementation Order (Pragmatic)
1. .gitignore + requirements pinning + robust DATA_DIR.
2. Implement `compute_ebi` + make `ebi_plot` actually compute and display EBI for the newest (or a fixed) date. Add a note "EBI now computed client-side from raw bands".
3. Parse dates + add working image date selector (dropdown or slider over indices). Update ebi_plot + filename.
4. Add the EBI time-series line chart (biggest "aha" moment for users).
5. Season-aware cached weather + corrected chill/GDD (at minimum for the 2025 event).
6. Refactor into modules + tests.
7. Polish exports, explanations, and deployment story.

---

## Files to Touch (Current Hotspots)
- `dashboard/app.py` — almost everything lives here (monolith).
- `dashboard/shared.py` — nearly empty, should own paths + shared data.
- `dashboard/styles.css` — small extensions.
- `experimental/satellite_imaging.ipynb` — extract useful bits or archive.
- New: `dashboard/phenology.py`, `dashboard/satellite.py`, `scripts/compute_ebi.py`, `data/processed/` or similar.
- `README.md` — update "What's next" and run instructions.
- Root: `.gitignore`, perhaps `pyproject.toml` or just better `requirements`.

---

## Conclusion

This project has the **right ingredients** (good data, clear science story, working UI framework) but the **integration between satellite processing and the interactive dashboard is incomplete**. Closing that gap (EBI on real dates + time series + proper seasonal metrics) would transform it from a cool demo into something a grower or extension agent could actually use to understand risk in a given year.

Most of the highest-value work is **refactoring + porting existing notebook logic + adding reactivity**, not inventing new science.

If you want, I can:
- Implement Tier 1 items directly (start with EBI integration + date selector + time series plot).
- Create the refactored module structure.
- Write the batch `compute_ebi.py` script + update the dashboard to prefer precomputed single-band EBI products.
- Generate a design doc for a v2 "reproducible 2025 bloom analysis + live monitoring" version.
- Add tests + .gitignore + pinned deps as a quick baseline cleanup.

Just say the word (or pick 2–3 specific improvements) and we'll execute.

---

*Analysis based on full codebase read, data inspection (50 TIFs, 4 bands, uint8, UTM 16N, Mar–Sep 2025 dates), notebook cell extraction, and runtime behavior review.*
