"""Settings from env / .env (repo root or backend/)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    anthropic_api_key: str = ""
    mongodb_uri: str = ""  # empty = in-memory store
    mongodb_db: str = "lectureloop"
    model_smart: str = "claude-sonnet-5-5"
    model_fast: str = "claude-haiku-5-5"
    demo_mode: str = "true"  # true | false | cached
    cors_origins: str = "http://localhost:3000"
    llm_timeout_s: float = 20.0
    llm_provider: str = "anthropic"  # anthropic | fake (scripted offline model for tests/UI work)
    demo_transcript: str = "../demo/lecture_transcript.json"
    min_concept_s: float = 45.0  # segmenter guard: minimum seconds per concept


@lru_cache
def get_settings() -> Settings:
    return Settings()
