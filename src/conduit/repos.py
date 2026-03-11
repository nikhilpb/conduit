"""Repository configuration loader for Conduit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RepoConfig:
    """A configured repository that tools can operate on."""

    key: str
    url: str
    default_branch: str


def load_repos(config_path: str) -> dict[str, RepoConfig]:
    """Load repository configuration from a YAML file.

    Returns a mapping of repo key -> RepoConfig.
    Raises FileNotFoundError if the config file does not exist.
    Raises ValueError if the config is malformed.
    """

    path = Path(config_path)
    if not path.exists():
        return {}

    payload: Any = yaml.safe_load(path.read_text()) or {}
    raw_repos = payload.get("repos", {})
    if not isinstance(raw_repos, dict):
        raise ValueError("repos config must be a mapping")

    repos: dict[str, RepoConfig] = {}
    for key, entry in raw_repos.items():
        if not isinstance(entry, dict):
            raise ValueError(f"repo '{key}' must be a mapping")
        url = entry.get("url")
        if not url:
            raise ValueError(f"repo '{key}' must have a 'url' field")
        default_branch = entry.get("default_branch", "main")
        repos[key] = RepoConfig(key=key, url=url, default_branch=default_branch)

    return repos
