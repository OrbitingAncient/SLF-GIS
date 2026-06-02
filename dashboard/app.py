import numpy as np
import matplotlib.pyplot as plt
from faicons import icon_svg
import meteostat as ms
from datetime import datetime, timedelta
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

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from shiny import App, reactive, render, ui

# Import shared
from shared import app_dir

# ====================== CONFIG ======================
FARMS = {
    "Shoreline Fruit (Main)": (44.83444, -85.44333),
    "Old Mission Peninsula": (44.95, -85.52),
    "Leelanau Peninsula": (44.88, -85.75),
    "Antrim County": (44.99, -85.20)
}

DEFAULT_FARM = "Shoreline Fruit (Main)"

STATIONS = {
    "TVC": ms.Station("KTVC0", 44.7416, -85.5824),
    "ACB": ms.Station("KACB0", 44.9886, -85.1984)
}

DATA_DIR = Path("data")

# ====================== HELPERS ======================
def C_to_F(c): 
    return c * 9/5 + 32

def get_current_dates():
    today = datetime.now()
    start = datetime(today.year if today.month >= 3 else today.year - 1, 2, 1)
    end = today + timedelta(days=60)
    return start, end

def load_weather_data():
    try:
        start, end = get_current_dates()
        logger.info(f"Fetching weather from {start.date()} to {end.date()}")
        df_tvc = ms.hourly(STATIONS["TVC"], start, end).fetch()
        df_acb = ms.hourly(STATIONS["ACB"], start, end).fetch()
        logger.info("Weather loaded successfully")
        return df_tvc, df_acb
    except Exception as e:
        logger.error(f"Weather failed: {e}")
        return pd.DataFrame(), pd.DataFrame()

df_tvc, df_acb = load_weather_data()

def interpolate_temp(df1, df2, coord1, coord2, lat, lon):
    try:
        if df1.empty or df2.empty:
            return pd.DataFrame(columns=['temp_interp'])
        d1 = geodesic(coord1, (lat, lon)).km
        d2 = geodesic(coord2, (lat, lon)).km
        w1 = 1 / d1 if d1 > 0 else 1
        w2 = 1 / d2 if d2 > 0 else 1
        w_sum = w1 + w2

        merged = pd.merge(df1[['temp']], df2[['temp']], 
                         left_index=True, right_index=True, 
                         suffixes=('_1', '_2'), how='inner')
        
        if not merged.empty:
            merged['temp_interp'] = (merged['temp_1'] * w1 + merged['temp_2'] * w2) / w_sum
            return merged[['temp_interp']]
        return pd.DataFrame(columns=['temp_interp'])
    except Exception as e:
        logger.error(f"Interpolation error: {e}")
        return pd.DataFrame(columns=['temp_interp'])

def load_ebi_raster():
    try:
        tifs = sorted(DATA_DIR.glob("**/*.tif"), key=lambda x: x.stat().st_mtime, reverse=True)
        if tifs:
            with rasterio.open(tifs[0]) as src:
                return src.read(1), src.transform, tifs[0].name
        return None, None, None
    except Exception as e:
        logger.warning(f"Raster load failed: {e}")
        return None, None, None

# ====================== UI ======================
app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.input_select("farm_select", "Select Farm:", choices=list(FARMS.keys()), selected=DEFAULT_FARM),
        ui.input_slider("date_slider", "5-Day Window Start:", min=0, max=100, value=50, step=5),
        width="360px"
    ),
    ui.layout_column_wrap(
        ui.card(ui.card_header("🌡️ 5-Day Temperature"), ui.output_plot("dayplot"), full_screen=True),
        ui.card(ui.card_header("🗺️ Interactive Map"), ui.output_ui("interactive_map"), full_screen=True),
        width=1/2
    ),
    ui.card(ui.card_header("🌸 Enhanced Bloom Index (EBI)"), ui.output_plot("ebi_plot"), full_screen=True),
    ui.card(ui.card_header("📈 Trends & Frost Risk"), ui.output_plot("longterm_plot"), full_screen=True),
    ui.layout_column_wrap(
        ui.value_box("❄️ Chill Hours", ui.output_text("chill_points"), showcase=icon_svg("snowflake"), theme="primary"),
        ui.value_box("🌱 GDD Accumulated", ui.output_text("gdd_value"), showcase=icon_svg("seedling"), theme="success"),
        ui.value_box("🌸 Bloom Strength", ui.output_text("ebi_total"), showcase=icon_svg("leaf"), theme="warning"),
        ui.value_box("⚠️ Frost Alert", ui.output_text("frost_alert"), showcase=icon_svg("bell"), theme="danger"),
        fill=False
    ),
    ui.div(
        ui.download_button("download_csv", "📥 Export Data (CSV)"),
        ui.download_button("download_pdf", "📄 Export Full Report (PDF)"),
        style="text-align: center; margin: 20px 0; padding: 10px;"
    ),
    ui.include_css(app_dir / "styles.css"),
    title="Cherry Phenology Dashboard • Traverse City 2026",
    fillable=True
)

# ====================== SERVER ======================
def server(input, output, session):
    
    @reactive.calc
    def selected_farm_coord():
        return FARMS[input.farm_select()]
    
    @reactive.calc
    def interpolated_data():
        lat, lon = selected_farm_coord()
        coord1 = (STATIONS["TVC"].latitude, STATIONS["TVC"].longitude)
        coord2 = (STATIONS["ACB"].latitude, STATIONS["ACB"].longitude)
        return interpolate_temp(df_tvc, df_acb, coord1, coord2, lat, lon)
    
    @output
    @render.plot
    def dayplot():
        df = interpolated_data()
        fig, ax = plt.subplots(figsize=(10, 6))
        if not df.empty:
            temps_f = C_to_F(df['temp_interp'])
            ax.plot(df.index, temps_f, color='#e74c3c', linewidth=2.5, label='Orchard Temp')
            ax.axhline(32, color='blue', linestyle='--', label='Freezing')
            ax.set_title(f"5-Day Temperature - {input.farm_select()}")
            ax.set_ylabel("Temperature (°F)")
            ax.legend()
            ax.grid(True, alpha=0.3)
            plt.xticks(rotation=45)
        plt.tight_layout()
        return fig

    @output
    @render.ui
    def interactive_map():
        lat, lon = selected_farm_coord()
        m = folium.Map(location=[lat, lon], zoom_start=13, tiles="CartoDB positron")
        folium.Marker([lat, lon], popup=f"<b>{input.farm_select()}</b>", 
                     icon=folium.Icon(color="red", icon="tree")).add_to(m)
        return ui.HTML(m._repr_html_())

    @output
    @render.plot
    def ebi_plot():
        data, transform, filename = load_ebi_raster()
        fig, ax = plt.subplots(figsize=(9, 9))
        if data is not None:
            show(data, ax=ax, cmap='viridis')
            ax.set_title(f"EBI - {filename}")
        else:
            plt.text(0.5, 0.5, "No EBI Raster Found\n\nPlace .tif files in dashboard/data/", 
                    ha='center', va='center', fontsize=14)
        plt.tight_layout()
        return fig

    @output
    @render.plot
    def longterm_plot():
        df = interpolated_data()
        fig, ax = plt.subplots(figsize=(12, 7))
        if not df.empty:
            temps_f = C_to_F(df['temp_interp'])
            ax.plot(df.index, temps_f.rolling(24).mean(), label='24h Avg Temp', color='orange', linewidth=2)
            ax.set_title("Temperature Trend")
            ax.set_ylabel("Temperature (°F)")
            ax.legend()
            ax.grid(True, alpha=0.3)
        plt.tight_layout()
        return fig

    @output
    @render.text
    def chill_points():
        df = interpolated_data()
        if df.empty: return "N/A"
        chill = ((df['temp_interp'] >= 0) & (df['temp_interp'] < 7.22)).sum()
        return f"{int(chill)} hours"

    @output
    @render.text
    def gdd_value():
        df = interpolated_data()
        if df.empty: return "N/A"
        gdd = (df['temp_interp'] - 5).clip(lower=0).sum() / 24
        return f"{int(gdd)} GDD"

    @output
    @render.text
    def ebi_total():
        data, _, _ = load_ebi_raster()
        if data is not None:
            return f"Active (Max: {data.max():.2f})"
        return "Pending Raster"

    @output
    @render.text
    def frost_alert():
        df = interpolated_data()
        if df.empty: return "No data"
        recent_min_f = C_to_F(df['temp_interp'].tail(48).min())
        if recent_min_f < 34:
            return f"⚠️ FROST RISK - {recent_min_f:.1f}°F"
        elif recent_min_f < 38:
            return f"⚠️ Watch - {recent_min_f:.1f}°F"
        return "✅ No immediate risk"

    # Export handlers
    @output
    @render.download
    def download_csv():
        df = interpolated_data()
        if df.empty:
            df = pd.DataFrame({"status": ["No data available"]})
        return df.to_csv(index=True)

    @output
    @render.download
    def download_pdf():
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(100, 750, f"Cherry Dashboard Report - {input.farm_select()}")
        c.setFont("Helvetica", 12)
        c.drawString(100, 720, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        c.drawString(100, 690, f"Chill Hours: {chill_points()}")
        c.drawString(100, 670, f"GDD Accumulated: {gdd_value()}")
        c.drawString(100, 650, f"Frost Alert: {frost_alert()}")
        c.drawString(100, 630, f"Bloom Strength: {ebi_total()}")
        c.save()
        buf.seek(0)
        return buf.getvalue()

app = App(app_ui, server)

if __name__ == "__main__":
    app.run()