"""Server-owned model registry for Conduit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class ModelOption:
    key: str
    label: str
    model: str
    provider: str

    def to_payload(self) -> dict[str, str]:
        return {
            "label": self.label,
            "model": self.model,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    active_key: str
    options: tuple[ModelOption, ...]

    @property
    def active(self) -> ModelOption:
        for option in self.options:
            if option.key == self.active_key:
                return option
        raise KeyError(f"Unknown active model key: {self.active_key}")

    def with_active(self, active_key: str) -> "ModelRegistry":
        for option in self.options:
            if option.key == active_key:
                return ModelRegistry(active_key=active_key, options=self.options)
        raise KeyError(f"Unknown model key: {active_key}")

    def get(self, model_key: str) -> ModelOption:
        for option in self.options:
            if option.key == model_key:
                return option
        raise KeyError(f"Unknown model key: {model_key}")

    def to_payload(self) -> dict[str, object]:
        return {
            "active": self.active_key,
            "models": {
                option.key: option.to_payload()
                for option in self.options
            },
        }


DEFAULT_MODEL_OPTIONS: tuple[ModelOption, ...] = (
    ModelOption(
        key="claude_opus_4_6",
        label="Claude Opus 4.6",
        model="claude-opus-4-6",
        provider="anthropic",
    ),
    ModelOption(
        key="claude_sonnet_4_6",
        label="Claude Sonnet 4.6",
        model="claude-sonnet-4-6",
        provider="anthropic",
    ),
    ModelOption(
        key="gemini_3_flash",
        label="Gemini 3 Flash",
        model="gemini-3-flash-preview",
        provider="google",
    ),
    ModelOption(
        key="gemini_3_1_pro",
        label="Gemini 3.1 Pro",
        model="gemini-3.1-pro-preview",
        provider="google",
    ),
)

DEFAULT_ACTIVE_MODEL_KEY = "claude_opus_4_6"


def infer_provider(model_name: str) -> str:
    normalized = model_name.lower()
    if normalized.startswith("claude-"):
        return "anthropic"
    if normalized.startswith("gemini"):
        return "google"
    return "unknown"


def load_model_registry(
    path_str: str,
    *,
    fallback_model: str | None = None,
) -> ModelRegistry:
    path = Path(path_str)
    default_active_key = _model_key_for_name(fallback_model) or DEFAULT_ACTIVE_MODEL_KEY
    if not path.exists():
        return ModelRegistry(
            active_key=default_active_key,
            options=DEFAULT_MODEL_OPTIONS,
        )

    payload = yaml.safe_load(path.read_text()) or {}
    raw_models = payload.get("models") or {}
    options = _load_options(raw_models) or DEFAULT_MODEL_OPTIONS
    active_key = str(payload.get("active") or default_active_key)
    if not any(option.key == active_key for option in options):
        active_key = default_active_key
    return ModelRegistry(active_key=active_key, options=options)


def persist_model_registry(path_str: str, registry: ModelRegistry) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            registry.to_payload(),
            sort_keys=False,
        )
    )


def _load_options(raw_models: object) -> tuple[ModelOption, ...]:
    if not isinstance(raw_models, dict):
        return ()

    options: list[ModelOption] = []
    for key, value in raw_models.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue

        model_name = str(value.get("model") or "").strip()
        if not model_name:
            continue

        label = str(value.get("label") or model_name)
        provider = str(value.get("provider") or infer_provider(model_name))
        if provider not in {"anthropic", "google"}:
            continue

        options.append(
            ModelOption(
                key=key,
                label=label,
                model=model_name,
                provider=provider,
            )
        )

    return tuple(options)


def _model_key_for_name(model_name: str | None) -> str | None:
    if not model_name:
        return None

    for option in DEFAULT_MODEL_OPTIONS:
        if option.model == model_name:
            return option.key
    return None
