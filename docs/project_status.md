# Project status — 2026-08-31

## Current conclusion

- **NYC five-borough MVP:** PASS.
- **Greater New York public-data layer:** PASS for the fixed 30-county, four-state OMB CSA 408 footprint.
- **Local product status:** `COMPLETE_WITH_EXPLICIT_EXTERNAL_GATES`.

The local product includes ingestion, normalization, DuckDB and Parquet storage, spatial queries, address analysis, multi-site comparison, traceable metrics, bilingual responsive UI, exports, tests, QA, and recovery documentation.

## Verified local scale

| Item | Current verified value |
|---|---:|
| Logical database tables/views | 44 |
| Registered source definitions | 62 |
| External high-volume fact views | 5 |
| External Parquet partitions | 227 |
| Unit/integration tests | 67 passed |
| QA summary | 66 PASS, 0 FAIL, 5 WARN |

The working catalog and source snapshots are local artifacts and are not committed to GitHub. A public clone rebuilds them through `nyc-lens init` and `nyc-lens refresh`.

## Explicit external gates

- Commercial movement and commercial real-estate data require operator-supplied lawful access, files, permissions, and declarations.
- NJ TRANSIT requires credentials and legal review.
- CTtransit requires affirmative license acceptance by the operator.
- A current credential-free PATH GTFS archival right was not established.
- Google Places, Yelp, and individual movement traces remain outside the permanent database.
- Composite scoring remains `BLOCKED_CALIBRATION` until real completed-project outcomes and a reviewed model card exist.

These are intentional safety and evidence boundaries, not silently substituted data.
