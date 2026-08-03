from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------------------------------------------------------------------------
    # App
    # ---------------------------------------------------------------------------
    APP_ENV: Literal["local", "staging", "production"] = "local"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["console", "json"] = "console"
    LOG_FILE: str = "./logs/agent.jsonl"
    MAX_QUERY_LENGTH: int = Field(default=4000, ge=1)
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=1)

    # ---------------------------------------------------------------------------
    # Database
    # ---------------------------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/rag_db",
        description="Async PostgreSQL connection URL. Must use +asyncpg driver.",
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if "+asyncpg" not in v:
            raise ValueError(
                "DATABASE_URL must use the asyncpg driver. "
                "Example: postgresql+asyncpg://user:pass@host:5432/db"
            )
        return v

    # ---------------------------------------------------------------------------
    # LLM (Responses API vs Chat Completions API — POST {LLM_BASE_URL}/...)
    # ---------------------------------------------------------------------------
    LLM_API_STYLE: Literal["responses", "chat_completions"] = "responses"
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_API_KEY: str = "sk-xxx"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = Field(default=0.2, ge=0.0, le=2.0)
    LLM_MAX_OUTPUT_TOKENS: int = Field(default=2048, ge=1)
    LLM_TIMEOUT_S: float = Field(default=60.0, gt=0)
    LLM_STORE: bool = True
    LLM_REASONING_EFFORT: str | None = None

    @field_validator("LLM_REASONING_EFFORT", mode="before")
    @classmethod
    def coerce_empty_to_none(cls, v: object) -> object:
        """Treat an empty string (from .env blank value) as None."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    # ---------------------------------------------------------------------------
    # Agent loop
    # ---------------------------------------------------------------------------
    AGENT_MAX_ITERATIONS: int = Field(default=8, ge=1, le=50)
    AGENT_RUN_TIMEOUT_S: float = Field(default=180.0, gt=0)
    AGENT_CREDITS_BUDGET: int = Field(default=100, ge=0)
    AGENT_PARALLEL_TOOLS: bool = True
    TOOL_RESULT_PASSTHROUGH: bool = True
    TOOLS_DISABLED: list[str] = Field(default_factory=list)

    @field_validator("TOOLS_DISABLED", mode="before")
    @classmethod
    def parse_tools_disabled(cls, v: object) -> list[str]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(x).strip() for x in v if str(x).strip()]
        return v


    # ---------------------------------------------------------------------------
    # Verosight
    # ---------------------------------------------------------------------------
    VEROSIGHT_API_BASE: str = "https://api.verosight.com"
    VEROSIGHT_API_KEY: str = "vlt_live_xxx"
    VEROSIGHT_MCP_SSE_URL: str = "https://api.verosight.com/v1/mcp/sse"
    VEROSIGHT_ENABLED: bool = True
    VEROSIGHT_MODE: Literal["rest", "mcp", "both"] = "mcp"
    MCP_SERVERS_JSON: str = ""

    @model_validator(mode="after")
    def validate_no_placeholder_secrets(self) -> Settings:
        """Reject placeholder secrets in non-local environments.

        In local mode the placeholders are accepted so the server can start
        without keys (but any real LLM/Verosight call will fail with a clear
        ErrorReport — see app/schemas/errors.py).  In staging/production the
        placeholders are rejected at startup.
        """
        if self.APP_ENV != "local":
            if self.LLM_API_KEY == "sk-xxx":
                raise ValueError("LLM_API_KEY must be set in non-local environments.")
            if self.VEROSIGHT_API_KEY == "vlt_live_xxx":
                raise ValueError(
                    "VEROSIGHT_API_KEY must be set in non-local environments."
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance (loaded once at startup)."""
    return Settings()
