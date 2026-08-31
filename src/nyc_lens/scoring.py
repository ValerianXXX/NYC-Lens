from __future__ import annotations

from typing import Any

import duckdb

from .config import load_yaml
from .paths import config_dir


class ScoringNotCalibratedError(RuntimeError):
    pass


def scoring_config() -> dict[str, Any]:
    return load_yaml(config_dir() / "scoring.yml")


def scoring_status(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    config = scoring_config()
    active = conn.execute(
        """
        SELECT model_id, business_type, training_case_count, holdout_case_count,
               validation_metrics_json, activated_at, method_version
        FROM scoring_model
        WHERE status='ACTIVE'
        ORDER BY activated_at DESC
        """
    ).fetchdf().to_dict("records")
    if not active:
        return {
            "status": "BLOCKED_CALIBRATION",
            "active_models": [],
            "minimum_requirements": config["minimum_requirements"],
            "permitted_before_calibration": config["permitted_before_calibration"],
            "forbidden_before_calibration": config["forbidden_before_calibration"],
            "reason": "No user-approved real completed-project outcome set and holdout validation report are present; NYC Lens will not fabricate a composite score.",
        }
    return {
        "status": "ACTIVE",
        "active_models": active,
        "minimum_requirements": config["minimum_requirements"],
    }


def score_site(conn: duckdb.DuckDBPyConnection, site_id: str) -> dict[str, Any]:
    status = scoring_status(conn)
    if status["status"] != "ACTIVE":
        raise ScoringNotCalibratedError(status["reason"])
    if conn.execute("SELECT 1 FROM site_query WHERE site_id=?", [site_id]).fetchone() is None:
        raise KeyError(site_id)
    raise ScoringNotCalibratedError(
        "An active model registry row exists, but executable model activation is intentionally unavailable until its artifact and calibration report pass final QA."
    )
