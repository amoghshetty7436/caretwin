"""Build the twin: hospital graph -> Neo4j, simulated census -> Neo4j, run record -> Postgres.

    python -m app.seed            # idempotent full rebuild
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict

from app.config import get_settings
from app.db.neo4j import get_driver
from app.graph.loader import load_census, load_graph
from app.graph.model import generate_hospital
from app.sim.engine import SimParams, Simulation

log = logging.getLogger("caretwin.seed")


def save_run(name: str, params: SimParams, result: dict) -> int | None:
    try:
        from app.db.postgres import SimulationRun, get_session, init_db
        init_db()
        with get_session() as s:
            run = SimulationRun(name=name, params=asdict(params), kpis=result["kpis"], census=result["census"],
                                runtime_ms=result["runtime_ms"])
            s.add(run)
            s.commit()
            return run.id
    except Exception as exc:  # Postgres is optional for the graph-only demo
        log.warning("could not persist simulation run: %s", exc)
        return None


def seed(census_days: float = 28.0) -> dict:
    s = get_settings()
    t0 = time.perf_counter()
    driver = get_driver()
    graph_stats = load_graph(driver, generate_hospital(s.graph_seed))
    params = SimParams(days=census_days, seed=s.census_seed)
    sim = Simulation(params)
    result = sim.run()
    patients = sim.current_patients()
    n = load_census(driver, patients)
    run_id = save_run("census-baseline", params, result)
    return {"graph": graph_stats, "census_patients": n, "simulation_run_id": run_id, "kpis": result["kpis"],
            "seconds": round(time.perf_counter() - t0, 2)}


def is_seeded() -> bool:
    try:
        rec = get_driver().execute_query("MATCH (h:Hospital) RETURN count(h) AS c").records
        return bool(rec and rec[0]["c"])
    except Exception:
        return False


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(seed(), indent=2, default=str))
