"""Run validated analysis in an owned resource-bound process and reconcile IDs."""
from __future__ import annotations

from asyncio import CancelledError
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shutil
import tempfile
import threading

import duckdb

from .analysis_contracts import normalized_request
from .analysis_state import record_comparison_failure, record_site_failure
from .analyze import get_site
from .bounded_process import ProcessLimits, ProcessOutcome, run_python_worker
from .comparison import get_comparison
from .database_admission import database_admission
from .execution_context import DatabaseLimits
from .paths import database_path, project_home
from .util import new_id

WORKER_SCRIPT = Path(__file__).with_name("analysis_worker.py")
MAX_REQUEST_BYTES = 262_144


@dataclass(frozen=True)
class AnalysisAttempt:
    operation_id: str
    site_ids: list[str]
    comparison_ids: list[str]


def prepare_attempt(operation: str, request: dict) -> AnalysisAttempt:
    _, count = normalized_request(operation, request)
    return AnalysisAttempt(new_id("analysis"), [new_id("site") for _ in range(count)],
                           [] if operation == "site" else [new_id("comparison")])


@dataclass
class AnalysisExecution:
    operation_id: str
    status: str
    site_ids: list[str]
    comparison_ids: list[str]
    result: dict | None
    process: ProcessOutcome | None
    database_cleanup_confirmed: bool
    saved_states: dict[str, str]
    database_settings: dict
    error_code: str | None = None


def _read_response(path: Path, operation_id: str, result_id: str) -> dict:
    try:
        with path.open("rb") as stream:
            content = stream.read(65_537)
        if len(content) > 65_536:
            return {}
        value = json.loads(content)
        if (not isinstance(value, dict) or value.get("schema") != 1 or value.get("operation_id") != operation_id
            or set(value) - {"schema", "operation_id", "status", "result_id", "database_settings", "error_type"}
            or value.get("status") not in {"SUCCEEDED", "FAILED"}
            or value.get("status") == "SUCCEEDED" and value.get("result_id") != result_id):
            return {}
        return value
    except (OSError, ValueError, UnicodeError):
        return {}


def _saved_states(conn, site_ids: list[str], comparison_ids: list[str]) -> dict[str, str]:
    states = {}
    for identifier in site_ids:
        row = conn.execute("SELECT status FROM site_query WHERE site_id=?", [identifier]).fetchone()
        states[identifier] = row[0] if row else "NOT_CREATED"
    for identifier in comparison_ids:
        row = conn.execute("SELECT status FROM site_comparison WHERE comparison_id=?", [identifier]).fetchone()
        states[identifier] = row[0] if row else "NOT_CREATED"
    return states


def execute_bounded_analysis(operation: str, request: dict, *, cancel: threading.Event | None = None,
                             process_limits: ProcessLimits | None = None,
                             database_limits: DatabaseLimits | None = None,
                             attempt: AnalysisAttempt | None = None) -> AnalysisExecution:
    """Internal service entry point. Transports cannot provide scripts or paths.

    Returned cancellation confirms process exit, not rollback of completed child
    analyses. A stopped process and database cleanup are separate observations.
    """
    normalized, count = normalized_request(operation, request)
    limits, database_policy = process_limits or ProcessLimits(), database_limits or DatabaseLimits()
    if database_policy.memory_mb > limits.memory_mb // 2:
        raise ValueError("The database buffer budget must leave memory for the worker libraries")
    reserved = attempt or prepare_attempt(operation, normalized)
    operation_id, sites, comparisons = reserved.operation_id, reserved.site_ids, reserved.comparison_ids
    if len(sites) != count or len(comparisons) != (0 if operation == "site" else 1):
        raise ValueError("Invalid internal analysis identity count")
    if cancel is not None and cancel.is_set():
        return AnalysisExecution(operation_id, "CANCELLED", sites, comparisons, None, None, True,
                                 {value: "NOT_CREATED" for value in sites + comparisons}, {})
    home, database = project_home(), database_path().resolve()
    envelope = {"schema": 1, "operation_id": operation_id, "operation": operation, "request": normalized,
                "home": str(home), "database": str(database), "site_ids": sites, "comparison_ids": comparisons,
                "database_limits": asdict(database_policy)}
    content = json.dumps(envelope, ensure_ascii=False).encode("utf8")
    if len(content) > MAX_REQUEST_BYTES:
        raise ValueError("Analysis request exceeds its encoded transport limit")
    expected = sites[0] if operation == "site" else comparisons[0]
    with database_admission(database):
        if not database.is_file():
            raise ValueError("Initialize the local database before analysis")
        cache = (home / "data/cache/analysis-workers").resolve()
        cache.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="owned-", dir=cache)).resolve()
        directory.relative_to(cache)
        stopped = False
        try:
            (directory / "spill").mkdir()
            (directory / "request.json").write_bytes(content)
            outcome = run_python_worker(WORKER_SCRIPT, [str(directory / "request.json")], limits=limits, cancel=cancel)
            stopped = outcome.process_stopped
            if not stopped:
                raise RuntimeError("WORKER_STOP_UNCONFIRMED")
            response = _read_response(directory / "response.json", operation_id, expected)
            status = outcome.reason if outcome.reason in {"CANCELLED", "TIMED_OUT"} else "FAILED"
            result, states, cleanup = None, {}, False
            error_code = "WORKER_STOPPED" if status != "FAILED" else "WORKER_RESULT_UNAVAILABLE"
            # The worker has exited and released DuckDB's file lock. Open only
            # the captured database, not a possibly changed process environment.
            try:
                with duckdb.connect(str(database)) as conn:
                    conn.execute("SET TimeZone='UTC'")
                    if outcome.returncode == 0 and response.get("status") == "SUCCEEDED":
                        result = get_site(conn, expected) if operation == "site" else get_comparison(conn, expected)
                        if (result and (operation == "site" or [row["site_id"] for row in result["sites"]] == sites)
                            and (operation != "assistant_comparison" or
                                result.get("assistant", {}).get("confirmed_intent") == normalized["intent"]
                                and result.get("assistant", {}).get("original_request") == normalized["original_request"])):
                            status, error_code, cleanup = "SUCCEEDED", None, True
                    if status != "SUCCEEDED":
                        failure = CancelledError() if status == "CANCELLED" else TimeoutError() if status == "TIMED_OUT" else RuntimeError()
                        for identifier in sites:
                            record_site_failure(conn, identifier, failure)
                        before = _saved_states(conn, sites, comparisons)
                        completed = [identifier for identifier in sites if before[identifier] == "SUCCEEDED"]
                        for identifier in comparisons:
                            record_comparison_failure(conn, identifier, completed, failure)
                        result = None
                    states = _saved_states(conn, sites, comparisons)
                    cleanup = all(value != "RUNNING" for value in states.values())
            except Exception:
                result, cleanup = None, False
                if status == "SUCCEEDED":
                    status = "FAILED"
                error_code = "DATABASE_RECONCILIATION_UNAVAILABLE"
            if status == "FAILED" and error_code == "WORKER_RESULT_UNAVAILABLE":
                error_type = response.get("error_type")
                if error_type in {"ValueError", "ValidationError"}:
                    error_code = "ANALYSIS_INPUT_OR_DATA_UNAVAILABLE"
                elif error_type in {"MemoryError", "OutOfMemoryException"}:
                    error_code = "ANALYSIS_MEMORY_LIMIT"
            return AnalysisExecution(operation_id, status, sites, comparisons, result, outcome, cleanup, states,
                                     response.get("database_settings", {}), error_code)
        finally:
            if stopped:
                # Only remove this newly created owned directory, and only
                # after actual worker exit. Preserve it if stopping is uncertain.
                directory.resolve().relative_to(cache)
                shutil.rmtree(directory)
