"""Runtime configuration for Conduit."""

from functools import cached_property
from functools import lru_cache
import os

from pydantic import AliasChoices
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from conduit.tool_permissions import load_tool_permissions


class Settings(BaseSettings):
    """Application settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CONDUIT_",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "conduit"
    model: str = "claude-sonnet-4-6"
    models_config_path: str = "config/models.yaml"
    host: str = "0.0.0.0"
    port: int = 18423
    db_path: str = "data/conduit.db"
    tool_permissions_path: str = "config/tools.yaml"
    internal_user_id: str = "single-user"
    anthropic_max_tokens: int = 8192
    anthropic_thinking_budget_tokens: int = 2048
    anthropic_interleaved_thinking: bool = True
    search_timeout_seconds: float = 15.0
    search_max_results: int = 5
    fetch_timeout_seconds: float = 15.0
    fetch_max_chars: int = 12_000
    fetch_user_agent: str = "Conduit/0.1"
    polymarket_timeout_seconds: float = 15.0
    google_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        repr=False,
    )
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        repr=False,
    )
    brave_api_key: str | None = Field(
        default=None,
        validation_alias="BRAVE_API_KEY",
        repr=False,
    )

    @cached_property
    def tool_permissions(self) -> dict[str, str]:
        """Return the resolved per-tool permission policy."""

        return load_tool_permissions(self.tool_permissions_path)

    def provider_api_key_configured_for(self, provider: str) -> bool:
        """Return whether a given provider has credentials configured."""

        if provider == "anthropic":
            return bool(self.anthropic_api_key)
        if provider == "google":
            return bool(self.google_api_key)
        return False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance."""

    settings = Settings()

    # ADK providers read credentials from process environment variables, so
    # mirror values loaded from `.env` into the runtime environment.
    if settings.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)

    return settings
