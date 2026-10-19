import numpy as np
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from satellite import compute_ebi, compute_ndvi, vegetation_mask


def test_ebi_uint8_finite_and_positive_on_bright_bloomish_pixel():
    blue = np.array([[40.0, 0.0]])
    green = np.array([[80.0, 0.0]])
    red = np.array([[90.0, 0.0]])
    ebi = compute_ebi(blue, green, red, eps=256.0)
    assert np.isfinite(ebi[0, 0])
    assert ebi[0, 0] > 0
    assert np.isnan(ebi[0, 1])


def test_ebi_matches_chen_formula():
    r, g, b, eps = 100.0, 80.0, 50.0, 256.0
    expected = (r + g + b) / ((g / b) * (r - b + eps))
    got = compute_ebi(np.array([[b]]), np.array([[g]]), np.array([[r]]), eps=256.0)[0, 0]
    assert abs(got - expected) < 1e-9


def test_ndvi_high_for_healthy_canopy():
    red = np.array([[20.0]])
    nir = np.array([[80.0]])
    ndvi = compute_ndvi(red, nir)[0, 0]
    assert ndvi > 0.5


def test_vegetation_mask_uses_ndvi_or_green_dominance():
    green = np.array([[80.0, 10.0]])
    red = np.array([[40.0, 80.0]])
    blue = np.array([[30.0, 80.0]])
    nir = np.array([[90.0, 10.0]])
    mask = vegetation_mask(green, red, blue, nir)
    assert bool(mask[0, 0]) is True
    assert bool(mask[0, 1]) is False
