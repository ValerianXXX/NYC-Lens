# Geographic publication and retained evidence — 2026-10-31

Boundary refreshes previously committed replacement facts and lineage before
writing their derived exports and final ingestion/freshness state. Failures could
leave new data behind a failed run, readers could see mismatched states, and a
partial export could overwrite the old file. The boundary helper and H3 rebuild
also deleted historical source lineage, removing links used to trace earlier
snapshots. Their local transaction handling did not roll back propagated interrupts.

These refreshes now reuse the existing publication transaction and temporary-frame
helpers. Geometry preparation and a new snapshot-specific Parquet export happen
first. One database transaction then replaces current facts, appends lineage,
records snapshot details and finishes the ingestion run with source freshness.
The shared helper joins this transaction instead of committing inside it.

## Scope and compatibility

| Entry point | Configured inputs | Contract observations |
| --- | --- | --- |
| `refresh_nyc_geography` | NYC blocks, tracts, NTA, CDTA, community districts and boroughs | NTA and borough replacement, failure and separate-reader cases |
| `refresh_zcta` | Census ZCTA extract filtered by the borough union | 100 synthetic intersecting features, failure and low-coverage cases |
| `refresh_h3_grid` | Derived grid from the five borough boundaries | Real H3 generation above 40,000 cells over synthetic polygons, legacy alias and interruption |
| `refresh_hazard_layer` | Evacuation zones and the existing 2050/2100 planning layers | Synthetic evacuation-zone replacement, failure and owned child exit |
| `refresh_taxi_zones` | TLC taxi-zone polygon package | A real synthetic shapefile ZIP with 263 zones; duplicate and coverage rejection |

The configured source definitions, source URLs, calculation rules, H3 resolution,
minimum/maximum counts, geometry checks and planning/privacy caveats remain.
The table distinguishes the code scope from representative synthetic observations;
it is not live acceptance of every configured source. Monthly taxi-trip ingestion
and other importers are outside this publication change.

Every successful result keeps its existing keys and adds `standardized_parquet`.
It points to `data/standardized/<source_id>/<snapshot_id>/<filename>`. Fixed
`current`, `2020`, planning-vintage and H3-resolution paths are retained but are
no longer updated by these entry points. External consumers must use the returned
path. The application reads catalog facts; a repository search found no internal
consumer depending on these old filenames. Earlier reports and files are not rewritten.

No dependency, environment variable, schema migration or data-model change is
required. `derived_h3` active rows are still superseded by `derived_h3_grid` as
before, while their historical lineage now remains available. Preserving history
increases disk usage; this is not a retention or garbage-collection policy.

## Failure and recovery semantics

- A failed or partial new export cannot replace a previous file. Prepared files
  from unsuccessful attempts remain as inspectable candidates, not active facts.
- A database failure rolls back facts, new lineage, snapshot details, successful
  run state and freshness together. The outer refresh then records `FAILED`.
- Propagated interrupts roll back and release temporary relations. A process exit
  during publication leaves the previously committed facts intact and the new
  attempt `INDETERMINATE`; it is not silently retried or marked successful.
- Raw downloaded snapshots and their original manifests remain. Download success
  is distinct from publication, and neither establishes upstream completeness.
- A helper called inside an unrelated caller-owned transaction refuses to begin
  another transaction rather than committing the caller's work. Ordinary ingestion
  still uses existing catalog admission; unrestricted out-of-band writers remain
  outside the validated execution path.

## Frozen comparison and recovery evidence

The initial five-kind comparison produced **10 passes / 22 failures in 70.89 s**.
Inspecting all helper callers then identified TLC taxi zones, so coverage expanded
before implementation. The final 38 contracts were frozen at
`7194b6ece8fcb346a6b6a4f8b36d13857120fbf6`, based on delivery
`331582d141a168c0505a5f06be3f61daece59041`. The expanded old-runtime result was
**12 passes / 26 failures in 74.20 s**. Runtime
`2d65481e78ee108eaad6790bee3aa3c68e003d32` passes the **same 38 contracts in 69.25 s**,
without assertion changes. Both original failure logs are retained.

The frozen cases cover lineage/export/freshness/commit failure for six input
kinds; observation from a separate connection before final publication; retained
old lineage and export bytes after success; and interrupted hazard/H3 replacement.
State comparisons include all current boundary/cell rows, lineage, successful
runs and source timestamps, using compact digests to keep large-grid diagnostics
readable. Failures cannot be hidden by changing a success flag.

Eleven additional checks passed in **20.30 s**. They preserve six existing
coverage gates and duplicate taxi-zone rejection; verify caller-owned transaction
refusal and explicit retry; retain legacy H3 lineage; and run an owned child that
calls `os._exit(83)` after the freshness SQL but before commit. The parent reopens
the catalog and verifies the previous data, lineage, freshness and file hashes,
alongside the retained indeterminate attempt and raw snapshots. This exercises a
real process exit, not power loss or a crash at every possible instruction.

The H3 checks use actual geometry union, H3 indexing and polygon generation with
the unchanged 40,000-cell floor. All source inputs are explicitly synthetic.
GeoJSON/shapefile parsing, projection, DuckDB transactions, lineage and Parquet
output execute locally for real. Downloads and the TIGERweb HTTP response are
substituted; no official endpoint, city data, model, Hermes, browser or independent
user observation is claimed for this milestone. There is no measured AI quality
or full-data performance gain; the elapsed times above are test-run observations.

The [verification receipt](../reports/evaluations/2026-10-31/boundary-publication/verification.json)
records full regression results, versions, artifact hashes and creation-time
hosted status. CI retains Ubuntu's full suite and adds both new suites to Windows.
The previous baseline's 276-pass/three-full-data-skip result remains historical.

The current full local suite passed **325 tests**, with **three unchanged
full-data skips** and 90 existing deprecation warnings, in **375.01 s**. The
environment was Windows / Python 3.11.3 / DuckDB 1.5.6 / GeoPandas 1.2.0 /
H3 4.5.0 / Pandas 2.3.3 / PyArrow 22.0.0. All three existing JavaScript syntax
checks passed. The full run includes both new suites and the existing analysis,
comparison, evidence, worker, HTTP, CLI, MCP and prior publication checks; it does
not substitute for the missing full-data tests or independent user acceptance.

## Run, adopt and roll back

```powershell
pip install -e ".[dev,agent]"
python -m pytest tests/unit/test_boundary_publication.py tests/unit/test_boundary_publication_recovery.py
python -m pytest

# Existing command; uses configured public sources and the selected local catalog.
nyc-lens refresh --source nyc_borough_boundaries
```

No new configuration is needed. Use a separate data home for synthetic experiments,
and inspect the saved attempt and returned output path after a refresh. Do not
rewrite failed statuses or delete retained evidence to make a retry appear clean.

This branch builds on PR #10; review dependencies in order before merging. Before
adoption on populated data, close services/workers/connections and verify a matching
catalog, raw/standardized data and configuration backup. A normal reviewed code
revert requires no schema rollback but restores historical-lineage deletion,
partial-publication and fixed-file overwrite behavior. Retain new evidence and
restore a matching closed backup if data rollback is needed. No production
deployment, migration, data restoration or release has been performed.

## Still open

- GeoJSON/TIGERweb response completeness and transfer limits require their own
  checks. The prior CSV sentinel guard does not cover these formats. Preserved
  minimum/maximum counts alone do not prove complete or current upstream data.
- Other source importers, multi-snapshot dependencies and every metric's
  actual-record provenance still need review and acceptance.
- Ingestion byte/time/memory limits, immutable-file retention, disk quotas and
  full-data backup/restore performance remain open. Preparing an export is not
  a complete filesystem/database durability protocol.
- Complete local data and independent user/client/device acceptance remain
  unavailable. This publication milestone does not complete the NYC-Lens GOAL.

The [DuckDB 1.5 concurrency reference](https://duckdb.org/docs/1.5/connect/concurrency)
was rechecked on 2026-10-31 for the installed 1.5.6 runtime's in-process model.
Its transaction-reference page was unavailable through the documentation reader
and is not evidence. The project reuses its existing transaction helper and
verifies behavior above; no new database server, third-party code copy or
dependency migration was introduced.
