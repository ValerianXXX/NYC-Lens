"""Capture source dates with results; retrieval age is not an expiry policy."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import duckdb

from .util import utc_now


def _utc(value: datetime) -> datetime:
    # The catalog stores UTC in DuckDB TIMESTAMP columns without a timezone.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SourceObservationCapture:
    """One observation time and one lookup per source/snapshot in an analysis."""

    def __init__(self, conn: duckdb.DuckDBPyConnection, *, observed_at: datetime | None = None):
        self.conn = conn
        self.observed_at = _utc(observed_at or utc_now())
        self.cache: dict[tuple[str | None, str | None], dict[str, Any]] = {}

    def capture(self, source_id: str | None, snapshot_id: str | None) -> dict[str, Any]:
        key = (source_id, snapshot_id)
        if key in self.cache:
            return self.cache[key]
        observation: dict[str, Any] = {
            "capture_state": "DERIVED" if source_id == "derived" else "MISSING",
            "observed_at": self.observed_at.isoformat(),
            "retrieved_at": None,
            "retrieval_age_days": None,
            "reference_start": None,
            "reference_end": None,
            "vintage": None,
            "snapshot_status": None,
            "currentness": "NOT_ASSESSED",
            "limitations": ["Retrieval age and source reference period do not establish expiry or whether the source has changed."],
        }
        row = self.conn.execute(
            "SELECT source_id, retrieved_at, valid_from, valid_to, vintage, status "
            "FROM dataset_snapshot WHERE snapshot_id=?", [snapshot_id]
        ).fetchone() if snapshot_id else None
        if row and row[0] == source_id:
            retrieved = _utc(row[1]) if row[1] else None
            age = (self.observed_at - retrieved).total_seconds() / 86400 if retrieved else None
            observation.update({
                "capture_state": "CAPTURED",
                "retrieved_at": retrieved.isoformat() if retrieved else None,
                "retrieval_age_days": round(age, 4) if age is not None and age >= 0 else None,
                "reference_start": row[2].isoformat() if row[2] else None,
                "reference_end": row[3].isoformat() if row[3] else None,
                "vintage": row[4],
                "snapshot_status": row[5],
            })
            if age is not None and age < 0:
                observation["limitations"].append("SOURCE_RETRIEVAL_IN_FUTURE")
        elif row:
            observation["capture_state"] = "SOURCE_MISMATCH"
        self.cache[key] = observation
        return observation


def observation_columns(metric: dict[str, Any]) -> dict[str, Any]:
    """Export only the saved observation, never live catalog metadata."""
    observation = metric.get("source_observation") or {}
    return {
        "source_observation_state": observation.get("capture_state", "NOT_RECORDED"),
        "source_observed_at": observation.get("observed_at"),
        "source_retrieved_at": observation.get("retrieved_at"),
        "source_retrieval_age_days": observation.get("retrieval_age_days"),
        "source_reference_start": observation.get("reference_start"),
        "source_reference_end": observation.get("reference_end"),
        "source_currentness": observation.get("currentness", "NOT_ASSESSED"),
    }
