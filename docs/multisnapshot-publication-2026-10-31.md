# Multi-snapshot boundary publication — 2026-10-31

TIGER block groups and FEMA flood-screening polygons span multiple input files.
Both importers previously committed boundaries and removed historical lineage
before exporting Parquet. An export or freshness failure could therefore leave
new facts behind despite a failed refresh. This milestone repairs that local
publication boundary. Synthetic geometry does not establish live source coverage.

## Change and compatibility

1. Retain each original input snapshot and its hash.
2. Prepare an export under a unique ingestion-run directory, preserving previous
   fixed-path exports and files from earlier attempts.
3. Write a **locally derived** `publication.json` receipt containing input roles,
   snapshot IDs/hashes, export hash and counts. It is not an upstream HTTP response
   or independent evidence and adds nothing to download/request accounting.
4. Commit active facts, additional lineage, new-input metadata, completion and
   freshness together using the existing publication transaction. Historical
   entity-to-snapshot links remain available to earlier saved analyses.
5. Return `standardized_parquet`, `publication_snapshot_id` and
   `publication_manifest`. A prepared file or raw-file success is not publication:
   **the matching `ingestion_run.status` remains the authority**.

TIGER reports actual requests and `reused_snapshots`, verifies cached bytes through
the existing fixed-vintage cache and leaves reused raw metadata unchanged. A full
cache hit makes no requests. FEMA retains page-level snapshot IDs in facts, stores
only compact page metadata while staging, and cleans up temporary staging after
handled failures and interruptions. The 30-county requirement, 10,000-polygon
minimum, 500-record pages, clipping, deduplication and planning caveats remain.

Metric snapshot selection for these two sources now requires a snapshot referenced
by active geometry. A publication receipt, count response or unused candidate does
not become the metric's source. Stored historical analyses are not rewritten.

No schema migration, new dependency, credential or model call is introduced.
Consumers must use the returned export path rather than the two old fixed paths:

| Source | Layout beneath `data/standardized` |
| --- | --- |
| `tiger_bg_2024` | `tiger_bg_2024/2024/<run_id>/greater_nyc_block_groups.parquet` |
| `fema_nfhl_special_flood_hazard_areas` | `fema_nfhl_special_flood_hazard_areas/current/<run_id>/sfha_boundaries.parquet` |

## Failure, retry and rollback

Export, lineage, freshness and known commit failures retain the previous active
publication and export hashes. An interrupted attempt stays `INDETERMINATE`; it
must not be relabelled successful or automatically retried. Prepared files and
raw receipts may remain for diagnosis. This change does not add automatic deletion
or retained-file quotas.

A separate counterexample covers a driver error **after actual COMMIT**. When
rollback cannot be confirmed, these two importers preserve the stored outcome and
propagate an inspection note instead of blindly writing `FAILED`. Check the run,
freshness and active snapshots through a fresh connection before retrying. Other
importers' uncertain-commit handling still needs review.

The refresh CLI keeps its nonzero exit code for this command-level failure but
reports `INDETERMINATE`, `requires_catalog_inspection: true` and `recorded_run_id`.
That is the caller's uncertainty, not a claim that a committed database run failed.

Before real use, stop writers and back up the catalog together with referenced raw
and standardized files. After an ordinary failure, keep using the old publication.
After a crash, reopen the catalog, inspect the indeterminate attempt and start a
separate explicit refresh when its source/resources are ready. To roll back code,
restore the reviewed prior revision in a separate checkout and retain all receipts.
Old code still overwrites fixed exports and deletes lineage; reverting code alone
does not undo a data publication. Restore the coordinated catalog/files backup if
the data itself must be reverted. No production deployment or migration is included.

## Reproduction and observations

Use the existing environment (`pip install -e ".[dev,agent]"`), then:

```text
python -m pytest tests/unit/test_multisnapshot_publication.py
python -m pytest tests/unit/test_multisnapshot_publication_recovery.py tests/unit/test_multisnapshot_commit_ack.py tests/unit/test_external_publication_readiness.py
python -m pytest tests/unit/test_multisnapshot_cli_outcome.py tests/unit/test_multisnapshot_metric_binding.py tests/unit/test_evidence_snapshot_contract.py
```

Normal source refresh commands remain `nyc-lens refresh --source tiger_bg_2024`
and `nyc-lens refresh --source fema_nfhl_special_flood_hazard_areas`. They download
real public data; FEMA requires the existing CSA county definitions and geometry.
They are not the synthetic test commands and were not run against full live data
in this milestone. Keep `NYC_LENS_HOME` on the intended isolated data directory.

Tests replace only transport. They parse real ZIP/shapefile containers for four
states and all 30 county identifiers, and 20 GeoJSON pages of 500 synthetic flood
polygons. Clipping, DuckDB staging and Parquet export execute locally. TIGER's 30
synthetic rows exercise county-set validation, not real block-group coverage.

| Observation | Result |
| --- | --- |
| Frozen 15 publication/cache contracts before repair | 4 passed, 11 failed; 158.13s |
| Same 15 at first repair `698e235` | 15 passed; 133.52s |
| Frozen lost-commit-acknowledgement cases before their repair | Both failed: committed successes were relabelled failed |
| Publication/recovery/acknowledgement checks after that repair | 32 passed; 247.35s; includes 7 existing transaction checks |
| Four CLI/metric consumer checks before consumer fixes | 4 failed; 43.19s; includes newly introduced receipt-selection regression |
| Consumer/commit/evidence checks after consumer fixes `02f30cc` | 10 passed; 89.67s; includes 4 existing evidence/export checks |
| Final recovery checks and complete regression | Actual terminal/pending state in the linked receipt |

The 15 contracts cover export/lineage/freshness/commit failure, reader visibility,
history/export retention, interruption and cache accounting. Supplementary checks
cover source floors, receipt failure, damaged cache, staging failure, actual owned
process exit and explicit retry. These are automated synthetic/local checks, not
independent human or populated-data acceptance. Single-run elapsed differences do
not establish a speed or production-cost improvement.

One consumer-check invocation named a nonexistent unrelated test file and ran no
tests (exit 4). Its log is retained separately; the corrected invocation produced
the 10-pass result above. No check or correctness assertion was removed to pass.

[Verification and retained failures](../reports/evaluations/2026-10-31/multisnapshot-publication/verification.json)
bind results to revisions and file hashes. Earlier complete-suite passes belong
to their earlier revision and do not certify this change.

## Official reference and remaining requirements

[DuckDB transaction documentation](https://duckdb.org/docs/2.0/sql/statements/transactions)
was read on 2026-10-31 for explicit commit, rollback and reader visibility; the
stable URL redirected to version 2.0. The installed DuckDB 1.5.6 is tested locally
and is not upgraded. Browser retrieval of version-1.5 docs and FEMA layer metadata
failed; no current FEMA transport or full source-refresh validation is claimed.

Still outstanding: stable FEMA pagination/ID completeness across changing live
responses; strict count/control validation; actual row-to-file binding instead of
snapshot-level linkage; source effective-date consistency; ingest disk/memory/time
limits and retained-file lifecycle; remaining adapters; the unavailable full data;
the original 12-scenario and independent-user acceptance. No model quality, MCP
Apps client, real city ranking, customer adoption or business outcome is inferred.
