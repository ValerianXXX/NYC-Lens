# System architecture

## Data flow

```text
Public read-only sources and operator-approved local adapters
  -> append-only raw files, manifests, and SHA-256 hashes
  -> standardized GeoParquet and Parquet
  -> DuckDB catalog, compact facts, external Parquet views, and evidence index
  -> EPSG:32118 spatial calculations and H3 resolution 10
  -> NYC benchmarks, site queries, catchments, metrics, and comparisons
  -> CLI, FastAPI, bilingual web interface, and JSON/CSV/HTML/XLSX exports
```

DuckDB is the local query and metadata hub. Geometry is persisted in EPSG:4326; New York distance and area calculations use EPSG:32118. Walking areas use a versioned OpenStreetMap network, bounded Dijkstra reach, road corridors, and a concave hull. They are not represented as simple circles.

## Storage layers

1. **Raw** — immutable retrieval directories with URL, query, vintage, license, media type, byte size, SHA-256, HTTP status, and retrieval outcome.
2. **Standardized** — normalized fields, missing-value conventions, dates, units, coordinates, classifications, and stable entity IDs.
3. **Analytics** — address, parcel, boundary, H3, population, jobs, business, point-of-interest, mobility, health, environment, catchment, benchmark, and metric tables.

High-volume `building`, `property_record`, `regulatory_record`, `poi`, and `workforce_estimate` facts are stored in 227 partitioned Parquet files and queried through DuckDB external views. Transactions, snapshots, evidence indexes, and smaller analytical facts remain in DuckDB. Each external table has a manifest with partition hashes, row counts, byte counts, and atomic replacement metadata.

## Geographic scope

- **New York City:** parcel, building, 2020 Census, ACS, LODES, business, mobility, environment, regulatory, real-estate, and citywide benchmark coverage.
- **Greater New York:** the 30 counties or county equivalents and four states in OMB New York–Newark CSA 408, with TIGER, ACS, LODES, Overture, FEMA, CDC PLACES, and available MTA regional-rail evidence.
- Outside NYC, an Overture building is described as a building, never as a tax lot.

## Traceability

```text
site_metric
  -> calculation_version
  -> evidence_record_ids_json
  -> standardized_record.entity_id
  -> dataset_snapshot
  -> raw_file
  -> source_url
```

Zero counts retain the calculation version, source snapshot, and queried catchment even when no contributing entity exists. Derived ratios, proxy indexes, and discrete classifications are labeled as `derived_ratio`, `model_proxy`, and `model_classification`.

## Local-service boundary

- Refreshes are isolated by source; one failure does not erase successful sources.
- The API and web interface bind to `127.0.0.1` by default.
- Commercial mobility and real-estate adapters require operator-provided lawful files and declarations.
- Composite scoring remains blocked until real completed-project outcomes and a reviewed calibration protocol exist.
- A service database is unnecessary at the current single-machine scale; migration should follow measured concurrency or query-pressure evidence.
