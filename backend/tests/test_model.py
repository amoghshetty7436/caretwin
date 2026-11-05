from collections import Counter

import pytest

from app.graph.model import WARDS, generate_hospital


def test_exactly_300_inpatient_beds(hospital):
    beds = [n for n in hospital.nodes if n["label"] == "Bed" and n["inpatient"]]
    assert len(beds) == 300
    assert sum(w["beds"] for w in WARDS if w["inpatient"]) == 300


def test_node_count_near_4100(hospital):
    assert 3900 <= len(hospital.nodes) <= 4300


def test_ids_unique(hospital):
    ids = [n["id"] for n in hospital.nodes]
    assert len(ids) == len(set(ids))


def test_edges_reference_existing_nodes(hospital):
    ids = {n["id"] for n in hospital.nodes}
    bad = [e for e in hospital.edges if e[0] not in ids or e[2] not in ids]
    assert bad == []


def test_deterministic():
    a, b = generate_hospital(42), generate_hospital(42)
    assert a.nodes == b.nodes and a.edges == b.edges


def test_different_seed_changes_staff_names():
    a, b = generate_hospital(1), generate_hospital(2)
    names_a = [n["name"] for n in a.nodes if n["label"] == "Staff"][:50]
    names_b = [n["name"] for n in b.nodes if n["label"] == "Staff"][:50]
    assert names_a != names_b


@pytest.mark.parametrize("ward", [w["code"] for w in WARDS if w["beds"]])
def test_ward_bed_counts(hospital, ward):
    expected = next(w["beds"] for w in WARDS if w["code"] == ward)
    beds = [n for n in hospital.nodes if n["label"] == "Bed" and n["ward"] == ward]
    assert len(beds) == expected


def test_every_bed_has_power(hospital):
    by_id = hospital.by_id()
    powered = {s for s, r, d, _ in hospital.edges if r == "DEPENDS_ON" and by_id[d]["kind"] == "circuit"}
    beds = {n["id"] for n in hospital.nodes if n["label"] == "Bed"}
    assert beds <= powered


def test_every_asset_has_power(hospital):
    by_id = hospital.by_id()
    powered = {s for s, r, d, _ in hospital.edges if r == "DEPENDS_ON" and by_id[d]["kind"] == "circuit"}
    assets = {n["id"] for n in hospital.nodes if n["label"] == "Asset"}
    assert assets <= powered


def test_inpatient_beds_have_oxygen(hospital):
    o2 = {s for s, r, d, _ in hospital.edges if r == "DEPENDS_ON" and d.startswith("ZV-O2-")}
    beds = {n["id"] for n in hospital.nodes if n["label"] == "Bed" and n["inpatient"]}
    assert beds <= o2


def test_transfer_switches_are_redundant(hospital):
    edges = [(s, d, p) for s, r, d, p in hospital.edges if r == "DEPENDS_ON" and s.startswith("ATS-")]
    assert edges and all(p["required"] is False for _, _, p in edges)
    assert {d for _, d, _ in edges} == {"UTIL-1", "GEN-1", "GEN-2"}


def test_other_dependencies_required(hospital):
    others = [p for s, r, d, p in hospital.edges if r == "DEPENDS_ON" and not s.startswith("ATS-")]
    assert all(p["required"] for p in others)


def test_dependency_graph_is_acyclic(hospital):
    adj: dict[str, list[str]] = {}
    for s, r, d, _ in hospital.edges:
        if r == "DEPENDS_ON":
            adj.setdefault(s, []).append(d)
    state: dict[str, int] = {}

    def visit(n):
        state[n] = 1
        for m in adj.get(n, []):
            if state.get(m) == 1:
                raise AssertionError(f"cycle via {n}->{m}")
            if m not in state:
                visit(m)
        state[n] = 2

    for n in list(adj):
        if n not in state:
            visit(n)


def test_icu_has_ventilators(hospital):
    vents = Counter(n["ward"] for n in hospital.nodes if n["label"] == "Asset" and n["kind"] == "ventilator")
    assert all(vents[w] > 0 for w in ("MICU", "SICU", "CVICU", "CCU"))


def test_staff_have_certifications_and_shift(hospital):
    staff = {n["id"] for n in hospital.nodes if n["label"] == "Staff"}
    shifted = {s for s, r, _, _ in hospital.edges if r == "WORKS_SHIFT"}
    assert staff == shifted
    nurses = [n for n in hospital.nodes if n["label"] == "Staff" and n["role"] == "registered_nurse"]
    certified = {s for s, r, d, _ in hospital.edges if r == "HAS_CERT" and d == "CERT-ACLS"}
    assert all(n["id"] in certified for n in nurses)


def test_asset_status_values(hospital):
    statuses = {n["status"] for n in hospital.nodes if n["label"] == "Asset"}
    assert statuses <= {"in_service", "available", "maintenance"}


def test_label_counts(hospital):
    c = hospital.label_counts()
    assert c["Hospital"] == 1 and c["Ward"] == len(WARDS) and c["Floor"] == 8
    assert c["Staff"] > 1500 and c["Asset"] > 1500
