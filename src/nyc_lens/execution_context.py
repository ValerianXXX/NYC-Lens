"""Private worker identity and database settings, never model-selected options."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterator

from .util import json_dumps, new_id

MAX_WORKER_RESULT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class DatabaseLimits:
    memory_mb: int = 512
    threads: int = 2
    spill_mb: int = 1024

    def __post_init__(self) -> None:
        for value, low, high in ((self.memory_mb, 64, 8192), (self.threads, 1, 8), (self.spill_mb, 0, 8192)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid bounded worker database policy")


@dataclass
class _WorkerContext:
    sites: deque[str]
    comparisons: deque[str]
    limits: DatabaseLimits
    spill_directory: Path


_WORKER: ContextVar[_WorkerContext | None] = ContextVar("nyc_analysis_worker", default=None)


def allocate_identity(kind: str) -> str:
    current = _WORKER.get()
    if current is None:
        return new_id(kind)
    identities = current.sites if kind == "site" else current.comparisons if kind == "comparison" else None
    if identities is None or not identities:
        raise RuntimeError("WORKER_ATTEMPT_IDENTITIES_EXHAUSTED")
    return identities.popleft()


def in_worker() -> bool:
    return _WORKER.get() is not None


def result_json(result: dict) -> str:
    value = json_dumps(result)
    if in_worker() and len(value.encode("utf8")) > MAX_WORKER_RESULT_BYTES:
        raise ValueError("ANALYSIS_RESULT_SIZE_LIMIT")
    return value


def database_options() -> dict[str, str]:
    current = _WORKER.get()
    if current is None:
        return {}
    return {"memory_limit": f"{current.limits.memory_mb}MiB", "threads": str(current.limits.threads),
            "max_temp_directory_size": f"{current.limits.spill_mb}MiB", "temp_directory": str(current.spill_directory)}


@contextmanager
def worker_context(site_ids: list[str], comparison_ids: list[str], limits: DatabaseLimits,
                   spill_directory: Path) -> Iterator[None]:
    if _WORKER.get() is not None:
        raise RuntimeError("Nested worker identity scopes are forbidden")
    if not 1 <= len(site_ids) <= 20 or len(comparison_ids) > 1:
        raise ValueError("Invalid worker identity population")
    for kind, identities in (("site", site_ids), ("comparison", comparison_ids)):
        if len(set(identities)) != len(identities) or any(not re.fullmatch(kind + r"_[0-9a-f]{32}", value) for value in identities):
            raise ValueError("Invalid reserved worker identities")
    directory = Path(spill_directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Worker spill directory is unavailable")
    token = _WORKER.set(_WorkerContext(deque(site_ids), deque(comparison_ids), limits, directory))
    try:
        yield
    finally:
        _WORKER.reset(token)
