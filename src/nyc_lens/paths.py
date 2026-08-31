from __future__ import annotations

import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_HOME = PACKAGE_DIR.parents[1]


def project_home() -> Path:
    configured = os.getenv("NYC_LENS_HOME")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_HOME


def config_dir() -> Path:
    return project_home() / "config"


def data_dir() -> Path:
    return project_home() / "data"


def database_path() -> Path:
    configured = os.getenv("NYC_LENS_DATABASE")
    return Path(configured).expanduser().resolve() if configured else data_dir() / "nyc_lens.duckdb"


def ensure_runtime_dirs() -> None:
    for relative in (
        "data/raw",
        "data/staged",
        "data/standardized",
        "data/marts",
        "data/manifests",
        "data/cache",
        "reports/samples",
        "reports/comparisons",
        "reports/qa",
    ):
        (project_home() / relative).mkdir(parents=True, exist_ok=True)
