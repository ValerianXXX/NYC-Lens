# External data generations — 2026-10-31

This is a local storage and failure-recovery milestone. It does not certify the
complete ingestion pipeline, the unavailable 17 GB dataset or real site-selection
conclusions. No model calls, source downloads, production migration or deployment
were performed.

## Problem and implementation

Replacing a source previously moved its existing Parquet directory aside, wrote
the same paths, changed the DuckDB view, and then rebuilt catalog rows and a
shared manifest. An existing reader could consequently read new bytes through
an old transaction. Another connection could see a new view with an old catalog.
A late failure or process exit could leave publication state inconsistent.

Layout `2.0.0` writes every candidate to an exclusive directory:

```text
data/standardized/_external_generations/<table>/generation_<id>/
  region=<region>/source=<source>/year=<year>/part-*.parquet
  _manifest.json
```

The manifest describes the complete active table, including retained files from
other sources. It records its predecessor's path/hash and the source snapshot IDs
actually present in the files. The view, partition catalog and table catalog
switch in one DuckDB transaction. A failed writer leaves unreferenced files;
initialization never promotes these candidates. Old files and manifests remain.
A source moving between regions is replaced across the whole table once.

`publication_transaction` is shared by storage helpers. Nested failures mark the
whole owned transaction for rollback even if a caller catches an inner error.
An unrelated caller-owned `BEGIN` is rejected rather than silently committed;
callers that need coordinated metadata changes must explicitly use this context.
Materialized-table replacement retains its transactional behavior.

Initialization imports the legacy directory layout once when no catalog exists,
including empty tables. Later starts use only committed partition entries.
Catalog copying during the existing compaction/migration workflow imports the
existing heads before registering new ones; no catalog schema migration is added.

Analysis checks the manifest hash and requires every recorded v2 source snapshot
to have its own matching, completed ingestion run. A later successful run for the
same source cannot stand in for that run. Pending or damaged publications return
the existing input/data-unavailable response; they do not return new counts
labeled with an earlier completed snapshot. Manifests larger than 16 MiB are
rejected. The guard reads manifests, not all Parquet files on each analysis.

## Verification and preserved failures

Base: `3cc08649e012d15a2770fceec3f9a4b97002ce12` (PR #7).
Fourteen contracts were frozen at
`54825db7ab4c54a5c6acc7820042e0c068633797` before implementation.
Runtime: `3f4fa3d4c1a9a2ed185bafd19a1b5258dbeb8a99`.
Environment: Windows, Python 3.11.3, DuckDB 1.5.6; no dependency upgrades.

| Check | Observation |
| --- | --- |
| Existing storage baseline | 2 passed |
| Same 14 frozen contracts before implementation | 3 passed, 11 failed |
| First generation implementation | 13 passed, 1 failed: empty-frame date typing |
| Fixed original contracts and existing storage tests | 16 passed, 14.99 s |
| Ten added recovery checks, first attempt | 9 passed, 1 failed: swallowed nested failure |
| Storage/recovery/real-engine transport milestone | 27 passed, 38.15 s |
| Seven manifest/run readiness checks | 7 passed, 4.92 s after correcting required fixture columns |
| Complete project regression | 233 passed, 3 existing full-data skips, 90 deprecation warnings; 382.51 s |

The frozen tests cover reader survival, simultaneous view/catalog visibility,
RuntimeError and KeyboardInterrupt rollback, refused COMMIT, retained old bytes
and manifests, empty replacement, other-source preservation, region changes,
duplicate input, abandoned files, unregistered legacy files and a real child
process exiting at the publication boundary. Recovery checks add competing
connections, legacy adoption, damaged inputs, unrelated transactions and an
offline closed-catalog backup/restore. The old file-existence test now checks the
actual catalog-bound files and sizes instead of requiring the retired layout.

One new engine fixture initially omitted completion of its revised source run.
It observed counts `[1, 0]` incorrectly labeled with the prior snapshot. Its
original source and failed log are retained. The final test keeps this condition:
it first requires HTTP 422, then completes the matching run and requires a new
successful comparison with the revised snapshot. Old `[1, 3]` results, evidence
and JSON reports remain unchanged. The final seven readiness fixtures initially
omitted required schema columns; the fixture rows were completed without changing
their assertions, schema constraints or product behavior.

This is FastAPI's in-process HTTP transport with actual owned worker processes,
DuckDB, Parquet, spatial calculations, stored results and evidence/report routes.
It is not a new browser, physical-device, Hermes or independent-human observation.
Both addresses and all records are explicitly synthetic. No new AI-quality,
latency or full-data performance benefit is claimed.

See [machine-readable receipt and log hashes](../reports/evaluations/2026-10-31/external-generations/verification.json).
Raw local failures were retained; published copies normalize line endings and
redact workstation paths. Hosted CI is reported separately for the delivery SHA.

## Reproduce and operate

```powershell
pip install -e ".[dev,agent]"
python -m pytest tests/unit/test_external_generation_contract.py tests/unit/test_external_generation_recovery.py tests/unit/test_external_generation_analysis.py tests/unit/test_external_publication_readiness.py tests/unit/test_external_storage.py
python -m pytest
```

Tests use isolated synthetic homes. The existing `python -m nyc_lens.demo --port
8766` remains available for the Web experience; it seeds small materialized
tables, while the generation integration test explicitly converts its own tiny
POI fixture to Parquet. `nyc-lens storage-status` and `nyc-lens qa` remain the
operator inspection commands. No new credentials or environment variables exist.

Before using new code against a populated home, stop its services/workers, finish
or checkpoint and close its database connections, and make a verified backup of
the catalog and entire data/configuration home. Preserve the old generation files
with the backup. Deployment and production migration remain separate approvals.

Recovery with this code can restore the reviewed closed catalog backup while
retaining all referenced generation files at their original home. The synthetic
restore test checks old rows/catalog and retained new/old files. A code downgrade
alone is unsafe: older initialization discovers the legacy directory tree and
does not understand v2 heads. Downgrading requires a matching pre-upgrade catalog
and files, followed by isolated validation. That old-code downgrade, full-data
restore and power-loss recovery have not been performed here. Never delete
generation directories to make a failed publication appear recovered.

## Remaining work

- Integrate source data, lineage, source freshness and terminal ingestion state
  into one publication across all adapters; the new joinable transaction enables
  this but does not itself change every adapter. Empty source replacements and
  sources with multiple active snapshots need explicit publication-level metadata
  and completeness semantics. The readiness guard only proves the recorded
  nonempty v2 snapshot/run prerequisites; legacy v1 manifests lack those fields.
- Audit every metric's chosen snapshot against its actual records, including
  additive sources and successful metadata snapshots with no active facts. This
  guard is not a general proof that all source labels are correct.
- Preserve a coherent multi-table generation through direct library callers and
  out-of-band writers. Existing Web/HTTP/CLI/MCP flows retain database admission;
  low-level calls must coordinate separately. A transaction protects a reader's
  snapshot, not arbitrary modifications of files outside the application.
- Add reviewed disk quotas, retention and garbage collection. Old generations
  and abandoned candidates currently consume disk; there is no automatic cleanup.
- Validate complete data, operator restoration, source refresh under sustained
  load, independent users and remaining client/device scenarios.

DuckDB's [version 1.5 concurrency documentation](https://duckdb.org/docs/1.5/connect/concurrency)
was checked on 2026-10-31. Its in-process transaction model informed this change;
the observed behavior is verified by the local contracts above. No external
source code was copied, no client-server database was introduced, and no upgrade
to the newer documentation's default major version was made.
