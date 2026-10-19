from weather_look import classify_forecast, classify_obs, harvest_tone, score_tone


def test_forecast_keywords():
    assert classify_forecast("Thunderstorms likely") == "thunderstorm"
    assert classify_forecast("Rain showers") == "rain"
    assert classify_forecast("Snow") == "snow"
    assert classify_forecast("Patchy fog") == "fog"
    assert classify_forecast("Sunny", 92) == "heat"
    assert classify_forecast("Sunny") == "sunny"
    assert classify_forecast("Partly cloudy") == "partly"
    assert classify_forecast("Overcast") == "cloudy"
    assert classify_forecast("Windy") == "wind"
    assert classify_forecast("Frost advisory") == "frost"


def test_tones():
    assert harvest_tone("Closed 2026-08-01") == "gray"
    assert harvest_tone("Opens 2026-07-10") == "orange"
    assert harvest_tone("In window") == "green"
    assert score_tone(95) == "green"
    assert score_tone(75) == "orange"
    assert score_tone(40) == "red"
