"""Build the 50-question assistant eval set with gold answers.

Gold values come from hand-written Cypher in THIS file, deliberately independent of
backend/app/graph/queries.py (the code under test). Each question lists the key facts
an answer must contain. Run against a freshly seeded twin (python -m app.seed) so the
census matches CARETWIN_CENSUS_SEED.

    python eval/build_gold.py        # writes eval/questions.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.neo4j import get_driver  # noqa: E402


def one(cypher: str, **p):
    rec = get_driver().execute_query(cypher, p).records
    return rec[0][0] if rec else None


def free_beds(ward: str) -> int:
    return one("""MATCH (b:Bed {ward: $w}) WHERE NOT (b)<-[:OCCUPIES]-() RETURN count(b)""", w=ward)


def assets(where: str, **p) -> int:
    return one(f"MATCH (a:Asset) WHERE {where} RETURN count(a)", **p)


def staff(where: str, **p) -> int:
    return one(f"MATCH (s:Staff) WHERE {where} RETURN count(s)", **p)


def impact(ids: list[str]) -> tuple[int, int]:
    """(beds, patients) under the same redundancy semantics, written independently."""
    deps = one("""
        MATCH (r:Entity) WHERE r.id IN $ids
        OPTIONAL MATCH (x)-[:DEPENDS_ON {required:false}]->(r)
        WITH collect(DISTINCT r) AS rs, collect(DISTINCT x) AS xs
        WITH rs + [x IN xs WHERE ALL(t IN [(x)-[:DEPENDS_ON {required:false}]->(t) | t] WHERE t.id IN $ids)] AS roots
        UNWIND roots AS root
        OPTIONAL MATCH (d)-[:DEPENDS_ON*1..10 {required:true}]->(root)
        WITH roots, collect(DISTINCT d) AS ds
        RETURN [n IN roots + ds | n.id]""", ids=ids)
    beds = one("""
        MATCH (b:Bed) WHERE b.id IN $deps OR EXISTS { (a:Asset)-[:ASSIGNED_TO]->(b) WHERE a.id IN $deps }
        RETURN collect(b.id)""", deps=deps)
    pts = one("MATCH (p:Patient)-[:OCCUPIES]->(b:Bed) WHERE b.id IN $beds RETURN count(p)", beds=beds)
    return len(beds), pts


def main() -> None:
    Q: list[dict] = []

    def add(cat: str, question: str, expected: list, any_of: list | None = None, note: str = ""):
        Q.append({"id": f"q{len(Q) + 1:02d}", "category": cat, "question": question,
                  "expected": [str(e) for e in expected], "any_of": [str(a) for a in (any_of or [])], "note": note})

    occ = one("MATCH (p:Patient)-[:OCCUPIES]->(:Bed {inpatient:true}) RETURN count(p)")
    tot = one("MATCH (b:Bed {inpatient:true}) RETURN count(b)")
    # ---- capacity (10)
    add("capacity", "How many inpatient beds are occupied right now across the hospital?", [occ])
    add("capacity", "How many free beds does the Medical ICU have?", [free_beds("MICU")])
    add("capacity", "Any open beds in Medicine A?", [free_beds("MEDA")])
    add("capacity", "How many beds are available in Hematology/Oncology?", [free_beds("ONC")])
    icu_free = sum(free_beds(w) for w in ("MICU", "SICU", "CVICU", "CCU"))
    add("capacity", "How many ICU beds are free in total?", [icu_free])
    add("capacity", "What's the current occupancy of the stepdown unit — how many beds are taken?",
        [one("MATCH (p:Patient)-[:OCCUPIES]->(:Bed {ward:'SDU'}) RETURN count(p)")])
    wards = get_driver().execute_query("""
        MATCH (w:Ward {inpatient:true}) MATCH (b:Bed {ward:w.code})
        OPTIONAL MATCH (p:Patient)-[:OCCUPIES]->(b)
        WITH w, toFloat(count(p))/count(b) AS occ RETURN w.code AS code, occ ORDER BY occ DESC""").records
    top = max(r["occ"] for r in wards)
    add("capacity", "Which ward has the highest occupancy?", [], any_of=[f"WARD-{r['code']}" for r in wards
                                                                          if r["occ"] == top])
    low = min(r["occ"] for r in wards)
    add("capacity", "Which inpatient ward has the lowest occupancy?", [], any_of=[f"WARD-{r['code']}" for r in wards
                                                                                   if r["occ"] == low])
    add("capacity", "How many free beds are there in the Neurology ward?", [free_beds("NEURO")])
    add("capacity", "What is the overall hospital occupancy? Give the number of free inpatient beds.", [tot - occ])

    # ---- assets (10)
    add("assets", "How many ventilators are available hospital-wide?",
        [assets("a.kind='ventilator' AND a.status='available'")])
    add("assets", "How many infusion pumps does the MICU have?", [assets("a.kind='infusion_pump' AND a.ward='MICU'")])
    add("assets", "How many defibrillators are currently in maintenance?",
        [assets("a.kind='defibrillator' AND a.status='maintenance'")])
    add("assets", "Count the dialysis machines in the Surgical ICU.",
        [assets("a.kind='dialysis_machine' AND a.ward='SICU'")])
    add("assets", "How many CT scanners do we have?", [assets("a.kind='ct_scanner'")])
    add("assets", "How many infusion pumps are overdue for preventive maintenance?",
        [assets("a.kind='infusion_pump' AND a.pm_overdue")])
    add("assets", "How many available patient monitors are on floor 2?",
        [assets("a.kind='patient_monitor' AND a.status='available' AND a.floor=2")])
    add("assets", "How many wheelchairs are stationed in the emergency department?",
        [assets("a.kind='wheelchair' AND a.ward='ED'")])
    add("assets", "How many crash carts does Medicine B have?", [assets("a.kind='crash_cart' AND a.ward='MEDB'")])
    add("assets", "How many telemetry transmitters are on the cardiology ward?",
        [assets("a.kind='telemetry_transmitter' AND a.ward='CARD'")])

    # ---- staff (10)
    rn = "s.role='registered_nurse'"
    cert = "EXISTS { (s)-[:HAS_CERT]->(:Certification {code:$c}) }"
    add("staff", "How many registered nurses are on duty in the MICU right now?",
        [staff(f"{rn} AND s.ward='MICU' AND s.on_duty")])
    add("staff", "How many CCRN-certified nurses work in the Surgical ICU?",
        [staff(f"{rn} AND s.ward='SICU' AND {cert}", c="CCRN")])
    add("staff", "How many respiratory therapists hold the VENT competency hospital-wide?",
        [staff(f"s.role='respiratory_therapist' AND {cert}", c="VENT")])
    add("staff", "How many ACLS-certified nurses are on duty in the ED?",
        [staff(f"{rn} AND s.ward='ED' AND s.on_duty AND {cert}", c="ACLS")])
    add("staff", "How many night shift nurses are assigned to Medicine A?",
        [staff(f"{rn} AND s.ward='MEDA' AND s.shift='NIGHT'")])
    add("staff", "How many physicians work in the emergency department?", [staff("s.role='physician' AND s.ward='ED'")])
    add("staff", "How many charge nurses does oncology have?", [staff("s.role='charge_nurse' AND s.ward='ONC'")])
    add("staff", "How many pharmacists does the hospital employ?", [staff("s.role='pharmacist'")])
    add("staff", "How many ED nurses have TNCC?", [staff(f"{rn} AND s.ward='ED' AND {cert}", c="TNCC")])
    add("staff", "How many CVICU nurses are CRRT certified?", [staff(f"{rn} AND s.ward='CVICU' AND {cert}", c="CRRT")])

    # ---- failure impact (15): expected = beds and patients affected
    cases = [
        ("What happens if the MICU oxygen zone valve fails?", ["ZV-O2-MICU"]),
        ("If the floor 2 critical branch panel trips, how many patients are affected?", ["PNL-2CR"]),
        ("What if generator 2 goes down?", ["GEN-2"]),
        ("What happens if we lose utility power and generator 2 also fails?", ["UTIL-1", "GEN-2"]),
        ("Impact of circuit 1 in the SICU failing?", ["CKT-SICU-01"]),
        ("What is affected if the floor 3 network switch A goes offline?", ["SW-3A"]),
        ("What happens if the critical branch transfer switch fails?", ["ATS-CR"]),
        ("What if ventilator #3 breaks?", ["AST-VENTILATOR-0003"]),
        ("What happens if the oxygen manifold fails?", ["O2-MAN"]),
        ("What is the impact of a UPS 3 failure?", ["UPS-3"]),
        ("If the floor 6 normal panel loses power, what is impacted?", ["PNL-6N"]),
        ("What happens if core switch 1 fails?", ["CORE-1"]),
        ("What if the medical air compressor plant fails?", ["AIR-COMP"]),
        ("Impact if the life safety transfer switch fails?", ["ATS-LS"]),
        ("What happens if CKT-CARD-03 trips?", ["CKT-CARD-03"]),
    ]
    for question, ids in cases:
        b, p = impact(ids)
        if b:
            add("impact", question, [b, p], note=f"failed={ids}: beds={b} patients={p}")
        else:
            add("impact", question, [], any_of=["no downstream impact", "no impact", "not affected", "0 beds",
                                                "redundant"], note=f"failed={ids}: no impact")

    # ---- dependencies (3)
    add("dependency", "What does AST-VENTILATOR-0003 depend on?", ["CKT-MICU-03", "ZV-O2-MICU"])
    add("dependency", "What powers the floor 4 critical panel? What is it fed by?", ["ATS-CR"])
    bed = one("MATCH (b:Bed {ward:'MICU'}) RETURN b.id ORDER BY b.id LIMIT 1")
    circ = one("MATCH (:Bed {id:$b})-[:DEPENDS_ON]->(c:Infrastructure {kind:'circuit'}) RETURN c.id", b=bed)
    add("dependency", f"What does bed {bed} depend on?", [circ, "ZV-O2-MICU"])

    # ---- patient lookup (2)
    pts = get_driver().execute_query("""MATCH (p:Patient)-[:OCCUPIES]->(b:Bed) RETURN p.id AS p, b.id AS b, b.ward AS w
        ORDER BY p.id SKIP 5 LIMIT 2""").records
    for r in pts:
        add("patient", f"Where is patient {r['p']} right now?", [r["b"], f"WARD-{r['w']}"])

    assert len(Q) == 50, len(Q)
    fingerprint = {"census_patients": occ, "nodes": one("MATCH (n) RETURN count(n)")}
    out = ROOT / "eval" / "questions.jsonl"
    with out.open("w") as f:
        f.write(json.dumps({"_meta": fingerprint}) + "\n")
        for q in Q:
            f.write(json.dumps(q) + "\n")
    print(f"wrote {len(Q)} questions -> {out} {fingerprint}")




def build_heldout() -> None:
    base = {json.loads(line).get("id"): json.loads(line)
            for line in (ROOT / "eval/questions.jsonl").read_text().splitlines()[1:]}
    meta = (ROOT / "eval/questions.jsonl").read_text().splitlines()[0]
    para = json.loads((ROOT / "eval/heldout_paraphrases.json").read_text())
    with (ROOT / "eval/questions_heldout.jsonl").open("w") as f:
        f.write(meta + "\n")
        for qid, text in para.items():
            if qid.startswith("_"):
                continue
            q = dict(base[qid], id=f"h-{qid}", question=text)
            f.write(json.dumps(q) + "\n")
    print("wrote held-out paraphrase set")


if __name__ == "__main__":
    main()
    build_heldout()
