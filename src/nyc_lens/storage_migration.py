from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import duckdb

from .paths import database_path, data_dir, project_home
from .storage import (
    EXTERNAL_TABLE_SPECS,
    MAX_SINGLE_FILE_BYTES,
    _ensure_empty_parquet,
    _read_parquet_expression,
    _write_source_partitions,
    ensure_external_storage,
    external_facts_root,
    external_parquet_files,
    rebuild_external_catalog,
    refresh_external_view,
    schema_sql_without_external_tables,
    table_kind,
)
from .util import new_id, safe_component, sha256_file, utc_now


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATION_MANAGED_CATALOGS = {"external_table_catalog", "external_partition_catalog"}
REGIONAL_EXTERNAL_SOURCES = {"overture_buildings", "overture_places", "lodes_2023"}


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _configure_temp_directory(
    conn: duckdb.DuckDBPyConnection,
    temp_directory: Path,
) -> None:
    temp_directory.mkdir(parents=True, exist_ok=True)
    conn.execute("SET temp_directory = ?", [str(temp_directory.resolve())])


def _cleanup_operation_temp(operation_temp_root: Path) -> bool:
    expected_parent = (data_dir() / "cache" / "duckdb_temp").resolve()
    if operation_temp_root.parent.resolve() != expected_parent:
        raise ValueError(f"Unsafe DuckDB temporary spill path: {operation_temp_root}")
    if operation_temp_root.exists():
        shutil.rmtree(operation_temp_root)
    return not operation_temp_root.exists()


def _fingerprint(
    conn: duckdb.DuckDBPyConnection,
    relation_sql: str,
    table_name: str,
) -> dict[str, Any]:
    spec = EXTERNAL_TABLE_SPECS[table_name]
    values = ", ".join(
        f"coalesce(CAST({_quoted_identifier(column)} AS VARCHAR), '<NULL>')"
        for column, _ in spec.columns
    )
    row_hash = f"hash({values})"
    result = conn.execute(
        f"""
        SELECT count(*) AS row_count,
               count(DISTINCT {_quoted_identifier(spec.id_column)}) AS distinct_id_count,
               sum(CAST({row_hash} AS HUGEINT)) AS hash_sum,
               bit_xor({row_hash}) AS hash_xor
        FROM {relation_sql}
        """
    ).fetchone()
    return {
        "row_count": int(result[0]),
        "distinct_id_count": int(result[1]),
        "hash_sum": str(result[2] or 0),
        "hash_xor": str(result[3] or 0),
    }


def _source_counts(conn: duckdb.DuckDBPyConnection, relation_sql: str) -> list[dict[str, Any]]:
    return [
        {"source_id": row[0], "row_count": int(row[1])}
        for row in conn.execute(
            f"SELECT source_id, count(*) FROM {relation_sql} GROUP BY source_id ORDER BY source_id"
        ).fetchall()
    ]


def _export_external_tables(
    old_conn: duckdb.DuckDBPyConnection,
    staging_facts: Path,
    *,
    table_names: set[str] | None = None,
) -> dict[str, Any]:
    exports: dict[str, Any] = {}
    for table_name, spec in EXTERNAL_TABLE_SPECS.items():
        if table_names is not None and table_name not in table_names:
            continue
        if table_kind(old_conn, table_name) != "BASE TABLE":
            raise ValueError(f"Expected a materialized source table before migration: {table_name}")
        table_root = staging_facts / table_name
        _ensure_empty_parquet(old_conn, spec, root=staging_facts)
        sources = [
            str(row[0])
            for row in old_conn.execute(
                f"SELECT DISTINCT source_id FROM {_quoted_identifier(table_name)} ORDER BY source_id"
            ).fetchall()
        ]
        partitions: list[dict[str, Any]] = []
        for source_id in sources:
            region = "greater_nyc" if source_id in REGIONAL_EXTERNAL_SOURCES else "nyc"
            destination = (
                table_root / f"region={region}" / f"source={safe_component(source_id)}"
            )
            destination.mkdir(parents=True, exist_ok=False)
            source_entries = _write_source_partitions(
                old_conn,
                spec,
                table_name,
                source_id,
                destination,
            )
            partitions.extend(
                {
                    **entry,
                    "source_id": source_id,
                    "relative_path": entry["path"].relative_to(staging_facts).as_posix(),
                }
                for entry in source_entries
            )
        files = external_parquet_files(table_name, root=staging_facts)
        parquet_sql = _read_parquet_expression(files)
        before = _fingerprint(old_conn, _quoted_identifier(table_name), table_name)
        after = _fingerprint(old_conn, parquet_sql, table_name)
        if before != after:
            raise ValueError(
                f"Parquet fingerprint mismatch for {table_name}: before={before}, after={after}"
            )
        before_sources = _source_counts(old_conn, _quoted_identifier(table_name))
        after_sources = _source_counts(old_conn, parquet_sql)
        if before_sources != after_sources:
            raise ValueError(f"Per-source row mismatch for {table_name}")
        oversized = [path for path in files if path.stat().st_size >= MAX_SINGLE_FILE_BYTES]
        if oversized:
            raise ValueError(f"External export created a file at or above 5 GB: {oversized[0]}")
        exports[table_name] = {
            "fingerprint": before,
            "source_counts": before_sources,
            "partition_files": len([path for path in files if path.name != "_empty.parquet"]),
            "byte_size": sum(path.stat().st_size for path in files),
            "partitions": [
                {
                    **entry,
                    "path": entry["relative_path"],
                    "min_record_date": str(entry["min_record_date"]) if entry["min_record_date"] else None,
                    "max_record_date": str(entry["max_record_date"]) if entry["max_record_date"] else None,
                }
                for entry in partitions
            ],
        }
    return exports


def _copy_catalog_tables(
    old_database: Path,
    new_database: Path,
    *,
    temp_directory: Path | None = None,
) -> dict[str, int]:
    schema_sql = schema_sql_without_external_tables(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn = duckdb.connect(str(new_database))
    conn.execute("SET TimeZone='UTC'")
    if temp_directory is not None:
        _configure_temp_directory(conn, temp_directory)
    try:
        conn.execute(schema_sql)
        # Catalog rows are copied below. Do not register empty generation heads
        # before importing the existing catalog's primary keys.
        for table_name in EXTERNAL_TABLE_SPECS:
            refresh_external_view(conn, table_name)
        conn.execute(f"ATTACH '{_sql_path(old_database)}' AS old_catalog (READ_ONLY)")
        old_tables = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT table_name
                FROM duckdb_tables()
                WHERE database_name='old_catalog' AND schema_name='main'
                """
            ).fetchall()
        }
        new_tables = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT table_name FROM duckdb_tables()
                WHERE database_name=current_database() AND schema_name='main'
                """
            ).fetchall()
        }
        unknown = sorted(old_tables - set(EXTERNAL_TABLE_SPECS) - new_tables)
        if unknown:
            raise ValueError(f"New schema would omit materialized tables: {', '.join(unknown)}")
        copied: dict[str, int] = {}
        for table_name in sorted(old_tables & new_tables):
            quoted = _quoted_identifier(table_name)
            conn.execute(
                f"INSERT INTO main.{quoted} BY NAME SELECT * FROM old_catalog.main.{quoted}"
            )
            copied[table_name] = int(
                conn.execute(f"SELECT count(*) FROM main.{quoted}").fetchone()[0]
            )
            old_count = int(
                conn.execute(f"SELECT count(*) FROM old_catalog.main.{quoted}").fetchone()[0]
            )
            if copied[table_name] != old_count:
                raise ValueError(
                    f"Catalog row mismatch for {table_name}: {copied[table_name]}/{old_count}"
                )
        existing_external_catalogs = {
            str(row[0])
            for row in conn.execute(
                "SELECT table_name FROM external_table_catalog"
            ).fetchall()
        }
        conn.execute("DETACH old_catalog")
        for table_name in EXTERNAL_TABLE_SPECS:
            refresh_external_view(conn, table_name)
            if table_name not in existing_external_catalogs:
                rebuild_external_catalog(conn, table_name)
        conn.execute("CHECKPOINT")
        return copied
    finally:
        conn.close()


def _verify_new_database(
    old_database: Path,
    new_database: Path,
    exports: dict[str, Any],
    *,
    temp_directory: Path | None = None,
) -> dict[str, Any]:
    old_conn = duckdb.connect(str(old_database), read_only=True)
    new_conn = duckdb.connect(str(new_database), read_only=True)
    if temp_directory is not None:
        _configure_temp_directory(old_conn, temp_directory / "old")
        _configure_temp_directory(new_conn, temp_directory / "new")
    try:
        external: dict[str, Any] = {}
        for table_name in EXTERNAL_TABLE_SPECS:
            before = _fingerprint(old_conn, _quoted_identifier(table_name), table_name)
            after = _fingerprint(new_conn, _quoted_identifier(table_name), table_name)
            if before != after or before != exports[table_name]["fingerprint"]:
                raise ValueError(
                    f"Post-build fingerprint mismatch for {table_name}: {before}/{after}"
                )
            before_sources = _source_counts(old_conn, _quoted_identifier(table_name))
            after_sources = _source_counts(new_conn, _quoted_identifier(table_name))
            if before_sources != after_sources:
                raise ValueError(f"Post-build per-source mismatch for {table_name}")
            external[table_name] = {
                "fingerprint": after,
                "source_counts": after_sources,
                "table_type": table_kind(new_conn, table_name),
            }
        old_counts = {
            str(row[0]): int(row[1])
            for row in old_conn.execute(
                """
                SELECT table_name, estimated_size FROM duckdb_tables()
                WHERE schema_name='main' ORDER BY table_name
                """
            ).fetchall()
            if str(row[0]) not in EXTERNAL_TABLE_SPECS
            and str(row[0]) not in MIGRATION_MANAGED_CATALOGS
        }
        new_counts: dict[str, int] = {}
        for table_name in old_counts:
            new_counts[table_name] = int(
                new_conn.execute(
                    f"SELECT count(*) FROM {_quoted_identifier(table_name)}"
                ).fetchone()[0]
            )
            old_count = int(
                old_conn.execute(
                    f"SELECT count(*) FROM {_quoted_identifier(table_name)}"
                ).fetchone()[0]
            )
            if new_counts[table_name] != old_count:
                raise ValueError(
                    f"Post-build catalog mismatch for {table_name}: {new_counts[table_name]}/{old_count}"
                )
        catalog_rows = int(new_conn.execute("SELECT count(*) FROM external_table_catalog").fetchone()[0])
        if catalog_rows != len(EXTERNAL_TABLE_SPECS):
            raise ValueError(f"External catalog has {catalog_rows} table rows")
        return {"external": external, "catalog_table_counts": new_counts}
    finally:
        old_conn.close()
        new_conn.close()


def migrate_to_external_storage() -> dict[str, Any]:
    """Atomically migrate high-volume facts while retaining full Raw provenance."""
    old_database = database_path().resolve()
    expected_data_root = data_dir().resolve()
    if old_database.parent != expected_data_root:
        raise ValueError(f"Database migration is restricted to the project data directory: {old_database}")
    if not old_database.is_file():
        raise FileNotFoundError(old_database)
    operation_id = "sa_" + new_id("tmp").split("_", 1)[1][:12]
    migration_root = data_dir() / "standardized" / "_storage_migrations" / operation_id
    staging_facts = migration_root / "facts"
    final_facts = external_facts_root()
    new_database = data_dir() / f"nyc_lens.migrating.{operation_id}.duckdb"
    backup_database = data_dir() / f"nyc_lens.pre_a.{operation_id}.duckdb"
    report_path = project_home() / "reports" / "qa" / f"storage_migration_{operation_id}.json"
    for path in (new_database, backup_database):
        if path.exists():
            raise FileExistsError(path)
    if final_facts.exists():
        existing_files = list(final_facts.rglob("*.parquet"))
        if existing_files:
            raise FileExistsError(
                f"External facts root already contains {len(existing_files)} Parquet files: {final_facts}"
            )
    migration_root.mkdir(parents=True, exist_ok=False)
    started_at = utc_now()
    report: dict[str, Any] = {
        "operation_id": operation_id,
        "layout_version": "1.0.0",
        "started_at": started_at.isoformat(),
        "old_database": str(old_database),
        "new_database": str(new_database),
        "external_facts_root": str(final_facts),
        "status": "IN_PROGRESS",
    }
    facts_published = False
    original_moved = False
    promoted = False
    try:
        checkpoint_conn = duckdb.connect(str(old_database))
        checkpoint_conn.execute("CHECKPOINT")
        checkpoint_conn.close()
        old_sha256 = sha256_file(old_database)
        old_bytes = old_database.stat().st_size
        old_conn = duckdb.connect(str(old_database), read_only=True)
        try:
            exports = _export_external_tables(old_conn, staging_facts)
        finally:
            old_conn.close()
        report["old_database_sha256"] = old_sha256
        report["old_database_bytes"] = old_bytes
        report["exports"] = exports

        final_facts.parent.mkdir(parents=True, exist_ok=True)
        if final_facts.exists():
            final_facts.rmdir()
        os.replace(staging_facts, final_facts)
        facts_published = True
        copied = _copy_catalog_tables(old_database, new_database)
        report["copied_catalog_tables"] = copied
        new_bytes = new_database.stat().st_size
        if new_bytes >= MAX_SINGLE_FILE_BYTES:
            raise ValueError(
                f"Compacted catalog is still at or above 5 GB: {new_bytes / 1_000_000_000:.3f} GB"
            )
        verification = _verify_new_database(old_database, new_database, exports)
        report["verification"] = verification
        report["new_database_bytes"] = new_bytes
        report["new_database_sha256"] = sha256_file(new_database)

        os.replace(old_database, backup_database)
        original_moved = True
        os.replace(new_database, old_database)
        promoted = True
        promoted_conn = duckdb.connect(str(old_database), read_only=True)
        try:
            promoted_counts = {
                table_name: int(
                    promoted_conn.execute(
                        f"SELECT count(*) FROM {_quoted_identifier(table_name)}"
                    ).fetchone()[0]
                )
                for table_name in EXTERNAL_TABLE_SPECS
            }
            catalog_rows = int(
                promoted_conn.execute("SELECT count(*) FROM external_table_catalog").fetchone()[0]
            )
        finally:
            promoted_conn.close()
        expected_counts = {
            table_name: int(details["fingerprint"]["row_count"])
            for table_name, details in exports.items()
        }
        if promoted_counts != expected_counts or catalog_rows != len(EXTERNAL_TABLE_SPECS):
            raise ValueError(
                f"Promoted database verification failed: {promoted_counts}/{expected_counts}/{catalog_rows}"
            )
        backup_database.unlink()
        original_moved = False
        report.update(
            {
                "status": "SUCCEEDED",
                "completed_at": utc_now().isoformat(),
                "promoted_database": str(old_database),
                "promoted_database_bytes": old_database.stat().st_size,
                "promoted_database_sha256": sha256_file(old_database),
                "retired_materialized_backup_deleted": True,
                "raw_snapshots_preserved": True,
                "external_parquet_preserved": True,
            }
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report["report_path"] = str(report_path)
        return report
    except Exception as exc:
        if promoted and old_database.exists():
            failed_promoted = data_dir() / f"nyc_lens.failed_promotion.{operation_id}.duckdb"
            os.replace(old_database, failed_promoted)
            promoted = False
        if original_moved and backup_database.exists():
            os.replace(backup_database, old_database)
            original_moved = False
        if facts_published and final_facts.exists():
            failed_facts = migration_root / "facts_failed"
            if not failed_facts.exists():
                os.replace(final_facts, failed_facts)
        report.update(
            {
                "status": "FAILED",
                "completed_at": utc_now().isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "original_database_restored": old_database.is_file(),
            }
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        raise


def compact_catalog_database() -> dict[str, Any]:
    """Atomically rebuild an already-externalized catalogue below the 5 GB cap.

    This removes avoidable physical-index bloat while preserving every catalogue
    row and every external Parquet file. The original database is only retired
    after full pre-promotion and post-promotion verification.
    """
    old_database = database_path().resolve()
    expected_data_root = data_dir().resolve()
    if old_database.parent != expected_data_root:
        raise ValueError(f"Database compaction is restricted to the project data directory: {old_database}")
    if not old_database.is_file():
        raise FileNotFoundError(old_database)

    operation_id = "compact_" + new_id("tmp").split("_", 1)[1][:12]
    new_database = data_dir() / f"nyc_lens.compacting.{operation_id}.duckdb"
    backup_database = data_dir() / f"nyc_lens.pre_compact.{operation_id}.duckdb"
    expansion_root = data_dir() / "standardized" / "_catalog_compactions" / operation_id
    staging_facts = expansion_root / "facts"
    report_path = project_home() / "reports" / "qa" / f"catalog_compaction_{operation_id}.json"
    operation_temp_root = data_dir() / "cache" / "duckdb_temp" / operation_id
    for path in (new_database, backup_database):
        if path.exists():
            raise FileExistsError(path)
    if operation_temp_root.exists():
        raise FileExistsError(operation_temp_root)
    operation_temp_root.mkdir(parents=True, exist_ok=False)

    started_at = utc_now()
    report: dict[str, Any] = {
        "operation_id": operation_id,
        "started_at": started_at.isoformat(),
        "old_database": str(old_database),
        "new_database": str(new_database),
        "temporary_spill_directory": str(operation_temp_root),
        "status": "IN_PROGRESS",
        "external_parquet_mutated": False,
        "raw_snapshots_mutated": False,
    }
    original_moved = False
    promoted = False
    committed = False
    published_external_tables: list[str] = []
    try:
        checkpoint_conn = duckdb.connect(str(old_database))
        try:
            _configure_temp_directory(checkpoint_conn, operation_temp_root / "checkpoint")
            checkpoint_conn.execute("CHECKPOINT")
        finally:
            checkpoint_conn.close()
        report["old_database_bytes"] = old_database.stat().st_size
        report["old_database_sha256"] = sha256_file(old_database)

        old_conn = duckdb.connect(str(old_database), read_only=True)
        try:
            _configure_temp_directory(old_conn, operation_temp_root / "source")
            fingerprints: dict[str, Any] = {}
            materialized_external_tables: set[str] = set()
            for table_name in EXTERNAL_TABLE_SPECS:
                kind = table_kind(old_conn, table_name)
                if kind not in {"VIEW", "BASE TABLE"}:
                    raise ValueError(f"Missing external-storage source relation: {table_name}")
                if kind == "BASE TABLE":
                    materialized_external_tables.add(table_name)
                fingerprints[table_name] = {
                    "fingerprint": _fingerprint(old_conn, _quoted_identifier(table_name), table_name),
                    "source_counts": _source_counts(old_conn, _quoted_identifier(table_name)),
                }
            incremental_exports = (
                _export_external_tables(
                    old_conn,
                    staging_facts,
                    table_names=materialized_external_tables,
                )
                if materialized_external_tables
                else {}
            )
            duplicate_lineage_ids = int(
                old_conn.execute(
                    """
                    SELECT count(*) FROM (
                        SELECT standardized_record_id
                        FROM standardized_record
                        GROUP BY standardized_record_id
                        HAVING count(*) > 1
                    ) duplicates
                    """
                ).fetchone()[0]
            )
            if duplicate_lineage_ids:
                raise ValueError(f"standardized_record contains {duplicate_lineage_ids:,} duplicate ids")
            duplicate_workforce_ids = int(
                old_conn.execute(
                    """
                    SELECT count(*) FROM (
                        SELECT workforce_estimate_id
                        FROM workforce_estimate
                        GROUP BY workforce_estimate_id
                        HAVING count(*) > 1
                    ) duplicates
                    """
                ).fetchone()[0]
            )
            if duplicate_workforce_ids:
                raise ValueError(f"workforce_estimate contains {duplicate_workforce_ids:,} duplicate ids")
        finally:
            old_conn.close()

        for table_name in sorted(materialized_external_tables):
            staged_table = staging_facts / table_name
            final_table = external_facts_root() / table_name
            if final_table.parent.resolve() != external_facts_root().resolve():
                raise ValueError(f"Unsafe external table target: {final_table}")
            if final_table.exists():
                if any(final_table.iterdir()):
                    raise FileExistsError(
                        f"Incremental external target already contains files: {final_table}"
                    )
                final_table.rmdir()
            os.replace(staged_table, final_table)
            published_external_tables.append(table_name)
        report.update(
            {
                "expanded_external_tables": sorted(materialized_external_tables),
                "incremental_exports": incremental_exports,
                "external_parquet_mutated": bool(materialized_external_tables),
            }
        )

        copied = _copy_catalog_tables(
            old_database,
            new_database,
            temp_directory=operation_temp_root / "copy",
        )
        report["copied_catalog_tables"] = copied
        new_bytes = new_database.stat().st_size
        if new_bytes >= MAX_SINGLE_FILE_BYTES:
            raise ValueError(
                f"Compacted catalogue is still at or above 5 GB: {new_bytes / 1_000_000_000:.3f} GB"
            )
        verification = _verify_new_database(
            old_database,
            new_database,
            fingerprints,
            temp_directory=operation_temp_root / "verify",
        )
        new_conn = duckdb.connect(str(new_database), read_only=True)
        try:
            _configure_temp_directory(new_conn, operation_temp_root / "validate")
            duplicate_lineage_ids = int(
                new_conn.execute(
                    """
                    SELECT count(*) FROM (
                        SELECT standardized_record_id
                        FROM standardized_record
                        GROUP BY standardized_record_id
                        HAVING count(*) > 1
                    ) duplicates
                    """
                ).fetchone()[0]
            )
            duplicate_workforce_ids = int(
                new_conn.execute(
                    """
                    SELECT count(*) FROM (
                        SELECT workforce_estimate_id
                        FROM workforce_estimate
                        GROUP BY workforce_estimate_id
                        HAVING count(*) > 1
                    ) duplicates
                    """
                ).fetchone()[0]
            )
            primary_key_count = int(
                new_conn.execute(
                    """
                    SELECT count(*) FROM duckdb_constraints()
                    WHERE table_name='standardized_record' AND constraint_type='PRIMARY KEY'
                    """
                ).fetchone()[0]
            )
            workforce_primary_key_count = int(
                new_conn.execute(
                    """
                    SELECT count(*) FROM duckdb_constraints()
                    WHERE table_name='workforce_estimate' AND constraint_type='PRIMARY KEY'
                    """
                ).fetchone()[0]
            )
            compacted_logical_key_checks: dict[str, dict[str, int | str]] = {}
            for table_name, key_column in (
                ("parcel", "parcel_id"),
                ("census_estimate", "census_estimate_id"),
                ("inspection", "inspection_id"),
                ("transaction_case", "transaction_case_id"),
            ):
                duplicate_count = int(
                    new_conn.execute(
                        f"""
                        SELECT count(*) FROM (
                            SELECT {key_column} FROM {table_name}
                            GROUP BY {key_column} HAVING count(*) > 1
                        ) duplicate_keys
                        """
                    ).fetchone()[0]
                )
                physical_primary_keys = int(
                    new_conn.execute(
                        """
                        SELECT count(*) FROM duckdb_constraints()
                        WHERE table_name=? AND constraint_type='PRIMARY KEY'
                        """,
                        [table_name],
                    ).fetchone()[0]
                )
                compacted_logical_key_checks[table_name] = {
                    "key_column": key_column,
                    "duplicate_ids": duplicate_count,
                    "physical_primary_keys": physical_primary_keys,
                }
        finally:
            new_conn.close()
        compacted_logical_key_failures = {
            table_name: details
            for table_name, details in compacted_logical_key_checks.items()
            if int(details["duplicate_ids"]) or int(details["physical_primary_keys"])
        }
        if (
            duplicate_lineage_ids
            or duplicate_workforce_ids
            or primary_key_count
            or workforce_primary_key_count
            or compacted_logical_key_failures
        ):
            raise ValueError(
                "Catalogue compaction invariant failed: "
                f"lineage_duplicates={duplicate_lineage_ids}, "
                f"workforce_duplicates={duplicate_workforce_ids}, "
                f"lineage_primary_keys={primary_key_count}, "
                f"workforce_primary_keys={workforce_primary_key_count}, "
                f"other_logical_key_failures={compacted_logical_key_failures}"
            )
        report.update(
            {
                "verification": verification,
                "new_database_bytes": new_bytes,
                "new_database_sha256": sha256_file(new_database),
                "standardized_record_duplicate_ids": duplicate_lineage_ids,
                "standardized_record_physical_primary_keys": primary_key_count,
                "workforce_estimate_duplicate_ids": duplicate_workforce_ids,
                "workforce_estimate_physical_primary_keys": workforce_primary_key_count,
                "compacted_logical_key_checks": compacted_logical_key_checks,
            }
        )

        os.replace(old_database, backup_database)
        original_moved = True
        os.replace(new_database, old_database)
        promoted = True
        promoted_conn = duckdb.connect(str(old_database), read_only=True)
        try:
            _configure_temp_directory(promoted_conn, operation_temp_root / "promoted")
            promoted_bytes = old_database.stat().st_size
            promoted_lineage_rows = int(
                promoted_conn.execute("SELECT count(*) FROM standardized_record").fetchone()[0]
            )
            promoted_catalog_rows = int(
                promoted_conn.execute("SELECT count(*) FROM external_table_catalog").fetchone()[0]
            )
        finally:
            promoted_conn.close()
        if (
            promoted_bytes >= MAX_SINGLE_FILE_BYTES
            or promoted_lineage_rows != copied["standardized_record"]
            or promoted_catalog_rows != len(EXTERNAL_TABLE_SPECS)
        ):
            raise ValueError(
                "Promoted catalogue verification failed: "
                f"bytes={promoted_bytes}, lineage={promoted_lineage_rows}, external_catalog={promoted_catalog_rows}"
            )

        report.update(
            {
                "status": "SUCCEEDED",
                "completed_at": utc_now().isoformat(),
                "promoted_database": str(old_database),
                "promoted_database_bytes": promoted_bytes,
                "promoted_database_sha256": sha256_file(old_database),
                "retired_index_bloated_backup_deleted": False,
            }
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        # Exact, verified backup path; deletion happens only after the promoted
        # database and the durable report both pass all checks above.
        if backup_database.parent.resolve() != expected_data_root:
            raise ValueError(f"Unsafe compaction backup path: {backup_database}")
        backup_database.unlink()
        original_moved = False
        committed = True
        report["retired_index_bloated_backup_deleted"] = True
        if expansion_root.exists():
            expected_expansion_parent = (data_dir() / "standardized" / "_catalog_compactions").resolve()
            if expansion_root.parent.resolve() != expected_expansion_parent:
                raise ValueError(f"Unsafe compaction staging path: {expansion_root}")
            shutil.rmtree(expansion_root)
        report["temporary_spill_directory_deleted"] = _cleanup_operation_temp(
            operation_temp_root
        )
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report["report_path"] = str(report_path)
        return report
    except Exception as exc:
        if not committed:
            if promoted and old_database.exists():
                failed_promoted = data_dir() / f"nyc_lens.failed_compaction.{operation_id}.duckdb"
                os.replace(old_database, failed_promoted)
                promoted = False
            if original_moved and backup_database.exists():
                os.replace(backup_database, old_database)
                original_moved = False
            for table_name in reversed(published_external_tables):
                final_table = external_facts_root() / table_name
                if (
                    final_table.exists()
                    and final_table.parent.resolve() == external_facts_root().resolve()
                ):
                    shutil.rmtree(final_table)
        failed_new_database_deleted = False
        if (
            new_database.exists()
            and new_database.parent.resolve() == expected_data_root
            and new_database.name.startswith("nyc_lens.compacting.compact_")
        ):
            new_database.unlink()
            failed_new_database_deleted = True
        failed_staging_deleted = False
        if expansion_root.exists():
            expected_expansion_parent = (data_dir() / "standardized" / "_catalog_compactions").resolve()
            if expansion_root.parent.resolve() == expected_expansion_parent:
                shutil.rmtree(expansion_root)
                failed_staging_deleted = True
        failed_temp_deleted = False
        temp_cleanup_error = None
        try:
            failed_temp_deleted = _cleanup_operation_temp(operation_temp_root)
        except Exception as cleanup_exc:  # pragma: no cover - filesystem-specific
            temp_cleanup_error = str(cleanup_exc)
        report.update(
            {
                "status": "FAILED_AFTER_COMMIT" if committed else "FAILED",
                "completed_at": utc_now().isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "original_database_available": old_database.is_file(),
                "failed_new_database_deleted": failed_new_database_deleted,
                "failed_staging_deleted": failed_staging_deleted,
                "failed_temp_deleted": failed_temp_deleted,
                "temp_cleanup_error": temp_cleanup_error,
            }
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        raise


def external_storage_status() -> dict[str, Any]:
    path = database_path()
    conn = duckdb.connect(str(path), read_only=True)
    try:
        tables = {}
        for table_name, spec in EXTERNAL_TABLE_SPECS.items():
            kind = table_kind(conn, table_name)
            row_count = int(
                conn.execute(f"SELECT count(*) FROM {_quoted_identifier(table_name)}").fetchone()[0]
            )
            distinct_count = int(
                conn.execute(
                    f"SELECT count(DISTINCT {_quoted_identifier(spec.id_column)}) "
                    f"FROM {_quoted_identifier(table_name)}"
                ).fetchone()[0]
            )
            tables[table_name] = {
                "table_type": kind,
                "row_count": row_count,
                "distinct_id_count": distinct_count,
            }
        catalog = conn.execute(
            "SELECT * FROM external_table_catalog ORDER BY table_name"
        ).fetchdf().to_dict("records") if table_kind(conn, "external_table_catalog") else []
        return {
            "database": str(path),
            "database_bytes": path.stat().st_size,
            "database_sha256": sha256_file(path),
            "tables": tables,
            "catalog": catalog,
        }
    finally:
        conn.close()
