from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

import yaml

from .paths import config_dir


MAX_CONFIGURATION_FILE_BYTES = 2_000_000
MAX_CONFIGURATION_SNAPSHOT_BYTES = 8_000_000
MAX_CONFIGURATION_FILES = 64


@dataclass(frozen=True)
class _ConfigurationSnapshot:
    root: Path
    files: Mapping[str, bytes]
    fingerprint: str


_active_snapshot: ContextVar[_ConfigurationSnapshot | None] = ContextVar(
    "nyc_lens_configuration_snapshot", default=None,
)


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(MAX_CONFIGURATION_FILE_BYTES + 1)
    if len(raw) > MAX_CONFIGURATION_FILE_BYTES:
        raise ValueError("CONFIGURATION_FILE_LIMIT: a YAML file exceeds 2,000,000 bytes")
    return raw


@lru_cache(maxsize=32)
def _parse_yaml(path_label: str, raw: bytes) -> dict[str, Any]:
    # Exact bytes, not timestamps, identify a cached parse. Never return this
    # mutable object directly to a caller or an analysis context.
    try:
        value = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("CONFIGURATION_YAML_INVALID: expected valid UTF-8 YAML") from exc
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("CONFIGURATION_MAPPING_REQUIRED: expected a YAML mapping")
    return value


def _capture_files(root: Path) -> dict[str, bytes]:
    paths = sorted(root.glob("*.yml"))
    if len(paths) > MAX_CONFIGURATION_FILES:
        raise ValueError("CONFIGURATION_FILE_COUNT_LIMIT: at most 64 YAML files")
    files: dict[str, bytes] = {}
    total = 0
    for path in paths:
        raw = _read_bounded(path)
        total += len(raw)
        if total > MAX_CONFIGURATION_SNAPSHOT_BYTES:
            raise ValueError("CONFIGURATION_SNAPSHOT_LIMIT: at most 8,000,000 YAML bytes")
        files[path.name] = raw
    return files


def _capture_snapshot() -> _ConfigurationSnapshot:
    root = config_dir().resolve()
    # Bound retries when files change during capture. The retained exact byte set
    # is immutable for this request; this is not a multi-file edit transaction.
    for _ in range(2):
        files = _capture_files(root)
        if files != _capture_files(root):
            continue
        digest = hashlib.sha256()
        for name, raw in sorted(files.items()):
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(raw)
            digest.update(b"\0")
        return _ConfigurationSnapshot(root, MappingProxyType(files), digest.hexdigest())
    raise ValueError("CONFIGURATION_CHANGED_DURING_CAPTURE: retry after editing finishes")


@contextmanager
def configuration_snapshot() -> Iterator[None]:
    """Pin all YAML bytes for one operation; nested analysis shares the snapshot."""
    if _active_snapshot.get() is not None:
        yield
        return
    token = _active_snapshot.set(_capture_snapshot())
    try:
        yield
    finally:
        _active_snapshot.reset(token)


def configuration_fingerprint() -> str:
    with configuration_snapshot():
        snapshot = _active_snapshot.get()
        assert snapshot is not None
        return snapshot.fingerprint


def clear_configuration_cache() -> None:
    """Clear parsed-file acceleration without altering an in-flight snapshot."""
    _parse_yaml.cache_clear()


def load_yaml(path: Path) -> dict[str, Any]:
    snapshot = _active_snapshot.get()
    # Resolve the parent, not a possibly replaced file symlink. A configured
    # filename must keep using captured bytes even if its disk target changes.
    if snapshot is not None and path.parent.resolve() == snapshot.root:
        if path.name not in snapshot.files:
            raise FileNotFoundError("Configuration file was not present in the request snapshot")
        raw = snapshot.files[path.name]
        label = str(snapshot.root / path.name)
    else:
        path = path.resolve()
        raw = _read_bounded(path)
        label = str(path)
    return deepcopy(_parse_yaml(label, raw))


def _configuration_file(name: str) -> Path:
    snapshot = _active_snapshot.get()
    return (snapshot.root if snapshot is not None else config_dir()) / name


def sources_config() -> dict[str, Any]:
    return load_yaml(_configuration_file("sources.yml"))


def geographies_config() -> dict[str, Any]:
    return load_yaml(_configuration_file("geographies.yml"))


def metrics_config() -> dict[str, Any]:
    return load_yaml(_configuration_file("metrics.yml"))


def taxonomy_config() -> dict[str, Any]:
    return load_yaml(_configuration_file("business_taxonomy.yml"))


def test_sites_config() -> dict[str, Any]:
    return load_yaml(_configuration_file("test_sites.yml"))


def get_source(source_id: str) -> dict[str, Any]:
    try:
        return sources_config()["sources"][source_id]
    except KeyError as exc:
        raise KeyError(f"Unknown source_id: {source_id}") from exc
