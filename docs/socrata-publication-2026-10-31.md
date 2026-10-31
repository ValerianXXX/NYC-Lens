# Atomic source refreshes — 2026-10-31

The DOHMH restaurant, hotel-property and air-quality/health importers previously
deleted old facts and inserted replacements, lineage and completion metadata in
separate commits. A failed insertion, lineage registration, export or final
freshness update could leave a partial database. Even a successful import could
expose a mixed state to another connection. Their fixed export paths also
overwrote earlier derived evidence.

These three existing importers now prepare a snapshot-specific derived export,
then use the existing publication transaction to commit facts, lineage, successful
run status and source freshness together. This is a data-correctness repair, not
a new source, metric, model or conclusion about a real neighborhood.

## Publication and recovery behavior

1. Start the existing ingestion attempt and retain metadata/data downloads in the
   existing raw snapshot store. Download evidence is distinct from publication.
2. Parse and standardize with the existing business rules. Store row count/schema
   on the downloaded snapshot. Prepare the derived Parquet at
   `data/standardized/<source_id>/<snapshot_id>/<filename>`.
3. Begin one database transaction. Replace only this source's facts, register
   lineage, finish the ingestion run and update the source's successful timestamp.
   Commit them together. Temporary registered frames are released on both success
   and failure; the shared lineage helper has the same cleanup guarantee.
4. If publication fails, roll back its database changes before recording a failed
   attempt. Keep the previous facts, their lineage, successful timestamp and
   earlier exports. Retain raw evidence and abandoned new exports for inspection.
   A process exit or uncaught interrupt leaves the attempt `INDETERMINATE`; it is
   not reported as successful and is not retried automatically.

Existing saved reports and historical lineage are not rewritten. A successful
empty hotel response has a completed zero-row audit and removes that source's
current hotel rows. This tests technical handling of an empty response; it does
not prove that a truncated upstream response is a complete real dataset.

The three refresh return objects add `standardized_parquet`, the exact new file
path. Existing keys remain. These importers no longer replace the old
`current/*.parquet` files; an external script reading those fixed paths must use
the returned snapshot-specific path. Old files are retained and are not an
automatically maintained current-data alias. The application queries its catalog
tables, not those old export filenames.

## Frozen comparison and additional checks

Twenty contracts were committed at
`2d5d09b8532c6895a66ef09eca2fd47a0586c38d` before implementation. At the previous
runtime they produced **one pass and 19 failures in 10.46 seconds**. After the
repair the same contracts passed **20/20 in 9.72 seconds** without assertion
changes. They cover all three adapters and:

- Failure during fact insertion, lineage, source freshness, commit and export.
- Separate-connection observations of facts, lineage, successful run and freshness.
- Retained raw snapshots and previous derived files, with no leaked temporary views.
- Interrupted restaurant import and successful empty hotel import.

Three additional recovery checks passed in 5.32 seconds: an owned child process
exits during the restaurant transaction; a failed hotel import can be explicitly
retried on the same connection; an air/health refresh retains unrelated-source
facts and historical lineage while current rows name their new snapshot. The
full suite also rechecks the final clearer form of that last snapshot assertion.

Runtime `5d0134e46326503b718129bca81ad75e98024e99` is based on PR #8 at
`07b495540527ae1ed8dfd44dd17fbacf80eeb60c`. Complete results, source/log hashes and
creation-time hosted status are in the
[verification receipt](../reports/evaluations/2026-10-31/socrata-publication/verification.json)
and draft PR. Earlier PR #8's passing checks are not substituted for this change.
Original failed logs remain available. No test was deleted or weakened.

The complete local suite at this runtime passed **256 tests**, with **three
existing full-data skips** and 90 existing deprecation warnings, in **333.30
seconds**. Windows CI now includes these source-publication checks alongside the
prior storage/process suites; Ubuntu keeps its full suite. Hosted results remain
separate from this local observation.

Downloads in these checks are substituted with explicitly synthetic CSV/JSON;
the code really parses them, saves raw snapshots, writes DuckDB/Parquet, records
lineage and executes/rolls back transactions. The child-process exit is real.
This is not a new official-endpoint, full-dataset, browser, Hermes, independent
user or power-loss observation. Model requests and source downloads are zero.

## Run and operate

```powershell
pip install -e ".[dev,agent]"
python -m pytest tests/unit/test_socrata_publication.py tests/unit/test_socrata_publication_recovery.py
python -m pytest
```

The usual CLI refresh commands and local configuration remain. No new environment
variable, dependency, database schema or credential is required. Run ingestion
through the existing catalog-admission mechanism; a low-level library caller
still owns cross-process coordination. These importers remain direct operations,
not newly bounded analysis workers.

Before activating against populated data, close services/workers and database
connections, then verify a backup of the catalog and data/configuration home.
Retain the raw files and all referenced generations/exports with it. An ordinary
reviewed code revert requires no schema rollback but restores the old partial
write behavior and fixed-path export overwrites. For data rollback, restore the
matching closed backup and validate in isolation; do not delete retained files or
change failed run statuses to manufacture recovery. Production activation and
data restoration have not been performed by this milestone.

The [DuckDB 1.5 concurrency documentation](https://duckdb.org/docs/1.5/connect/concurrency)
was rechecked on 2026-10-31. Its in-process transaction/conflict model informed
the use of the existing helper, while the observations above establish behavior
in this environment. The separate transactions-reference page was unavailable
through the documentation reader and is not used as verification evidence.
Installed DuckDB remains 1.5.6; no third-party implementation was copied and no
database service or dependency upgrade was introduced.

## Still open

- Other importers still need their own source/freshness/lineage publication audit;
  this change covers exactly these three adapters. External-generation readiness
  and the limitations in the [prior milestone](external-generations-2026-10-31.md)
  remain relevant.
- Upstream completeness, pagination/truncation detection, multi-snapshot sources,
  empty-source semantics across the full registry and every metric's actual-record
  provenance require separate validation.
- Retained and abandoned exports consume disk. There is no automatic retention or
  garbage collection, and no disk-quota guarantee.
- Coordinated configuration changes during ingestion, arbitrary out-of-band
  writers, full-data performance/restore, crash-at-every-instruction and power-loss
  behavior are not established by these tests.
- The full 17 GB data and independent-user/client/device acceptance remain open.
  Neither these tests nor a draft PR mean the complete NYC-Lens GOAL is achieved.
