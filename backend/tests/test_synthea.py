import pytest

from app.sim.synthea import build_pool, map_unit
from tests.conftest import FIXTURES


@pytest.mark.parametrize("desc,reason,unit", [
    ("Admission to intensive care unit (procedure)", None, "ICU"),
    ("Encounter for problem (procedure)", "Sepsis (disorder)", "ICU"),
    ("Admission to surgical transplant department (procedure)", "Awaiting transplantation of kidney", "TRANSPLANT"),
    ("Encounter for problem (procedure)", "Non-small cell carcinoma of lung  TNM stage 1 (disorder)", "ONC"),
    ("Hospital admission (procedure)", "Chronic congestive heart failure (disorder)", "CARD"),
    ("Hospital admission (procedure)", "Seizure disorder (disorder)", "NEURO"),
    ("Admission to surgical department (procedure)", "Appendicitis (disorder)", "SURG"),
    ("Drug rehabilitation and detoxification (regime/therapy)", "Dependent drug abuse (disorder)", "PSYCH"),
    ("Hospital admission (procedure)", "Pulmonary emphysema (disorder)", "MED"),
    (None, None, "MED"),
])
def test_map_unit(desc, reason, unit):
    assert map_unit(desc, reason) == unit


@pytest.fixture(scope="module")
def pool():
    return build_pool(FIXTURES / "synthea")


def test_pool_shapes(pool):
    patients, enc, admissions = pool
    assert len(patients) == 40
    assert set(enc.ENCOUNTERCLASS) <= {"inpatient", "emergency"}
    assert len(admissions) == (enc.ENCOUNTERCLASS == "inpatient").sum()


def test_pool_columns_and_values(pool):
    _, _, adm = pool
    assert {"encounter_id", "patient_id", "age", "sex", "unit_type", "pathway", "reason"} <= set(adm.columns)
    assert adm.age.between(0, 120).all()
    assert set(adm.pathway) <= {"ed", "elective", "direct"}
    assert not adm.reason.str.contains(r"\(disorder\)").any()


def test_ed_linkage_marks_pathway(pool):
    _, enc, adm = pool
    ip = enc[enc.ENCOUNTERCLASS == "inpatient"]
    assert (adm.pathway == "ed").sum() <= len(ip)
