from enviroweather import parse_enviroweather_csv
from satellite import cloud_snow_mask
from weather_look import harvest_tone
import numpy as np


def test_parse_enviroweather_temp_f():
    csv = "Date/Time,Air Temp (F),Rel Hum (%),Precip (in),Leaf Wetness (hr)\n2026-07-01 13:00,78,55,0.00,0\n"
    df = parse_enviroweather_csv(csv, "wmt")
    assert "temp_c" in df.columns
    assert abs(float(df["temp_c"].iloc[0]) - (78 - 32) * 5 / 9) < 0.2
    assert df["source"].iloc[0].startswith("enviroweather")


def test_cloud_snow_white_pixel():
    blue = np.array([[220.0, 40.0]])
    green = np.array([[222.0, 80.0]])
    red = np.array([[218.0, 40.0]])
    mask = cloud_snow_mask(blue, green, red)
    assert bool(mask[0, 0]) is True
    assert bool(mask[0, 1]) is False
