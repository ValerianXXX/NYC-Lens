# Windows external-generation paths — 2026-10-31

External Parquet publication could fail when its generated file path exceeded
Windows' ordinary path limit, even when the configured data home was shorter.
The error could report that an existing parent directory was missing. This
milestone fixes that storage boundary using the existing dependencies.

## What changed

- Use native extended paths for external-generation directories, Parquet SQL,
  filesystem checks, file hashes, manifest writes and publication-readiness reads.
- Keep stored catalog paths relative and unchanged. Database identities, task
  namespaces and configured homes keep their existing representations.
- Preserve quoted and Chinese names, immutable old generations, old readers,
  manifest ancestry and atomic rollback. Existing catalog-boundary checks remain.
- Include affected path/storage/hash code in the calculation fingerprint for
  newly computed results. Historical results retain their recorded fingerprint.
- Add the new contracts to Windows CI and shorten the README's implementation
  history into product capabilities, validation links and explicit limits.

The code does not change registry settings, dependencies, numeric calculations,
schema, source coverage thresholds, scoring rules or production data.

## Evidence and interpretation

Frozen cases: `4a91f643a1000462d848e15a1b94e2065a2d9dc2`.
Runtime: `ae972a47848e778e3bc6386d621f533c9024d9ba`.

| Observation | Result | What it establishes |
| --- | --- | --- |
| Six fixed long-path cases before repair | 6 setup errors, 5.49 s | Initialization failed before the later assertions; these share one path defect, not six independent historic bugs. |
| Identical cases after repair | 6 passed, 9.04 s | Actual local Windows/DuckDB publication, old-reader retention, saved bytes/hashes, source-run prerequisite checks, quoted/Chinese paths and rollback. |
| Related checks | 66 passed, 30 warnings, 91.70 s | Existing publication/recovery, configuration and evidence contracts plus nine supplemental path-boundary/discovery cases. This set excludes the six frozen cases above. |
| Base candidate's complete local suite | 486 passed, 3 failed, 3 skipped | The original cancellation, long-path and timeout-startup failures are retained; base runtime `0effac1` was unchanged. |
| Base candidate's hosted checks | Both jobs passed | Linux 487 passed / 5 skipped; Windows eight contract groups passed. Hosted success does not erase local failures. |
| This repair's complete checks | Running with an observed cancellation failure | The full run captured a Windows file-sharing exception immediately after worker exit. Related checks are not substituted for complete acceptance. |

[Verification receipt and original redacted logs](../reports/evaluations/2026-10-31/windows-external-paths/verification.json)
include exact source hashes, the unchanged frozen test hash, environment and the
separate diagnostic runs. Original unredacted logs and failed synthetic databases
are retained in the task workspace; public logs replace the local account path.
No model calls, new model quality, speed or cost gains are claimed.

## Reproduce and operate

Use the existing Python environment and dependencies:

```console
pip install -e ".[dev,agent]"
python -m pytest tests/unit/test_external_storage_long_paths.py tests/unit/test_external_path_boundary.py
```

These are isolated synthetic fixtures and do not access the configured business
database or fetch public datasets. The fixed fixtures deliberately create long
descendant paths; reducing the temporary path length is not the repair.
Normal application configuration stays in `.env.example`; there is no new flag.
External-storage refreshes use the new addressing automatically.

For a failed refresh, retain the original run, raw inputs and catalog. Inspect its
saved outcome before any explicit rerun. This change does not automatically retry
an ingestion or an analysis and does not declare a failed task successful.

## Separate unresolved cancellation/timeout observations

The base full run's legacy MCP cancellation stopped its owned worker but left the
second site and comparison `RUNNING`, with `database_cleanup_confirmed=false`.
Copies of its retained DB/WAL were readable, with the first site `SUCCEEDED`.
The immediate reconciliation exception was not captured in that run. Two later
diagnostic runs passed cancellation/timeout assertions; that does not establish
the cause or a fix. The new full-run harness captured `IOException` at the
database-open step after a separate HTTP cancellation: Windows reported that
another process was using the file, while the receipt confirmed worker exit.
The holder was not identified, and the original MCP failure's exact exception
is still unavailable. This supports a follow-up bounded file-release check;
the path repair itself does not fix or hide that failure. Observation changed
no production code, timeout budget or test assertion.

The base HTTP timeout failure is distinct: at the ten-second deadline no analysis
rows had been created. Cleanup was confirmed, but the test expected a completed
first child and a timed-out second child. Its timing cause remains under review.
No timeout was increased and no expected state was weakened here.

## Compatibility, rollback and remaining limits

This is a source change requiring normal review before release; no production
deployment or database migration was performed. Catalog/manifest formats remain
compatible. Back up the catalog and its referenced data/manifests together before
changing installed code. A reviewed code revert restores prior behavior but also
restores long-path failures; keep this compatible build available if long paths
already exist. Do not rename or remove individual generation files to roll back.

Validation uses local Windows, Python 3.11.3 and DuckDB 1.5.6. UNC/network shares,
Explorer behavior, every ingestion/export/migration path, independent machines
and the full regional dataset have not been accepted by this milestone. The fix
is scoped to external-generation I/O; it is not a claim that every application
path supports arbitrary lengths. Full-data, cancellation cleanup and independent
user acceptance remain open.

Official references checked on 2026-11-01 UTC (2026-10-31 local):
[Microsoft's path rules](https://learn.microsoft.com/windows/win32/fileio/maximum-file-path-limitation)
describe extended local/UNC syntax and its separator requirements.
[DuckDB issue #20384](https://github.com/duckdb/duckdb/issues/20384) and
[upstream change #20983](https://github.com/duckdb/duckdb/pull/20983) document
related long-path handling; a merged upstream change does not prove that the
installed runtime has it. The local defect and repair above were measured with
the same installed runtime. A separate [WAL recovery issue](https://github.com/duckdb/duckdb/issues/24767)
was reported fixed for 1.5.6; it is not established as the cause of our intermittent
cancellation failure. No upstream workaround or manual WAL alteration was used.
