"""Deterministic generator for the CareTwin hospital graph.

Models a 300-bed acute-care hospital as a property graph:

    Hospital <-IN_HOSPITAL- Floor <-ON_FLOOR- Ward <-IN_WARD- Room <-IN_ROOM- Bed
    Staff -WORKS_IN-> Ward, Staff -HAS_CERT-> Certification, Staff -WORKS_SHIFT-> Shift
    Asset -ASSIGNED_TO-> Bed | Asset -STATIONED_IN-> Ward
    Asset/Bed/Infrastructure -DEPENDS_ON-> Infrastructure (power, medical gas, network)

Every node carries a unique ``id`` plus ``name`` and ``kind``. The generator is pure
Python (no database) so it can be unit-tested and reused by the eval harness.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Static layout. Inpatient beds sum to exactly 300.
# unit_type values match the calibration keys produced from MIMIC-IV transfers.
# ---------------------------------------------------------------------------
WARDS: list[dict] = [
    # code, name, unit_type, floor, beds, single_room_share
    {"code": "ED", "name": "Emergency Department", "unit_type": "ED", "floor": 1, "beds": 24, "inpatient": False},
    {"code": "PACU", "name": "Post-Anesthesia Care Unit", "unit_type": "PACU", "floor": 1, "beds": 12, "inpatient": False},
    {"code": "OR", "name": "Operating Suite", "unit_type": "OR", "floor": 1, "beds": 8, "inpatient": False},
    {"code": "IMG", "name": "Imaging Department", "unit_type": "IMAGING", "floor": 1, "beds": 0, "inpatient": False},
    {"code": "MICU", "name": "Medical ICU", "unit_type": "ICU", "floor": 2, "beds": 20, "inpatient": True},
    {"code": "SICU", "name": "Surgical ICU", "unit_type": "ICU", "floor": 2, "beds": 16, "inpatient": True},
    {"code": "CVICU", "name": "Cardiovascular ICU", "unit_type": "ICU", "floor": 2, "beds": 12, "inpatient": True},
    {"code": "CCU", "name": "Coronary Care Unit", "unit_type": "ICU", "floor": 2, "beds": 10, "inpatient": True},
    {"code": "SDU", "name": "Stepdown Unit", "unit_type": "STEPDOWN", "floor": 3, "beds": 24, "inpatient": True},
    {"code": "CARD", "name": "Cardiology Ward", "unit_type": "CARD", "floor": 3, "beds": 28, "inpatient": True},
    {"code": "MEDA", "name": "Medicine A", "unit_type": "MED", "floor": 4, "beds": 32, "inpatient": True},
    {"code": "NEURO", "name": "Neurology Ward", "unit_type": "NEURO", "floor": 4, "beds": 24, "inpatient": True},
    {"code": "MEDB", "name": "Medicine B", "unit_type": "MED", "floor": 5, "beds": 32, "inpatient": True},
    {"code": "ONC", "name": "Hematology/Oncology", "unit_type": "ONC", "floor": 5, "beds": 24, "inpatient": True},
    {"code": "SURG", "name": "General Surgery", "unit_type": "SURG", "floor": 6, "beds": 30, "inpatient": True},
    {"code": "ORTHO", "name": "Orthopedics & Trauma", "unit_type": "SURG", "floor": 6, "beds": 24, "inpatient": True},
    {"code": "TXP", "name": "Transplant Unit", "unit_type": "TRANSPLANT", "floor": 7, "beds": 12, "inpatient": True},
    {"code": "PSY", "name": "Behavioral Health", "unit_type": "PSYCH", "floor": 7, "beds": 12, "inpatient": True},
]

FLOORS = {0: "Basement / Plant", 1: "Level 1 - Emergency & Perioperative", 2: "Level 2 - Critical Care",
          3: "Level 3 - Cardiac & Stepdown", 4: "Level 4 - Medicine & Neurology",
          5: "Level 5 - Medicine & Oncology", 6: "Level 6 - Surgical", 7: "Level 7 - Transplant & Behavioral"}

CERTIFICATIONS = [
    ("BLS", "Basic Life Support"), ("ACLS", "Advanced Cardiovascular Life Support"),
    ("PALS", "Pediatric Advanced Life Support"), ("CCRN", "Critical Care Registered Nurse"),
    ("TNCC", "Trauma Nursing Core Course"), ("CEN", "Certified Emergency Nurse"),
    ("OCN", "Oncology Certified Nurse"), ("CNOR", "Perioperative Nurse"),
    ("VENT", "Mechanical Ventilation Competency"), ("CRRT", "Continuous Renal Replacement Therapy"),
    ("NIHSS", "NIH Stroke Scale"), ("CPI", "Crisis Prevention Intervention"),
]

SHIFTS = [("DAY", "Day shift 07:00-15:00"), ("EVE", "Evening shift 15:00-23:00"), ("NIGHT", "Night shift 23:00-07:00")]

# Bedside asset templates per unit type: (asset_type, per_bed_rate, needs_network, battery_minutes)
BEDSIDE: dict[str, list[tuple[str, float, bool, int]]] = {
    "ICU": [("patient_monitor", 1.0, True, 60), ("infusion_pump", 3.0, False, 240), ("ventilator", 0.7, False, 90),
            ("smart_bed_frame", 1.0, False, 0), ("feeding_pump", 0.5, False, 120)],
    "STEPDOWN": [("patient_monitor", 1.0, True, 60), ("infusion_pump", 2.0, False, 240), ("smart_bed_frame", 1.0, False, 0)],
    "CARD": [("telemetry_transmitter", 1.0, True, 1440), ("infusion_pump", 1.5, False, 240), ("smart_bed_frame", 1.0, False, 0)],
    "NEURO": [("telemetry_transmitter", 0.6, True, 1440), ("infusion_pump", 1.2, False, 240), ("smart_bed_frame", 1.0, False, 0),
              ("scd_pump", 0.6, False, 0)],
    "MED": [("infusion_pump", 1.2, False, 240), ("smart_bed_frame", 1.0, False, 0), ("scd_pump", 0.5, False, 0)],
    "ONC": [("infusion_pump", 2.0, False, 240), ("smart_bed_frame", 1.0, False, 0)],
    "SURG": [("infusion_pump", 1.2, False, 240), ("smart_bed_frame", 1.0, False, 0), ("scd_pump", 0.8, False, 0),
             ("pca_pump", 0.4, False, 240)],
    "TRANSPLANT": [("patient_monitor", 1.0, True, 60), ("infusion_pump", 2.5, False, 240), ("smart_bed_frame", 1.0, False, 0)],
    "PSYCH": [],
    "ED": [("patient_monitor", 1.0, True, 60), ("infusion_pump", 1.0, False, 240), ("stretcher", 1.0, False, 0)],
    "PACU": [("patient_monitor", 1.0, True, 60), ("infusion_pump", 1.0, False, 240), ("stretcher", 1.0, False, 0)],
    "OR": [("anesthesia_machine", 1.0, False, 30), ("patient_monitor", 1.0, True, 60), ("surgical_light", 2.0, False, 0),
           ("electrosurgical_unit", 1.0, False, 0), ("or_table", 1.0, False, 0), ("infusion_pump", 2.0, False, 240)],
}

# Shared (ward-level) assets: (asset_type, count_formula(beds) , battery_minutes)
SHARED: list[tuple[str, callable, int, set[str] | None]] = [
    ("crash_cart", lambda b: 1 + b // 16, 0, None),
    ("defibrillator", lambda b: 1 + b // 16, 180, None),
    ("vitals_monitor", lambda b: max(1, b // 8), 300, None),
    ("glucometer", lambda b: max(1, b // 6), 600, None),
    ("portable_suction", lambda b: max(1, b // 10), 60, None),
    ("bladder_scanner", lambda b: 1, 120, {"MED", "SURG", "NEURO", "STEPDOWN", "ONC", "CARD"}),
    ("wheelchair", lambda b: max(1, b // 6), 0, None),
    ("portable_ultrasound", lambda b: 1, 90, {"ICU", "ED"}),
    ("dialysis_machine", lambda b: max(1, b // 6), 0, {"ICU", "TRANSPLANT"}),
    ("transport_ventilator", lambda b: 1, 240, {"ICU", "ED"}),
    ("central_monitoring_station", lambda b: 1, 0, {"ICU", "STEPDOWN", "ED", "CARD", "PACU", "TRANSPLANT", "NEURO"}),
]

IMAGING_ASSETS = [("ct_scanner", 2), ("mri_scanner", 1), ("xray_room", 3), ("portable_xray", 4), ("fluoroscopy_unit", 1)]

# Staff headcount per ward = sum(rate * beds) per role, rounded; plus hospital-wide roles.
STAFF_RATES: dict[str, dict[str, float]] = {
    "ICU": {"registered_nurse": 2.3, "physician": 0.35, "resident": 0.3, "respiratory_therapist": 0.35, "patient_care_tech": 0.5},
    "STEPDOWN": {"registered_nurse": 1.5, "physician": 0.2, "resident": 0.2, "patient_care_tech": 0.5, "respiratory_therapist": 0.1},
    "CARD": {"registered_nurse": 1.1, "physician": 0.2, "resident": 0.15, "patient_care_tech": 0.45},
    "NEURO": {"registered_nurse": 1.1, "physician": 0.2, "resident": 0.15, "patient_care_tech": 0.45},
    "MED": {"registered_nurse": 1.0, "physician": 0.2, "resident": 0.2, "patient_care_tech": 0.5},
    "ONC": {"registered_nurse": 1.2, "physician": 0.2, "resident": 0.1, "patient_care_tech": 0.45},
    "SURG": {"registered_nurse": 1.0, "physician": 0.25, "resident": 0.2, "patient_care_tech": 0.5},
    "TRANSPLANT": {"registered_nurse": 1.6, "physician": 0.3, "resident": 0.15, "patient_care_tech": 0.5},
    "PSYCH": {"registered_nurse": 1.2, "physician": 0.2, "patient_care_tech": 1.0},
    "ED": {"registered_nurse": 2.2, "physician": 0.7, "resident": 0.4, "patient_care_tech": 0.6, "respiratory_therapist": 0.1},
    "PACU": {"registered_nurse": 1.6, "physician": 0.1, "patient_care_tech": 0.3},
    "OR": {"registered_nurse": 4.0, "physician": 3.0, "surgical_tech": 3.0, "anesthetist": 1.5},
    "IMAGING": {},
}
WARD_FIXED_ROLES = {"unit_clerk": 3, "charge_nurse": 3, "case_manager": 1}
HOSPITAL_ROLES = {"pharmacist": 40, "transporter": 55, "environmental_services": 130, "biomedical_engineer": 15,
                  "radiology_tech": 45, "facilities_engineer": 25, "bed_manager": 8, "dietitian": 16,
                  "physical_therapist": 35, "security_officer": 30, "lab_technologist": 60, "float_pool_nurse": 80,
                  "phlebotomist": 40, "social_worker": 14, "occupational_therapist": 12, "speech_therapist": 6,
                  "chaplain": 4, "interpreter": 10}

ROLE_CERTS = {
    "registered_nurse": ["BLS", "ACLS"], "float_pool_nurse": ["BLS", "ACLS"], "charge_nurse": ["BLS", "ACLS"], "physician": ["BLS", "ACLS"],
    "resident": ["BLS", "ACLS"], "respiratory_therapist": ["BLS", "ACLS", "VENT"], "anesthetist": ["BLS", "ACLS", "VENT"],
    "patient_care_tech": ["BLS"], "surgical_tech": ["BLS"], "transporter": ["BLS"], "radiology_tech": ["BLS"],
    "security_officer": ["CPI"],
}
UNIT_CERTS = {  # extra certifications common in a unit (probability for nurses)
    "ICU": [("CCRN", 0.6), ("VENT", 0.5), ("CRRT", 0.3)], "ED": [("CEN", 0.5), ("TNCC", 0.6), ("PALS", 0.5)],
    "ONC": [("OCN", 0.6)], "OR": [("CNOR", 0.6)], "NEURO": [("NIHSS", 0.8)], "PSYCH": [("CPI", 0.9)],
    "STEPDOWN": [("CCRN", 0.2)], "TRANSPLANT": [("CCRN", 0.3), ("CRRT", 0.3)],
}

FIRST = ["Ava", "Liam", "Noah", "Emma", "Mia", "Lucas", "Amara", "Ravi", "Sofia", "Kenji", "Zara", "Omar", "Priya",
         "Elena", "Mateo", "Chloe", "Arjun", "Leila", "Ethan", "Grace", "Hana", "Ivan", "Jade", "Kofi", "Luca", "Maya",
         "Nina", "Owen", "Quinn", "Rosa", "Sam", "Tara", "Uma", "Victor", "Wei", "Yara", "Zoe", "Ben", "Dev", "Isla"]
LAST = ["Patel", "Nguyen", "Garcia", "Smith", "Kim", "Okafor", "Silva", "Chen", "Haddad", "Ivanova", "Johnson",
        "Kowalski", "Lopez", "Muller", "Nakamura", "O'Brien", "Petrov", "Rossi", "Sato", "Tanaka", "Walker", "Young",
        "Zhang", "Ahmed", "Brown", "Costa", "Diaz", "Evans", "Fischer", "Gupta", "Hughes", "Jensen", "Shetty", "Rao"]


@dataclass
class HospitalGraph:
    nodes: list[dict] = field(default_factory=list)  # {"id", "label", "name", "kind", ...props}
    edges: list[tuple[str, str, str, dict]] = field(default_factory=list)  # (src_id, REL, dst_id, props)

    def add(self, label: str, node_id: str, name: str, kind: str | None = None, **props) -> str:
        self.nodes.append({"id": node_id, "label": label, "name": name, "kind": kind or label.lower(), **props})
        return node_id

    def rel(self, src: str, rel: str, dst: str, **props) -> None:
        if rel == "DEPENDS_ON":
            # required=False marks a redundant source: the dependent only fails when *all*
            # of its redundant sources fail (e.g. transfer switch fed by utility OR generator).
            props.setdefault("required", True)
        self.edges.append((src, rel, dst, props))

    # --- convenience views used by tests/eval -----------------------------------
    def by_id(self) -> dict[str, dict]:
        return {n["id"]: n for n in self.nodes}

    def label_counts(self) -> Counter:
        return Counter(n["label"] for n in self.nodes)


def _infra(g: HospitalGraph, rng: random.Random) -> dict:
    """Power, medical gas and network backbone. Returns lookup tables for wiring."""
    g.add("Hospital", "HOSP", "CareTwin General Hospital", beds=300, city="Boston, MA")
    for f, name in FLOORS.items():
        g.add("Floor", f"FL{f}", name, level=f)
        g.rel(f"FL{f}", "IN_HOSPITAL", "HOSP")

    infra: dict = {"panels": {}, "circuits": {}, "o2": {}, "air": {}, "switch": {}}
    g.add("Infrastructure", "UTIL-1", "Utility Feed A (13.8 kV)", kind="utility_feed", floor=0)
    for i in (1, 2):
        g.add("Infrastructure", f"GEN-{i}", f"Emergency Generator {i} (1.5 MW diesel)", kind="generator", floor=0)
        g.rel(f"GEN-{i}", "LOCATED_ON", "FL0")
    g.add("Infrastructure", "ATS-LS", "Automatic Transfer Switch - Life Safety", kind="transfer_switch", floor=0)
    g.add("Infrastructure", "ATS-CR", "Automatic Transfer Switch - Critical Branch", kind="transfer_switch", floor=0)
    for ats in ("ATS-LS", "ATS-CR"):
        g.rel(ats, "DEPENDS_ON", "UTIL-1", required=False)
        g.rel(ats, "LOCATED_ON", "FL0")
    g.rel("ATS-LS", "DEPENDS_ON", "GEN-1", required=False)
    g.rel("ATS-CR", "DEPENDS_ON", "GEN-2", required=False)
    g.rel("UTIL-1", "LOCATED_ON", "FL0")
    for i in range(1, 5):
        g.add("Infrastructure", f"UPS-{i}", f"UPS {i} (critical IT & monitoring)", kind="ups", floor=0)
        g.rel(f"UPS-{i}", "DEPENDS_ON", "ATS-CR")
        g.rel(f"UPS-{i}", "LOCATED_ON", "FL0")

    # Network core
    for i in (1, 2):
        g.add("Infrastructure", f"CORE-{i}", f"Core Network Switch {i}", kind="core_switch", floor=0)
        g.rel(f"CORE-{i}", "DEPENDS_ON", f"UPS-{i}")
        g.rel(f"CORE-{i}", "LOCATED_ON", "FL0")

    # Medical gas sources
    g.add("Infrastructure", "O2-BULK", "Bulk Liquid Oxygen Tank", kind="oxygen_source", floor=0)
    g.add("Infrastructure", "O2-MAN", "Oxygen Reserve Manifold", kind="oxygen_manifold", floor=0)
    g.rel("O2-MAN", "DEPENDS_ON", "O2-BULK")
    g.add("Infrastructure", "AIR-COMP", "Medical Air Compressor Plant", kind="medical_air_plant", floor=0)
    g.rel("AIR-COMP", "DEPENDS_ON", "ATS-LS")
    for n in ("O2-BULK", "O2-MAN", "AIR-COMP"):
        g.rel(n, "LOCATED_ON", "FL0")

    for f in range(1, 8):
        # three branch panels per floor
        for branch, src in (("N", "UTIL-1"), ("LS", "ATS-LS"), ("CR", "ATS-CR")):
            pid = f"PNL-{f}{branch}"
            label = {"N": "Normal", "LS": "Life-Safety", "CR": "Critical"}[branch]
            g.add("Infrastructure", pid, f"Floor {f} {label} Branch Panel", kind="power_panel", floor=f, branch=branch)
            # normal-branch panels have no generator backup, so they hard-depend on the utility
            g.rel(pid, "DEPENDS_ON", src)
            g.rel(pid, "LOCATED_ON", f"FL{f}")
            infra["panels"][(f, branch)] = pid
        for s in (1, 2):
            sid = f"SW-{f}{'AB'[s - 1]}"
            g.add("Infrastructure", sid, f"Floor {f} Network Switch {'AB'[s - 1]}", kind="network_switch", floor=f)
            g.rel(sid, "DEPENDS_ON", f"CORE-{s}")
            g.rel(sid, "DEPENDS_ON", f"UPS-{2 + s}")
            g.rel(sid, "LOCATED_ON", f"FL{f}")
            infra["switch"][(f, s)] = sid
    return infra


def generate_hospital(seed: int = 42) -> HospitalGraph:
    rng = random.Random(seed)
    g = HospitalGraph()
    infra = _infra(g, rng)

    for code, title in CERTIFICATIONS:
        g.add("Certification", f"CERT-{code}", title, kind="certification", code=code)
    for code, title in SHIFTS:
        g.add("Shift", f"SHIFT-{code}", title, kind="shift", code=code)

    asset_seq: Counter = Counter()
    staff_seq = 0
    today = date(2026, 11, 1)

    def new_asset(atype: str, ward: dict, battery: int, network: bool, circuit: str, switch: str) -> str:
        asset_seq[atype] += 1
        aid = f"AST-{atype.upper().replace('_', '-')}-{asset_seq[atype]:04d}"
        status = rng.choices(["in_service", "available", "maintenance"], [0.78, 0.17, 0.05])[0]
        pm = today - timedelta(days=rng.randint(5, 400))
        g.add("Asset", aid, f"{atype.replace('_', ' ').title()} #{asset_seq[atype]}", kind=atype, status=status,
              ward=ward["code"], floor=ward["floor"], battery_minutes=battery, last_pm=pm.isoformat(),
              pm_overdue=(today - pm).days > 365, manufacturer=rng.choice(["Medline", "Philips", "GE", "Baxter",
                                                                          "BD", "Draeger", "Stryker", "Hill-Rom"]))
        g.rel(aid, "DEPENDS_ON", circuit)
        if network:
            g.rel(aid, "DEPENDS_ON", switch)
        return aid

    for w in WARDS:
        wid = f"WARD-{w['code']}"
        g.add("Ward", wid, w["name"], kind="ward", code=w["code"], unit_type=w["unit_type"], floor=w["floor"],
              capacity=w["beds"], inpatient=w["inpatient"])
        g.rel(wid, "ON_FLOOR", f"FL{w['floor']}")
        f = w["floor"]
        critical = w["unit_type"] in {"ICU", "STEPDOWN", "ED", "PACU", "OR", "TRANSPLANT"}

        # medical gas zone valves (one O2 per unit with beds; med air for critical units)
        o2 = air = None
        if w["beds"]:
            o2 = g.add("Infrastructure", f"ZV-O2-{w['code']}", f"{w['name']} O2 Zone Valve", kind="o2_zone_valve", floor=f)
            g.rel(o2, "DEPENDS_ON", "O2-MAN")
            g.rel(o2, "LOCATED_ON", f"FL{f}")
            if critical:
                air = g.add("Infrastructure", f"ZV-AIR-{w['code']}", f"{w['name']} Medical Air Zone Valve",
                            kind="air_zone_valve", floor=f)
                g.rel(air, "DEPENDS_ON", "AIR-COMP")
                g.rel(air, "LOCATED_ON", f"FL{f}")

        # branch circuits: 1 per 4 beds (min 2); critical units fed from CR branch, others alternate N/LS
        n_circ = max(2, -(-w["beds"] // 4))
        circuits = []
        for c in range(1, n_circ + 1):
            branch = "CR" if critical else ("N" if c % 2 else "LS")
            cid = f"CKT-{w['code']}-{c:02d}"
            g.add("Infrastructure", cid, f"{w['name']} Circuit {c:02d}", kind="circuit", floor=f, branch=branch)
            g.rel(cid, "DEPENDS_ON", infra["panels"][(f, branch)])
            g.rel(cid, "LOCATED_ON", f"FL{f}")
            circuits.append(cid)
        switch_a, switch_b = infra["switch"][(f, 1)], infra["switch"][(f, 2)]

        # rooms + beds
        beds: list[str] = []
        single_share = 1.0 if w["unit_type"] in {"ICU", "STEPDOWN", "TRANSPLANT", "OR"} else 0.45
        if w["unit_type"] in {"ED", "PACU"}:
            single_share = 1.0
        remaining, room_no = w["beds"], 0
        while remaining > 0:
            room_no += 1
            size = 1 if (remaining == 1 or rng.random() < single_share) else 2
            rid = f"RM-{w['code']}-{f}{room_no:02d}"
            rkind = {"OR": "operating_room", "ED": "ed_bay", "PACU": "pacu_bay"}.get(w["unit_type"], "patient_room")
            g.add("Room", rid, f"{w['code']} Room {f}{room_no:02d}", kind=rkind, ward=w["code"], floor=f, size=size)
            g.rel(rid, "IN_WARD", wid)
            for b in range(size):
                bid = f"BED-{w['code']}-{f}{room_no:02d}{'AB'[b]}"
                circuit = circuits[(len(beds)) % len(circuits)]
                g.add("Bed", bid, f"{w['code']} {f}{room_no:02d}{'AB'[b]}", kind="inpatient_bed" if w["inpatient"]
                      else rkind, ward=w["code"], unit_type=w["unit_type"], floor=f, inpatient=w["inpatient"],
                      isolation=(size == 1 and rng.random() < 0.15))
                g.rel(bid, "IN_ROOM", rid)
                g.rel(bid, "DEPENDS_ON", circuit)
                if o2:
                    g.rel(bid, "DEPENDS_ON", o2)
                if air and w["unit_type"] in {"ICU", "OR"}:
                    g.rel(bid, "DEPENDS_ON", air)
                beds.append(bid)
            remaining -= size

        # bedside assets
        central_station = None
        for atype, rate, network, battery in BEDSIDE.get(w["unit_type"], []):
            count = round(rate * len(beds))
            for i in range(count):
                bed = beds[i % len(beds)]
                circuit = circuits[i % len(circuits)]
                aid = new_asset(atype, w, battery, network, circuit, switch_a if i % 2 == 0 else switch_b)
                g.rel(aid, "ASSIGNED_TO", bed)
                if atype == "ventilator" and air:
                    g.rel(aid, "DEPENDS_ON", air)
                if atype == "ventilator" and o2:
                    g.rel(aid, "DEPENDS_ON", o2)

        # shared ward assets
        for atype, formula, battery, allowed in SHARED:
            if allowed is not None and w["unit_type"] not in allowed:
                continue
            if not w["beds"]:
                continue
            for i in range(formula(w["beds"])):
                network = atype == "central_monitoring_station"
                aid = new_asset(atype, w, battery, network, circuits[i % len(circuits)], switch_a)
                g.rel(aid, "STATIONED_IN", wid)
                if atype == "central_monitoring_station":
                    central_station = aid
        # monitors/telemetry stream to the unit's central station
        if central_station:
            for n in g.nodes:
                if n["label"] == "Asset" and n.get("ward") == w["code"] and n["kind"] in {
                        "patient_monitor", "telemetry_transmitter"}:
                    g.rel(n["id"], "DEPENDS_ON", central_station)

        if w["unit_type"] == "IMAGING":
            for atype, count in IMAGING_ASSETS:
                for _ in range(count):
                    aid = new_asset(atype, w, 0, True, circuits[asset_seq[atype] % len(circuits)], switch_a)
                    g.rel(aid, "STATIONED_IN", wid)

        # staff
        roles = dict(STAFF_RATES.get(w["unit_type"], {}))
        size_basis = max(w["beds"], 6)
        counts = {r: max(1, round(rate * size_basis)) for r, rate in roles.items()}
        for r, c in WARD_FIXED_ROLES.items():
            if w["beds"]:
                counts[r] = c
        for role, c in counts.items():
            for _ in range(c):
                staff_seq += 1
                sid = f"STF-{staff_seq:05d}"
                shift = rng.choices(SHIFTS, [0.42, 0.30, 0.28])[0][0]
                g.add("Staff", sid, f"{rng.choice(FIRST)} {rng.choice(LAST)}", kind="staff", role=role, ward=w["code"],
                      shift=shift, fte=rng.choice([1.0, 1.0, 1.0, 0.9, 0.8, 0.6]),
                      on_duty=rng.random() < 0.33)
                g.rel(sid, "WORKS_IN", wid)
                g.rel(sid, "WORKS_SHIFT", f"SHIFT-{shift}")
                certs = set(ROLE_CERTS.get(role, []))
                if role in {"registered_nurse", "charge_nurse"}:
                    for cert, p in UNIT_CERTS.get(w["unit_type"], []):
                        if rng.random() < p:
                            certs.add(cert)
                for cert in sorted(certs):
                    g.rel(sid, "HAS_CERT", f"CERT-{cert}")

    # hospital-wide staff (attached to the hospital, not a ward)
    for role, c in HOSPITAL_ROLES.items():
        for _ in range(c):
            staff_seq += 1
            sid = f"STF-{staff_seq:05d}"
            shift = rng.choices(SHIFTS, [0.5, 0.3, 0.2])[0][0]
            g.add("Staff", sid, f"{rng.choice(FIRST)} {rng.choice(LAST)}", kind="staff", role=role, ward=None,
                  shift=shift, fte=1.0, on_duty=rng.random() < 0.33)
            g.rel(sid, "WORKS_SHIFT", f"SHIFT-{shift}")
            g.rel(sid, "EMPLOYED_BY", "HOSP")
            for cert in ROLE_CERTS.get(role, []):
                g.rel(sid, "HAS_CERT", f"CERT-{cert}")
    return g


if __name__ == "__main__":  # pragma: no cover
    hg = generate_hospital()
    print(len(hg.nodes), "nodes", len(hg.edges), "edges")
    for k, v in sorted(hg.label_counts().items()):
        print(f"  {k:15s} {v}")
    beds = [n for n in hg.nodes if n["label"] == "Bed" and n["inpatient"]]
    print("inpatient beds", len(beds))
