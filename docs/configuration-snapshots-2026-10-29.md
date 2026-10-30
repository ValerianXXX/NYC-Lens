# Configuration snapshots and calculation provenance

Milestone date: 2026-10-29, America/New_York. Verification timestamps are UTC in the [machine-readable record](../evaluations/configuration-snapshots-verification-2026-10-29.json). This work builds on the comparison/evidence boundary changes; it does not introduce a new model, dataset, service or dependency.

## Problem and resulting behavior

The five configuration loaders previously cached their first result indefinitely. Editing a file, switching the configuration directory or mutating a returned nested object could leave later analyses using stale or caller-modified rules. Calculation versioning independently hashed the current files, so a stored result could claim a configuration it had not used.

`configuration_snapshot()` now pins exact `.yml` bytes for an analysis, comparison or initialization. Nested calls share those bytes; separate request contexts remain isolated. `calculation_version()` fingerprints the captured bytes and includes configuration loading/versioning code in its code hash. The next operation sees changed files, including same-size changes with unchanged timestamps. Cached parses are bounded and copied before returning to callers.

No database schema or stored report is rewritten. New calculation versions may be created because code fingerprints now include `config.py` and `db.py`. Existing comparison rules continue to withhold ranks/differences when calculation versions differ.

## Frozen regression evidence

Baseline code: `9f82bb4b64839077df58e3c36b1249819e656333`. The 19 configuration contracts were committed before the repair in `2a91648c931a75cbe22ff60b0c027d65b289d54a`. Their contents and expectations were not changed afterward.

| Check | Before | After |
| --- | --- | --- |
| Existing full suite | 88 passed, 3 skipped | — |
| Frozen configuration contracts | 1 passed, 18 failed | 19 passed |
| Additional integration and boundary tests | Not run on baseline | 6 passed |
| Full suite including new tests | — | 113 passed, 3 skipped, 39.22 seconds |

The frozen cases cover all five loaders, same-size/timestamp edits, nested caller mutation, directory changes, invalid updates, parse reuse, nested and simultaneous contexts, exception recovery, exact provenance and oversized files. Additional checks cover deleted files, invalid UTF-8/YAML, file count/total byte bounds and continuously changing capture. The 182 full-run warnings concern existing Shapely transform deprecations; no warning filter or test was disabled. Three populated-database tests remain skipped because the complete local dataset is unavailable.

## Actual components exercised with synthetic data

The integration test uses FastAPI's in-process HTTP test client, an isolated DuckDB database, the existing spatial engine and the repository's explicitly synthetic demo. Only a wrapper around the real site-analysis function edits the temporary home's copied taxonomy after the first site completes. It does not substitute calculated values or model answers.

1. A confirmed two-address request starts with synthetic restaurant counts **1 and 3**. The taxonomy changes between its two site calculations. Both still use the captured original rules and original configuration hash.
2. The next request uses the changed taxonomy and calculates **0 and 0**, with a different calculation version but the same dataset snapshots.
3. Exporting the first comparison still returns **1 and 3**.
4. Restoring the original configuration bytes produces **1 and 3** again and reuses the first calculation version. All three comparisons and six site analyses finish; none remain `RUNNING`.

Missing information and the disabled composite score remain visible. This verifies real local components with synthetic input, not a live network server, a real commercial location or independent user acceptance. No new model requests or live-source data queries were performed. Existing Web assets were unchanged, so this stage does not claim new visual or mobile testing.

## Reproduce and operate

Use the existing supported Python environment and dependencies:

```sh
pip install -e ".[dev,agent]"
python -m pytest tests/unit/test_configuration_snapshot.py
python -m pytest tests/unit/test_configuration_analysis_integration.py
python -m pytest
```

No new setting is required. `NYC_LENS_HOME/config` remains the configuration location; `.env.example` documents its behavior. An existing service can observe a completed YAML edit on its next analysis request. Restart after Python code or process-environment changes.

Capture permits at most 64 `.yml` files, 2,000,000 bytes per file and 8,000,000 total bytes. It compares two captures, retries once on change, then reports `CONFIGURATION_CHANGED_DURING_CAPTURE`. Invalid UTF-8/YAML, non-mapping documents and limit violations fail instead of silently using a previous parse. Repair or restore the file, finish editing, and retry. A missing file captured earlier remains available only to the in-flight operation; a later operation must read the current configuration.

Finish related multi-file edits while analysis is idle. Two matching captures are a bounded stability check, not an atomic configuration publishing transaction. Cache limits concern configuration input; they do not establish a query time, memory or cancellation budget. Historical results retain hashes, not a newly introduced archive of every old configuration byte set. Keep reviewed configuration revisions in version control if they must be reconstructed.

## Rollback and remaining work

This is source-only preparation for a draft PR; no release, deployment or production migration was performed. A code rollback uses the reviewed earlier application revision and restarts the service while retaining the database and existing reports. Rolling back to the baseline also restores its known stale-cache limitation, so do not live-edit configuration under that version. Restoring configuration bytes was verified only in the isolated synthetic test above. Back up an actual data directory before any separately approved deployment.

Still open: full-data/source-freshness acceptance, dataset concurrency semantics, broader query resource limits, cancellation/interruption recovery, further client acceptance and independent human testing. This repair makes configuration provenance consistent; it does not establish an AI-quality, cost or site-selection benefit.
