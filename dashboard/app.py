"""Shoreline Fruit harvest dashboard."""

from __future__ import annotations

import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from faicons import icon_svg
from shiny import App, reactive, render, ui

from compare import comparison_table
from enviroweather import fetch_ftp, ftp_status, list_stations, parse_enviroweather_csv, save_station
from farms import default_region_id, get_region, get_region_by_name, region_choices, regions
from field_log import append_sample, load_log
from harvest import fruit_pct_final_diameter, modeled_bloom_date, ops_calendar, recommend_harvest, usda_calendar
from planner import FROST_CHECKLIST, field_adjust, next_48h_min_f, plan_days, shake_this_week
from processors import load_plants, plant_status
from session_state import load_ui_state, pin_ok, pin_required, save_ui_state
from iem_asos import load_or_fetch_iem
from nws_alerts import fetch_alerts, fetch_forecast
from phenology import (
    c_to_f,
    current_stage,
    frost_status,
    gdd_msu_base42,
    gdd_post_chill,
    gdd_zavalloni,
    load_phenology_config,
    simple_chill_hours,
    utah_chill_units,
)
from reports import build_pdf, morning_sheet_pdf, weather_csv_bytes
from prefetch import cache_label, load_metrics, prefetch_season, save_metrics
from satellite import ebi_time_series, list_scenes, scene_ebi_product
from shared import APP_DIR, REFERENCE_DIR
from weather import daily_summary, load_cached, load_or_fetch_weather
from weather_look import bg_url, classify_forecast, classify_obs, harvest_tone, score_tone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
plt.rcParams.update({"figure.facecolor": "white", "axes.grid": True, "grid.alpha": 0.25, "font.size": 11})

REGION_NAMES = list(region_choices().keys())
_SAVED = load_ui_state()
DEFAULT_NAME = _SAVED["region"] if _SAVED.get("region") in REGION_NAMES else next(
    r["name"] for r in regions() if r["id"] == default_region_id()
)
DEFAULT_SEASON = _SAVED["season"] if _SAVED.get("season") in ("2025", "2026") else "2026"
PHENO = load_phenology_config()
SCENES = list_scenes()
SCENE_LABELS = [s.date.isoformat() for s in SCENES] or ["none"]
DEFAULT_SCENE = _SAVED["scene_date"] if _SAVED.get("scene_date") in SCENE_LABELS else (SCENE_LABELS[-1] if SCENES else "none")
PLOT_H = 420


def _bloom_default(season: int) -> date:
    saved = _SAVED.get("bloom_date")
    if saved and str(_SAVED.get("season")) == str(season):
        try:
            return date.fromisoformat(str(saved)[:10])
        except ValueError:
            pass
    if season == 2025:
        return date(2025, 4, 29)
    return date(season, 5, 13)


def _fig(width=10.5, height=4.4):
    fig, ax = plt.subplots(figsize=(width, height), dpi=110)
    return fig, ax


def _style_dates(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(25)
        lbl.set_ha("right")


app_ui = ui.page_navbar(
    ui.nav_panel(
        "Harvest",
        ui.output_ui("shake_week_tile"),
        ui.p("Shake window for Montmorency tart cherries. Color and firmness still decide the day.", class_="note"),
        ui.output_ui("weather_hero"),
        ui.output_ui("harvest_kpis"),
        ui.output_ui("forecast_strip"),
        ui.card(ui.card_header("Why this date"), ui.output_ui("harvest_reasons")),
        ui.card(
            ui.card_header("Maturity models vs calendar"),
            ui.output_plot("harvest_models_plot", height=f"{PLOT_H}px", fill=False),
            full_screen=True,
        ),
        ui.card(
            ui.card_header("Fruit fill"),
            ui.output_plot("fruit_fill_plot", height=f"{PLOT_H}px", fill=False),
            full_screen=True,
        ),
        ui.accordion(
            ui.accordion_panel("Ops calendar", ui.output_ui("ops_table")),
            ui.accordion_panel("All regions", ui.output_ui("company_board")),
            id="harvest_extra",
            open=False,
            multiple=True,
        ),
        value="Harvest",
    ),
    ui.nav_panel("Overview", ui.output_ui("page_overview"), value="Overview"),
    ui.nav_panel("Phenology", ui.output_ui("page_phenology"), value="Phenology"),
    ui.nav_panel("Bloom", ui.output_ui("page_bloom"), value="Bloom"),
    ui.nav_panel("Weather", ui.output_ui("page_weather"), value="Weather"),
    ui.nav_panel("Planner", ui.output_ui("page_planner"), value="Planner"),
    ui.nav_panel("Field", ui.output_ui("page_field"), value="Field"),
    ui.nav_panel("Compare", ui.output_ui("page_compare"), value="Compare"),
    ui.nav_panel("Frost", ui.output_ui("page_frost"), value="Frost"),
    ui.nav_panel("Pests", ui.output_ui("page_pests"), value="Pests"),
    ui.nav_panel("Data", ui.output_ui("page_data"), value="Data"),
    ui.nav_panel("Sources", ui.output_ui("page_sources"), value="Sources"),
    ui.nav_panel("Reports", ui.output_ui("page_reports"), value="Reports"),
    sidebar=ui.sidebar(
        ui.h4("Shoreline Fruit"),
        ui.input_select("region", "Region", choices=REGION_NAMES, selected=DEFAULT_NAME),
        ui.input_select("season", "Season", choices=["2025", "2026"], selected=DEFAULT_SEASON),
        ui.input_date("bloom_date", "Full bloom date", value=_bloom_default(int(DEFAULT_SEASON)).isoformat()),
        ui.input_select("scene_date", "Satellite scene", choices=SCENE_LABELS, selected=DEFAULT_SCENE),
        ui.input_numeric("crews", "Crews available", value=4, min=1, max=40),
        ui.input_action_button("refresh", "Refresh this region", class_="btn-primary"),
        ui.input_action_button("prefetch", "Download season data"),
        ui.output_text("prefetch_msg"),
        ui.output_ui("cache_status"),
        ui.hr(),
        ui.input_password("pin", "PIN"),
        ui.input_action_button("unlock", "Unlock"),
        ui.output_text("lock_msg"),
        ui.hr(),
        ui.output_ui("sidebar_meta"),
        width="300px",
    ),
    id="main_tab",
    title="Shoreline Fruit GIS",
    fillable=False,
    header=ui.TagList(
        ui.include_css(APP_DIR / "styles.css"),
        ui.busy_indicators.use(),
        ui.tags.link(
            rel="icon",
            href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ccircle cx='8' cy='8' r='7' fill='%231a5c45'/%3E%3C/svg%3E",
        ),
    ),
)


def server(input, output, session):
    refresh_tick = reactive.value(0)
    unlocked = reactive.value(not pin_required())
    log_tick = reactive.value(0)
    last_season = reactive.value(int(DEFAULT_SEASON))

    @reactive.effect
    def _sync_bloom():
        yr = int(input.season())
        prev = last_season.get()
        if prev == yr:
            return
        last_season.set(yr)
        ui.update_date("bloom_date", value=_bloom_default(yr).isoformat())

    @reactive.effect
    def _persist_state():
        bloom = input.bloom_date()
        save_ui_state(
            {
                "region": input.region(),
                "season": input.season(),
                "bloom_date": str(bloom)[:10] if bloom else "",
                "scene_date": input.scene_date(),
            }
        )

    @reactive.effect
    @reactive.event(input.unlock)
    def _unlock():
        if pin_ok(input.pin()):
            unlocked.set(True)
        else:
            unlocked.set(False)

    @reactive.effect
    def _on_refresh():
        if input.refresh() > 0:
            load_or_fetch_weather(region(), season(), force=True)
            refresh_tick.set(refresh_tick.get() + 1)

    @reactive.calc
    def region():
        return get_region_by_name(input.region())

    @reactive.calc
    def season() -> int:
        return int(input.season())

    @reactive.calc
    def weather_pack():
        refresh_tick.get()
        return load_or_fetch_weather(region(), season(), force=False)

    @reactive.calc
    def weather_df() -> pd.DataFrame:
        return weather_pack()[0]

    @reactive.calc
    def weather_status() -> str:
        return weather_pack()[1]

    @reactive.calc
    def metrics():
        cached = load_metrics(region()["id"], season())
        if cached:
            return cached
        df = weather_df()
        return save_metrics(region()["id"], season(), df)

    prefetch_msg_val = reactive.value("")

    @reactive.effect
    @reactive.event(input.prefetch)
    def _prefetch():
        yr = season()
        with ui.Progress(min=0, max=1) as prog:
            def cb(i, n, label):
                prog.set(min(1, i / max(n, 1)), message="Downloading season data", detail=label)

            msg = prefetch_season(yr, progress=cb, force=False)
        prefetch_msg_val.set(msg)
        refresh_tick.set(refresh_tick.get() + 1)
        ui.notification_show("Season data ready.", duration=6)

    @output
    @render.text
    def prefetch_msg():
        return prefetch_msg_val.get()

    @output
    @render.ui
    def cache_status():
        refresh_tick.get()
        return ui.p(cache_label(), class_="text-muted")

    @output
    @render.text
    def lock_msg():
        if not pin_required():
            return "No PIN set (config/auth.json)."
        return "Unlocked." if unlocked.get() else "Locked. Enter PIN to save field samples."

    def on_tab(name: str) -> bool:
        try:
            return input.main_tab() == name
        except Exception:
            return name == "Harvest"

    @output
    @render.ui
    def page_overview():
        if not on_tab("Overview"):
            return ui.div()
        return ui.TagList(
            ui.output_ui("overview_hero"),
            ui.output_ui("overview_kpis"),
            ui.card(
                ui.card_header("Last 7 days"),
                ui.output_plot("temp_week", height=f"{PLOT_H}px", fill=False),
                full_screen=True,
            ),
            ui.card(
                ui.card_header("Growing regions"),
                ui.output_plot("regions_map", height=f"{PLOT_H}px", fill=False),
                full_screen=True,
            ),
            ui.card(ui.card_header("Season"), ui.output_ui("season_status")),
        )

    @output
    @render.ui
    def page_phenology():
        if not on_tab("Phenology"):
            return ui.div()
        return ui.TagList(
            ui.card(ui.card_header("Chill and heat"), ui.output_ui("phenology_kpis")),
            ui.card(ui.card_header("Montmorency stages"), ui.output_ui("stage_cards")),
            ui.card(
                ui.card_header("GDD trend"),
                ui.output_plot("gdd_plot", height=f"{PLOT_H}px", fill=False),
                full_screen=True,
            ),
        )

    @output
    @render.ui
    def page_bloom():
        if not on_tab("Bloom"):
            return ui.div()
        return ui.TagList(
            ui.card(
                ui.card_header("Enhanced Bloom Index"),
                ui.output_ui("ebi_caption"),
                ui.output_plot("ebi_plot", height="520px", fill=False),
                full_screen=True,
            ),
            ui.card(
                ui.card_header("Bloom time series 2025"),
                ui.output_plot("ebi_series_plot", height=f"{PLOT_H}px", fill=False),
                full_screen=True,
            ),
        )

    @output
    @render.ui
    def page_weather():
        if not on_tab("Weather"):
            return ui.div()
        return ui.TagList(
            ui.output_ui("weather_page_hero"),
            ui.output_ui("weather_forecast_strip"),
            ui.card(
                ui.card_header("Hourly temperature"),
                ui.output_plot("temp_long", height=f"{PLOT_H}px", fill=False),
                full_screen=True,
            ),
            ui.card(
                ui.card_header("Daily min max precip"),
                ui.output_plot("daily_plot", height=f"{PLOT_H}px", fill=False),
                full_screen=True,
            ),
            ui.output_ui("weather_source"),
            ui.accordion(
                ui.accordion_panel("Airport observations", ui.output_ui("iem_source")),
                id="weather_extra",
                open=False,
            ),
        )

    @output
    @render.ui
    def page_frost():
        if not on_tab("Frost"):
            return ui.div()
        return ui.TagList(
            ui.output_ui("frost_hero"),
            ui.card(ui.card_header("Next 48 hours"), ui.output_ui("frost_48h")),
            ui.card(ui.card_header("Stage frost check"), ui.output_ui("frost_detail")),
            ui.card(ui.card_header("Night checklist"), ui.output_ui("frost_checks")),
            ui.card(ui.card_header("NWS alerts"), ui.output_ui("nws_alerts_ui")),
        )

    @output
    @render.ui
    def page_planner():
        if not on_tab("Planner"):
            return ui.div()
        return ui.TagList(
            ui.output_ui("planner_shake_tile"),
            ui.card(ui.card_header("Next 10 days"), ui.output_ui("planner_table")),
            ui.card(ui.card_header("Field vs calendar"), ui.output_ui("planner_field")),
        )

    @output
    @render.ui
    def page_field():
        if not on_tab("Field"):
            return ui.div()
        return ui.TagList(
            ui.card(
                ui.card_header("New sample"),
                ui.input_slider("sample_color", "Color (1 pale, 5 red)", min=1, max=5, value=3),
                ui.input_slider("sample_firm", "Firmness (1 soft, 5 firm)", min=1, max=5, value=3),
                ui.input_numeric("sample_brix", "Brix", value=12.0, min=0, max=30, step=0.1),
                ui.input_text("sample_notes", "Notes"),
                ui.input_action_button("sample_save", "Save sample"),
                ui.output_text("sample_msg"),
            ),
            ui.card(ui.card_header("Log"), ui.output_ui("sample_table")),
            ui.card(ui.card_header("Processor specs"), ui.output_ui("processor_board")),
        )

    @output
    @render.ui
    def page_compare():
        if not on_tab("Compare"):
            return ui.div()
        return ui.card(ui.card_header("Southwest vs west central vs northwest"), ui.output_ui("compare_table"))

    @output
    @render.ui
    def page_data():
        if not on_tab("Data"):
            return ui.div()
        return ui.TagList(
            ui.card(
                ui.card_header("Enviroweather CSV"),
                ui.p(ftp_status()),
                ui.input_text("ew_station", "Station id", value="wmt"),
                ui.input_file("ew_csv", "CSV export", accept=[".csv"]),
                ui.input_action_button("ew_save", "Save CSV"),
                ui.input_action_button("ew_ftp", "Fetch FTP"),
                ui.output_text("ew_msg"),
                ui.output_ui("ew_list"),
            ),
            ui.card(
                ui.card_header("Sentinel-2"),
                ui.p("Drop YYYYMMDD.tif clips in data/sentinel2/. Bloom tab will list them next to PlanetScope."),
            ),
        )

    @output
    @render.ui
    def page_reports():
        if not on_tab("Reports"):
            return ui.div()
        return ui.card(
            ui.card_header("Exports"),
            ui.download_button("download_csv", "Weather CSV"),
            ui.download_button("download_pdf", "PDF report"),
            ui.download_button("download_morning", "Morning sheet"),
        )

    @output
    @render.ui
    def page_pests():
        if not on_tab("Pests"):
            return ui.div()
        return ui.card(ui.card_header("Enviroweather models"), ui.output_ui("pest_ui"))

    @output
    @render.ui
    def page_sources():
        if not on_tab("Sources"):
            return ui.div()
        return ui.TagList(
            ui.card(ui.card_header("Inputs"), ui.output_ui("sources_ui")),
            ui.card(ui.card_header("Ground truth"), ui.output_ui("truth_ui")),
        )

    @reactive.calc
    def selected_scene():
        if not SCENES:
            return None
        wanted = input.scene_date()
        for s in SCENES:
            if s.date.isoformat() == wanted:
                return s
        return SCENES[-1]

    @reactive.calc
    def harvest_call():
        bloom = input.bloom_date()
        if bloom is None:
            bloom = _bloom_default(season())
        elif isinstance(bloom, str):
            bloom = date.fromisoformat(bloom[:10])
        elif hasattr(bloom, "date") and not isinstance(bloom, date):
            bloom = bloom.date()
        return recommend_harvest(
            region(),
            season(),
            weather_df(),
            bloom,
            bloom_source="bloom date",
        )

    @reactive.calc
    def shake_plan():
        return plan_days(weather_df(), date.today(), n=10, call=harvest_call())

    @output
    @render.ui
    def sidebar_meta():
        r = region()
        ew = r.get("enviroweather") or {}
        return ui.div(
            ui.p(f"{r['zone']} | {r['county']} County"),
            ui.a("Enviroweather", href=ew.get("url", "https://enviroweather.msu.edu/"), target="_blank"),
            ui.p(weather_status(), class_="text-muted"),
        )

    @output
    @render.text
    def kpi_chill():
        m = metrics()
        return f"{m['utah']:.0f} / 954 CU"

    @output
    @render.text
    def kpi_gdd():
        return f"{metrics()['gdd4']:.0f} GDD"

    @output
    @render.text
    def kpi_stage():
        return metrics()["stage"]["current"]["label"]

    @output
    @render.text
    def kpi_frost():
        return metrics()["frost"]

    def _wx_card(name, temp, short, kind):
        temp_txt = f"{int(temp)}°" if temp is not None and temp == temp else ""
        return ui.div(
            ui.div(
                ui.span(name or "", class_="wx-name"),
                ui.span(temp_txt, class_="wx-temp"),
                ui.span(short or kind, class_="wx-short"),
                class_="wx-card-inner",
            ),
            class_="wx-card",
            style=f"background-image:url('{bg_url(kind)}')",
        )

    def _hero_tag():
        r = region()
        df = weather_df()
        kind = classify_obs(df)
        temp = None
        if not df.empty and "temp_c" in df.columns:
            temp = float(c_to_f(df["temp_c"].iloc[-1]))
        temp_txt = f"{temp:.0f}°" if temp is not None and temp == temp else ""
        labels = {
            "thunderstorm": "Thunderstorms",
            "snow": "Snow",
            "rain": "Rain",
            "fog": "Fog",
            "frost": "Frost",
            "wind": "Windy",
            "heat": "Hot",
            "cloudy": "Cloudy",
            "partly": "Partly cloudy",
            "sunny": "Sunny",
        }
        return ui.div(
            ui.div(
                ui.div(r["name"], class_="wx-kicker"),
                ui.div(temp_txt, class_="wx-temp"),
                ui.div(labels.get(kind, kind), class_="wx-kind"),
                class_="wx-hero-inner",
            ),
            class_="wx-hero",
            style=f"background-image:url('{bg_url(kind)}')",
        )

    def _forecast_tags():
        r = region()
        periods = fetch_forecast(r["lat"], r["lon"])
        if not periods:
            return ui.div()
        cards = []
        for p in periods[:6]:
            kind = classify_forecast(p.get("short") or "", p.get("temp_f"), p.get("wind") or "")
            cards.append(_wx_card(p.get("name"), p.get("temp_f"), p.get("short"), kind))
        return ui.div(*cards, class_="wx-strip")

    @output
    @render.ui
    def weather_hero():
        return _hero_tag()

    @output
    @render.ui
    def overview_hero():
        return _hero_tag()

    @output
    @render.ui
    def weather_page_hero():
        return _hero_tag()

    @output
    @render.ui
    def frost_hero():
        return ui.div(
            ui.div(
                ui.div("Frost watch", class_="wx-kicker"),
                ui.div(metrics()["frost"], class_="wx-kind"),
                class_="wx-hero-inner",
            ),
            class_="wx-hero",
            style=f"background-image:url('{bg_url('frost')}')",
        )

    @output
    @render.ui
    def forecast_strip():
        return _forecast_tags()

    @output
    @render.ui
    def weather_forecast_strip():
        return _forecast_tags()

    @output
    @render.ui
    def harvest_kpis():
        c = harvest_call()
        tone = harvest_tone(c.status)
        tiles = [
            ("Peak", c.recommended_peak.isoformat(), "green"),
            ("Window", f"{c.recommended_start} to {c.recommended_end}", "blue"),
            ("Days after bloom", f"{c.dafb_today} days", "orange"),
            ("Status", c.status, tone),
        ]
        return ui.div(
            *[
                ui.div(
                    ui.span(label, class_="kpi-label"),
                    ui.span(value, class_=f"kpi-value tone-{t}"),
                    class_=f"kpi-tile tone-{t}",
                )
                for label, value, t in tiles
            ],
            class_="kpi-row",
        )

    @output
    @render.ui
    def overview_kpis():
        m = metrics()
        frost = m.get("frost") or ""
        frost_t = "red" if "CRITICAL" in frost else ("orange" if "WATCH" in frost else "green")
        tiles = [
            ("Utah chill", f"{m['utah']:.0f} / 954 CU", "blue"),
            ("GDD from 1 Mar", f"{m['gdd4']:.0f} GDD", "green"),
            ("Stage", m["stage"]["current"]["label"], "orange"),
            ("Frost", frost, frost_t),
        ]
        return ui.div(
            *[
                ui.div(
                    ui.span(label, class_="kpi-label"),
                    ui.span(value, class_=f"kpi-value tone-{t}"),
                    class_=f"kpi-tile tone-{t}",
                )
                for label, value, t in tiles
            ],
            class_="kpi-row",
        )

    @output
    @render.ui
    def harvest_reasons():
        c = harvest_call()
        modeled = c.modeled_bloom.isoformat() if c.modeled_bloom else "not reached"
        bits = [ui.p(f"Bloom date {c.bloom.isoformat()}. Modeled bloom {modeled}. {c.confidence} {c.swd_watch}")]
        bits.append(ui.p(field_adjust(c)))
        bits.append(ui.tags.ul(*[ui.tags.li(x) for x in c.reasons]))
        if c.caveats:
            bits.append(ui.tags.ul(*[ui.tags.li(x) for x in c.caveats]))
        return ui.TagList(*bits)

    def _shake_div():
        c = harvest_call()
        msg = shake_this_week(shake_plan(), c)
        tone = harvest_tone(c.status)
        if "Best shake" in msg:
            tone = "green"
        return ui.div(msg, class_=f"shake-week tone-{tone}")

    @output
    @render.ui
    def shake_week_tile():
        return _shake_div()

    @output
    @render.ui
    def planner_shake_tile():
        return _shake_div()

    @output
    @render.ui
    def planner_table():
        plan = shake_plan()
        crews = input.crews() or 4
        if plan.empty:
            return ui.p("No daily weather.")
        header = ui.tags.tr(
            ui.tags.th("Rank"),
            ui.tags.th("Date"),
            ui.tags.th("Min F"),
            ui.tags.th("Max F"),
            ui.tags.th("Rain mm"),
            ui.tags.th("Score"),
            ui.tags.th("In window"),
            ui.tags.th("Note"),
        )
        body = []
        for _, rec in plan.sort_values("rank").iterrows():
            body.append(
                ui.tags.tr(
                    ui.tags.td(str(int(rec["rank"]))),
                    ui.tags.td(str(rec["date"])),
                    ui.tags.td(str(rec["tmin_f"])),
                    ui.tags.td(str(rec["tmax_f"])),
                    ui.tags.td(str(rec["precip_mm"])),
                    ui.tags.td(str(int(rec["ops_score"]))),
                    ui.tags.td(str(rec["in_window"])),
                    ui.tags.td(str(rec["note"])),
                    class_=f"row-{score_tone(float(rec['ops_score']))}",
                )
            )
        return ui.TagList(
            ui.p(f"{crews} crews on the board. Score is weather, not headcount."),
            ui.tags.table(header, *body, class_="table table-sm"),
        )

    @output
    @render.ui
    def planner_field():
        return ui.p(field_adjust(harvest_call()))

    @output
    @render.ui
    def frost_48h():
        low, when = next_48h_min_f(weather_df())
        m = metrics()
        stage = m["stage"]["current"]
        crit = stage.get("critical_temp_f", 28)
        if low is None:
            return ui.p("No forecast lows.")
        tone = "red" if low <= crit else ("orange" if low <= crit + 4 else "green")
        return ui.TagList(
            ui.p(f"Lowest in the next 48 h: {low:.1f} F on {when}."),
            ui.p(f"Stage {stage['label']}. Critical {crit} F."),
            ui.p("Protect if the low meets the stage threshold. This is not an auto start.", class_=f"tone-{tone}"),
        )

    @output
    @render.ui
    def frost_checks():
        boxes = [ui.tags.label(ui.tags.input(type="checkbox"), " ", item) for item in FROST_CHECKLIST]
        return ui.div(*boxes, class_="check-list")

    @output
    @render.ui
    def compare_table():
        rows = comparison_table(season())
        header = ui.tags.tr(
            ui.tags.th("Belt"),
            ui.tags.th("Region"),
            ui.tags.th("Bloom"),
            ui.tags.th("Peak"),
            ui.tags.th("Window"),
            ui.tags.th("GDD after bloom"),
            ui.tags.th("Days to peak"),
            ui.tags.th("Source"),
        )
        body = [
            ui.tags.tr(
                ui.tags.td(str(r["belt"])),
                ui.tags.td(str(r["region"])),
                ui.tags.td(str(r["bloom"])),
                ui.tags.td(str(r["peak"])),
                ui.tags.td(str(r["window"])),
                ui.tags.td("" if r["gdd"] is None else f"{r['gdd']:.0f}"),
                ui.tags.td("" if r["ahead"] is None else str(r["ahead"])),
                ui.tags.td(str(r["source"])),
            )
            for r in rows
        ]
        return ui.tags.table(header, *body, class_="table table-sm")

    @output
    @render.ui
    def processor_board():
        cards = []
        for plant in load_plants():
            try:
                reg = get_region(plant["region_id"])
            except KeyError:
                continue
            st = plant_status(plant, reg, season())
            tone = "red" if st["miss"] else "green"
            cards.append(
                ui.card(
                    ui.card_header(plant["name"]),
                    ui.p(f"{st['start']} to {st['end']}"),
                    ui.p(plant.get("notes") or ""),
                    ui.tags.ul(*[ui.tags.li(f) for f in st["flags"]]),
                    class_=f"tone-{tone}",
                )
            )
        return ui.layout_column_wrap(*cards, width=1 / 3, fill=False)

    @output
    @render.plot(height=PLOT_H)
    def harvest_models_plot():
        c = harvest_call()
        fig, ax = _fig(11.2, 4.6)
        gdd_day = c.gdd_peak_date or c.dafb_peak
        rows = [
            ("DAFB", c.dafb_start, c.dafb_peak, c.dafb_end),
            ("GDD", gdd_day - timedelta(days=2), gdd_day, gdd_day + timedelta(days=2)),
            ("USDA", c.usda["harvest_begin"], c.usda["peak_start"], c.usda["peak_end"]),
            ("Call", c.recommended_start, c.recommended_peak, c.recommended_end),
        ]
        for i, (_label, a, b, d) in enumerate(rows):
            ax.hlines(i, pd.Timestamp(a), pd.Timestamp(d), colors="#1a5c45", lw=16, alpha=0.4)
            ax.plot(pd.Timestamp(b), i, "o", color="#c45c26", ms=13, zorder=5)
        ax.axvline(pd.Timestamp(date.today()), color="#333", ls=":", lw=1.4)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([r[0] for r in rows])
        ax.invert_yaxis()
        left = min(r[1] for r in rows) - timedelta(days=5)
        right = max(r[3] for r in rows) + timedelta(days=5)
        ax.set_xlim(pd.Timestamp(left), pd.Timestamp(right))
        ax.set_xlabel("Date")
        ax.set_title(c.region_name)
        _style_dates(ax)
        fig.tight_layout()
        return fig

    @output
    @render.plot(height=PLOT_H)
    def fruit_fill_plot():
        c = harvest_call()
        df = weather_df()
        fig, ax = _fig(11.2, 4.6)
        if df.empty:
            ax.text(0.5, 0.5, "No weather", ha="center", transform=ax.transAxes)
            return fig
        temps = pd.to_numeric(df["temp_c"], errors="coerce")
        temps.index = pd.to_datetime(df.index)
        window = temps[temps.index >= pd.Timestamp(c.bloom)]
        if window.empty:
            ax.text(0.5, 0.5, "No post bloom temperatures", ha="center", transform=ax.transAxes)
            return fig
        gdd = (window - 4.0).clip(lower=0).cumsum() / 24.0
        daily = gdd.resample("D").max()
        fill = daily.map(fruit_pct_final_diameter)
        ax.plot(fill.index, fill.values, color="#1a5c45", lw=2.4, label="Fruit fill %")
        ax.axhline(90, color="#889990", ls="--", label="90% size")
        ax.axvline(pd.Timestamp(c.recommended_peak), color="#c45c26", ls="--", label="Peak")
        ax.set_ylabel("%")
        ax.set_ylim(0, 105)
        ax.legend(loc="lower right")
        _style_dates(ax)
        fig.tight_layout()
        return fig

    @output
    @render.ui
    def ops_table():
        c = harvest_call()
        cal = ops_calendar(weather_df(), c.recommended_start, days=21)
        if cal.empty:
            return ui.p("No daily weather.")
        header = ui.tags.tr(
            ui.tags.th("Date"),
            ui.tags.th("Min F"),
            ui.tags.th("Max F"),
            ui.tags.th("Rain mm"),
            ui.tags.th("Score"),
            ui.tags.th("Note"),
        )
        body = [
            ui.tags.tr(
                ui.tags.td(str(rec["date"])),
                ui.tags.td(str(rec["tmin_f"])),
                ui.tags.td(str(rec["tmax_f"])),
                ui.tags.td(str(rec["precip_mm"])),
                ui.tags.td(str(int(rec["ops_score"]))),
                ui.tags.td(str(rec["note"])),
                class_=f"row-{score_tone(float(rec['ops_score']))}",
            )
            for _, rec in cal.iterrows()
        ]
        return ui.tags.table(header, *body, class_="table table-sm")



    @output
    @render.ui
    def company_board():
        yr = season()
        header = ui.tags.tr(
            ui.tags.th("Region"),
            ui.tags.th("Belt"),
            ui.tags.th("Peak"),
            ui.tags.th("Window"),
            ui.tags.th("Source"),
        )
        rows = [header]
        for r in regions():
            cached = load_cached(r["id"], yr)
            if cached is None or cached.empty:
                usda = usda_calendar(r, yr)
                rows.append(
                    ui.tags.tr(
                        ui.tags.td(r["name"]),
                        ui.tags.td(r["zone"]),
                        ui.tags.td(str(usda["peak_start"])),
                        ui.tags.td(f"{usda['harvest_begin']} to {usda['peak_end']}"),
                        ui.tags.td("USDA usual"),
                    )
                )
                continue
            local_bloom = modeled_bloom_date(cached, yr) or input.bloom_date()
            call = recommend_harvest(r, yr, cached, local_bloom, bloom_source="modeled")
            rows.append(
                ui.tags.tr(
                    ui.tags.td(r["name"]),
                    ui.tags.td(r["zone"]),
                    ui.tags.td(str(call.recommended_peak)),
                    ui.tags.td(f"{call.recommended_start} to {call.recommended_end}"),
                    ui.tags.td("Cached weather"),
                )
            )
        return ui.tags.table(*rows, class_="table table-sm")

    @output
    @render.ui
    def season_status():
        m = metrics()
        r = region()
        done = m.get("chill_done")
        if not done or done in ("None", "null"):
            done_txt = "not reached"
        elif hasattr(done, "strftime"):
            done_txt = done.strftime("%Y-%m-%d")
        else:
            done_txt = str(done)[:10]
        bloom = input.bloom_date()
        return ui.TagList(
            ui.p(f"{r['name']} ({r['lat']:.3f}, {r['lon']:.3f}). Public centroid."),
            ui.p(f"Simple chill hours {m['simple']}. Utah complete {done_txt}. Post chill GDD {m['gdd_pc']:.0f}."),
            ui.p(f"GDD42 from 1 Jan {m['gdd42']:.0f}. Bloom date {bloom}."),
        )

    def _week_figure(df: pd.DataFrame, title: str):
        fig, ax = _fig()
        if df.empty:
            ax.text(0.5, 0.5, "No weather", ha="center", transform=ax.transAxes)
            return fig
        end = df.index.max()
        w = df.loc[df.index >= end - pd.Timedelta(days=7)]
        ax.plot(w.index, c_to_f(w["temp_c"]), color="#c45c26", lw=2, label="Orchard temp")
        ax.axhline(32, color="#2b6cb0", ls="--", label="32 F")
        ax.set_ylabel("F")
        ax.set_title(title)
        ax.legend()
        fig.tight_layout()
        return fig

    @output
    @render.plot(height=PLOT_H)
    def temp_week():
        return _week_figure(weather_df(), region()["name"])

    @output
    @render.plot(height=PLOT_H)
    def regions_map():
        selected = region()
        fig, ax = _fig(10.5, 4.6)
        for r in regions():
            here = r["id"] == selected["id"]
            ax.scatter(
                r["lon"],
                r["lat"],
                s=160 if here else 90,
                c="#c45c26" if here else "#1a5c45",
                zorder=3,
            )
            ax.annotate(r["county"], (r["lon"], r["lat"]), fontsize=8, xytext=(4, 4), textcoords="offset points")
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_title("Michigan growing regions")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        return fig

    @output
    @render.ui
    def phenology_kpis():
        m = metrics()
        return ui.div(
            ui.p(f"Utah CU {m['utah']:.0f} of 954. Simple hours {m['simple']} (32 to 45 F)."),
            ui.p(f"Zavalloni GDD {m['gdd4']:.0f}. MSU GDD42 {m['gdd42']:.0f}. Post chill {m['gdd_pc']:.0f}."),
        )

    @output
    @render.ui
    def stage_cards():
        gdd4 = metrics()["gdd4"]
        cards = []
        for s in PHENO["stages_zavalloni_from_mar1_base4c"]:
            if gdd4 >= s["gdd"]:
                status, theme = "Reached", "success"
            elif gdd4 >= s["gdd"] - 15:
                status, theme = "Near", "warning"
            else:
                status, theme = "Later", "secondary"
            cards.append(
                ui.card(
                    ui.card_header(s["label"]),
                    ui.p(f"{s['gdd']} GDD | {s['critical_temp_f']} F critical"),
                    ui.p(status, class_=f"text-{theme}"),
                )
            )
        return ui.layout_column_wrap(*cards, width=1 / 4, fill=False)

    @output
    @render.plot(height=PLOT_H)
    def gdd_plot():
        df = weather_df()
        fig, ax = _fig()
        if df.empty:
            ax.text(0.5, 0.5, "No weather", ha="center", transform=ax.transAxes)
            return fig
        yr = season()
        start = pd.Timestamp(year=yr, month=3, day=1)
        temps = df.loc[df.index >= start, "temp_c"]
        if temps.empty:
            ax.text(0.5, 0.5, "No post March temperatures", ha="center", transform=ax.transAxes)
            return fig
        gdd = (temps - 4.0).clip(lower=0).cumsum() / 24.0
        gdd = gdd.resample("D").max()
        ax.plot(gdd.index, gdd.values, color="#1a5c45", lw=2, label="GDD base 4 C")
        ax.axvline(pd.Timestamp(input.bloom_date()), color="#c45c26", ls="--", label="Bloom")
        ax.set_ylabel("GDD")
        ax.legend()
        fig.tight_layout()
        return fig

    @output
    @render.ui
    def ebi_caption():
        scene = selected_scene()
        if scene is None:
            return ui.p("No PlanetScope scenes in data/planetscope.")
        return ui.p(f"Scene {scene.date.isoformat()} ({scene.name}).")

    @output
    @render.plot(height=520)
    def ebi_plot():
        fig, axes = plt.subplots(1, 2, figsize=(11, 5.2), dpi=110)
        scene = selected_scene()
        if scene is None:
            axes[0].text(0.5, 0.5, "No scenes", ha="center")
            axes[1].axis("off")
            return fig
        stats = scene_ebi_product(scene.path)
        rgb = np.asarray(stats["rgb"], dtype=float)
        peak = np.nanmax(rgb) or 1.0
        axes[0].imshow(np.clip(rgb / peak, 0, 1))
        axes[0].set_title("True color")
        axes[0].axis("off")
        masked = stats["masked"]
        finite = masked[np.isfinite(masked)]
        vmin = float(np.nanpercentile(finite, 2)) if finite.size else 0
        vmax = float(np.nanpercentile(finite, 98)) if finite.size else 1
        im = axes[1].imshow(masked, cmap="magma", vmin=vmin, vmax=vmax)
        axes[1].set_title(f"EBI mean={stats['mean']:.3f}" if stats["mean"] is not None else "EBI")
        axes[1].axis("off")
        fig.colorbar(im, ax=axes[1], fraction=0.046, pad=0.04, label="EBI")
        fig.tight_layout()
        return fig

    @output
    @render.plot(height=PLOT_H)
    def ebi_series_plot():
        fig, ax = _fig()
        series = ebi_time_series(force=False)
        if series.empty:
            ax.text(0.5, 0.5, "No EBI series", ha="center", transform=ax.transAxes)
            return fig
        if "cloudy" in series.columns:
            series = series[series["cloudy"] != True]
        if series.empty:
            ax.text(0.5, 0.5, "All scenes flagged cloudy", ha="center", transform=ax.transAxes)
            return fig
        x = pd.to_datetime(series["date"])
        ax.plot(x, series["mean"], marker="o", color="#1a5c45", label="Mean EBI (cloud masked)")
        ax.plot(x, series["p90"], marker=".", color="#c45c26", label="90th percentile")
        ax.axvspan(pd.Timestamp("2025-04-26"), pd.Timestamp("2025-05-10"), color="#f3d9a4", alpha=0.4, label="2025 bloom")
        scene = selected_scene()
        if scene is not None:
            ax.axvline(pd.Timestamp(scene.date), color="#333", ls=":")
        ax.set_ylabel("EBI")
        ax.legend()
        fig.tight_layout()
        return fig

    @output
    @render.plot(height=PLOT_H)
    def temp_long():
        df = weather_df()
        fig, ax = _fig()
        if df.empty:
            ax.text(0.5, 0.5, "No weather", ha="center", transform=ax.transAxes)
            return fig
        s = c_to_f(pd.to_numeric(df["temp_c"], errors="coerce"))
        if len(s) > 2500:
            s = s.resample("6h").mean()
        ax.plot(s.index, s.values, color="#c45c26", lw=1.0, alpha=0.7, label="Temp")
        ax.plot(s.index, s.rolling(8).mean(), color="#1a5c45", lw=2, label="Daily mean")
        ax.axhline(32, color="#2b6cb0", ls="--")
        ax.set_ylabel("F")
        ax.legend()
        fig.tight_layout()
        return fig

    @output
    @render.plot(height=PLOT_H)
    def daily_plot():
        daily = daily_summary(weather_df())
        fig, ax = _fig()
        if daily.empty:
            ax.text(0.5, 0.5, "No daily series", ha="center", transform=ax.transAxes)
            return fig
        ax.fill_between(daily.index, c_to_f(daily["tmin_c"]), c_to_f(daily["tmax_c"]), color="#c45c26", alpha=0.25, label="Min max")
        ax.plot(daily.index, c_to_f(daily["tmean_c"]), color="#1a5c45", lw=1.5, label="Mean")
        if "precip_mm" in daily.columns:
            ax2 = ax.twinx()
            ax2.bar(daily.index, daily["precip_mm"], color="#2b6cb0", alpha=0.35, width=1.0)
            ax2.set_ylabel("mm")
        ax.set_ylabel("F")
        ax.legend(loc="upper left")
        fig.tight_layout()
        return fig

    @output
    @render.ui
    def weather_source():
        r = region()
        airport = r.get("airport") or {}
        return ui.p(f"{weather_status()}. Airport {airport.get('id', 'n/a')} {airport.get('name', '')}.")

    @output
    @render.ui
    def iem_source():
        r = region()
        df, status = load_or_fetch_iem(r, season())
        if df.empty:
            return ui.p(status)
        return ui.TagList(ui.p(status), ui.pre(df.tail(5).to_string()))

    @output
    @render.ui
    def frost_detail():
        m = metrics()
        stage = m["stage"]["current"]
        return ui.TagList(
            ui.p(f"Stage {stage['label']}. Critical {stage['critical_temp_f']} F."),
            ui.p(f"Last 72 h min {m['min_f']:.1f} F" if m["min_f"] == m["min_f"] else "No recent min."),
            ui.p(m["frost"]),
        )

    @output
    @render.ui
    def nws_alerts_ui():
        r = region()
        alerts = fetch_alerts(r["lat"], r["lon"])
        if not alerts:
            return ui.p("No active NWS alerts.")
        return ui.TagList(*[ui.card(ui.card_header(a.get("event", "Alert")), ui.p(a.get("headline", ""))) for a in alerts])

    @output
    @render.ui
    def pest_ui():
        cards = []
        for pest in PHENO["pest_watch_windows"]:
            cards.append(
                ui.card(
                    ui.card_header(pest["name"]),
                    ui.p(pest["timing"]),
                    ui.a("Enviroweather model", href=pest["enviroweather"], target="_blank"),
                )
            )
        r = region()
        ew = r.get("enviroweather") or {}
        cards.append(
            ui.card(
                ui.card_header("Station"),
                ui.a(ew.get("name", "Enviroweather"), href=ew.get("url", "https://enviroweather.msu.edu/"), target="_blank"),
            )
        )
        return ui.layout_column_wrap(*cards, width=1 / 2, fill=False)

    @output
    @render.ui
    def sources_ui():
        return ui.TagList(
            ui.p(ui.span("Open-Meteo", class_="source-pill"), " hourly weather, cached on disk"),
            ui.p(ui.span("IEM ASOS", class_="source-pill"), " airport daily max min precip"),
            ui.p(ui.span("NWS", class_="source-pill"), " alerts and 7 day forecast, cached"),
            ui.p(ui.span("PlanetScope", class_="source-pill"), f" {len(SCENES)} local scenes"),
            ui.p(ui.span("USDA RMA", class_="source-pill"), " usual Michigan harvest dates"),
            ui.p(ui.span("Enviroweather", class_="source-pill"), " pest models (linked)"),
            ui.p(weather_status()),
        )

    @output
    @render.ui
    def truth_ui():
        path = REFERENCE_DIR / "ground_truth.csv"
        if not path.exists():
            return ui.p("Add rows to data/reference/ground_truth.csv")
        df = pd.read_csv(path)
        rows = [ui.tags.tr(*[ui.tags.th(c) for c in df.columns])]
        for _, rec in df.iterrows():
            rows.append(ui.tags.tr(*[ui.tags.td(str(rec[c])) for c in df.columns]))
        return ui.tags.table(*rows, class_="table table-sm")

    @render.download_button(filename=lambda: f"slf_weather_{region()['id']}_{season()}.csv")
    def download_csv():
        yield weather_csv_bytes(weather_df())

    @render.download_button(filename=lambda: f"slf_report_{region()['id']}_{season()}.pdf")
    def download_pdf():
        m = metrics()
        r = region()
        c = harvest_call()
        lines = [
            f"Harvest {c.recommended_start} to {c.recommended_end}, peak {c.recommended_peak}",
            f"{c.status}. Bloom {c.bloom}. DAFB {c.dafb_today}. GDD after bloom {c.gdd4_after_bloom:.0f}.",
            f"Utah CU {m['utah']:.0f}. GDD from 1 Mar {m['gdd4']:.0f}. {m['frost']}",
            weather_status(),
        ]
        tmp = Path(tempfile.mkdtemp())
        fig = _week_figure(weather_df(), r["name"])
        p = tmp / "week.png"
        fig.savefig(p, dpi=120, bbox_inches="tight")
        plt.close(fig)
        yield build_pdf(region_name=r["name"], season=season(), summary_lines=lines, figure_paths=[p])

    @render.download_button(filename=lambda: f"slf_morning_{region()['id']}_{date.today().isoformat()}.pdf")
    def download_morning():
        c = harvest_call()
        plan = shake_plan()
        low, when = next_48h_min_f(weather_df())
        frost_line = f"48 h low {low:.1f} F on {when}" if low is not None else "No 48 h low"
        rows = []
        if not plan.empty:
            for _, rec in plan.sort_values("rank").iterrows():
                rows.append(
                    f"{int(rec['rank'])}. {rec['date']}  score {int(rec['ops_score'])}  rain {rec['precip_mm']} mm  {rec['note']}"
                )
        yield morning_sheet_pdf(
            region_name=region()["name"],
            shake_line=shake_this_week(plan, c),
            field_line=field_adjust(c),
            frost_line=frost_line,
            plan_rows=rows[:12],
            checklist=list(FROST_CHECKLIST),
        )

    sample_msg_val = reactive.value("")

    @reactive.effect
    @reactive.event(input.sample_save)
    def _save_sample():
        if pin_required() and not unlocked.get():
            sample_msg_val.set("Unlock first.")
            return
        brix = input.sample_brix()
        append_sample(
            when=date.today(),
            region_id=region()["id"],
            color=int(input.sample_color() or 3),
            firmness=int(input.sample_firm() or 3),
            brix=None if brix in (None, "") else float(brix),
            notes=input.sample_notes() or "",
        )
        log_tick.set(log_tick.get() + 1)
        sample_msg_val.set("Saved.")

    @output
    @render.text
    def sample_msg():
        return sample_msg_val.get()

    @output
    @render.ui
    def sample_table():
        log_tick.get()
        df = load_log()
        if df.empty:
            return ui.p("No samples yet.")
        header = ui.tags.tr(*[ui.tags.th(c) for c in df.columns])
        body = [ui.tags.tr(*[ui.tags.td(str(rec[c])) for c in df.columns]) for _, rec in df.tail(20).iterrows()]
        return ui.tags.table(header, *body, class_="table table-sm")

    ew_msg_val = reactive.value("")

    @reactive.effect
    @reactive.event(input.ew_save)
    def _ew_save():
        files = input.ew_csv()
        if not files:
            ew_msg_val.set("Choose a CSV.")
            return
        raw = Path(files[0]["datapath"]).read_bytes()
        sid = input.ew_station() or "wmt"
        df = parse_enviroweather_csv(raw, sid)
        save_station(sid, df)
        ew_msg_val.set(f"Saved {sid} ({len(df)} rows).")

    @reactive.effect
    @reactive.event(input.ew_ftp)
    def _ew_ftp():
        ok, msg = fetch_ftp()
        ew_msg_val.set(msg)

    @output
    @render.text
    def ew_msg():
        return ew_msg_val.get()

    @output
    @render.ui
    def ew_list():
        ew_msg_val.get()
        names = list_stations()
        if not names:
            return ui.p("No station files yet.")
        return ui.tags.ul(*[ui.tags.li(n) for n in names])


app = App(app_ui, server, static_assets=str(APP_DIR / "www"))

if __name__ == "__main__":
    app.run()
