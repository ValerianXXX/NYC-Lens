# Windows worker ownership and recovery — 2026-10-31

A Windows virtual-environment launcher could start the actual interpreter before
the launcher joined the application's resource-limited Job. Stopping that launcher
was not proof that the interpreter holding DuckDB was owned and stopped. Separately,
the complete suite observed a database sharing error immediately after an HTTP
cancellation reported worker exit. These are concrete observations, not proof that
every historical cancellation failure had the same cause.

## Behavior and compatibility

Windows CPython virtual environments now launch the actual interpreter directly
using CPython's existing virtual-environment handoff. The child's environment must
match the parent's, and its start permission names its actual PID. A wrong PID,
legacy start frame, mismatched environment or failed resource guard cannot run the
payload. Time/memory limits, ownership checks and original cancellation assertions
remain. There is no shell, arbitrary client command, new dependency or framework.

After confirmed worker exit, recovery checks the captured database's Windows
sharing availability within a one-second retry window, while holding the existing
database admission. It opens only an existing file and writes no bytes. A transient
sharing violation can settle; persistent sharing, permission and other errors remain
failures. It never reruns the analysis or changes a failed task into success. A new
external opener can still race the final DuckDB connection, which must succeed on
its own. This is not a hard OS-I/O deadline for an unresponsive network filesystem.

Completed child results stay saved. Incomplete children and comparisons receive the
original cancellation/timeout state only when database reconciliation succeeds.
Unrelated records remain untouched. Receipts continue to distinguish process exit
from confirmed database cleanup. HTTP, CLI and MCP formats are unchanged. The
bootstrap's new PID/prefix handshake is internal and must be upgraded with its
supervisor; mixed versions fail closed. No schema/data migration is needed.

## Evidence

The final frozen cases are commit `6fdd0527c9d85c3d8569761aa5423fad2822e68f`.
The runtime is `deae418552c4e251f665b4a69cbbac5e30f22e27`.

| Observation | Result and scope |
| --- | --- |
| Frozen cases before repair | 1 passed / 3 failed, 9.66 s. Two transient native-sharing checks failed recovery; actual interpreter ownership failed. Persistent sharing correctly remained a failure. |
| Identical frozen cases after repair | 4 passed, 9.24 s. The test files are unchanged. This is correctness evidence, not a speed or cost comparison. |
| Actual ownership reproduction | With delayed assignment, launcher PID 30920 differed from interpreter PID 34932; `IsProcessInJob(actual, ownedJob)` was false before repair. The repaired actual interpreter belongs to the owned Job, retains the venv, and has exited after cancellation. |
| File-sharing cases | A real separate process holds a real synthetic DuckDB file with native Windows sharing rules. The analysis-worker outcome is explicitly a test double; these three cases alone are not end-to-end cancellation acceptance. |
| Related application checks | 80 passed, 461.39 s. Actual worker, HTTP cancellation, the installed MCP SDK's in-process client cancellation, CLI, recovery and three invalid-start checks. This set excludes the four frozen cases above. |
| Actual desktop browser | Prepare and confirm two synthetic addresses, wait for the second actual query, cancel, reload, switch to Chinese and check the same task. First child remains readable (HTTP 200); incomplete comparison is unavailable (existing HTTP 503 contract). One saved job remains; no automatic retry. Database states are `SUCCEEDED / CANCELLED / CANCELLED`, cleanup is confirmed, and owned worker directories are gone. |
| Complete local regression | Not yet accepted for this repair. Earlier failures remain recorded in the path milestone. |
| Hosted compatibility | The workflow now includes actual venv workers on Python 3.11, 3.12 and 3.13, plus the existing Linux/full and Windows contract jobs. A configured matrix is not a passing result. |

The first fixture attempt had three PID-identity setup failures. The corrected
fixture explicitly launched its stdlib-only file holder with the base interpreter,
kept the strict PID assertion and added the actual-worker ownership case. Its next
attempt also exposed a cleanup race: terminating an already exiting interpreter
can fail even though it subsequently exits. The final fixture requires actual exit
through its retained handle and always closes that handle. Both earlier failed logs
are retained alongside the final before/after logs; they are not product gains.

The original legacy MCP failure did not retain its immediate database exception or
identify a file holder. The later HTTP sharing exception and the independently
reproduced ownership defect support this repair, without retroactively identifying
the original holder. The previous ten-second HTTP timeout failure with no rows yet
created is also a distinct observation; its deadline and expected states are intact.

No real geographic conclusions, model quality/cost gain, new model call, real
Hermes-client run or independent-user acceptance is claimed here.

[Verification receipt and original redacted logs](../reports/evaluations/2026-10-31/windows-worker-recovery/verification.json)
retain source hashes, the actual ownership observation and previous fixture failures.
The [browser screenshot](../reports/evaluations/2026-10-31/windows-worker-recovery/browser-cancelled-zh.jpg)
shows the original cancelled task after reload. This browser run uses the existing
test-only worker that deliberately holds the second actual query. The first read-only
browser verification script incorrectly expected HTTP 409 at the result endpoint;
the pre-existing endpoint/test contract is HTTP 503 with `ANALYSIS_RESULT_NOT_READY`.
Its corrected script also verifies that no result is returned. The initial error,
script and correction are retained; no product status or existing test was changed.

## Reproduce

Use the existing dependencies in a CPython virtual environment:

```console
python -m venv .venv
# Activate .venv using your shell, then:
python -m pip install -e ".[dev,agent]"
python -m pytest tests/unit/test_windows_worker_identity.py tests/unit/test_database_release_reconciliation.py tests/unit/test_worker_start_identity.py
python -m pytest tests/unit/test_analysis_jobs.py tests/unit/test_legacy_http_analysis.py tests/unit/test_bounded_cli.py
```

The native sharing/Job tests require Windows and skip on Linux; portable startup,
worker and application checks also run on Linux. The data is synthetic and isolated.
Normal operation needs no new configuration flag or credential; `.env.example`
remains applicable. Non-CPython Windows venvs fail closed, and macOS still lacks a
supported resource guard. Store/embedded/third-party interpreter distributions,
UNC shares and independent machines are not established by the local CPython 3.11.3
and DuckDB 1.5.6 result.

## Release and rollback

This is a reviewed source candidate, not a production installation. Stop active
analysis and inspect its saved receipt before updating supervisor/bootstrap together.
Back up the catalog, referenced generations/manifests and receipts as a coherent
set. A reviewed code revert restores the former launcher/instant-open behavior and
its limitations. It does not undo saved analyses or clear an unresolved receipt.
Do not remove database/WAL files, kill unrelated processes or edit a success flag
to recover. Preserve unconfirmed states for explicit inspection; no automatic
analysis retry, data deletion or production migration was performed.

Official implementation references checked 2026-11-01 UTC (2026-10-31 local):
[CPython 3.11 launcher](https://github.com/python/cpython/blob/v3.11.3/PC/launcher.c)
and [CPython 3.13 path initialization](https://github.com/python/cpython/blob/v3.13.0/Modules/getpath.py)
describe the existing venv handoff. This uses that behavior without copying their
implementation. [Microsoft Job assignment](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)
describes descendant membership; assigning a parent after a descendant exists is
not evidence that the earlier descendant joined that Job.
[CreateFileW sharing rules](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
and [DuckDB 1.5.6 file opening](https://github.com/duckdb/duckdb/blob/v1.5.6/src/common/local_file_system.cpp)
inform the narrow, non-mutating sharing probe. Supported-version behavior still
requires the actual tests above.
