# Acceptance record

Acceptance reflects the local build and its recorded evidence as of 2026-08-31. It does not claim that gated commercial sources or real-project calibration data exist.

| Capability | Status | Evidence boundary |
|---|---|---|
| Address standardization | PASS | Public Census geocoder plus local NYC parcel matching where available |
| Six catchment types | PASS | Three projected radial buffers and three OSM network walking areas |
| Demographics | PASS | ACS estimates and margins of error; Decennial baseline kept separate |
| Workforce | PASS | LODES jobs and industries, never described as unique people |
| Competition | PASS | Direct, secondary, substitute, complement, status, and density measures |
| Mobility | PASS | Subway, bus, rail, bike, pedestrian, traffic, and area aggregates |
| Property and regulation | PASS | Public transaction and administrative screening evidence |
| Health and environment | PASS | Area values and mapped overlap only |
| Traceability | PASS | Source, snapshot, year, confidence, calculation version, and evidence IDs |
| Multi-site comparison | PASS | Like-for-like raw metrics without an uncalibrated score |
| Local API and UI | PASS | FastAPI, responsive MapLibre interface, English default, Chinese localization, dark mode |
| Exports | PASS | JSON, CSV, XLSX, and HTML site reports; comparison exports |
| Automated tests | PASS | 67 passed |
| Local QA | PASS | 66 PASS, 0 FAIL, 5 documented warnings |
| Clean initialization | PASS | Isolated schema, source registry, and external-view probe |
| Commercial vendor feeds | GATED | No contract, credential, purchase, or redistribution right assumed |
| Composite score | GATED | `BLOCKED_CALIBRATION` pending real outcomes and model review |

## Publication boundary

The public Git repository contains source, configuration, tests, documentation, and reproducible ingestion logic. It excludes the populated 17 GB working data directory, credentials, generated reports, and local outputs. Upstream datasets retain their own terms and attribution requirements.

## Known non-blocking warnings

- Some public sources have publisher-defined coverage gaps or unspecified portal license wording.
- Static schedules do not represent observed service.
- Survey and model estimates retain uncertainty and interpretation limits.
- Walking-network completeness varies with OpenStreetMap coverage.
- A third-party Starlette TestClient deprecation warning remains upstream and does not fail the suite.
