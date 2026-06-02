import numpy as np
import matplotlib.pyplot as plt
from faicons import icon_svg
import meteostat as ms
from datetime import datetime, timedelta, date
from geopy.distance import geodesic
import pandas as pd
import logging
import rasterio
from rasterio.plot import show
import folium
from pathlib import Path
import io
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from shiny import App, reactive, render, ui
from shared import app_dir

# ====================== CONFIG ======================
FARMS = {
    # High Acreage Counties
    "Leelanau County (High)": (44.88, -85.75),
    "Grand Traverse County (High)": (44.83, -85.45),
    "Oceana County (High)": (43.65, -86.35),
    "Benzie County": (44.62, -86.15),
    "Manistee County": (44.25, -86.30),
    "Mason County": (43.95, -86.45),
    "Van Buren County": (42.25, -86.30),
    "Berrien County": (41.95, -86.45),
    
    # Key Stations
    "Williamsburg (wmt) - Main": (44.8153, -85.4445),
    "Traverse City (nwm)": (44.8831, -85.6777),
    "Lake Leelanau (llu)": (44.9636, -85.7444),
    "Elk Rapids (elk)": (44.8448, -85.4062),
    "Kewadin": (45.00, -85.35),
    "Benzonia": (44.62, -86.15),
    "Ludington": (43.95, -86.45),
    "Hart / Mears area": (43.70, -86.35),
    "South Haven area": (42.40, -86.25),
    "Benton Harbor / Berrien": (42.10, -86.45),
}

DEFAULT_FARM = "Williamsburg (wmt) - Main"

PHENOLOGY_STAGES = [
    {"stage": "Swollen Bud (First Swell)", "sweet_10": 17, "sweet_90": 5},
    {"stage": "Bud Burst (Green Tip)", "sweet_10": 25, "sweet_90": 14},
    {"stage": "Tight Cluster", "sweet_10": 26, "sweet_90": 17},
    {"stage": "White Bud / First White", "sweet_10": 27, "sweet_90": 24},
    {"stage": "First Bloom", "sweet_10": 28, "sweet_90": 25},
    {"stage": "Full Bloom", "sweet_10": 28, "sweet_90": 25},
    {"stage": "Post-bloom", "sweet_10": 28, "sweet_90": 25},
]

STATIONS = {
    "TVC": ms.Station("KTVC0", 44.7416, -85.5824),
    "ACB": ms.Station("KACB0", 44.9886, -85.1984)
}

DATA_DIR = Path("data")

# ====================== HELPERS ======================
def C_to_F(c): return c * 9/5 + 32

def load_weather_data():
    try:
        start = datetime(2026, 2, 1)
        end = datetime.now() + timedelta(days=30)
        df_tvc = ms.hourly(STATIONS["TVC"], start, end).fetch()
        df_acb = ms.hourly(STATIONS["ACB"], start, end).fetch()
        return df_tvc, df_acb
    except:
        return pd.DataFrame(), pd.DataFrame()

def interpolate_temp(df1, df2, coord1, coord2, lat, lon):
    if df1.empty or df2.empty:
        return pd.DataFrame(columns=['temp_interp'])
    try:
        d1 = geodesic(coord1, (lat, lon)).km or 0.1
        d2 = geodesic(coord2, (lat, lon)).km or 0.1
        w1, w2 = 1/d1, 1/d2
        merged = pd.merge(df1[['temp']], df2[['temp']], left_index=True, right_index=True, suffixes=('_1','_2'), how='inner')
        if not merged.empty:
            merged['temp_interp'] = (merged['temp_1']*w1 + merged['temp_2']*w2) / (w1+w2)
            return merged[['temp_interp']]
        return pd.DataFrame(columns=['temp_interp'])
    except:
        return pd.DataFrame(columns=['temp_interp'])

def compute_gdd(df):
    if df.empty: return 0
    return int((df['temp_interp'] - 5).clip(lower=0).sum() / 24)

def compute_gdd_since_bloom(df, bloom_date):
    if df.empty or bloom_date is None: return 0
    try:
        after = df[df.index.date >= bloom_date]
        return int((after['temp_interp'] - 5).clip(lower=0).sum() / 24)
    except:
        return 0

# ====================== UI ======================
app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.input_select("farm_select", "Select Farm / Station:", choices=list(FARMS.keys()), selected=DEFAULT_FARM),
        ui.input_date("bloom_date", "Bloom Date (approx)", value=date(2026, 4, 20)),
        ui.input_numeric("target_gdd", "Target GDD for Harvest", value=950),
        ui.input_action_button("refresh_data", "🔄 Refresh Data", class_="btn-primary mt-3"),
        width="280px"
    ),
    ui.navset_tab(
        ui.nav_panel("Overview",
            ui.layout_columns(
                ui.value_box("Current GDD", ui.output_text("gdd_value"), theme="success"),
                ui.value_box("GDD Since Bloom", ui.output_text("gdd_since_bloom"), theme="primary"),
                ui.value_box("Days to Target", ui.output_text("days_to_target"), theme="warning"),
            ),
            ui.card(ui.card_header("Temperature Trend"), ui.output_plot("dayplot")),
            ui.card(ui.card_header("Major Cherry Areas + Stations"), ui.output_ui("all_farms_map"), style="height: 420px;"),
        ),
        ui.nav_panel("Phenology Reference",
            ui.card(ui.card_header("Current GDD vs Growth Stages"), ui.output_text("current_gdd_phenology")),
            ui.card(ui.card_header("Cherry Growth Stages (from your image)"), ui.output_ui("phenology_stages_ui")),
        ),
        ui.nav_panel("Pest & Disease Calendar",
            ui.card(ui.card_header("Pest Risk by Current GDD"), ui.output_ui("pest_guide_ui")),
        ),
        ui.nav_panel("Data Management & Reference",
            ui.card(ui.card_header("Data Availability & Station GDD (June 2, 2026)"), ui.output_ui("data_availability_ui")),
            ui.card(ui.card_header("Quick Links"), ui.layout_columns(
                ui.a("Williamsburg (wmt)", href="https://enviroweather.msu.edu/stations/wmt", target="_blank", class_="btn btn-sm btn-outline-primary"),
                ui.a("Traverse City (nwm)", href="https://enviroweather.msu.edu/stations/nwm", target="_blank", class_="btn btn-sm btn-outline-primary"),
                ui.a("Plum Curculio Model", href="https://enviroweather.msu.edu/crops/cherry/plumcurculio", target="_blank", class_="btn btn-sm btn-outline-success"),
                ui.a("Cherry Leaf Spot (wmt)", href="https://enviroweather.msu.edu/crops/cherry/cherryleafspot?selectedStation=wmt", target="_blank", class_="btn btn-sm btn-outline-success"),
                ui.a("Spotted Wing Drosophila", href="https://enviroweather.msu.edu/crops/cherry/spottedwingdrosophila", target="_blank", class_="btn btn-sm btn-outline-success"),
                ui.a("MSU Fruit IPM Resources", href="https://www.canr.msu.edu/ipm/agriculture/fruit/", target="_blank", class_="btn btn-sm btn-outline-secondary"),
            )),
        ),
        ui.nav_panel("EBI Explorer (Satellite)",
            ui.card(ui.card_header("Enhanced Bloom Index"), ui.output_plot("ebi_plot")),
        ),
        ui.nav_panel("Reports",
            ui.card(ui.layout_columns(
                ui.download_button("download_csv", "Download CSV"),
                ui.download_button("download_pdf", "Download PDF"),
            )),
        ),
    ),
    ui.include_css(app_dir / "styles.css"),
    title="SLF-GIS • Cherry Phenology Dashboard",
    fillable=True
)

# ====================== SERVER ======================
def server(input, output, session):

    @reactive.effect
    def _refresh():
        if input.refresh_data() > 0:
            weather_data.set(None)

    @reactive.calc
    def selected_coord():
        return FARMS[input.farm_select()]

    @reactive.calc
    def interpolated_data():
        df_tvc, df_acb = load_weather_data()
        lat, lon = selected_coord()
        return interpolate_temp(df_tvc, df_acb, (44.7416, -85.5824), (44.9886, -85.1984), lat, lon)

    @output
    @render.text
    def gdd_value(): return f"{compute_gdd(interpolated_data())} GDD"

    @output
    @render.text
    def gdd_since_bloom():
        return f"{compute_gdd_since_bloom(interpolated_data(), input.bloom_date())} GDD since bloom"

    @output
    @render.text
    def days_to_target():
        current = compute_gdd_since_bloom(interpolated_data(), input.bloom_date())
        target = input.target_gdd() or 950
        remaining = max(0, target - current)
        return f"~{int(remaining / 10)} days to {target} GDD"

    @output
    @render.ui
    def all_farms_map():
        m = folium.Map(location=[44.0, -86.0], zoom_start=7, tiles="CartoDB positron")
        for name, (lat, lon) in FARMS.items():
            folium.Marker([lat, lon], popup=name, icon=folium.Icon(color="red", icon="tree")).add_to(m)
        return ui.HTML(m._repr_html_())

    @output
    @render.ui
    def phenology_stages_ui():
        current = compute_gdd(interpolated_data())
        cards = []
        for s in PHENOLOGY_STAGES:
            status = "Early"
            color = "secondary"
            if current >= s["sweet_90"]:
                status, color = "Reached (90%)", "success"
            elif current >= s["sweet_10"]:
                status, color = "In Progress", "warning"
            cards.append(ui.card(
                ui.card_header(s["stage"]),
                ui.p(f"Sweet Cherry: 10% @ {s['sweet_10']} GDD | 90% @ {s['sweet_90']} GDD"),
                ui.p(f"Current: {status} ({current} GDD)", class_=f"text-{color}")
            ))
        return ui.div(*cards)

    @output
    @render.ui
    def pest_guide_ui():
        current = compute_gdd(interpolated_data())
        return ui.p(f"Current GDD: {current}. Pest models linked in Data Management tab.")

    @output
    @render.ui
    def data_availability_ui():
        return ui.HTML("""
        <table class="table table-sm">
        <tr><th>Station</th><th>GDD50 (June 1)</th><th>Priority</th></tr>
        <tr><td>Williamsburg (wmt)</td><td>334.7</td><td>Highest</td></tr>
        <tr><td>Traverse City (nwm)</td><td>318.1</td><td>High</td></tr>
        <tr><td>Kewadin</td><td>345.7</td><td>High</td></tr>
        <tr><td>Ludington</td><td>376.4</td><td>High (Oceana)</td></tr>
        <tr><td>Southwest MI stations</td><td>600+</td><td>Very advanced</td></tr>
        </table>
        """)

    @output
    @render.plot
    def dayplot():
        df = interpolated_data()
        fig, ax = plt.subplots(figsize=(10,5))
        if not df.empty:
            ax.plot(df.index, C_to_F(df['temp_interp']), color='#e74c3c')
            ax.axhline(32, color='blue', linestyle='--')
            ax.grid(True, alpha=0.3)
        return fig

    @output
    @render.plot
    def ebi_plot():
        data, transform, filename = load_ebi_raster()
        fig, ax = plt.subplots(figsize=(9,9))
        if data is not None:
            show(data, ax=ax, cmap='viridis')
            ax.set_title(f"EBI - {filename}")
        else:
            plt.text(0.5, 0.5, "Add .tif files to data/ folder", ha='center')
        return fig

    @output
    @render.download(filename="report.pdf")
    def download_pdf():
        df = interpolated_data()
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(80, 750, f"Report - {input.farm_select()}")
        c.setFont("Helvetica", 11)
        c.drawString(80, 720, f"GDD: {compute_gdd(df)}")
        c.save()
        buf.seek(0)
        return buf.getvalue()

    @output
    @render.download(filename="data.csv")
    def download_csv():
        df = interpolated_data()
        return df.to_csv(index=True).encode("utf-8")

app = App(app_ui, server)

if __name__ == "__main__":
    app.run()