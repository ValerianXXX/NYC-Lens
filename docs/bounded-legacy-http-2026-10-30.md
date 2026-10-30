# Bounded legacy HTTP analysis

This 30 October 2026 source milestone closes a resource-boundary gap in the public
API. It does not deploy anything, modify the populated dataset or establish new
model accuracy, client acceptance or real-location conclusions.

## Behavior and architecture

Previously `/api/site-analysis`, `/api/site-comparison` and
`/api/assistant/compare` invoked the query engine directly in the serving
process. They now wait asynchronously for the same `AnalysisJobs` execution used
by Web tasks and MCP tools. Numeric computations and saved result formats still
come from the existing engine; no model-generated numbers or new scoring system
are introduced. A completed first site survives interruption of the second.

The existing limits apply: 120 seconds per worker, 2048 MiB Windows Job committed
memory or Linux `RLIMIT_AS`, and DuckDB 512 MiB buffer, two threads and 1024 MiB
spill. Windows and Linux limits have different semantics. Public inputs cannot
change these defaults, provide a script, execute SQL or choose filesystem paths.
The shared site/comparison request schemas reject unknown fields, unsupported
catchments, oversized addresses and incompatible comparison scopes. The
assistant confirmation gate remains required.

All analysis/planning POST routes now enforce the existing 262,144-byte body
limit before JSON parsing, including `/api/assistant/interpret`. Bounding that
body does not place the interpretation provider call inside an analysis worker.
No provider configuration, model, dependency or billing option is changed.

## Client contract

The three compatibility endpoints still return the saved analysis JSON on
success. The response also has `X-Analysis-Job-Id`. A caller can supply an optional
`Idempotency-Key` containing exactly 32 lowercase hexadecimal characters:

```python
import json
import urllib.request
from uuid import uuid4
from nyc_lens.demo import DEMO_ADDRESSES

key = uuid4().hex  # Keep this same key for recovery of this exact request.
payload = {"address": DEMO_ADDRESSES[0], "catchments": ["500m"]}
request = urllib.request.Request(
    "http://127.0.0.1:8766/api/site-analysis",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "Idempotency-Key": key},
)
with urllib.request.urlopen(request, timeout=140) as response:
    print(response.headers['X-Analysis-Job-Id'])
    result = json.load(response)
```

After a lost response, inspect `/api/analysis-jobs/analysis_{key}` or resubmit the
identical operation/payload/key. This reattaches to the original job, including
its terminal failure; it does not rerun it. Reusing a key for different input
returns 422. Omitting a key retains independent-request semantics: the server
generates a new identity, so a retry after losing the response can create a new
analysis. Prefer the explicit job API for interactive start/status/cancel flows.

The status/cancel/result routes work with the returned identifier. A timeout
returns 504, cancellation 409, input/data failure 422, and other execution or
resource failures 503. Terminal failures include the durable job ID, saved status
and database-cleanup confirmation. Admission failures remain distinct. A
cancelled transport requests the shared cancellation path; an ordinary socket
disconnect is not claimed to automatically cancel work. A caller can recover by
its key and cancel explicitly. No automatic rerun is introduced.

Upstream HTTP exceptions cross the worker boundary as an allowlisted category.
Original exception text, service response bodies and credentials are not copied
into the error response or job receipt. A retained successful child result does
not convert an interrupted comparison into success.

## Verification

The existing three API error contracts passed before modification. Seventeen new
contracts were frozen at `68648f3` before implementation: all failed against the
direct-route baseline in 34.09 seconds. The same seventeen then passed in 45.63
seconds without changing assertions. The successful requests run actual owned
workers against isolated synthetic DuckDB data, with effective budgets read from
the child connection. Repeated keys produce identical results and only the
original site/comparison rows. Both comparison routes preserve competitor values
**1 and 3**; these are synthetic fixture values, not real business conclusions.

The timeout test holds the second site's actual SQL query inside a trusted test
worker with a ten-second deadline. The process exits, the first site remains
successful, and the second site/comparison become `TIMED_OUT`. Repeating the same
key returns that failure and leaves row counts unchanged. Changing the payload
under that key is rejected. No live service is slowed or accessed.

A separate actual-worker failure fixture raises an HTTP exception without any
network access. Its safe category reaches the HTTP response and its original
text does not escape; the process is stopped and no analysis rows are invented.
This new check and the three existing readable-error contracts pass in 5.60
seconds. The two existing analysis-error fixtures now inject at the job boundary;
all original status/message assertions remain. That is a test seam update, not
removal of failure coverage.

The first complete run had one failure, 179 passes and three full-data skips
in 161.44 seconds. Its configuration-change test injected an edit in the serving
process, which the new child could not inherit. A private test worker now makes
that same between-site edit in the isolated synthetic home. Original numeric,
configuration-hash, historical-report and restore assertions remain; added
checks confirm that the edit happened after the first site in another PID.
The original failing log is retained rather than relabeled as a pass.

The focused repaired configuration test passed in 10.88 seconds. The final
complete local suite at `5f2547e` passed **180 tests, with three full-data skips
and 90 deprecation warnings, in 165.50 seconds**. The full dataset remains
unavailable; synthetic results do not substitute for those skipped checks.

The [receipt](../evaluations/bounded-legacy-http-2026-10-30.json) records the full
suite result, code revision and log hashes. Hosted Ubuntu full-suite and Windows
process/HTTP checks remain separately reported on the PR. There is no new browser
session, physical device, real Hermes model call or independent participant
acceptance for this HTTP-only change. Existing UI assets were unchanged.

## Remaining limits and rollback

CLI/direct library analysis, ingestion, exports and other read endpoints do not
inherit this worker boundary. Full-data latency/memory and freshness, atomic data
generations, external-writer coordination and whole-database/report disk quotas
remain open. macOS has no implemented guard and refuses these bounded analysis
routes; the existing direct CLI/library remains available without this claim.
The 120-second default has not been calibrated on the full local dataset.

No schema migration or production action is required for this source change.
Before rollback, cancel/wait for owned tasks, stop the service, and preserve the
database, source evidence, configuration and job journals. Revert these commits
or restore the prior checkout without deleting task history. The prior code can
read saved result schemas, but its compatibility endpoints lack these limits and
ignore idempotency headers. Clients must not assume replay protection after a
rollback. No default-branch merge or public release has been performed.
