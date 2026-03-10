"""ADK Web entrypoint for Conduit."""

from conduit.agent import build_root_agent
from conduit.config import get_settings
from conduit.model_registry import load_model_registry


settings = get_settings()
model_registry = load_model_registry(
    settings.models_config_path,
    fallback_model=settings.model,
)
root_agent = build_root_agent(
    settings,
    model_name=model_registry.active.model,
)
