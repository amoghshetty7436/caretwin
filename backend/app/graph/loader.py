"""Load a generated HospitalGraph (and a census overlay) into Neo4j."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from neo4j import Driver

from app.graph.model import HospitalGraph

LABELS = ["Hospital", "Floor", "Ward", "Room", "Bed", "Staff", "Certification", "Shift", "Asset",
          "Infrastructure", "Patient"]


def _chunks(items: list, n: int = 2000) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def ensure_schema(driver: Driver) -> None:
    with driver.session() as s:
        s.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE")
        for label in LABELS:
            s.run(f"CREATE INDEX {label.lower()}_id IF NOT EXISTS FOR (n:{label}) ON (n.id)")
        s.run("CREATE INDEX asset_kind IF NOT EXISTS FOR (n:Asset) ON (n.kind)")
        s.run("CREATE INDEX staff_role IF NOT EXISTS FOR (n:Staff) ON (n.role)")
        s.run("CREATE FULLTEXT INDEX entity_name IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.id]")


def load_graph(driver: Driver, g: HospitalGraph, wipe: bool = True) -> dict:
    ensure_schema(driver)
    with driver.session() as s:
        if wipe:
            s.run("MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS")
        by_label: dict[str, list[dict]] = defaultdict(list)
        for n in g.nodes:
            props = {k: v for k, v in n.items() if k != "label" and v is not None}
            by_label[n["label"]].append(props)
        for label, rows in by_label.items():
            for chunk in _chunks(rows):
                s.run(f"UNWIND $rows AS r CREATE (n:Entity:{label}) SET n = r", rows=chunk)
        by_rel: dict[str, list[dict]] = defaultdict(list)
        for src, rel, dst, props in g.edges:
            by_rel[rel].append({"s": src, "d": dst, "p": props})
        for rel, rows in by_rel.items():
            for chunk in _chunks(rows, 5000):
                s.run(f"UNWIND $rows AS r MATCH (a:Entity {{id: r.s}}), (b:Entity {{id: r.d}}) "
                      f"CREATE (a)-[e:{rel}]->(b) SET e = r.p", rows=chunk)
        counts = s.run("MATCH (n) RETURN count(n) AS nodes").single()["nodes"]
        rels = s.run("MATCH ()-[r]->() RETURN count(r) AS rels").single()["rels"]
    return {"nodes": counts, "relationships": rels}


def load_census(driver: Driver, patients: list[dict]) -> int:
    """Replace the Patient overlay. Each patient dict needs id, bed_id and display props."""
    with driver.session() as s:
        s.run("MATCH (p:Patient) DETACH DELETE p")
        for chunk in _chunks(patients, 1000):
            s.run("""
                UNWIND $rows AS r
                MATCH (b:Bed {id: r.bed_id})
                CREATE (p:Entity:Patient) SET p = r
                CREATE (p)-[:OCCUPIES]->(b)
            """, rows=chunk)
        return s.run("MATCH (p:Patient) RETURN count(p) AS c").single()["c"]
