import json
import math

import pandas as pd
import pytest

from app.config import REPO_ROOT
from app.sim.calibration import DISCHARGE, UNIT_TYPES, _lognormal_fit, _shrink, build_segments, calibrate, map_careunit

MIMIC = REPO_ROOT / "data" / "external" / "mimic-iv-demo-2.2"


@pytest.mark.parametrize("name,expected", [
    ("Emergency Department", "ED"), ("Emergency Department Observation", "ED"),
    ("Medical Intensive Care Unit (MICU)", "ICU"), ("Coronary Care Unit (CCU)", "ICU"),
    ("Trauma SICU (TSICU)", "ICU"), ("Neuro Stepdown", "STEPDOWN"), ("Hematology/Oncology Intermediate", "STEPDOWN"),
    ("Medicine/Cardiology", "CARD"), ("Cardiac Surgery", "CARD"), ("Med/Surg/Trauma", "SURG"), ("Vascular", "SURG"),
    ("Medicine", "MED"), ("Observation", "MED"), ("Transplant", "TRANSPLANT"), ("Psychiatry", "PSYCH"),
    ("Hematology/Oncology", "ONC"), ("Neurology", "NEURO"), ("PACU", "PACU"), ("Discharge Lounge", DISCHARGE),
    ("Unknown", None), (float("nan"), None), ("", None),
])
def test_map_careunit(name, expected):
    assert map_careunit(name) == expected


def test_lognormal_fit_recovers_parameters():
    import numpy as np
    x = np.random.default_rng(0).lognormal(3.0, 0.6, 5000)
    fit = _lognormal_fit(x)
    assert abs(fit["mu"] - 3.0) < 0.05 and abs(fit["sigma"] - 0.6) < 0.05


def test_shrink_pulls_small_samples_toward_pool():
    pooled = {"mu": 4.0, "sigma": 1.0}
    small = _shrink({"mu": 2.0, "sigma": 0.5, "n": 3}, pooled)
    big = _shrink({"mu": 2.0, "sigma": 0.5, "n": 3000}, pooled)
    assert 2.0 < small["mu"] < 4.0 and abs(big["mu"] - 2.0) < 0.02
    assert small["pooling_weight"] < big["pooling_weight"]


def test_build_segments_merges_consecutive_units():
    t = pd.DataFrame({
        "subject_id": [1] * 4, "hadm_id": [10] * 4, "transfer_id": [1, 2, 3, 4],
        "eventtype": ["ED", "admit", "transfer", "discharge"],
        "careunit": ["Emergency Department", "Medicine", "Medicine", None],
        "intime": pd.to_datetime(["2100-01-01 00:00", "2100-01-01 05:00", "2100-01-02 05:00", "2100-01-03 05:00"]),
        "outtime": pd.to_datetime(["2100-01-01 05:00", "2100-01-02 05:00", "2100-01-03 05:00", None]),
    })
    segs = build_segments(t)
    assert list(segs.unit) == ["ED", "MED"]
    assert segs.iloc[1].hours == pytest.approx(48)
    assert list(segs.next_unit) == ["MED", DISCHARGE]


@pytest.fixture(scope="module")
def cal():
    return calibrate(MIMIC)


def test_calibration_counts(cal):
    assert cal["n_admissions"] == 275 and cal["n_patients"] == 100


def test_all_units_have_stay_distributions(cal):
    for u in UNIT_TYPES + ["PACU"]:
        d = cal["unit_stay_hours"][u]
        assert d["sigma"] > 0 and math.isfinite(d["mu"])


def test_transition_rows_sum_to_one(cal):
    for state, row in cal["transitions"].items():
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-3), state


def test_ed_always_admits(cal):
    assert DISCHARGE not in cal["transitions"]["ED"]


def test_arrival_profile(cal):
    assert len(cal["arrival_hour"]) == 24 and sum(cal["arrival_hour"]) == pytest.approx(1.0, abs=1e-3)


def test_validation_block(cal):
    v = cal["validation"]["hospital_los_days"]
    assert 4 < v["median"] < 6 and v["n"] == 275


def test_committed_calibration_matches_source(cal):
    committed = json.loads((REPO_ROOT / "data/calibration/mimic_demo_calibration.json").read_text())
    assert committed["n_segments"] == cal["n_segments"]
    assert committed["transitions"] == cal["transitions"]
