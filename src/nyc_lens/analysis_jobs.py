"""Local durable job receipts, idempotent starts and explicit cancellation.

The journal stores identities and hashes, not addresses or model input. Refresh
reattaches to the same attempt. Restart recovery never starts another analysis.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

import anyio
import duckdb

from .analysis_contracts import normalized_request
from .analysis_execution import AnalysisAttempt, _saved_states, execute_bounded_analysis, prepare_attempt
from .analysis_state import record_comparison_failure, record_site_failure
from .analyze import get_site
from .comparison import get_comparison
from .database_admission import DatabaseBusyError, database_admission
from .paths import database_path, project_home
from .util import utc_now

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "INTERRUPTED"}
_active: dict[str, tuple[threading.Thread, threading.Event]] = {}
_active_lock = threading.Lock()


class JobUnavailableError(RuntimeError):
    pass


class AnalysisFailedError(JobUnavailableError):
    """A terminal job with a durable receipt, distinct from admission failure."""
    def __init__(self, receipt: dict):
        self.receipt = dict(receipt)
        super().__init__(f"{receipt['status']}: {receipt['error_code']}; job={receipt['job_id']}")


class _Cancellation(threading.Event):
    def __init__(self, marker: Path):
        super().__init__()
        self.marker = marker

    def is_set(self):
        return super().is_set() or self.marker.exists()

    def wait(self, timeout=None):
        super().wait(timeout)
        return self.is_set()


class AnalysisJobs:
    def __init__(self):
        self.database, self.home = database_path().resolve(), project_home()
        namespace = hashlib.sha256(str(self.database).encode()).hexdigest()[:16]
        self.directory = self.home / "data/cache/analysis-jobs" / namespace
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, identifier: str) -> Path:
        if not re.fullmatch(r"analysis_[0-9a-f]{32}", identifier):
            raise KeyError("Unknown analysis job")
        return self.directory / (identifier + ".json")

    def _read(self, identifier: str) -> dict:
        path = self._path(identifier)
        try:
            with path.open("rb") as stream:
                raw = stream.read(65_537)
        except FileNotFoundError:
            raise KeyError("Unknown analysis job") from None
        try:
            value = json.loads(raw)
            if (len(raw) > 65_536 or value["job_id"] != identifier or value["schema"] != 1 or
                value["operation"] not in {"site", "comparison", "assistant_comparison"} or
                value["status"] not in TERMINAL | {"RUNNING"} or
                not isinstance(value["site_ids"], list) or not isinstance(value["comparison_ids"], list) or
                not 1 <= len(value["site_ids"]) <= 20 or len(value["comparison_ids"]) > 1):
                raise ValueError("Invalid receipt")
            for kind in ("site", "comparison"):
                if not all(re.fullmatch(kind + r"_[0-9a-f]{32}", item) for item in value[kind + "_ids"]):
                    raise ValueError("Invalid reserved identities")
        except (ValueError, TypeError, KeyError):
            raise JobUnavailableError("JOB_RECEIPT_INVALID") from None
        return value

    def _write(self, receipt: dict, *, create=False):
        path = self._path(receipt["job_id"])
        # Only the job lease owner writes receipts. Cancellation uses a separate
        # marker, so it cannot overwrite a concurrently completed receipt.
        target = path if create else path.with_suffix(".tmp")
        with target.open("x" if create else "w", encoding="utf8") as stream:
            json.dump(receipt, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        if not create:
            os.replace(target, path)

    def start(self, operation: str, request: dict, *, request_id: str) -> dict:
        if os.name != "nt" and sys.platform != "linux":
            raise JobUnavailableError("RESOURCE_GUARD_UNAVAILABLE")
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("A 32-character lowercase request identifier is required")
        normalized, _ = normalized_request(operation, request)
        identifier = "analysis_" + request_id
        path = self._path(identifier)
        fingerprint = hashlib.sha256(json.dumps([operation, normalized], sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        with database_admission(path.with_suffix(".start")):
            if path.exists():
                existing = self._read(identifier)
                if existing["request_sha256"] != fingerprint:
                    raise ValueError("The request identifier already belongs to a different request")
                return self.get(identifier)
            if sum(1 for _ in self.directory.glob("analysis_*.json")) >= 2000:
                raise JobUnavailableError("JOB_HISTORY_CAPACITY_REQUIRES_ARCHIVE")
            attempt = prepare_attempt(operation, normalized)
            attempt = AnalysisAttempt(identifier, attempt.site_ids, attempt.comparison_ids)
            receipt = {"schema": 1, "job_id": identifier, "operation": operation, "status": "RUNNING",
                       "request_sha256": fingerprint, "site_ids": attempt.site_ids, "comparison_ids": attempt.comparison_ids,
                       "created_at": str(utc_now()), "completed_at": None, "result_id": None,
                       "process": None, "database_cleanup_confirmed": False, "saved_states": {}, "error_code": None}
            ready, cancel, startup_errors = threading.Event(), _Cancellation(path.with_suffix(".cancel")), []
            thread = threading.Thread(target=self._run, args=(receipt, normalized, attempt, cancel, ready, startup_errors),
                                      name="nyc-analysis-" + request_id[:8], daemon=True)
            with _active_lock:
                if any(key.startswith(str(self.directory) + os.sep) for key in _active):
                    raise DatabaseBusyError()
                _active[str(path)] = (thread, cancel)
            thread.start()
            if not ready.wait(5):
                cancel.set()
                raise JobUnavailableError("JOB_START_UNCONFIRMED")
            if startup_errors:
                raise JobUnavailableError("JOB_START_FAILED")
        return self.get(identifier)

    def _run(self, receipt, request, attempt, cancel, ready, startup_errors):
        path = self._path(receipt["job_id"])
        try:
            with database_admission(path.with_suffix(".lease")):
                self._write(receipt, create=True)
                ready.set()
                try:
                    if project_home() != self.home or database_path().resolve() != self.database:
                        raise JobUnavailableError("RUNTIME_CONFIGURATION_CHANGED")
                    result = execute_bounded_analysis(receipt["operation"], request, cancel=cancel, attempt=attempt)
                    receipt.update(status=result.status, process=asdict(result.process) if result.process else None,
                        database_cleanup_confirmed=result.database_cleanup_confirmed, saved_states=result.saved_states,
                        database_settings=result.database_settings, error_code=result.error_code,
                        result_id=(result.comparison_ids or result.site_ids)[0] if result.status == "SUCCEEDED" else None)
                except BaseException as error:
                    receipt.update(status="FAILED", error_code="DATABASE_BUSY" if isinstance(error, DatabaseBusyError) else "WORKER_START_OR_STOP_UNCONFIRMED")
                    if isinstance(error, DatabaseBusyError):
                        receipt.update(database_cleanup_confirmed=True,
                            saved_states={item: "NOT_CREATED" for item in attempt.site_ids + attempt.comparison_ids})
                receipt["completed_at"] = str(utc_now())
                self._write(receipt)
        except BaseException as error:
            startup_errors.append(type(error).__name__)
        finally:
            with _active_lock:
                _active.pop(str(path), None)
            ready.set()

    def get(self, identifier: str) -> dict:
        receipt = self._read(identifier)
        path = self._path(identifier)
        if receipt["status"] not in TERMINAL:
            try:
                with database_admission(path.with_suffix(".lease")):
                    receipt = self._read(identifier)
                    if receipt["status"] not in TERMINAL:
                        # Acquiring this lease proves there is no supervisor
                        # owning this journal. Do not kill an old or reused PID.
                        with database_admission(self.database), duckdb.connect(str(self.database)) as conn:
                            failure = SystemExit("Lost analysis supervisor")
                            for item in receipt["site_ids"]:
                                record_site_failure(conn, item, failure)
                            states = _saved_states(conn, receipt["site_ids"], receipt["comparison_ids"])
                            completed = [item for item in receipt["site_ids"] if states[item] == "SUCCEEDED"]
                            for item in receipt["comparison_ids"]:
                                record_comparison_failure(conn, item, completed, failure)
                            states = _saved_states(conn, receipt["site_ids"], receipt["comparison_ids"])
                        receipt.update(status="INTERRUPTED", completed_at=str(utc_now()), saved_states=states,
                            database_cleanup_confirmed=all(value != "RUNNING" for value in states.values()),
                            error_code="SUPERVISOR_EXIT_PROCESS_NOT_OBSERVED")
                        self._write(receipt)
            except DatabaseBusyError:
                pass  # The lease is live, or another operation still owns DB admission.
            except duckdb.Error:
                receipt["error_code"] = "DATABASE_RECOVERY_PENDING"
            if receipt["status"] not in TERMINAL and path.with_suffix(".cancel").exists():
                receipt = {**receipt, "status": "CANCELLING"}
        return receipt

    def cancel(self, identifier: str) -> dict:
        receipt = self.get(identifier)
        if receipt["status"] not in TERMINAL:
            self._path(identifier).with_suffix(".cancel").touch(exist_ok=True)
        return self.get(identifier)

    def result(self, identifier: str) -> dict:
        receipt = self.get(identifier)
        if receipt["status"] != "SUCCEEDED":
            raise JobUnavailableError("ANALYSIS_RESULT_NOT_READY")
        expected = (receipt["comparison_ids"] or receipt["site_ids"])[0]
        if expected != receipt["result_id"]:
            raise JobUnavailableError("JOB_RESULT_ID_MISMATCH")
        with database_admission(self.database), duckdb.connect(str(self.database), read_only=True) as conn:
            result = get_site(conn, expected) if receipt["operation"] == "site" else get_comparison(conn, expected)
        if result is None:
            raise JobUnavailableError("SAVED_RESULT_UNAVAILABLE")
        return result


def run_analysis_sync(operation: str, request: dict, *, request_id: str | None = None) -> dict:
    """Bounded blocking entry point for CLI/library callers without a DB handle.

    The same durable jobs own execution, admission and saved results. Reusing a
    request ID reattaches, including to a failed attempt; it never retries it.
    """
    from uuid import uuid4
    jobs = AnalysisJobs()
    key = request_id if request_id is not None else uuid4().hex
    identifier = "analysis_" + key
    try:
        receipt = jobs.start(operation, request, request_id=key)
        while receipt["status"] not in TERMINAL:
            time.sleep(.05)
            receipt = jobs.get(identifier)
        if receipt["status"] != "SUCCEEDED":
            raise AnalysisFailedError(receipt)
        return jobs.result(identifier)
    except KeyboardInterrupt:
        # Interruption can also arrive while the supervisor is creating its
        # receipt. Wait only for this request, and never assume cleanup worked.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                receipt = jobs.cancel(identifier)
                if receipt["status"] in TERMINAL:
                    break
            except (KeyError, JobUnavailableError, DatabaseBusyError):
                pass
            time.sleep(.05)
        raise


async def run_analysis_async(operation: str, request: dict, *, request_id: str | None = None) -> dict:
    """Transport cancellation requests cleanup; stable IDs reattach without reruns."""
    from uuid import uuid4
    jobs = AnalysisJobs()
    receipt = jobs.start(operation, request, request_id=request_id if request_id is not None else uuid4().hex)
    identifier = receipt["job_id"]
    try:
        while receipt["status"] not in TERMINAL:
            await anyio.sleep(.05)
            receipt = await anyio.to_thread.run_sync(jobs.get, identifier)
        if receipt["status"] != "SUCCEEDED":
            raise AnalysisFailedError(receipt)
        return await anyio.to_thread.run_sync(jobs.result, identifier)
    except anyio.get_cancelled_exc_class():
        with anyio.CancelScope(shield=True):
            jobs.cancel(identifier)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                receipt = await anyio.to_thread.run_sync(jobs.get, identifier)
                if receipt["status"] in TERMINAL:
                    break
                await anyio.sleep(.05)
        raise


def stop_active_jobs() -> None:
    with _active_lock:
        active = list(_active.values())
    for _, cancel in active:
        cancel.set()
    deadline = time.monotonic() + 7
    for thread, _ in active:
        thread.join(max(0, deadline - time.monotonic()))
