# Local data directory

This directory is populated by NYC Lens on the operator's machine. The full
working dataset is intentionally not committed to Git.

Run the following commands from the repository root after installing the
package:

```text
nyc-lens init
nyc-lens refresh
nyc-lens status
nyc-lens storage-status
nyc-lens qa
```

The expected structure is:

- `nyc_lens.duckdb`: local DuckDB catalog and query state;
- `raw/`: append-only source snapshots and manifests;
- `staged/`: intermediate normalization artifacts;
- `standardized/`: normalized GeoParquet/Parquet facts and external views;
- `marts/`: derived analytical layers;
- `cache/`: bounded, replaceable working caches.

Do not commit a populated database, credentials, or downloaded upstream files.
For exact reconstruction and backup guidance, see
[`docs/recovery.md`](../docs/recovery.md).
