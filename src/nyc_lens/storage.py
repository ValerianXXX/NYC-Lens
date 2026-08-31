from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from .paths import data_dir, project_home
from .util import new_id, safe_component, sha256_file, utc_now


LAYOUT_VERSION = "1.0.0"
MAX_ROWS_PER_FILE = 500_000
MAX_SINGLE_FILE_BYTES = 5_000_000_000


@dataclass(frozen=True)
class ExternalTableSpec:
    table_name: str
    id_column: str
    date_column: str
    columns: tuple[tuple[str, str], ...]


EXTERNAL_TABLE_SPECS: dict[str, ExternalTableSpec] = {
    "building": ExternalTableSpec(
        table_name="building",
        id_column="building_id",
        date_column="valid_from",
        columns=(
            ("building_id", "VARCHAR"), ("bin", "VARCHAR"), ("bbl", "VARCHAR"),
            ("name", "VARCHAR"), ("address", "VARCHAR"), ("min_longitude", "DOUBLE"),
            ("min_latitude", "DOUBLE"), ("max_longitude", "DOUBLE"),
            ("max_latitude", "DOUBLE"), ("geom_wkt", "VARCHAR"),
            ("attributes_json", "JSON"), ("source_id", "VARCHAR"),
            ("snapshot_id", "VARCHAR"), ("retrieved_at", "TIMESTAMP"),
            ("valid_from", "DATE"), ("valid_to", "DATE"),
            ("geography_type", "VARCHAR"), ("geography_id", "VARCHAR"),
            ("measure_type", "VARCHAR"), ("confidence", "DOUBLE"),
            ("method_version", "VARCHAR"),
        ),
    ),
    "property_record": ExternalTableSpec(
        table_name="property_record",
        id_column="property_record_id",
        date_column="record_date",
        columns=(
            ("property_record_id", "VARCHAR"), ("parcel_id", "VARCHAR"),
            ("record_type", "VARCHAR"), ("record_date", "DATE"), ("amount", "DOUBLE"),
            ("attributes_json", "JSON"), ("source_id", "VARCHAR"),
            ("snapshot_id", "VARCHAR"), ("retrieved_at", "TIMESTAMP"),
            ("valid_from", "DATE"), ("valid_to", "DATE"),
            ("geography_type", "VARCHAR"), ("geography_id", "VARCHAR"),
            ("measure_type", "VARCHAR"), ("confidence", "DOUBLE"),
            ("method_version", "VARCHAR"),
        ),
    ),
    "regulatory_record": ExternalTableSpec(
        table_name="regulatory_record",
        id_column="regulatory_record_id",
        date_column="issued_at",
        columns=(
            ("regulatory_record_id", "VARCHAR"), ("parcel_id", "VARCHAR"),
            ("record_type", "VARCHAR"), ("status", "VARCHAR"),
            ("issued_at", "DATE"), ("closed_at", "DATE"),
            ("attributes_json", "JSON"), ("source_id", "VARCHAR"),
            ("snapshot_id", "VARCHAR"), ("retrieved_at", "TIMESTAMP"),
            ("valid_from", "DATE"), ("valid_to", "DATE"),
            ("geography_type", "VARCHAR"), ("geography_id", "VARCHAR"),
            ("measure_type", "VARCHAR"), ("confidence", "DOUBLE"),
            ("method_version", "VARCHAR"),
        ),
    ),
    "poi": ExternalTableSpec(
        table_name="poi",
        id_column="poi_id",
        date_column="valid_from",
        columns=(
            ("poi_id", "VARCHAR"), ("source_poi_id", "VARCHAR"),
            ("name", "VARCHAR"), ("basic_category", "VARCHAR"),
            ("taxonomy_json", "JSON"), ("address", "VARCHAR"),
            ("longitude", "DOUBLE"), ("latitude", "DOUBLE"),
            ("source_items_json", "JSON"), ("status", "VARCHAR"),
            ("source_id", "VARCHAR"), ("snapshot_id", "VARCHAR"),
            ("retrieved_at", "TIMESTAMP"), ("valid_from", "DATE"),
            ("valid_to", "DATE"), ("geography_type", "VARCHAR"),
            ("geography_id", "VARCHAR"), ("measure_type", "VARCHAR"),
            ("confidence", "DOUBLE"), ("method_version", "VARCHAR"),
        ),
    ),
    "workforce_estimate": ExternalTableSpec(
        table_name="workforce_estimate",
        id_column="workforce_estimate_id",
        date_column="valid_from",
        columns=(
            ("workforce_estimate_id", "VARCHAR"), ("geography_id", "VARCHAR"),
            ("geography_type", "VARCHAR"), ("segment", "VARCHAR"),
            ("job_type", "VARCHAR"), ("measure", "VARCHAR"),
            ("value", "DOUBLE"), ("longitude", "DOUBLE"),
            ("latitude", "DOUBLE"), ("current_county_geoid", "VARCHAR"),
            ("state_abbr", "VARCHAR"), ("source_id", "VARCHAR"),
            ("snapshot_id", "VARCHAR"), ("retrieved_at", "TIMESTAMP"),
            ("valid_from", "DATE"), ("valid_to", "DATE"),
            ("measure_type", "VARCHAR"), ("confidence", "DOUBLE"),
            ("method_version", "VARCHAR"),
        ),
    ),
}


def schema_sql_without_external_tables(schema_sql: str) -> str:
    """Remove external fact-table DDL while retaining it in schema.sql for test fixtures."""
    result = schema_sql
    for table_name in EXTERNAL_TABLE_SPECS:
        result = re.sub(
            rf"CREATE TABLE IF NOT EXISTS\s+{re.escape(table_name)}\s*\(.*?\);\s*",
            "",
            result,
            flags=re.IGNORECASE | re.DOTALL,
        )
        result = re.sub(
            rf"ALTER TABLE\s+{re.escape(table_name)}\s+.*?;\s*",
            "",
            result,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return result


def external_facts_root() -> Path:
    return data_dir() / "standardized" / "facts"


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _relation_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Unsafe relation name: {value}")
    return _quoted_identifier(value)


def table_kind(conn: duckdb.DuckDBPyConnection, table_name: str) -> str | None:
    row = conn.execute(
        """
        SELECT table_type FROM information_schema.tables
        WHERE table_schema='main' AND table_name=?
        """,
        [table_name],
    ).fetchone()
    return str(row[0]) if row else None


def is_external_table(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    return table_name in EXTERNAL_TABLE_SPECS and table_kind(conn, table_name) == "VIEW"


def _table_root(table_name: str, *, root: Path | None = None) -> Path:
    return (root or external_facts_root()) / table_name


def _ensure_empty_parquet(
    conn: duckdb.DuckDBPyConnection,
    spec: ExternalTableSpec,
    *,
    root: Path | None = None,
) -> Path:
    table_root = _table_root(spec.table_name, root=root)
    table_root.mkdir(parents=True, exist_ok=True)
    empty_path = table_root / "_empty.parquet"
    if empty_path.is_file():
        return empty_path
    select_list = ", ".join(
        f"CAST(NULL AS {data_type}) AS {_quoted_identifier(column)}"
        for column, data_type in spec.columns
    )
    conn.execute(
        f"COPY (SELECT {select_list} WHERE FALSE) TO '{_sql_path(empty_path)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    return empty_path


def external_parquet_files(table_name: str, *, root: Path | None = None) -> list[Path]:
    return sorted(_table_root(table_name, root=root).rglob("*.parquet"))


def _read_parquet_expression(files: list[Path]) -> str:
    if not files:
        raise ValueError("External table has no Parquet schema seed")
    paths = ",".join(f"'{_sql_path(path)}'" for path in files)
    return f"read_parquet([{paths}], union_by_name=true, hive_partitioning=false)"


def refresh_external_view(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    *,
    root: Path | None = None,
) -> None:
    spec = EXTERNAL_TABLE_SPECS[table_name]
    kind = table_kind(conn, table_name)
    if kind == "BASE TABLE":
        raise ValueError(f"Cannot replace materialized table {table_name} with a view implicitly")
    _ensure_empty_parquet(conn, spec, root=root)
    files = external_parquet_files(table_name, root=root)
    select_list = ", ".join(
        f"CAST({_quoted_identifier(column)} AS {data_type}) AS {_quoted_identifier(column)}"
        for column, data_type in spec.columns
    )
    conn.execute(
        f"CREATE OR REPLACE VIEW {_quoted_identifier(table_name)} AS "
        f"SELECT {select_list} FROM {_read_parquet_expression(files)}"
    )


def ensure_external_storage(
    conn: duckdb.DuckDBPyConnection,
    *,
    root: Path | None = None,
) -> dict[str, str]:
    status: dict[str, str] = {}
    for table_name in EXTERNAL_TABLE_SPECS:
        kind = table_kind(conn, table_name)
        if kind == "BASE TABLE":
            status[table_name] = "materialized_pending_migration"
            continue
        refresh_external_view(conn, table_name, root=root)
        status[table_name] = "external_parquet_view"
    return status


def _assert_within(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"Unsafe storage path outside expected root: {resolved}")


def _incoming_stats(
    conn: duckdb.DuckDBPyConnection,
    spec: ExternalTableSpec,
    relation: str,
    source_id: str,
) -> tuple[int, int]:
    relation_sql = _relation_name(relation)
    count, distinct_count = conn.execute(
        f"SELECT count(*), count(DISTINCT {_quoted_identifier(spec.id_column)}) "
        f"FROM {relation_sql} WHERE source_id=?",
        [source_id],
    ).fetchone()
    count = int(count)
    distinct_count = int(distinct_count)
    if count != distinct_count:
        raise ValueError(
            f"External {spec.table_name}/{source_id} has {count - distinct_count:,} duplicate ids"
        )
    return count, distinct_count


def _write_source_partitions(
    conn: duckdb.DuckDBPyConnection,
    spec: ExternalTableSpec,
    relation: str,
    source_id: str,
    destination: Path,
) -> list[dict[str, Any]]:
    relation_sql = _relation_name(relation)
    date_sql = _quoted_identifier(spec.date_column)
    id_sql = _quoted_identifier(spec.id_column)
    groups = conn.execute(
        f"""
        SELECT coalesce(CAST(year({date_sql}) AS VARCHAR), 'unknown') AS record_year,
               count(*) AS row_count
        FROM {relation_sql}
        WHERE source_id=?
        GROUP BY 1 ORDER BY 1
        """,
        [source_id],
    ).fetchall()
    entries: list[dict[str, Any]] = []
    columns = ", ".join(_quoted_identifier(column) for column, _ in spec.columns)
    for raw_year, raw_count in groups:
        record_year = str(raw_year)
        row_count = int(raw_count)
        if record_year != "unknown" and not re.fullmatch(r"\d{4}", record_year):
            raise ValueError(f"Unexpected partition year for {spec.table_name}: {record_year}")
        year_dir = destination / f"year={record_year}"
        year_dir.mkdir(parents=True, exist_ok=True)
        buckets = max(1, math.ceil(row_count / MAX_ROWS_PER_FILE))
        year_filter = (
            f"{date_sql} IS NULL" if record_year == "unknown"
            else f"year({date_sql})={int(record_year)}"
        )
        for bucket in range(buckets):
            output = year_dir / f"part-{bucket:05d}.parquet"
            bucket_filter = "TRUE" if buckets == 1 else f"hash({id_sql}) % {buckets} = {bucket}"
            conn.execute(
                f"""
                COPY (
                    SELECT {columns} FROM {relation_sql}
                    WHERE source_id=? AND {year_filter} AND {bucket_filter}
                ) TO '{_sql_path(output)}'
                (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
                """,
                [source_id],
            )
            byte_size = output.stat().st_size
            if byte_size >= MAX_SINGLE_FILE_BYTES:
                raise ValueError(f"External partition exceeds 5 GB safety cap: {output}")
            file_count, distinct_ids, minimum, maximum = conn.execute(
                f"""
                SELECT count(*), count(DISTINCT {id_sql}),
                       min({date_sql}), max({date_sql})
                FROM read_parquet('{_sql_path(output)}')
                """
            ).fetchone()
            if int(file_count) != int(distinct_ids):
                raise ValueError(f"Duplicate ids written to external partition: {output}")
            entries.append(
                {
                    "record_year": record_year,
                    "path": output,
                    "row_count": int(file_count),
                    "distinct_id_count": int(distinct_ids),
                    "byte_size": byte_size,
                    "sha256": sha256_file(output),
                    "min_record_date": minimum,
                    "max_record_date": maximum,
                }
            )
    return entries


def _partition_dimensions(path: Path, table_root: Path) -> tuple[str, str, str]:
    relative = path.relative_to(table_root)
    parts = relative.parts
    dimensions: dict[str, str] = {}
    for part in parts[:-1]:
        if "=" in part:
            key, value = part.split("=", 1)
            dimensions[key] = value
    return (
        dimensions.get("region", "unknown"),
        dimensions.get("source", "unknown"),
        dimensions.get("year", "unknown"),
    )


def rebuild_external_catalog(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    spec = EXTERNAL_TABLE_SPECS[table_name]
    table_root = _table_root(table_name, root=root)
    files = [path for path in external_parquet_files(table_name, root=root) if path.name != "_empty.parquet"]
    entries: list[dict[str, Any]] = []
    id_sql = _quoted_identifier(spec.id_column)
    date_sql = _quoted_identifier(spec.date_column)
    written_at = utc_now()
    for path in files:
        region, source_id, record_year = _partition_dimensions(path, table_root)
        row_count, distinct_count, minimum, maximum = conn.execute(
            f"""
            SELECT count(*), count(DISTINCT {id_sql}), min({date_sql}), max({date_sql})
            FROM read_parquet('{_sql_path(path)}')
            """
        ).fetchone()
        relative_path = path.resolve().relative_to(project_home().resolve()).as_posix()
        path_sha256 = sha256_file(path)
        partition_key = f"{table_name}|{relative_path}|{path_sha256}"
        entries.append(
            {
                "partition_id": "extpart_" + hashlib.sha256(partition_key.encode("utf-8")).hexdigest()[:24],
                "table_name": table_name,
                "region": region,
                "source_id": source_id,
                "record_year": record_year,
                "relative_path": relative_path,
                "row_count": int(row_count),
                "distinct_id_count": int(distinct_count),
                "byte_size": path.stat().st_size,
                "sha256": path_sha256,
                "min_record_date": minimum,
                "max_record_date": maximum,
            }
        )
    manifest = {
        "layout_version": LAYOUT_VERSION,
        "table_name": table_name,
        "format": "parquet",
        "partitioning": ["region", "source", "year", "hash_bucket"],
        "columns": [{"name": name, "duckdb_type": data_type} for name, data_type in spec.columns],
        "generated_at": written_at.isoformat(),
        "row_count": sum(item["row_count"] for item in entries),
        "byte_size": sum(item["byte_size"] for item in entries),
        "file_count": len(entries),
        "partitions": [
            {**item, "min_record_date": str(item["min_record_date"]) if item["min_record_date"] else None,
             "max_record_date": str(item["max_record_date"]) if item["max_record_date"] else None}
            for item in entries
        ],
    }
    manifest_path = table_root / "_manifest.json"
    temporary_manifest = table_root / f"._manifest.{new_id('tmp').split('_', 1)[1][:12]}.json"
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary_manifest, manifest_path)
    manifest_sha = sha256_file(manifest_path)
    conn.execute("DELETE FROM external_partition_catalog WHERE table_name=?", [table_name])
    for item in entries:
        conn.execute(
            """
            INSERT INTO external_partition_catalog VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                item["partition_id"], table_name, item["region"], item["source_id"],
                item["record_year"], item["relative_path"], item["row_count"],
                item["distinct_id_count"], item["byte_size"], item["sha256"],
                item["min_record_date"], item["max_record_date"], written_at,
                LAYOUT_VERSION,
            ],
        )
    storage_root = table_root.resolve().relative_to(project_home().resolve()).as_posix()
    conn.execute(
        """
        INSERT INTO external_table_catalog VALUES (?, 'parquet', ?, ?::JSON, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (table_name) DO UPDATE SET
            storage_format=excluded.storage_format,
            storage_root=excluded.storage_root,
            partitioning_json=excluded.partitioning_json,
            row_count=excluded.row_count,
            byte_size=excluded.byte_size,
            file_count=excluded.file_count,
            manifest_sha256=excluded.manifest_sha256,
            last_verified_at=excluded.last_verified_at,
            layout_version=excluded.layout_version
        """,
        [
            table_name, storage_root, json.dumps(manifest["partitioning"]),
            manifest["row_count"], manifest["byte_size"], manifest["file_count"],
            manifest_sha, written_at, LAYOUT_VERSION,
        ],
    )
    return {**manifest, "manifest_path": str(manifest_path), "manifest_sha256": manifest_sha}


def replace_source_from_relation(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    source_id: str,
    relation: str,
    region: str = "nyc",
) -> tuple[int, int]:
    if table_name not in EXTERNAL_TABLE_SPECS:
        raise ValueError(f"Table is not configured for external storage: {table_name}")
    spec = EXTERNAL_TABLE_SPECS[table_name]
    previous = int(
        conn.execute(
            f"SELECT count(*) FROM {_quoted_identifier(table_name)} WHERE source_id=?",
            [source_id],
        ).fetchone()[0]
    )
    incoming, _ = _incoming_stats(conn, spec, relation, source_id)
    if not is_external_table(conn, table_name):
        relation_sql = _relation_name(relation)
        conn.execute("BEGIN")
        try:
            conn.execute(
                f"DELETE FROM {_quoted_identifier(table_name)} WHERE source_id=?",
                [source_id],
            )
            conn.execute(
                f"INSERT INTO {_quoted_identifier(table_name)} BY NAME "
                f"SELECT * FROM {relation_sql} WHERE source_id=?",
                [source_id],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return previous, incoming

    operation_id = "ext_" + new_id("tmp").split("_", 1)[1][:12]
    stage_root = data_dir() / "standardized" / "_external_staging" / operation_id
    backup_root = data_dir() / "standardized" / "_external_backups" / operation_id
    source_slug = safe_component(source_id)
    region_slug = safe_component(region)
    staged_source = stage_root / table_name / f"region={region_slug}" / f"source={source_slug}"
    final_source = _table_root(table_name) / f"region={region_slug}" / f"source={source_slug}"
    backup_source = backup_root / table_name / f"region={region_slug}" / f"source={source_slug}"
    _assert_within(staged_source, data_dir() / "standardized" / "_external_staging")
    _assert_within(final_source, external_facts_root())
    _assert_within(backup_source, data_dir() / "standardized" / "_external_backups")
    staged_source.mkdir(parents=True, exist_ok=False)
    entries = _write_source_partitions(conn, spec, relation, source_id, staged_source)
    if sum(item["row_count"] for item in entries) != incoming:
        raise ValueError(f"External staging row mismatch for {table_name}/{source_id}")
    final_source.parent.mkdir(parents=True, exist_ok=True)
    moved_old = False
    try:
        if final_source.exists():
            backup_source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(final_source, backup_source)
            moved_old = True
        os.replace(staged_source, final_source)
        refresh_external_view(conn, table_name)
        observed, distinct_count = conn.execute(
            f"SELECT count(*), count(DISTINCT {_quoted_identifier(spec.id_column)}) "
            f"FROM {_quoted_identifier(table_name)} WHERE source_id=?",
            [source_id],
        ).fetchone()
        if int(observed) != incoming or int(distinct_count) != incoming:
            raise ValueError(
                f"External publish verification failed for {table_name}/{source_id}: "
                f"{observed}/{distinct_count}/{incoming}"
            )
        rebuild_external_catalog(conn, table_name)
    except Exception:
        if final_source.exists():
            shutil.rmtree(final_source)
        if moved_old and backup_source.exists():
            backup_source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup_source, final_source)
        refresh_external_view(conn, table_name)
        raise
    if moved_old and backup_source.exists():
        shutil.rmtree(backup_source)
    return previous, incoming


def replace_source_from_parquet(
    conn: duckdb.DuckDBPyConnection,
    *,
    table_name: str,
    source_id: str,
    parquet_glob: Path,
    region: str = "nyc",
) -> tuple[int, int]:
    relation = "_nyc_lens_external_incoming"
    glob_sql = _sql_path(parquet_glob)
    conn.execute(
        f"CREATE OR REPLACE TEMP VIEW {relation} AS "
        f"SELECT * FROM read_parquet('{glob_sql}', union_by_name=true)"
    )
    try:
        return replace_source_from_relation(
            conn,
            table_name=table_name,
            source_id=source_id,
            relation=relation,
            region=region,
        )
    finally:
        conn.execute(f"DROP VIEW IF EXISTS {relation}")
