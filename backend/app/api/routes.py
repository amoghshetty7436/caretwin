"""REST API."""
from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from neo4j import Driver
from pydantic import BaseModel, Field

from app.assistant.agent import get_assistant
from app.db.neo4j import get_driver
from app.graph import queries as q
from app.sim.engine import SimParams, Simulation

router = APIRouter(prefix="/api")


def driver() -> Driver:
    return get_driver()


# --------------------------------------------------------------------------- graph
@router.get("/summary")
def summary(d: Driver = Depends(driver)):
    return q.summary(d)


@router.get("/wards")
def wards(d: Driver = Depends(driver)):
    return q.list_wards(d)


@router.get("/wards/{code}")
def ward(code: str, d: Driver = Depends(driver)):
    res = q.ward_detail(d, code)
    if res is None:
        raise HTTPException(404, f"ward {code} not found")
    return res


@router.get("/floors")
def floors(d: Driver = Depends(driver)):
    return q.floors(d)


@router.get("/floors/{level}")
def floor_plan(level: int, d: Driver = Depends(driver)):
    res = q.floor_plan(d, level)
    if res is None:
        raise HTTPException(404, f"floor {level} not found")
    return res


@router.get("/beds/availability")
def beds(unit_type: str | None = None, d: Driver = Depends(driver)):
    return q.bed_availability(d, unit_type)


@router.get("/search")
def search(q_: str = Query(..., alias="q", min_length=1, max_length=120), label: str | None = None,
           limit: int = Query(10, le=50), d: Driver = Depends(driver)):
    return q.search(d, q_, label=label, limit=limit)


@router.get("/nodes/{node_id}")
def node(node_id: str, d: Driver = Depends(driver)):
    n = q.get_node(d, node_id)
    if n is None:
        raise HTTPException(404, f"node {node_id} not found")
    return n


@router.get("/nodes/{node_id}/neighborhood")
def neighborhood(node_id: str, limit: int = Query(150, le=500), d: Driver = Depends(driver)):
    res = q.neighborhood(d, node_id, limit)
    if not res["nodes"]:
        raise HTTPException(404, f"node {node_id} not found")
    return res


@router.get("/nodes/{node_id}/dependencies")
def dependencies(node_id: str, d: Driver = Depends(driver)):
    return q.upstream_dependencies(d, node_id)


class ImpactRequest(BaseModel):
    node_ids: list[str] = Field(..., min_length=1, max_length=20)


@router.post("/impact")
def impact(req: ImpactRequest, d: Driver = Depends(driver)):
    t0 = time.perf_counter()
    res = q.failure_impact(d, req.node_ids)
    if "error" in res:
        raise HTTPException(404, res["error"])
    res["query_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return res


@router.get("/impact/{node_id}")
def impact_one(node_id: str, d: Driver = Depends(driver)):
    return impact(ImpactRequest(node_ids=[node_id]), d)


@router.get("/infrastructure")
def infrastructure(d: Driver = Depends(driver)):
    return q._rows(d, """MATCH (n:Infrastructure) RETURN n.id AS id, n.name AS name, n.kind AS kind,
                         n.floor AS floor ORDER BY n.floor, n.kind, n.id""")


@router.get("/assets")
def assets(kind: str | None = None, ward: str | None = None, status: str | None = None, floor: int | None = None,
           pm_overdue: bool | None = None, limit: int = Query(50, le=500), d: Driver = Depends(driver)):
    return q.find_assets(d, kind, ward, status, floor, pm_overdue, limit)


@router.get("/assets/kinds")
def assets_kinds(d: Driver = Depends(driver)):
    return q.asset_kinds(d)


@router.get("/staff")
def staff(ward: str | None = None, role: str | None = None, cert: str | None = None, shift: str | None = None,
          on_duty: bool | None = None, limit: int = Query(50, le=500), d: Driver = Depends(driver)):
    return q.find_staff(d, ward, role, cert, shift, on_duty, limit)


@router.get("/patients/{patient_id}")
def patient(patient_id: str, d: Driver = Depends(driver)):
    res = q.get_patient(d, patient_id)
    if res is None:
        raise HTTPException(404, f"patient {patient_id} not in census")
    return res


# --------------------------------------------------------------------------- simulation
class SimRequest(BaseModel):
    name: str = "what-if"
    days: float = Field(14, ge=1, le=60)
    seed: int = 42
    surge_multiplier: float = Field(1.0, ge=0.2, le=3.0)
    surge_start_h: float = Field(0, ge=0)
    surge_end_h: float = Field(0, ge=0)
    failed_node_ids: list[str] = Field(default_factory=list, max_length=20)
    outage_start_h: float = Field(24, ge=0)
    outage_duration_h: float = Field(48, ge=0)
    compare_baseline: bool = True


def _run(params: SimParams) -> dict:
    sim = Simulation(params)
    return sim.run()


@router.post("/simulations")
def simulate(req: SimRequest, d: Driver = Depends(driver)):
    offline: list[str] = []
    if req.failed_node_ids:
        imp = q.failure_impact(d, req.failed_node_ids)
        if "error" in imp:
            raise HTTPException(404, imp["error"])
        offline = [b for b in imp["beds"] if not b.startswith(("BED-ED-", "BED-PACU-", "BED-OR-"))]
    warm = 7 * 24
    params = SimParams(days=req.days, seed=req.seed, surge_multiplier=req.surge_multiplier,
                       surge_start_h=warm + req.surge_start_h, surge_end_h=warm + req.surge_end_h,
                       offline_beds=offline, outage_start_h=warm + req.outage_start_h,
                       outage_end_h=warm + req.outage_start_h + req.outage_duration_h)
    scenario = _run(params)
    out = {"name": req.name, "offline_beds": len(offline), "scenario": scenario}
    if req.compare_baseline and (offline or req.surge_multiplier != 1.0):
        base = _run(SimParams(days=req.days, seed=req.seed))
        out["baseline"] = base
        out["delta"] = {
            "occupancy": round((scenario["kpis"]["occupancy"] or 0) - (base["kpis"]["occupancy"] or 0), 4),
            "ed_boarding_p90_h": round(scenario["kpis"]["ed_boarding_h"]["p90"] - base["kpis"]["ed_boarding_h"]["p90"], 2),
            "ed_boarding_mean_h": round(scenario["kpis"]["ed_boarding_h"]["mean"] - base["kpis"]["ed_boarding_h"]["mean"], 2),
            "off_service_share": round(scenario["kpis"]["off_service_share"] - base["kpis"]["off_service_share"], 4),
            "peak_boarding": scenario["kpis"]["peak_boarding"] - base["kpis"]["peak_boarding"],
        }
    out["run_id"] = _persist(req.name, params, scenario)
    return out


def _persist(name: str, params: SimParams, res: dict) -> int | None:
    try:
        from app.db.postgres import SimulationRun, get_session
        with get_session() as s:
            run = SimulationRun(name=name, params=asdict(params), kpis=res["kpis"], census=res["census"],
                                runtime_ms=res["runtime_ms"])
            s.add(run)
            s.commit()
            return run.id
    except Exception:
        return None


@router.get("/simulations")
def list_simulations(limit: int = Query(20, le=100)):
    try:
        from sqlalchemy import select

        from app.db.postgres import SimulationRun, get_session
        with get_session() as s:
            rows = s.execute(select(SimulationRun).order_by(SimulationRun.id.desc()).limit(limit)).scalars().all()
            return [{"id": r.id, "name": r.name, "created_at": r.created_at, "kpis": r.kpis,
                     "runtime_ms": r.runtime_ms} for r in rows]
    except Exception as exc:
        raise HTTPException(503, f"postgres unavailable: {exc.__class__.__name__}")


@router.get("/simulations/{run_id}")
def get_simulation(run_id: int):
    from app.db.postgres import SimulationRun, get_session
    with get_session() as s:
        r = s.get(SimulationRun, run_id)
        if r is None:
            raise HTTPException(404, "run not found")
        return {"id": r.id, "name": r.name, "params": r.params, "kpis": r.kpis, "census": r.census}


@router.get("/population")
def population():
    """Synthea population stats from Postgres."""
    try:
        from sqlalchemy import func, select

        from app.db.postgres import SyntheaEncounter, SyntheaPatient, get_session
        with get_session() as s:
            pts = s.scalar(select(func.count()).select_from(SyntheaPatient))
            alive = s.scalar(select(func.count()).select_from(SyntheaPatient).where(SyntheaPatient.deathdate.is_(None)))
            by_class = dict(s.execute(select(SyntheaEncounter.encounter_class, func.count())
                                      .group_by(SyntheaEncounter.encounter_class)).all())
            by_unit = dict(s.execute(select(SyntheaEncounter.unit_type, func.count())
                                     .where(SyntheaEncounter.unit_type.is_not(None))
                                     .group_by(SyntheaEncounter.unit_type)).all())
        return {"patients": pts, "alive": alive, "encounters": by_class, "inpatient_by_unit": by_unit}
    except Exception as exc:
        raise HTTPException(503, f"postgres unavailable: {exc.__class__.__name__}")


# --------------------------------------------------------------------------- assistant
class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)


@router.post("/assistant/ask")
def ask(req: AskRequest, d: Driver = Depends(driver)):
    assistant = get_assistant()
    try:
        res = assistant.ask(d, req.question)
    except Exception as exc:  # LLM outage -> deterministic fallback
        from app.assistant.agent import OfflinePlanner
        res = OfflinePlanner().ask(d, req.question)
        res.provider = f"offline (fallback: {exc.__class__.__name__})"
    _log(req.question, res)
    return res.to_dict()


def _log(question: str, res) -> None:
    try:
        from app.db.postgres import AssistantLog, get_session
        with get_session() as s:
            s.add(AssistantLog(question=question, answer=res.answer, provider=res.provider, tool_calls=res.tool_calls,
                               citations=res.citations, latency_ms=res.latency_ms, grounded=res.grounded))
            s.commit()
    except Exception:
        pass
