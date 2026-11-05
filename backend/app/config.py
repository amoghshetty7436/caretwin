"""Runtime configuration, read from environment variables (prefix CARETWIN_)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CARETWIN_", env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "caretwin-dev"

    database_url: str = "postgresql+psycopg://caretwin:caretwin@localhost:5432/caretwin"

    # Graph + simulation seeds keep every environment (tests, eval, demo) reproducible.
    graph_seed: int = 42
    census_seed: int = 42
    calibration_path: Path = REPO_ROOT / "data" / "calibration" / "mimic_demo_calibration.json"
    synthea_pool_path: Path = REPO_ROOT / "data" / "calibration" / "synthea_admissions.csv.gz"

    # LLM assistant. provider: offline | openai_compat
    # openai_compat works with Groq, Gemini (OpenAI endpoint), Ollama, OpenRouter, OpenAI...
    llm_provider: str = "offline"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    llm_timeout_s: float = 30.0
    llm_max_tool_rounds: int = 8

    cors_origins: str = "http://localhost:5173,http://localhost:8080"


@lru_cache
def get_settings() -> Settings:
    return Settings()
