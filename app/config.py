from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/support_reply"
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: Literal["gemini", "anthropic"] = "gemini"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-3-5-haiku-20241022"
    llm_rate_limit_per_minute: int = 60
    llm_max_retries: int = 5

    helpdesk_timeout_seconds: float = 10.0
    helpdesk_max_retries: int = 5

    @field_validator("database_url")
    @classmethod
    def _asyncpg_scheme(cls, v: str) -> str:
        # Heroku's Postgres addon sets DATABASE_URL with the plain "postgres://"
        # scheme, which the asyncpg driver doesn't recognize.
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            return "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v


settings = Settings()
