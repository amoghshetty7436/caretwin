"""Neo4j driver lifecycle."""
from __future__ import annotations

from functools import lru_cache

from neo4j import Driver, GraphDatabase

from app.config import get_settings


@lru_cache
def get_driver() -> Driver:
    s = get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password),
                                max_connection_pool_size=100, connection_acquisition_timeout=10,
                                notifications_min_severity="OFF")


def close_driver() -> None:
    if get_driver.cache_info().currsize:
        get_driver().close()
        get_driver.cache_clear()
