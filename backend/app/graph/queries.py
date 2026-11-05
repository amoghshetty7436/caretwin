"""Read-side graph queries. Every function returns plain JSON-serialisable data.

These functions are the single source of truth used by the REST API *and* by the LLM
assistant's tools, so an answer the assistant cites can always be reproduced via the API.
"""
from __future__ import annotations

from neo4j import Driver

POWER_KINDS = {"utility_feed", "generator", "transfer_switch", "ups", "power_panel", "circuit"}
GAS_KINDS = {"oxygen_source", "oxygen_manifold", "o2_zone_valve", "medical_air_plant", "air_zone_valve"}
NETWORK_KINDS = {"core_switch", "network_switch", "central_monitoring_station"}
# life-support first when ranking replacement needs
KIND_PRIORITY = ["ventilator", "transport_ventilator", "anesthesia_machine", "patient_monitor", "defibrillator",
                 "infusion_pump", "dialysis_machine", "telemetry_transmitter", "pca_pump", "feeding_pump",
                 "portable_suction", "crash_cart", "vitals_monitor", "portable_ultrasound"]


def _rows(driver: Driver, cypher: str, **params) -> list[dict]:
    records, _, _ = driver.execute_query(cypher, params, database_="neo4j", routing_="r")
    return [r.data() for r in records]


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def summary(driver: Driver) -> dict:
    labels = _rows(driver, "MATCH (n:Entity) RETURN labels(n)[1] AS label, count(*) AS n ORDER BY label")
    beds = _rows(driver, """
        MATCH (b:Bed {inpatient: true})
        OPTIONAL MATCH (p:Patient)-[:OCCUPIES]->(b)
        RETURN count(DISTINCT b) AS beds, count(DISTINCT p) AS occupied""")[0]
    rels = _rows(driver, "MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
    nodes = sum(r["n"] for r in labels)
    return {"nodes": nodes, "relationships": rels, "by_label": {r["label"]: r["n"] for r in labels},
            "inpatient_beds": beds["beds"], "occupied_beds": beds["occupied"],
            "occupancy": round(beds["occupied"] / beds["beds"], 4) if beds["beds"] else 0.0}


def list_wards(driver: Driver) -> list[dict]:
    return _rows(driver, """
        MATCH (w:Ward)
        OPTIONAL MATCH (b:Bed)-[:IN_ROOM]->(:Room)-[:IN_WARD]->(w)
        OPTIONAL MATCH (p:Patient)-[:OCCUPIES]->(b)
        WITH w, count(DISTINCT b) AS beds, count(DISTINCT p) AS occupied
        OPTIONAL MATCH (s:Staff {on_duty: true})-[:WORKS_IN]->(w)
        WITH w, beds, occupied, count(s) AS staff_on_duty
        OPTIONAL MATCH (rn:Staff {on_duty: true})-[:WORKS_IN]->(w)
          WHERE rn.role IN ['registered_nurse', 'charge_nurse']
        RETURN w.id AS id, w.code AS code, w.name AS name, w.unit_type AS unit_type, w.floor AS floor,
               w.inpatient AS inpatient, beds, occupied, beds - occupied AS available,
               CASE WHEN beds = 0 THEN 0.0 ELSE round(toFloat(occupied) / beds, 4) END AS occupancy,
               staff_on_duty, count(rn) AS nurses_on_duty
        ORDER BY w.floor, w.code""")


def ward_detail(driver: Driver, code: str) -> dict | None:
    ward = _rows(driver, "MATCH (w:Ward {code: $code}) RETURN w {.*} AS w", code=code.upper())
    if not ward:
        return None
    beds = _rows(driver, """
        MATCH (b:Bed)-[:IN_ROOM]->(r:Room)-[:IN_WARD]->(:Ward {code: $code})
        OPTIONAL MATCH (p:Patient)-[:OCCUPIES]->(b)
        RETURN b.id AS id, b.name AS name, r.id AS room, b.isolation AS isolation,
               p.id AS patient_id, p.acuity AS acuity, p.reason AS reason, p.age AS age
        ORDER BY b.id""", code=code.upper())
    assets = _rows(driver, """
        MATCH (a:Asset {ward: $code})
        RETURN a.kind AS kind, a.status AS status, count(*) AS n ORDER BY kind, status""", code=code.upper())
    staff = _rows(driver, """
        MATCH (s:Staff)-[:WORKS_IN]->(:Ward {code: $code})
        RETURN s.role AS role, count(*) AS headcount, sum(CASE WHEN s.on_duty THEN 1 ELSE 0 END) AS on_duty
        ORDER BY headcount DESC""", code=code.upper())
    return {"ward": ward[0]["w"], "beds": beds, "assets": assets, "staff": staff}


def bed_availability(driver: Driver, unit_type: str | None = None) -> list[dict]:
    return _rows(driver, """
        MATCH (b:Bed {inpatient: true})
        WHERE $ut IS NULL OR b.unit_type = $ut
        OPTIONAL MATCH (p:Patient)-[:OCCUPIES]->(b)
        WITH b.unit_type AS unit_type, b.ward AS ward, count(b) AS beds, count(p) AS occupied,
             collect(CASE WHEN p IS NULL THEN b.id END) AS free
        RETURN unit_type, ward, beds, occupied, beds - occupied AS available, free[0..10] AS sample_free_beds
        ORDER BY unit_type, ward""", ut=unit_type.upper() if unit_type else None)


# ---------------------------------------------------------------------------
# Search / exploration
# ---------------------------------------------------------------------------
def search(driver: Driver, q: str, label: str | None = None, limit: int = 10) -> list[dict]:
    q = q.strip()
    if not q:
        return []
    exact = _rows(driver, """
        MATCH (n:Entity) WHERE toUpper(n.id) = toUpper($q) OR toLower(n.name) = toLower($q)
           OR (n:Ward AND toUpper(n.code) = toUpper($q))
        RETURN n.id AS id, labels(n)[1] AS label, n.name AS name, n.kind AS kind, 10.0 AS score LIMIT $limit""",
                  q=q, limit=limit)
    lucene = " AND ".join(f"{_escape(t)}~" if len(t) > 3 else f"{_escape(t)}*" for t in q.split())
    fuzzy = _rows(driver, """
        CALL db.index.fulltext.queryNodes('entity_name', $lq) YIELD node, score
        WHERE $label IS NULL OR $label IN labels(node)
        RETURN node.id AS id, labels(node)[1] AS label, node.name AS name, node.kind AS kind, score
        LIMIT $limit""", lq=lucene, label=label, limit=limit)
    seen, out = set(), []
    for r in exact + fuzzy:
        if r["id"] not in seen and (label is None or r["label"] == label):
            seen.add(r["id"])
            out.append(r)
    return out[:limit]


def _escape(t: str) -> str:
    special = '+-&|!(){}[]^"~*?:\\/'
    return "".join("\\" + c if c in special else c for c in t)


def get_node(driver: Driver, node_id: str) -> dict | None:
    rows = _rows(driver, "MATCH (n:Entity {id: $id}) RETURN n {.*, label: labels(n)[1]} AS n", id=node_id)
    return rows[0]["n"] if rows else None


def neighborhood(driver: Driver, node_id: str, limit: int = 150) -> dict:
    rows = _rows(driver, """
        MATCH (n:Entity {id: $id})
        OPTIONAL MATCH (n)-[r]-(m:Entity)
        WITH n, r, m LIMIT $limit
        RETURN n {.id, .name, .kind, label: labels(n)[1]} AS n,
               collect(CASE WHEN m IS NULL THEN null ELSE
                 {node: m {.id, .name, .kind, label: labels(m)[1]}, type: type(r),
                  outgoing: startNode(r) = n} END) AS links""", id=node_id, limit=limit)
    if not rows:
        return {"nodes": [], "links": []}
    center = rows[0]["n"]
    nodes = {center["id"]: center}
    links = []
    for link in rows[0]["links"]:
        m = link["node"]
        nodes[m["id"]] = m
        src, dst = (center["id"], m["id"]) if link["outgoing"] else (m["id"], center["id"])
        links.append({"source": src, "target": dst, "type": link["type"]})
    return {"nodes": list(nodes.values()), "links": links}


def upstream_dependencies(driver: Driver, node_id: str) -> list[dict]:
    """What does this node rely on (power / gas / network chain)?"""
    return _rows(driver, """
        MATCH path = (n:Entity {id: $id})-[:DEPENDS_ON*1..8]->(u:Entity)
        WITH u, min(length(path)) AS hops,
             all(r IN relationships(head(collect(path))) WHERE r.required) AS required
        RETURN u.id AS id, u.name AS name, u.kind AS kind, hops, required
        ORDER BY hops, id""", id=node_id)


# ---------------------------------------------------------------------------
# Asset / infrastructure failure impact
# ---------------------------------------------------------------------------
IMPACT_CYPHER = """
MATCH (x:Entity) WHERE x.id IN $ids
WITH collect(x) AS seeds
// nodes with redundant sources fail only when every redundant source is in the failed set
OPTIONAL MATCH (a:Entity)-[:DEPENDS_ON {required: false}]->(t:Entity)
WITH seeds, a, collect(t) AS ts
WITH seeds, [g IN collect(CASE WHEN all(t IN ts WHERE t IN seeds) THEN a END) WHERE g IS NOT NULL] AS extra
WITH seeds + extra AS roots
UNWIND roots AS root
OPTIONAL MATCH (d:Entity)-[:DEPENDS_ON*1..8 {required: true}]->(root)
WITH roots, collect(DISTINCT d) AS deps
WITH [n IN roots + deps WHERE n IS NOT NULL] AS impacted
UNWIND impacted AS n
WITH DISTINCT n
OPTIONAL MATCH (n)-[:ASSIGNED_TO]->(ab:Bed)
WITH n, ab
WITH collect(DISTINCT n) AS impacted,
     collect(DISTINCT CASE WHEN n:Bed THEN n END) + collect(DISTINCT ab) AS bed_list
UNWIND (CASE WHEN size(bed_list) = 0 THEN [null] ELSE bed_list END) AS b
WITH impacted, b
OPTIONAL MATCH (p:Patient)-[:OCCUPIES]->(b)
WITH impacted, collect(DISTINCT b {.id, .ward, .unit_type, .floor}) AS beds,
     collect(DISTINCT p {.id, .acuity, .reason, .age, .unit_type, bed: b.id, ward: b.ward}) AS patients
RETURN
  [n IN impacted WHERE n:Asset | n {.id, .name, .kind, .ward, .status, .battery_minutes,
      lost: [(n)-[:DEPENDS_ON]->(u) WHERE u IN impacted | u.kind]}] AS assets,
  [n IN impacted WHERE n:Infrastructure | n {.id, .name, .kind, .floor}] AS infrastructure,
  beds, patients
"""

SUBSTITUTE_CYPHER = """
UNWIND $needs AS need
MATCH (a:Asset {kind: need.kind, status: 'available'})
WHERE NOT a.id IN $impacted
WITH need, a, CASE WHEN a.ward = need.ward THEN 0 ELSE 1 END AS distance
ORDER BY distance, a.floor
WITH need, collect(a {.id, .ward, .floor, distance: distance})[0..3] AS options, count(a) AS total
RETURN need.kind AS kind, need.ward AS ward, need.count AS needed, total AS available_elsewhere, options
"""


def failure_impact(driver: Driver, ids: list[str] | str) -> dict:
    if isinstance(ids, str):
        ids = [ids]
    seeds = [get_node(driver, i) for i in ids]
    missing = [i for i, s in zip(ids, seeds) if s is None]
    if missing:
        return {"error": f"unknown node id(s): {', '.join(missing)}", "failed": ids}
    row = _rows(driver, IMPACT_CYPHER, ids=ids)[0]
    assets, beds, patients = row["assets"], row["beds"], [p for p in row["patients"] if p.get("id")]
    failed_kinds = {s["kind"] for s in seeds}
    power_failure = bool(failed_kinds & POWER_KINDS)

    impacted_ids = [a["id"] for a in assets]
    for a in assets:  # how the asset is affected, from which of its direct dependencies failed
        lost = set(a.pop("lost", []) or [])
        a["mode"] = ("equipment" if a["id"] in ids else "power" if "circuit" in lost else
                     "medical_gas" if lost & {"o2_zone_valve", "air_zone_valve"} else "network")
    on_battery = [a for a in assets if a["mode"] == "power" and (a.get("battery_minutes") or 0) > 0]
    need_counter: dict[tuple, int] = {}
    for a in assets:
        # network loss degrades remote monitoring but the device keeps working at the bedside
        if a["mode"] == "network" or (a["mode"] == "power" and (a.get("battery_minutes") or 0) > 0):
            continue
        need_counter[(a["kind"], a.get("ward"))] = need_counter.get((a["kind"], a.get("ward")), 0) + 1
    needs = [{"kind": k, "ward": w, "count": c} for (k, w), c in need_counter.items()
             if k not in {"smart_bed_frame", "central_monitoring_station", "surgical_light", "or_table", "xray_room",
                          "ct_scanner", "mri_scanner"}]
    rank = {k: i for i, k in enumerate(KIND_PRIORITY)}
    needs.sort(key=lambda n: (rank.get(n["kind"], 99), -n["count"], n["ward"] or ""))
    substitutes = _rows(driver, SUBSTITUTE_CYPHER, needs=needs[:40], impacted=impacted_ids) if needs else []
    substitutes.sort(key=lambda r: (rank.get(r["kind"], 99), -r["needed"], r["ward"] or ""))

    wards: dict[str, dict] = {}
    for b in beds:
        w = wards.setdefault(b["ward"], {"ward": b["ward"], "beds": 0, "patients": 0, "high_acuity": 0})
        w["beds"] += 1
    for p in patients:
        w = wards.setdefault(p["ward"], {"ward": p["ward"], "beds": 0, "patients": 0, "high_acuity": 0})
        w["patients"] += 1
        w["high_acuity"] += int((p.get("acuity") or 0) >= 4)
    for a in assets:
        if a.get("ward"):
            wards.setdefault(a["ward"], {"ward": a["ward"], "beds": 0, "patients": 0, "high_acuity": 0})
    for w in wards.values():
        w["assets"] = sum(1 for a in assets if a.get("ward") == w["ward"])

    category = ("power" if power_failure else "medical_gas" if failed_kinds & GAS_KINDS
                else "network" if failed_kinds & NETWORK_KINDS else "equipment")
    severity = _severity(len(patients), sum(int((p.get("acuity") or 0) >= 4) for p in patients), len(beds))
    return {
        "failed": [{"id": s["id"], "name": s["name"], "kind": s["kind"]} for s in seeds],
        "category": category,
        "severity": severity,
        "modes": {m: sum(1 for a in assets if a["mode"] == m) for m in ("equipment", "power", "medical_gas", "network")},
        "counts": {"assets": len(assets), "infrastructure": len(row["infrastructure"]), "beds": len(beds),
                   "patients": len(patients), "wards": len(wards), "assets_on_battery": len(on_battery)},
        "wards": sorted(wards.values(), key=lambda w: (-w["patients"], -w["beds"], w["ward"])),
        "patients": sorted(patients, key=lambda p: (-(p.get("acuity") or 0), p["id"])),
        "beds": sorted(b["id"] for b in beds),
        "assets": sorted(assets, key=lambda a: a["id"]),
        "infrastructure": sorted(row["infrastructure"], key=lambda n: n["id"]),
        "min_battery_minutes": min((a["battery_minutes"] for a in on_battery), default=None),
        "substitutes": substitutes,
    }


def _severity(patients: int, high_acuity: int, beds: int) -> str:
    if high_acuity >= 5 or patients >= 40:
        return "critical"
    if high_acuity >= 1 or patients >= 10:
        return "high"
    if patients or beds:
        return "moderate"
    return "low"


# ---------------------------------------------------------------------------
# Assets & staff
# ---------------------------------------------------------------------------
def find_assets(driver: Driver, kind: str | None = None, ward: str | None = None, status: str | None = None,
                floor: int | None = None, pm_overdue: bool | None = None, limit: int = 50) -> dict:
    where = """WHERE ($kind IS NULL OR a.kind = $kind) AND ($ward IS NULL OR a.ward = $ward)
               AND ($status IS NULL OR a.status = $status) AND ($floor IS NULL OR a.floor = $floor)
               AND ($pm IS NULL OR a.pm_overdue = $pm)"""
    params = dict(kind=kind, ward=ward.upper() if ward else None, status=status, floor=floor, pm=pm_overdue)
    total = _rows(driver, f"MATCH (a:Asset) {where} RETURN count(a) AS n", **params)[0]["n"]
    items = _rows(driver, f"""
        MATCH (a:Asset) {where}
        OPTIONAL MATCH (a)-[:ASSIGNED_TO]->(b:Bed)
        RETURN a.id AS id, a.name AS name, a.kind AS kind, a.ward AS ward, a.floor AS floor, a.status AS status,
               a.last_pm AS last_pm, a.pm_overdue AS pm_overdue, b.id AS bed
        ORDER BY a.id LIMIT $limit""", limit=limit, **params)
    return {"total": total, "items": items}


def asset_kinds(driver: Driver) -> list[dict]:
    return _rows(driver, """MATCH (a:Asset) RETURN a.kind AS kind, count(*) AS total,
        sum(CASE WHEN a.status='available' THEN 1 ELSE 0 END) AS available,
        sum(CASE WHEN a.status='maintenance' THEN 1 ELSE 0 END) AS maintenance ORDER BY kind""")


def find_staff(driver: Driver, ward: str | None = None, role: str | None = None, cert: str | None = None,
               shift: str | None = None, on_duty: bool | None = None, limit: int = 50) -> dict:
    where = """WHERE ($ward IS NULL OR s.ward = $ward) AND ($role IS NULL OR s.role = $role)
               AND ($shift IS NULL OR s.shift = $shift) AND ($on_duty IS NULL OR s.on_duty = $on_duty)
               AND ($cert IS NULL OR EXISTS { (s)-[:HAS_CERT]->(:Certification {code: $cert}) })"""
    params = dict(ward=ward.upper() if ward else None, role=role, cert=cert.upper() if cert else None,
                  shift=shift.upper() if shift else None, on_duty=on_duty)
    total = _rows(driver, f"MATCH (s:Staff) {where} RETURN count(s) AS n", **params)[0]["n"]
    items = _rows(driver, f"""
        MATCH (s:Staff) {where}
        OPTIONAL MATCH (s)-[:HAS_CERT]->(c:Certification)
        WITH s, collect(c.code) AS certs ORDER BY s.id LIMIT $limit
        RETURN s.id AS id, s.name AS name, s.role AS role, s.ward AS ward, s.shift AS shift,
               s.on_duty AS on_duty, certs""", limit=limit, **params)
    return {"total": total, "items": items}


def get_patient(driver: Driver, patient_id: str) -> dict | None:
    rows = _rows(driver, """
        MATCH (p:Patient {id: $id})-[:OCCUPIES]->(b:Bed)-[:IN_ROOM]->(r:Room)-[:IN_WARD]->(w:Ward)
        OPTIONAL MATCH (a:Asset)-[:ASSIGNED_TO]->(b)
        RETURN p {.*} AS patient, b.id AS bed, r.id AS room, w.code AS ward, w.name AS ward_name,
               collect(a {.id, .kind, .status}) AS bedside_assets""", id=patient_id)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Floor plan
# ---------------------------------------------------------------------------
def floors(driver: Driver) -> list[dict]:
    return _rows(driver, """
        MATCH (f:Floor)
        OPTIONAL MATCH (w:Ward)-[:ON_FLOOR]->(f)
        WITH f, collect(w.code) AS wards
        OPTIONAL MATCH (b:Bed {floor: f.level, inpatient: true})
        OPTIONAL MATCH (p:Patient)-[:OCCUPIES]->(b)
        RETURN f.level AS level, f.name AS name, wards, count(DISTINCT b) AS beds, count(DISTINCT p) AS occupied
        ORDER BY level""")


def floor_plan(driver: Driver, level: int) -> dict | None:
    floor = _rows(driver, "MATCH (f:Floor {level: $l}) RETURN f.id AS id, f.name AS name, f.level AS level", l=level)
    if not floor:
        return None
    wards = _rows(driver, """
        MATCH (w:Ward)-[:ON_FLOOR]->(:Floor {level: $l})
        OPTIONAL MATCH (r:Room)-[:IN_WARD]->(w)
        OPTIONAL MATCH (b:Bed)-[:IN_ROOM]->(r)
        OPTIONAL MATCH (p:Patient)-[:OCCUPIES]->(b)
        WITH w, r, collect(CASE WHEN b IS NULL THEN null ELSE
             {id: b.id, name: b.name, isolation: b.isolation, inpatient: b.inpatient,
              patient: CASE WHEN p IS NULL THEN null ELSE
                 {id: p.id, acuity: p.acuity, age: p.age, reason: p.reason} END} END) AS beds
        ORDER BY r.id
        WITH w, collect(CASE WHEN r IS NULL THEN null ELSE {id: r.id, kind: r.kind, size: r.size, beds: beds} END)
             AS rooms
        OPTIONAL MATCH (a:Asset)-[:STATIONED_IN]->(w)
        RETURN w.id AS id, w.code AS code, w.name AS name, w.unit_type AS unit_type, w.inpatient AS inpatient,
               rooms, count(a) AS shared_assets
        ORDER BY w.code""", l=level)
    infra = _rows(driver, """
        MATCH (n:Infrastructure)-[:LOCATED_ON]->(:Floor {level: $l})
        WHERE n.kind <> 'circuit'
        RETURN n.id AS id, n.name AS name, n.kind AS kind ORDER BY n.kind, n.id""", l=level)
    circuits = _rows(driver, """
        MATCH (c:Infrastructure {kind: 'circuit'})-[:LOCATED_ON]->(:Floor {level: $l})
        RETURN count(c) AS n""", l=level)[0]["n"]
    return {**floor[0], "wards": wards, "infrastructure": infra, "circuits": circuits}
