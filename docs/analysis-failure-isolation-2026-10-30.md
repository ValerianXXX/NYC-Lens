# Analysis failure ownership and interruption - October 30, 2026

One failed address analysis could previously mark another concurrent request for
the same address and business type as failed. Cleanup selected every `RUNNING`
row created after the failing request started. Propagated cancellation exceptions
could also leave analysis/comparison rows running, and a failed cleanup write
could hide the original error.

Each site attempt now allocates its own identifier before analysis. A shared
terminal-state helper updates only that identifier while it is still `RUNNING`.
It records ordinary failures as `FAILED`, propagated `KeyboardInterrupt` or
`asyncio.CancelledError` as `CANCELLED`, and other process-level exceptions as
`INTERRUPTED`. The original exception is always re-raised. A failed terminal write
adds an explicit note to that original exception; it does not claim successful
persistence. Error messages containing arbitrary external content are not saved
in terminal warnings; the exception type is retained.

The same helper handles comparison failures. Completed child sites remain
available as completed site analyses; a cancelled comparison retains their IDs
but has no completed comparison result. Partial catchments and metrics remain
in the database for inspection rather than being silently deleted. Site lookup,
reports, evidence and transaction lookup require a successful parent analysis.
Already terminal rows are not overwritten by later cleanup.

## Verification

The previous clean source was `2c488c061b7d81da0cc5393e45475b32fcc0c4b2`, the
open configuration-snapshot draft. The ten failure contracts were frozen before
repair in `1658639e4defba4779acd915514117ab706b2d85`:

| Check | Before | After |
| --- | --- | --- |
| Frozen ownership/interruption/read contracts | 1 passed, 9 failed | 10 passed unchanged |
| Additional real-component checks | 3 passed, 1 failed | 4 passed, 5.11 seconds |

The additional failing case exposed an unguarded transaction endpoint: it
returned HTTP 200 and an empty list for a cancelled site's partial catchment.
Joining the successful parent site now returns 404. The test and its assertions
were not changed to obtain the pass. Eighteen existing Shapely deprecation
warnings remain visible in the final additional-check run.

The checks exercise:

- Two actual connections to one temporary DuckDB file, with a controlled thread
  interleaving: one failed request cannot close the other same-address attempt.
  The analysis body is a controlled boundary fixture in this concurrency check.
- Actual synthetic spatial calculation: the first site finishes, then an injected
  cancellation interrupts the second after its catchment was saved. The second
  site and comparison become `CANCELLED`; no partial comparison is returned.
- Actual in-process HTTP requests: site, report, evidence and transaction reads
  for the cancelled site return 404. A separately initiated retry creates fresh
  IDs and calculates the unchanged synthetic restaurant counts **1 and 3**. The
  report exports successfully, cancelled history remains and no rows remain
  `RUNNING` in this controlled scenario.
- Already completed rows survive cleanup; raw exception details are not persisted;
  a failed status write does not replace the original exception.

These are synthetic contract and real local-component observations, not an
actual browser cancel click, MCP client cancellation or real-location result.
No model call, source ingestion, production data change or independent human
acceptance occurred. Full checks and their exact tested revision are reported in
the [verification receipt](../evaluations/analysis-failure-isolation-2026-10-30.json)
and the draft PR's checks; the earlier 113-pass result is not transferred here.

## Operation and recovery

No new setting, dependency or schema migration is needed. Restart after changing
Python code. Existing text status columns accept the additional terminal states;
callers must not treat an arbitrary non-`FAILED` status as success. The new helper
is included in calculation code fingerprints; historical reports are unchanged.

After the README setup:

```sh
python -m pytest tests/unit/test_analysis_failure_isolation.py
python -m pytest tests/unit/test_analysis_interruption_integration.py
python -m pytest
```

Finish or stop an operation explicitly before retrying it. This milestone allows
a **new manual attempt**, not automatic resumption or automatic retry. If a
connection fails during cleanup, the original attempt may remain `RUNNING`;
inspect it rather than assuming cancellation succeeded. A forced process kill,
power loss or database outage cannot execute Python cleanup. No automatic stale
row sweep, cross-process lease recovery or deletion has been introduced.

## Scope before broader cancellation and resource controls

This repair is a prerequisite for cancelling work without corrupting another
request's status. It does not itself add HTTP/MCP cancellation propagation, an
interrupt button, a hard time/RAM limit, a worker pool, atomic dataset snapshots
or a global ingestion/analysis lock. DuckDB's [Python connection guidance](https://duckdb.org/docs/stable/clients/python/overview)
(checked October 30, 2026; local test engine 1.5.6) distinguishes separate
connections from a shared connection and notes that global resource settings
affect the database. Those settings must not be presented as isolated per-request
budgets. The local concurrency check uses separate connections.

Full-data acceptance, dataset concurrency and freshness, query budgets,
cooperative cancellation and crash recovery, further real-client tests and
independent user testing remain open. No composite score is enabled.

Rollback uses a normal reviewed source revert and service restart, retaining the
database and reports. No data rollback or migration is required. Older code
contains the known failure-ownership defect and may expose manually retained
unsuccessful result JSON. Back up actual application data before any separately
approved deployment. This draft changes no deployed service.
