import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Anthropic
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-6"

    # Simulator
    simulator_base_url: str = "https://simulator-75lk3meynq-el.a.run.app"
    candidate_token: str = ""

    # Database path (relative to project root)
    database_path: str = "app.db"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Agent
    max_turns: int = 8
    request_timeout_seconds: int = 60

    @property
    def db_path(self) -> Path:
        """Resolve database path relative to project root."""
        path = Path(self.database_path)
        if not path.is_absolute():
            # Find project root by walking up from this file
            project_root = Path(__file__).parent.parent.parent
            path = project_root / path
        return path

    @property
    def policy_doc_path(self) -> Path:
        project_root = Path(__file__).parent.parent.parent
        return project_root / "policy_and_faq.md"


@lru_cache
def get_settings() -> Settings:
    return Settings()
