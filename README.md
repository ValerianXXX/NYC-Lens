# NYC Lens

NYC Lens is a local-first, auditable trade-area and commercial-location intelligence system for Greater New York. It converts public datasets into address-level catchments, traceable metrics, site comparisons, maps, and exportable reports without sending the working database to a hosted service.

**2026-10-27 upgrade:** bilingual requirements → editable two-address confirmation → existing analysis → map and sourced differences → report export. Try `pip install -e ".[dev,agent]"` then `python -m nyc_lens.demo --port 8766` for an isolated **synthetic** demonstration without the 17 GB dataset. AI interpretation is optional and disabled by default; no composite score is invented. Standard local MCP tools are available for compatible agents. [Configuration, validation evidence and remaining limits](docs/natural-language-comparison-2026-10-27.md) distinguish synthetic engine tests, a real Hermes planning call and verified Hermes MCP tool dispatch from the still-unverified model-driven conversation, full-data analysis and MCP Apps map integration.

The current release covers the five boroughs of New York City and the 30 county or county-equivalent areas in OMB New York–Newark CSA 408 across New York, New Jersey, Connecticut, and Pennsylvania.

## What it does

- Standardizes public addresses and resolves coordinates, Census geography, state, county, and NYC tax lots where available.
- Builds 250 m, 500 m, and 1 km projected buffers plus 5-, 10-, and 15-minute walking areas from a versioned OpenStreetMap network.
- Calculates ACS 2020–2024 population, income, age, household, education, language, housing, vehicle, and commuting measures with estimates and margins of error.
- Calculates 2023 LODES jobs and workplace-industry structure without treating jobs as unique people.
- Separates direct competitors, substitutes, demand complements, closed locations, and density measures.
- Adds subway, bus, regional rail, bike-share, pedestrian, road, and area-weighted TLC mobility evidence.
- Adds property, transaction, permit, violation, 311, environmental, flood, health, visitor, and public-facility evidence where licensed public sources are available.
- Produces transparent audience and market-signal classifications without inventing an uncalibrated composite score.
- Preserves source IDs, snapshot IDs, years, confidence, calculation versions, evidence IDs, warnings, and missing-data flags.
- Provides a CLI, FastAPI service, responsive MapLibre interface, multi-site comparison, and JSON, CSV, XLSX, and HTML exports.

## Why the 17 GB local data directory is not in Git

The full local build contains roughly 17 GB of downloaded and standardized data, including a working DuckDB catalog of roughly 4.8 GB. It is intentionally excluded from Git for three reasons:

1. GitHub blocks regular Git files larger than 100 MiB and recommends keeping repositories far smaller than this dataset.
2. Several upstream sources require source-specific attribution or have terms that should not be replaced by a blanket redistribution license.
3. Rebuilding from the authoritative endpoints produces fresher retrieval timestamps, hashes, and lineage than copying an old machine snapshot.

The application remains usable: the repository includes the schema, source registry, ingestion adapters, transformations, tests, and commands needed to rebuild the database locally. A new clone can initialize immediately; address analysis becomes available after the required sources have been refreshed.

## Requirements

- Windows 10/11, macOS, or Linux
- Python 3.11–3.13; Python 3.12 is recommended
- Approximately 25 GB of free disk space for a complete local rebuild
- Network access to the configured public data endpoints

Optional credentials are documented in `.env.example`. Do not commit a populated `.env` file.

## Quick start on Windows

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"

nyc-lens init
nyc-lens verify-clean-init
nyc-lens refresh
nyc-lens qa
nyc-lens serve --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`. The interface starts in English, supports a Chinese localization mode, and stores language and light/dark theme preferences only in the current browser.

The service binds to `127.0.0.1` by default. It is not a public deployment.

## Quick start on macOS or Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

nyc-lens init
nyc-lens verify-clean-init
nyc-lens refresh
nyc-lens qa
nyc-lens serve --host 127.0.0.1 --port 8765
```

## Data refresh and status

```powershell
# Refresh every implemented source. Sources are isolated, so one failure does not erase others.
nyc-lens refresh

# Refresh a single source.
nyc-lens refresh --source acs_2024_5yr
nyc-lens refresh --source mta_hourly_2025

# Inspect the catalog, external Parquet views, gated adapters, and scoring gate.
nyc-lens status
nyc-lens storage-status
nyc-lens adapters-status
nyc-lens scoring-status

# Validate source files, hashes, geometry, values, and evidence lineage.
nyc-lens qa
```

Raw snapshots are append-only under `data/raw/{source_id}/{vintage}/{retrieved_at}/`. Do not run multiple write-oriented commands against the same DuckDB catalog at the same time.

Sources that require an account, explicit license acceptance, commercial rights, or private credentials remain visibly gated. The refresh process does not silently sign up, purchase, scrape, or fabricate those sources.

## Analyze one site

```powershell
nyc-lens analyze `
  --address "1501 Broadway, New York, NY 10036" `
  --business-type "full_service_restaurant" `
  --catchments "250m,500m,1km,5min_walk,10min_walk,15min_walk"
```

The walking-network cache is reused when available. If a required local network snapshot does not exist, NYC Lens uses only the bounded public Overpass endpoints defined in configuration.

## Compare sites

```powershell
nyc-lens compare `
  --address "100 Supreme Court Drive, Mineola, NY 11501" `
  --address "920 Broad Street, Newark, NJ 07102" `
  --catchments "500m,1km" `
  --comparison-catchment "500m"
```

A comparison accepts 2–20 public addresses. It reports like-for-like raw values, within-batch percentiles, source years, confidence, and missing items. High-to-low ordering does not mean that a site is better and is not investment advice.

## Local API

Interactive API documentation is available at `http://127.0.0.1:8765/docs` while the local server is running.

Primary endpoints include:

- `GET /api/geocode?address=...`
- `POST /api/site-analysis`
- `POST /api/site-comparison`
- `GET /api/site/{site_id}`
- `GET /api/site/{site_id}/evidence`
- `GET /api/site/{site_id}/report?format=json|csv|xlsx|html`
- `GET /api/comparison/{comparison_id}/report?format=json|csv|xlsx`
- `GET /api/adapters/commercial-mobility`
- `GET /api/adapters/commercial-real-estate`
- `GET /api/scoring/status`

## Tests

```powershell
pytest
```

The current suite contains 67 passing tests. Tests do not download the complete dataset. A fully populated local build should also run `nyc-lens qa`, sample analyses, regional samples, and storage checks.

## Evidence and interpretation boundaries

- Administrative records, observed counts, survey estimates, model proxies, and unknown values are labeled separately.
- ACS values are area-level survey estimates with margins of error; they do not describe a specific person, household, building, or address.
- CDC, health, ethnicity, language, and income measures must never be assigned to an individual.
- Overture buildings outside NYC are buildings, not tax lots.
- Personal mobility traces, device identifiers, names, phone numbers, email addresses, and individual behavioral profiles are out of scope.
- Commercial mobility and commercial real-estate adapters remain gated until the operator supplies lawful data, permissions, and required declarations.
- Composite scoring remains `BLOCKED_CALIBRATION` until real completed-project outcomes, holdout criteria, and a reviewed model card exist.

## Documentation

- [Architecture](docs/architecture.md)
- [Methodology](docs/methodology.md)
- [Data catalog](docs/data_catalog.md)
- [Metric dictionary](docs/metric_dictionary.md)
- [Licensing and attribution](docs/licensing.md)
- [Privacy boundary](docs/privacy.md)
- [Installation, backup, and recovery](docs/recovery.md)
- [Project status](docs/project_status.md)
- [Acceptance record](docs/final_acceptance.md)
- [Post-acceptance expansion](docs/expansion_plan.md)

## License

The source is publicly visible but no blanket open-source license is granted. See [LICENSE](LICENSE) and [licensing and attribution](docs/licensing.md). Every upstream dataset retains its own license, terms, attribution, and use limitations.
