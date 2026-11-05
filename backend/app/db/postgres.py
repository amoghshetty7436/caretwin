"""PostgreSQL schema (SQLAlchemy 2.0). Holds the Synthea population, simulation runs and assistant logs."""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class SyntheaPatient(Base):
    __tablename__ = "synthea_patients"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    birthdate: Mapped[str] = mapped_column(String(10))
    deathdate: Mapped[str | None] = mapped_column(String(10), nullable=True)
    gender: Mapped[str] = mapped_column(String(2))
    race: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ethnicity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    county: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SyntheaEncounter(Base):
    __tablename__ = "synthea_encounters"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("synthea_patients.id"), index=True)
    encounter_class: Mapped[str] = mapped_column(String(16), index=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stop: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_type: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    pathway: Mapped[str | None] = mapped_column(String(16), nullable=True)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    name: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON)
    kpis: Mapped[dict] = mapped_column(JSON)
    census: Mapped[list] = mapped_column(JSON)  # hourly census per unit type
    runtime_ms: Mapped[float] = mapped_column(Float)


class AssistantLog(Base):
    __tablename__ = "assistant_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32))
    tool_calls: Mapped[list] = mapped_column(JSON)
    citations: Mapped[list] = mapped_column(JSON)
    latency_ms: Mapped[float] = mapped_column(Float)
    grounded: Mapped[bool] = mapped_column(Boolean, default=True)


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_size=20, max_overflow=20, pool_pre_ping=True)


@lru_cache
def _session_factory():
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Session:
    return _session_factory()()


def init_db() -> None:
    Base.metadata.create_all(get_engine())
