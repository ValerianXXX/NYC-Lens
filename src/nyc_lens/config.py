from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .paths import config_dir


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a mapping in {path}")
    return value


@lru_cache(maxsize=None)
def sources_config() -> dict[str, Any]:
    return load_yaml(config_dir() / "sources.yml")


@lru_cache(maxsize=None)
def geographies_config() -> dict[str, Any]:
    return load_yaml(config_dir() / "geographies.yml")


@lru_cache(maxsize=None)
def metrics_config() -> dict[str, Any]:
    return load_yaml(config_dir() / "metrics.yml")


@lru_cache(maxsize=None)
def taxonomy_config() -> dict[str, Any]:
    return load_yaml(config_dir() / "business_taxonomy.yml")


@lru_cache(maxsize=None)
def test_sites_config() -> dict[str, Any]:
    return load_yaml(config_dir() / "test_sites.yml")


def get_source(source_id: str) -> dict[str, Any]:
    try:
        return sources_config()["sources"][source_id]
    except KeyError as exc:
        raise KeyError(f"Unknown source_id: {source_id}") from exc
