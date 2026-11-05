import simpy
import pytest

from app.sim.engine import BedManager, SimParams, Simulation, _layout


@pytest.fixture(scope="module")
def baseline():
    sim = Simulation(SimParams(days=10, warmup_days=3, seed=3))
    return sim, sim.run()


def test_layout_has_300_beds():
    assert sum(len(v) for v in _layout().values()) == 300


def test_kpis_present(baseline):
    _, res = baseline
    k = res["kpis"]
    for key in ("arrivals", "occupancy", "occupancy_by_unit", "ed_boarding_h", "los_days", "off_service_share"):
        assert key in k
    assert 0.4 < k["occupancy"] <= 1.0
    assert k["arrivals"] > 100


def test_census_rows(baseline):
    _, res = baseline
    assert res["census"] and all("ICU" in r for r in res["census"])


def test_capacity_never_exceeded(baseline):
    sim, _ = baseline
    for row in sim.census:
        for unit, cap in sim.beds.capacity.items():
            assert row[unit] <= cap


def test_current_patients_unique_beds(baseline):
    sim, _ = baseline
    pts = sim.current_patients()
    assert len({p["bed_id"] for p in pts}) == len(pts)
    assert all(p["bed_id"].startswith("BED-") and 1 <= p["acuity"] <= 5 for p in pts)


def test_deterministic_for_seed():
    a = Simulation(SimParams(days=4, warmup_days=1, seed=11)).run()["kpis"]
    b = Simulation(SimParams(days=4, warmup_days=1, seed=11)).run()["kpis"]
    assert a == b


def test_surge_increases_arrivals():
    base = Simulation(SimParams(days=6, warmup_days=1, seed=5)).run()["kpis"]["arrivals"]
    surge = Simulation(SimParams(days=6, warmup_days=1, seed=5, surge_multiplier=2.0, surge_start_h=24,
                                 surge_end_h=24 * 7)).run()["kpis"]["arrivals"]
    assert surge > base * 1.4


def test_bed_outage_recorded():
    icu = [b for b, _ in _layout()["ICU"]][:30]
    sim = Simulation(SimParams(days=4, warmup_days=1, seed=5, offline_beds=icu, outage_start_h=30))
    sim.run()
    assert max(r["offline_beds"] for r in sim.census) == 30
    # no patient admitted after the outage began may be placed in an offline bed
    for bed in icu:
        pt = sim.beds.occupant.get(bed)
        assert pt is None or pt.arrival < 30


def test_bed_outage_ends():
    icu = [b for b, _ in _layout()["ICU"]][:10]
    sim = Simulation(SimParams(days=3, warmup_days=1, seed=5, offline_beds=icu, outage_start_h=10, outage_end_h=20))
    sim.run()
    assert sim.census[-1]["offline_beds"] == 0 and max(r["offline_beds"] for r in sim.census) == 10


def test_bed_manager_fifo_and_overflow():
    env = simpy.Environment()
    bm = BedManager(env, {"MED": [("B1", "MEDA")], "SURG": [("B2", "SURG")]})
    e1 = bm.request("MED", "p1")
    e2 = bm.request("MED", "p2")
    assert e1.triggered and not e2.triggered
    assert bm.try_overflow("MED", e2, type("P", (), {"off_service": False})())
    assert e2.triggered and e2.value == "B2"
    bm.release("B1")
    assert bm.free["MED"] == __import__("collections").deque(["B1"])


def test_bed_manager_block_and_unblock():
    env = simpy.Environment()
    bm = BedManager(env, {"ICU": [("I1", "MICU"), ("I2", "MICU")]})
    bm.block(["I1"])
    ev = bm.request("ICU", "p")
    assert ev.value == "I2"
    assert not bm.request("ICU", "q").triggered
    bm.unblock()
    assert bm.occupied("ICU") == 2


def test_waiting_patient_gets_released_bed():
    env = simpy.Environment()
    bm = BedManager(env, {"ICU": [("I1", "MICU")]})
    bm.request("ICU", "a")
    waiting = bm.request("ICU", "b")
    bm.release("I1")
    assert waiting.triggered and bm.occupant["I1"] == "b"
