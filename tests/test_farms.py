from farms import default_region_id, get_region, load_config, regions
from phenology import load_phenology_config


def test_farms_config_has_hq_and_regions():
    cfg = load_config()
    assert cfg["organization"] == "Shoreline Fruit INC"
    ids = {r["id"] for r in regions()}
    assert default_region_id() in ids
    assert "williamsburg_gt" in ids
    assert "oceana_hart" in ids
    assert "berrien" in ids
    for r in regions():
        assert -90 < r["lat"] < 90
        assert -180 < r["lon"] < 180


def test_williamsburg_is_near_public_hq():
    r = get_region("williamsburg_gt")
    assert abs(r["lat"] - 44.8153) < 0.02
    assert abs(r["lon"] + 85.4445) < 0.02


def test_phenology_config_has_zavalloni_stages():
    cfg = load_phenology_config()
    stages = cfg["stages_zavalloni_from_mar1_base4c"]
    labels = [s["id"] for s in stages]
    assert labels[0] == "side_green"
    assert "full_bloom" in labels
    assert cfg["chill"]["utah"]["requirement_cu"] == 954
