from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from .config import metrics_config, sources_config
from .paths import project_home
from .util import utc_now


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _metric_dictionary(path: Path) -> int:
    definitions = metrics_config().get("metrics", {})
    lines = [
        "# Metric dictionary",
        "",
        "This table is generated from `config/metrics.yml`. Metric values, years, confidence, margins of error, and evidence snapshots are determined by each query result.",
        "",
        "| metric_id | English name | Primary source | Fact/measure type | Aggregation | Primary limitation |",
        "|---|---|---|---|---|---|",
    ]
    for metric_id, definition in definitions.items():
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    metric_id,
                    definition.get("label_en") or metric_id.replace("_", " ").title(),
                    definition.get("source"),
                    definition.get("measure_type"),
                    definition.get("aggregation"),
                    definition.get("caveat_en") or "See source metadata and methodology.",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Survey estimates retain the published estimate and margin of error. Derived survey ratios use conservative first-order error propagation. Area-level health, income, language, and ethnicity statistics do not describe a specific person, household, building, or address.",
        ]
    )
    _write(path, lines)
    return len(definitions)


def _data_catalog(conn: duckdb.DuckDBPyConnection, path: Path) -> int:
    rows = conn.execute(
        """
        WITH latest_run AS (
            SELECT source_id, arg_max(status, started_at) AS latest_status,
                   max(started_at) AS latest_started_at
            FROM ingestion_run WHERE source_id IS NOT NULL GROUP BY source_id
        ), snapshot_summary AS (
            SELECT source_id, count(*) AS snapshots,
                   sum(coalesce(byte_size, 0)) AS raw_bytes,
                   arg_max(vintage, retrieved_at) AS latest_vintage,
                   max(retrieved_at) AS latest_retrieved_at
            FROM dataset_snapshot GROUP BY source_id
        ), linked AS (
            SELECT source_id, count(DISTINCT entity_id) AS linked_entities
            FROM standardized_record GROUP BY source_id
        )
        SELECT s.source_id, s.name, s.steward, p.latest_vintage,
               r.latest_status, p.latest_retrieved_at,
               coalesce(p.snapshots, 0), coalesce(p.raw_bytes, 0),
               coalesce(l.linked_entities, 0), s.update_frequency,
               s.geographic_precision, s.known_limitations
        FROM source_registry s
        LEFT JOIN latest_run r USING(source_id)
        LEFT JOIN snapshot_summary p USING(source_id)
        LEFT JOIN linked l USING(source_id)
        ORDER BY s.source_id
        """
    ).fetchall()
    generated = utc_now().date().isoformat()
    lines = [
        f"# Data catalog (generated {generated})",
        "",
        "`linked_entities` is the number of distinct entities traceable through `standardized_record`; it is not the complete fact-row count for every source. Interpret multi-table and aggregated sources together with status output and QA reports.",
        "",
        "| source_id | Name / steward | Latest vintage | Latest run | Latest retrieval | Snapshots | Raw bytes | linked_entities | Update frequency | Geographic precision | Known limitations |",
        "|---|---|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        name_and_steward = f"{row[1]} / {row[2]}"
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    row[0],
                    name_and_steward,
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                )
            )
            + " |"
        )
    _write(path, lines)
    return len(rows)


def _licensing(path: Path) -> tuple[int, int]:
    config = sources_config()
    sources = config.get("sources", {})
    excluded = config.get("excluded_sources", {})
    lines = [
        "# Licensing and attribution register",
        "",
        "The machine-readable authority is `config/sources.yml`. Every raw snapshot also records the source URL, license, filename, size, and SHA-256 hash. Pause refreshes and update the register when upstream terms change.",
        "",
        "| source_id | Steward | License/terms summary | Source page |",
        "|---|---|---|---|",
    ]
    for source_id, source in sources.items():
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    source_id,
                    source.get("steward"),
                    source.get("license"),
                    source.get("url"),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Explicitly excluded or gated sources",
            "",
            "| source_id | Status | Reason |",
            "|---|---|---|",
        ]
    )
    for source_id, definition in excluded.items():
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (source_id, definition.get("status"), definition.get("reason"))
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "OpenStreetMap/Overpass data retains separate raw snapshots and attribution. Google Places, Yelp, commercial mobility feeds, and individual movement traces are not stored in the permanent database.",
        ]
    )
    _write(path, lines)
    return len(sources), len(excluded)


def write_generated_docs(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    docs = project_home() / "docs"
    metric_count = _metric_dictionary(docs / "metric_dictionary.md")
    source_count = _data_catalog(conn, docs / "data_catalog.md")
    licensed_count, excluded_count = _licensing(docs / "licensing.md")
    return {
        "metric_dictionary": {"path": str(docs / "metric_dictionary.md"), "metrics": metric_count},
        "data_catalog": {"path": str(docs / "data_catalog.md"), "sources": source_count},
        "licensing": {
            "path": str(docs / "licensing.md"),
            "sources": licensed_count,
            "excluded_sources": excluded_count,
        },
    }
