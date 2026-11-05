import pytest

from tests.conftest import needs_neo4j, needs_pg

pytestmark = needs_neo4j


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["neo4j"] == "up"


@pytest.mark.parametrize("path", ["/api/summary", "/api/wards", "/api/wards/MICU", "/api/beds/availability",
                                  "/api/beds/availability?unit_type=ICU", "/api/search?q=generator",
                                  "/api/nodes/GEN-1", "/api/nodes/WARD-MICU/neighborhood",
                                  "/api/nodes/AST-VENTILATOR-0003/dependencies", "/api/impact/ZV-O2-MICU",
                                  "/api/infrastructure", "/api/assets?kind=ventilator", "/api/assets/kinds",
                                  "/api/staff?ward=MICU&cert=CCRN"])
def test_get_endpoints_ok(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert r.json()


@pytest.mark.parametrize("path", ["/api/wards/NOPE", "/api/nodes/NOPE", "/api/nodes/NOPE/neighborhood",
                                  "/api/impact/NOPE", "/api/patients/PT-NOPE"])
def test_404s(client, path):
    assert client.get(path).status_code == 404


def test_search_validation(client):
    assert client.get("/api/search").status_code == 422


def test_impact_post_multi(client):
    r = client.post("/api/impact", json={"node_ids": ["UTIL-1", "GEN-2"]})
    body = r.json()
    assert r.status_code == 200 and body["category"] == "power" and body["query_ms"] > 0


def test_impact_post_validation(client):
    assert client.post("/api/impact", json={"node_ids": []}).status_code == 422


def test_patient_endpoint(client):
    beds = client.get("/api/wards/MICU").json()["beds"]
    pid = next(b["patient_id"] for b in beds if b["patient_id"])
    r = client.get(f"/api/patients/{pid}")
    assert r.status_code == 200 and r.json()["ward"] == "MICU"


def test_simulation_with_outage(client):
    r = client.post("/api/simulations", json={"days": 5, "seed": 3, "failed_node_ids": ["ZV-O2-SICU"],
                                              "outage_start_h": 12, "outage_duration_h": 24})
    body = r.json()
    assert r.status_code == 200 and body["offline_beds"] == 16
    assert "delta" in body and "baseline" in body


def test_simulation_surge(client):
    r = client.post("/api/simulations", json={"days": 4, "surge_multiplier": 1.8, "surge_end_h": 48})
    assert r.status_code == 200 and r.json()["delta"]["occupancy"] >= -0.05


def test_simulation_bad_node(client):
    assert client.post("/api/simulations", json={"days": 3, "failed_node_ids": ["NOPE"]}).status_code == 404


def test_simulation_validation(client):
    assert client.post("/api/simulations", json={"days": 0}).status_code == 422


@needs_pg
def test_simulation_list_and_get(client):
    client.post("/api/simulations", json={"days": 2, "name": "pytest"})
    runs = client.get("/api/simulations").json()
    assert runs
    one = client.get(f"/api/simulations/{runs[0]['id']}")
    assert one.status_code == 200 and one.json()["kpis"]
    assert client.get("/api/simulations/999999").status_code == 404


@needs_pg
def test_population(client):
    r = client.get("/api/population")
    assert r.status_code == 200 and "patients" in r.json()


@pytest.mark.parametrize("question,needle", [
    ("How many free beds does the Medical ICU have?", "WARD-MICU"),
    ("What happens if the MICU oxygen zone valve fails?", "ZV-O2-MICU"),
    ("How many ventilators are available?", "available ventilator"),
])
def test_assistant_endpoint(client, question, needle):
    r = client.post("/api/assistant/ask", json={"question": question})
    body = r.json()
    assert r.status_code == 200 and needle in body["answer"] and body["grounded"]


def test_assistant_validation(client):
    assert client.post("/api/assistant/ask", json={"question": "x"}).status_code == 422
