import pytest

from app.graph import queries as q
from tests.conftest import needs_neo4j

pytestmark = needs_neo4j


@pytest.fixture(scope="module")
def d(seeded):
    return seeded["driver"]


def test_summary(d, seeded):
    s = q.summary(d)
    assert s["inpatient_beds"] == 300
    assert s["occupied_beds"] == seeded["info"]["census_patients"] > 0
    assert s["by_label"]["Hospital"] == 1


def test_list_wards_consistent(d):
    wards = q.list_wards(d)
    assert len(wards) == 18
    inpatient = [w for w in wards if w["inpatient"]]
    assert sum(w["beds"] for w in inpatient) == 300
    assert all(w["available"] == w["beds"] - w["occupied"] for w in wards)


def test_ward_detail(d):
    res = q.ward_detail(d, "micu")
    assert res["ward"]["code"] == "MICU" and len(res["beds"]) == 20
    assert any(a["kind"] == "ventilator" for a in res["assets"])
    assert q.ward_detail(d, "NOPE") is None


def test_bed_availability_filter(d):
    rows = q.bed_availability(d, "icu")
    assert {r["ward"] for r in rows} == {"MICU", "SICU", "CVICU", "CCU"}
    assert sum(r["beds"] for r in rows) == 58


@pytest.mark.parametrize("text,expected", [("GEN-2", "GEN-2"), ("Medical ICU", "WARD-MICU"), ("micu", "WARD-MICU"),
                                           ("ventilator 3", None), ("oxygen manifold", "O2-MAN")])
def test_search(d, text, expected):
    hits = q.search(d, text)
    assert hits
    if expected:
        assert expected in [h["id"] for h in hits[:3]]


def test_search_empty(d):
    assert q.search(d, "   ") == []


def test_get_node_and_neighborhood(d):
    assert q.get_node(d, "MISSING") is None
    n = q.neighborhood(d, "WARD-MICU")
    assert n["nodes"][0]["id"] == "WARD-MICU" and n["links"]
    assert q.neighborhood(d, "MISSING") == {"nodes": [], "links": []}


def test_upstream_of_ventilator(d):
    ups = {r["id"]: r for r in q.upstream_dependencies(d, "AST-VENTILATOR-0003")}
    assert {"ZV-O2-MICU", "O2-MAN", "ATS-CR"} <= set(ups)
    assert ups["UTIL-1"]["required"] is False


def test_impact_single_asset(d):
    r = q.failure_impact(d, "AST-VENTILATOR-0003")
    assert r["counts"]["assets"] == 1 and r["counts"]["beds"] == 1 and r["category"] == "equipment"
    assert r["modes"]["equipment"] == 1


def test_impact_generator_alone_is_redundant(d):
    r = q.failure_impact(d, ["GEN-2"])
    assert r["counts"]["beds"] == 0 and r["severity"] == "low"


def test_impact_utility_plus_generator_takes_critical_branch(d):
    r = q.failure_impact(d, ["UTIL-1", "GEN-2"])
    ats = {i["id"] for i in r["infrastructure"]}
    assert "ATS-CR" in ats and "ATS-LS" not in ats
    assert r["counts"]["beds"] > 150 and r["category"] == "power"


def test_impact_utility_alone_only_hits_normal_branch(d):
    r = q.failure_impact(d, ["UTIL-1"])
    infra = {i["id"] for i in r["infrastructure"]}
    assert "ATS-CR" not in infra and "PNL-4N" in infra and "PNL-4CR" not in infra


def test_impact_oxygen_valve(d):
    r = q.failure_impact(d, "ZV-O2-MICU")
    assert r["counts"]["beds"] == 20 and r["category"] == "medical_gas"
    assert {w["ward"] for w in r["wards"]} == {"MICU"}
    assert r["substitutes"] and r["substitutes"][0]["kind"] == "ventilator"


def test_impact_network_switch_degrades_not_disables(d):
    r = q.failure_impact(d, "SW-2A")
    assert r["category"] == "network" and r["modes"]["network"] == r["counts"]["assets"]
    assert r["min_battery_minutes"] is None


def test_impact_power_circuit_battery(d):
    r = q.failure_impact(d, "CKT-MICU-01")
    assert r["counts"]["assets_on_battery"] > 0 and r["min_battery_minutes"] > 0


def test_impact_patients_sorted_by_acuity(d):
    r = q.failure_impact(d, "PNL-2CR")
    acuities = [p["acuity"] for p in r["patients"]]
    assert acuities == sorted(acuities, reverse=True)


def test_impact_unknown(d):
    assert "error" in q.failure_impact(d, ["NOPE-1"])


@pytest.mark.parametrize("patients,high,beds,sev", [(0, 0, 0, "low"), (0, 0, 3, "moderate"), (2, 0, 2, "moderate"),
                                                    (2, 1, 2, "high"), (12, 0, 12, "high"), (5, 5, 5, "critical"),
                                                    (40, 0, 40, "critical")])
def test_severity(patients, high, beds, sev):
    assert q._severity(patients, high, beds) == sev


def test_find_assets_filters(d):
    allv = q.find_assets(d, kind="ventilator")
    avail = q.find_assets(d, kind="ventilator", status="available")
    assert allv["total"] == 40 and 0 < avail["total"] < allv["total"]
    assert all(i["status"] == "available" for i in avail["items"])
    assert q.find_assets(d, kind="ventilator", ward="micu")["total"] == 14


def test_find_staff_with_cert(d):
    vent = q.find_staff(d, role="respiratory_therapist", cert="vent")
    assert vent["total"] == q.find_staff(d, role="respiratory_therapist")["total"] > 0
    assert all("VENT" in s["certs"] for s in vent["items"])


def test_get_patient(d):
    rec = d.execute_query("MATCH (p:Patient) RETURN p.id AS id LIMIT 1").records[0]["id"]
    res = q.get_patient(d, rec)
    assert res["bed"].startswith("BED-") and res["ward"]
    assert q.get_patient(d, "PT-NOPE") is None


def test_asset_kinds(d):
    kinds = {r["kind"]: r for r in q.asset_kinds(d)}
    assert kinds["ct_scanner"]["total"] == 2
