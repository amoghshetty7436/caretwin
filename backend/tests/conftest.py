from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("CARETWIN_AUTOSEED", "0")
os.environ.setdefault("CARETWIN_LLM_PROVIDER", "offline")

FIXTURES = Path(__file__).parent / "fixtures"


def _neo4j_up() -> bool:
    try:
        from app.db.neo4j import get_driver
        get_driver().verify_connectivity()
        return True
    except Exception:
        return False


def _pg_up() -> bool:
    try:
        from sqlalchemy import text

        from app.db.postgres import get_engine
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


NEO4J = _neo4j_up()
PG = _pg_up()
needs_neo4j = pytest.mark.skipif(not NEO4J, reason="Neo4j not reachable")
needs_pg = pytest.mark.skipif(not PG, reason="Postgres not reachable")


@pytest.fixture(scope="session")
def hospital():
    from app.graph.model import generate_hospital
    return generate_hospital(42)


@pytest.fixture(scope="session")
def seeded():
    """Seed Neo4j (and Postgres if up) once per test session with the default seeds."""
    if not NEO4J:
        pytest.skip("Neo4j not reachable")
    from app.db.neo4j import get_driver
    from app.seed import seed
    info = seed()  # same defaults as the demo + eval gold
    return {"driver": get_driver(), "info": info}


@pytest.fixture(scope="session")
def client(seeded):
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c
