from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import duckdb

from .config import configuration_fingerprint, configuration_snapshot, sources_config, taxonomy_config
from .paths import database_path, ensure_runtime_dirs
from .storage import ensure_external_storage, schema_sql_without_external_tables
from .util import json_dumps, new_id, utc_now


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
CALCULATION_VERSION = "0.2.0"
CALCULATION_CODE_FILES = (
    "analyze.py",
    "analysis_state.py",
    "comparison.py",
    "config.py",
    "db.py",
    "evidence.py",
    "entities/buildings.py",
    "entities/parcels.py",
    "geocoding/service.py",
    "metrics/engine.py",
    "provenance.py",
    "spatial/catchments.py",
)


def _content_hash(paths: list[Path], *, root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        try:
            label = path.relative_to(root).as_posix()
        except ValueError:
            label = path.name
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def connect(*, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    ensure_runtime_dirs()
    conn = duckdb.connect(str(database_path()), read_only=read_only)
    conn.execute("SET TimeZone='UTC'")
    return conn


@contextmanager
def db_session(*, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = connect(read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


@configuration_snapshot()
def initialize_database() -> dict[str, Any]:
    ensure_runtime_dirs()
    schema_sql = schema_sql_without_external_tables(SCHEMA_PATH.read_text(encoding="utf-8"))
    spatial_status = "not_attempted"
    with db_session() as conn:
        conn.execute(schema_sql)
        storage_status = ensure_external_storage(conn)
        try:
            conn.execute("INSTALL spatial")
            conn.execute("LOAD spatial")
            spatial_status = "loaded"
        except Exception as exc:  # pragma: no cover - depends on extension mirror
            spatial_status = f"unavailable: {exc}"
        registered_at = utc_now()
        for source_id, source in sources_config().get("sources", {}).items():
            conn.execute(
                """
                INSERT INTO source_registry BY NAME
                SELECT
                    ? AS source_id, ? AS name, ? AS steward, ? AS source_url,
                    ? AS api_url, ? AS dataset_id, ? AS update_frequency,
                    ? AS geographic_precision, ? AS temporal_precision,
                    ? AS license, ? AS estimated_size, ? AS incremental_method,
                    NULL::TIMESTAMP AS last_successful_update, ? AS known_limitations,
                    ?::JSON AS config_json, ?::TIMESTAMP AS registered_at
                ON CONFLICT (source_id) DO UPDATE SET
                    name=excluded.name,
                    steward=excluded.steward,
                    source_url=excluded.source_url,
                    api_url=excluded.api_url,
                    dataset_id=excluded.dataset_id,
                    update_frequency=excluded.update_frequency,
                    geographic_precision=excluded.geographic_precision,
                    temporal_precision=excluded.temporal_precision,
                    license=excluded.license,
                    estimated_size=excluded.estimated_size,
                    incremental_method=excluded.incremental_method,
                    known_limitations=excluded.known_limitations,
                    config_json=excluded.config_json
                """,
                [
                    source_id,
                    source.get("name"),
                    source.get("steward"),
                    source.get("url"),
                    source.get("api_url"),
                    source.get("dataset_id"),
                    source.get("update_frequency"),
                    source.get("geographic_precision"),
                    source.get("temporal_precision"),
                    source.get("license"),
                    source.get("estimated_size"),
                    source.get("incremental_method"),
                    source.get("known_limitations"),
                    json_dumps(source),
                    registered_at,
                ],
            )
        for business_type, definition in taxonomy_config().get("business_types", {}).items():
            for relation in ("direct", "secondary", "substitutes", "complements"):
                relation_definition = definition.get(relation, {})
                for namespace, categories in relation_definition.items():
                    if not isinstance(categories, list):
                        continue
                    for category in categories:
                        category_key = f"{namespace}:{category}"
                        category_id = "category_" + hashlib.sha256(
                            f"{business_type}|{relation}|{category_key}".encode("utf-8")
                        ).hexdigest()[:24]
                        conn.execute(
                            """
                            INSERT INTO business_category VALUES (?, ?, ?, ?, 1.0, '0.1.0')
                            ON CONFLICT (category_id) DO UPDATE SET
                                business_type=excluded.business_type,
                                category=excluded.category,
                                relation=excluded.relation,
                                weight=excluded.weight,
                                method_version=excluded.method_version
                            """,
                            [category_id, business_type, category_key, relation],
                        )
        table_count = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='main'"
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE source_registry AS s SET last_successful_update=(
                SELECT max(r.completed_at) FROM ingestion_run r
                WHERE r.source_id=s.source_id
                  AND r.status IN ('SUCCEEDED', 'SUCCEEDED_WITH_WARNINGS')
            )
            """
        )
    return {
        "database": str(database_path()),
        "tables": table_count,
        "spatial": spatial_status,
        "external_storage": storage_status,
    }


def begin_run(conn: duckdb.DuckDBPyConnection, source_id: str | None, command: str) -> str:
    run_id = new_id("run")
    conn.execute(
        "INSERT INTO ingestion_run(run_id, source_id, command, started_at, status) VALUES (?, ?, ?, ?, 'INDETERMINATE')",
        [run_id, source_id, command, utc_now()],
    )
    return run_id


def finish_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    status: str,
    *,
    request_count: int = 0,
    downloaded_bytes: int = 0,
    inserted_records: int = 0,
    updated_records: int = 0,
    deleted_records: int = 0,
    retry_count: int = 0,
    schema_changes: Any = None,
    error: Any = None,
    warnings: Any = None,
    http_statuses: Any = None,
) -> None:
    completed_at = utc_now()
    if http_statuses is None:
        status_rows = conn.execute(
            "SELECT DISTINCT http_status FROM dataset_snapshot WHERE run_id=? AND http_status IS NOT NULL ORDER BY 1",
            [run_id],
        ).fetchall()
        http_statuses = [row[0] for row in status_rows]
    conn.execute(
        """
        UPDATE ingestion_run SET
            completed_at=?, status=?, request_count=?, downloaded_bytes=?,
            inserted_records=?, updated_records=?, deleted_records=?, retry_count=?,
            http_statuses_json=?::JSON, schema_changes_json=?::JSON,
            error_json=?::JSON, warnings_json=?::JSON
        WHERE run_id=?
        """,
        [
            completed_at,
            status,
            request_count,
            downloaded_bytes,
            inserted_records,
            updated_records,
            deleted_records,
            retry_count,
            json_dumps(http_statuses),
            json_dumps(schema_changes),
            json_dumps(error),
            json_dumps(warnings),
            run_id,
        ],
    )
    if status in {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"}:
        conn.execute(
            "UPDATE source_registry SET last_successful_update=? WHERE source_id=(SELECT source_id FROM ingestion_run WHERE run_id=?)",
            [completed_at, run_id],
        )


@configuration_snapshot()
def calculation_version(conn: duckdb.DuckDBPyConnection, name: str = "site_analysis") -> str:
    package_root = Path(__file__).resolve().parent
    code_hash = _content_hash(
        [package_root / relative for relative in CALCULATION_CODE_FILES],
        root=package_root,
    )
    config_hash = configuration_fingerprint()
    existing = conn.execute(
        """
        SELECT calculation_version_id FROM calculation_version
        WHERE name=? AND version=? AND code_hash=? AND config_hash=?
        LIMIT 1
        """,
        [name, CALCULATION_VERSION, code_hash, config_hash],
    ).fetchone()
    if existing:
        return existing[0]
    version_id = new_id("calc")
    conn.execute(
        "INSERT INTO calculation_version VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            version_id,
            name,
            CALCULATION_VERSION,
            code_hash,
            config_hash,
            utc_now(),
            "Code hash covers configuration loading/versioning, geocoding, parcel/building matching, catchments and metric engine; config hash covers the exact YAML bytes captured for this operation. Area weighting uses EPSG:32118 inside NYC and EPSG:26918 elsewhere in CSA 408; walk catchments use OSM network edges.",
        ],
    )
    return version_id
