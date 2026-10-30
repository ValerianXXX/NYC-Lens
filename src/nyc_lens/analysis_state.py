"""Terminal status belongs to one exact analysis attempt, never an address."""
from __future__ import annotations

from asyncio import CancelledError
from typing import Any

from .util import json_dumps, utc_now


def record_site_failure(conn: Any, site_id: str, error: BaseException) -> None:
    _record_failure(conn, site_id, error, comparison=False)


def record_comparison_failure(conn: Any, comparison_id: str, site_ids: list[str], error: BaseException) -> None:
    _record_failure(conn, comparison_id, error, comparison=True, site_ids=site_ids)


def _record_failure(conn: Any, identifier: str, error: BaseException, *, comparison: bool,
                    site_ids: list[str] | None = None) -> None:
    status = ("CANCELLED" if isinstance(error, (KeyboardInterrupt, CancelledError)) else
              "TIMED_OUT" if isinstance(error, TimeoutError) else
              "FAILED" if isinstance(error, Exception) else "INTERRUPTED")
    kind = "comparison" if comparison else "analysis"
    values = [status, utc_now(), json_dumps([f"{kind}_error:{type(error).__name__}"])]
    try:
        if comparison:
            conn.execute("""UPDATE site_comparison SET status=?, completed_at=?, warnings_json=?::JSON,
                result_json=NULL, site_ids_json=?::JSON
                WHERE comparison_id=? AND status='RUNNING'""",
                [*values, json_dumps(site_ids or []), identifier])
        else:
            conn.execute("""UPDATE site_query SET status=?, completed_at=?, warnings_json=?::JSON, result_json=NULL
                WHERE site_id=? AND status='RUNNING'""", [*values, identifier])
    except BaseException:
        # Preserve the original failure even with a broken connection or aborted
        # transaction. Do not falsely claim that terminal persistence succeeded.
        error.add_note("The terminal status could not be persisted; inspect the original attempt before manual retry.")
