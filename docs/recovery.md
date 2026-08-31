# Installation, backup, and recovery

## Rebuild on a new machine

1. Clone the source repository and copy no credentials from another machine.
2. Install Python 3.12 and run `pip install -e ".[dev]"` in a virtual environment.
3. Run `nyc-lens init`.
4. Run `nyc-lens verify-clean-init` to verify an isolated empty initialization.
5. Run `nyc-lens refresh` to retrieve implemented public sources.
6. Run `nyc-lens status`, `nyc-lens storage-status`, `nyc-lens qa`, and `pytest`.

The clean-init probe creates an isolated directory under `outputs/clean_init_*`. It does not modify the operator's production catalog.

A rebuild downloads current upstream material and creates new retrieval timestamps and hashes. It is reproducible by method and source, not necessarily byte-identical to an older snapshot.

## Preserve an exact local evidence state

Back up these items together:

- `data/nyc_lens.duckdb`;
- `data/raw/`;
- `data/standardized/`;
- `config/`;
- `reports/`;
- source code and dependency lock information.

The five external fact directories for `building`, `property_record`, `regulatory_record`, `poi`, and `workforce_estimate` must remain paired with their manifests and DuckDB catalog.

After restoration, run:

```text
nyc-lens init
nyc-lens status
nyc-lens storage-status
nyc-lens qa
pytest
```

`init` is idempotent and does not erase raw or analytical tables. Never delete an isolated raw file to repair a hash mismatch. Quarantine a damaged database and restore a complete matched backup.

## Catalog compaction

`nyc-lens compact-catalog` builds and verifies a replacement catalog before atomically swapping it into place. Keep a full backup first. Temporary spill files are limited to task-specific directories under `data/cache/duckdb_temp/compact_*`; do not recursively delete the entire data directory.
