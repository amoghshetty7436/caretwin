"""Turn Synthea CSV exports into a case-mix pool for the simulator.

Synthea gives us *who* arrives (age, sex, presenting problem, admission type); MIMIC-IV
gives us *how they flow* (ED boarding, unit stays, transfers). Synthea's own timestamps
are not used for timing: its encounter durations are templated (e.g. most ED visits are
exactly 1 hour) and its hour-of-day profile is flat.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

ELECTIVE_HINTS = ("surgical department", "non-urgent", "sterilization", "transplant", "elective")

UNIT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("ICU", ("intensive care", "sepsis", "respiratory failure", "cardiac arrest", "shock")),
    ("TRANSPLANT", ("transplant",)),
    ("PSYCH", ("drug abuse", "detoxification", "rehabilitation", "overdose", "psych", "suicid")),
    ("ONC", ("carcinoma", "neoplasm", "leukemia", "myeloma", "lymphoma", "malignant")),
    ("CARD", ("heart", "myocardial", "coronary", "aortic valve", "preinfarction", "atrial", "cardiac")),
    ("NEURO", ("seizure", "migraine", "neuropathy", "stroke", "concussion", "meningomyelocele", "epilep")),
    ("SURG", ("surgical", "orthopedic", "appendicitis", "sterilization", "fracture", "tendon", "ligament",
              "tubal pregnancy", "artificial joint", "rotator cuff", "laceration")),
    ("MED", ()),
]


def map_unit(description: str | float, reason: str | float) -> str:
    text = f"{description if isinstance(description, str) else ''} {reason if isinstance(reason, str) else ''}".lower()
    for unit, keys in UNIT_RULES:
        if any(k in text for k in keys):
            return unit
    return "MED"


def build_pool(csv_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (patients, encounters[inpatient+emergency], admissions pool)."""
    patients = pd.read_csv(csv_dir / "patients.csv", usecols=["Id", "BIRTHDATE", "DEATHDATE", "GENDER", "RACE",
                                                              "ETHNICITY", "CITY", "COUNTY"])
    enc = pd.read_csv(csv_dir / "encounters.csv", usecols=["Id", "START", "STOP", "PATIENT", "ENCOUNTERCLASS",
                                                           "DESCRIPTION", "REASONDESCRIPTION"])
    enc = enc[enc.ENCOUNTERCLASS.isin(["inpatient", "emergency"])].copy()
    enc["START"] = pd.to_datetime(enc.START, utc=True)
    enc["STOP"] = pd.to_datetime(enc.STOP, utc=True)

    ip = enc[enc.ENCOUNTERCLASS == "inpatient"].copy()
    em = enc[enc.ENCOUNTERCLASS == "emergency"][["PATIENT", "START", "REASONDESCRIPTION"]].rename(
        columns={"START": "START_ed", "REASONDESCRIPTION": "REASONDESCRIPTION_ed"})
    linked = ip[["Id", "PATIENT", "START"]].merge(em, on="PATIENT")
    dt_h = (linked.START - linked.START_ed).dt.total_seconds() / 3600
    linked = linked[(dt_h >= 0) & (dt_h <= 24)]
    via_ed = set(linked.Id)
    ed_reason = linked.drop_duplicates("Id").set_index("Id").REASONDESCRIPTION_ed

    ip["unit_type"] = [map_unit(d, r if isinstance(r, str) else ed_reason.get(i))
                       for i, d, r in zip(ip.Id, ip.DESCRIPTION, ip.REASONDESCRIPTION)]
    desc = ip.DESCRIPTION.fillna("").str.lower() + " " + ip.REASONDESCRIPTION.fillna("").str.lower()
    ip["pathway"] = ["ed" if i in via_ed else ("elective" if any(h in t for h in ELECTIVE_HINTS) else "direct")
                     for i, t in zip(ip.Id, desc)]
    enc = enc.merge(ip[["Id", "unit_type", "pathway"]], on="Id", how="left")

    pts = patients.set_index("Id")
    birth = pd.to_datetime(pts.BIRTHDATE.reindex(ip.PATIENT), utc=True).reset_index(drop=True)
    ages = ((ip.START.reset_index(drop=True) - birth).dt.days / 365.25).astype(int).to_numpy()
    pool = pd.DataFrame({
        "encounter_id": ip.Id.to_numpy(), "patient_id": ip.PATIENT.to_numpy(), "age": ages,
        "sex": pts.GENDER.reindex(ip.PATIENT).to_numpy(), "unit_type": ip.unit_type.to_numpy(),
        "pathway": ip.pathway.to_numpy(),
        "reason": ip.REASONDESCRIPTION.fillna(ip.DESCRIPTION).str.replace(r"\s*\((disorder|finding|situation|procedure|"
                                                                          r"morphologic abnormality)\)", "", regex=True)
        .str.replace(r"\s+", " ", regex=True).to_numpy(),
    })
    return patients, enc, pool


def ingest_postgres(patients: pd.DataFrame, enc: pd.DataFrame) -> dict:
    from sqlalchemy import delete, insert

    from app.db.postgres import SyntheaEncounter, SyntheaPatient, get_session, init_db
    init_db()

    def nz(v):
        return None if pd.isna(v) else v

    with get_session() as s:
        s.execute(delete(SyntheaEncounter))
        s.execute(delete(SyntheaPatient))
        prow = [{"id": r.Id, "birthdate": r.BIRTHDATE, "deathdate": nz(r.DEATHDATE), "gender": r.GENDER,
                 "race": nz(r.RACE), "ethnicity": nz(r.ETHNICITY), "city": nz(r.CITY), "county": nz(r.COUNTY)}
                for r in patients.itertuples()]
        for i in range(0, len(prow), 5000):
            s.execute(insert(SyntheaPatient), prow[i:i + 5000])
        erow = [{"id": r.Id, "patient_id": r.PATIENT, "encounter_class": r.ENCOUNTERCLASS,
                 "start": r.START.to_pydatetime(), "stop": None if pd.isna(r.STOP) else r.STOP.to_pydatetime(),
                 "description": nz(r.DESCRIPTION), "reason": nz(r.REASONDESCRIPTION), "unit_type": nz(r.unit_type),
                 "pathway": nz(r.pathway)} for r in enc.itertuples()]
        for i in range(0, len(erow), 5000):
            s.execute(insert(SyntheaEncounter), erow[i:i + 5000])
        s.commit()
    return {"patients": len(prow), "encounters": len(erow), "ingested_at": datetime.utcnow().isoformat()}


def load_pool(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)
