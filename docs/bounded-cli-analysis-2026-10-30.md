# Bounded CLI analysis and task recovery

This development change started October 30, 2026 and includes the October 31
receipt-concurrency correction. It is stacked on the legacy HTTP repair and
changes execution/recovery, not spatial calculations or dataset claims.

## Behavior

`analyze`, `compare`, `samples` and `regional-samples` previously ran the engine
inside the CLI process. They now use the same durable `AnalysisJobs` supervisor
and owned worker as the Web and MCP routes. Successful result JSON retains its
existing shape. Export runs only after analysis succeeds, using its original
write-capable database session because existing exports update derived records.

The existing worker limits apply: 120 seconds; 2048 MiB Windows Job committed
memory or Linux address-space guard; DuckDB 512 MiB buffer, two threads and
1024 MiB spill. Windows and Linux memory semantics differ. None of these limits
can be changed with a model/tool argument. The full dataset has not calibrated
these defaults. macOS has no guard and these CLI analysis commands now fail
closed, matching Web/MCP; they do not silently use the old unbounded path.

The synchronous library entry point is `nyc_lens.analysis_jobs.run_analysis_sync`.
It takes an operation and validated request data, not a connection, script, SQL
or filesystem path. Async callers retain `run_analysis_async`. Both share the
same supervisor, journal, admission lock, result validation and failure states.
The lower-level `analyze_site(conn, ...)` / `compare_sites(conn, ...)` functions
remain engine primitives used inside the worker; they are not newly sandboxed.

## Run, inspect and recover

Use a local home with its configured data. A new analysis request retains the
previous CLI database initialization and source-registration behavior. Reattaching
to an existing request skips that step, so it does not reopen an active worker's
database or replace source metadata merely to read an earlier result. The
standalone `nyc-lens init` command remains available for explicit setup.

```powershell
$requestId = [guid]::NewGuid().ToString('N')
nyc-lens analyze --address "1501 Broadway, New York, NY 10036" --catchments 500m --no-export --request-id $requestId
nyc-lens analysis-status --job-id "analysis_$requestId"
nyc-lens analysis-result --job-id "analysis_$requestId"
```

The real address above requires the corresponding local data; it is not an
observed real-location result. For an existing seeded synthetic home, use only
the two `DEMO_ADDRESSES` and geometric catchments such as `500m`.

`analyze` and `compare` accept an optional request ID of exactly 32 lowercase
hexadecimal characters. Preserve it before starting work. Repeating identical
inputs under that ID returns the original result or terminal failure without
another analysis. Different inputs under the same ID are rejected. Omitting the
ID creates a new request each invocation; this does not deduplicate repeated
shell commands. Request IDs are also printed to stderr, leaving stdout as JSON.

From another terminal using the same home/database:

```shell
nyc-lens analysis-cancel --job-id analysis_REPLACE_WITH_REQUEST_ID
nyc-lens analysis-status --job-id analysis_REPLACE_WITH_REQUEST_ID
```

Cancellation can first return `CANCELLING`. Inspect the terminal receipt's
`process.process_stopped` and `database_cleanup_confirmed`; a cancellation request
alone does not establish cleanup. An interrupted comparison preserves an already
successful first site while the second site and comparison remain incomplete.
`analysis-result` refuses an incomplete task. Native Ctrl+C triggers a bounded
cleanup attempt; the CLI exits 130 and the receipt remains the source of truth.
A second interrupt or forcibly closed console can prevent the wait from finishing.

Structured execution failures exit 1, invalid analysis input exits 2, and an
interrupted CLI wait exits 130. Failures expose fixed codes and a task identity,
not arbitrary upstream exception text. Starting a new ID after failure is an
explicit new attempt; no automatic retry is performed.

Sample commands remain sequential and emit one task ID per sample. They are not
an atomic resumable batch: restarting the whole command creates new requests.
Recover completed individual tasks using their recorded IDs. Export remains
outside the worker and may run again when explicitly requesting exports from a
completed analysis. `analysis-result` reads the saved result without re-exporting.

For a synchronous library caller, after initializing the selected home:

```python
from uuid import uuid4
from nyc_lens.analysis_jobs import run_analysis_sync
from nyc_lens.demo import DEMO_ADDRESSES

# Only valid in an already seeded isolated synthetic home.
result = run_analysis_sync("comparison", {
    "addresses": DEMO_ADDRESSES,
    "catchments": ["500m"],
    "comparison_catchment": "500m",
}, request_id=uuid4().hex)
```

No new dependency, model option or environment variable is required.

## Verification and retained failures

Twelve contracts were frozen at `53f4a8d` before product changes. All twelve
failed initially. Eleven failures identify absent command/library recovery or
missing worker receipts. The original `samples` baseline instead stopped at the
synthetic demo's walking-area prohibition before reaching its receipt assertion;
that case does not establish an additional resource regression. Its test-only
bounded request uses `500m`, and no real walking data or external query is used.
The three pre-existing readable API error tests passed before editing.

The first implementation passed ten contracts and failed two sample exports:
read-only export sessions were incompatible with existing derived-record writes.
The code restores the original write-capable export behavior. The next expanded
run passed thirteen and failed one new export assertion because it incorrectly
expected an HTML key in comparison's existing JSON/CSV/XLSX output. The harness
now checks the shared JSON report's synthetic label; no export capability or
original frozen assertion was removed. Both export checks then passed in 11.00
seconds. Original failure logs are retained by hash.

The first full regression at `da3d616` passed 194 tests, with three full-data
skips and 90 deprecation warnings, in 238.41 seconds. A subsequent compatibility
check found that skipping all existing-database initialization stopped new CLI
requests from refreshing source registration. A new frozen contract at `8990da8`
changes only synthetic source metadata: a new request must register it, a replay
must retain the prior registration/result, and another new ID must adopt the
next metadata. After correcting the test's initial `.yaml`/`.yml` filename
mistake, it reproduced the actual stale registration. The product fix at
`68c3bee` passes that contract in 10.16 seconds. Both initial harness failure and
corrected failure remain recorded; neither is relabeled as a passing run.

The next full run had one failed MCP business-flow test, 194 passes, three skips
and 90 warnings in 243.19 seconds. Its receipt read raised `PermissionError`
during concurrent publication. This shared-path failure was investigated rather
than retried until a pass appeared. Five contracts frozen at `199b9d7` all fail
before the repair, including a real held Windows handle that prevents replacing
the receipt. Transient read/replace failures and permanent denial are separate
cases; permanent denial must preserve the old receipt and remain unavailable.

`receipt_io.py` now uses the existing OS-lock primitive for complete reads,
initial creation and replacement. The supervisor lease remains the sole writer
authority. Local receipt I/O can retry within one second, but analysis is never
restarted. Persistent contention/access failure becomes `JOB_RECEIPT_UNAVAILABLE`,
not missing data or success. Unknown IDs do not create extra lock files. This
retry window does not interrupt an arbitrary blocking filesystem operation.

The five unchanged contracts and the originally failed MCP workflow pass in
18.56 seconds. A separate-process held-reader test also passes in 3.77 seconds:
the writer waits for the reader to close, the reader sees the original complete
receipt and the next read sees the new complete receipt. That added fixture
initially failed because its isolated child home lacked the public configuration
files required at import; copying those static files fixes the fixture without
changing assertions or adding a database. Both diagnostic failures are retained.

The design follows the documented distinction between Windows read/write and
delete/rename sharing access, checked October 31 in the official
[CreateFileW reference](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew).
The repair copies no external source, changes no ACL and does not terminate an
unrelated process to obtain a file handle. The [Python file-operation reference](https://docs.python.org/3.11/library/os.html#os.replace)
also permits access failures; atomic replacement is not treated as infallible.

The tests exercise actual Windows owned workers and isolated synthetic DuckDB
data, including the effective database budgets, two-address values 1 and 3,
stable-ID replay, changed-payload rejection and report-file creation. A separate
real CLI process observes and cancels another CLI's held second-site SQL query;
the first site survives, the worker stops and a replay returns the same failure.
The KeyboardInterrupt scenario injects the exception while that real worker is
active. This tests cleanup behavior, not a human pressing Ctrl+C in a native
console. No independent participant or new Hermes/model workflow was observed.

Full regression results and portable source/log hashes are recorded in the
[verification receipt](../evaluations/bounded-cli-analysis-2026-10-30.json).
The final full local run at `b9bf8f4` passes **201 tests**, with three full-data
skips and 90 deprecation warnings, in **253.77 seconds**. It includes all original
CLI contracts, source-registration recovery and receipt-I/O checks. The missing
populated dataset still prevents those three integration checks from running.
CI runs the new CLI contracts in its existing Windows process job as well as
the Ubuntu full suite. Hosted results are reported separately on the draft PR.
No model-quality, full-data latency or business-location improvement is claimed.

```shell
python -m pytest tests/unit/test_bounded_cli.py -q
python -m pytest
```

## Remaining work and rollback

Ingestion, export generation, arbitrary low-level library calls and other read
endpoints do not inherit these analysis limits. Full-data/freshness acceptance,
atomic dataset generations, external-writer coordination, whole-database/report
disk quotas and a macOS guard remain open. Real terminal signal behavior and
independent user acceptance still need observation.

No schema migration, populated-data modification or production action occurs
in this milestone. Before an approved source rollback, cancel/wait for owned
tasks and preserve the database, configuration, source evidence and job journals.
Stop older processes before switching versions: an older reader/writer does not
participate in the new receipt I/O lock. Do not unlink stable lock files to try
to unlock another process.
Use an ordinary reviewed revert; do not delete task history. Prior code can read
saved data but lacks these CLI flags and bounded CLI behavior. No default-branch
merge or public release has been performed.
