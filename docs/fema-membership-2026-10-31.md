# FEMA response membership and polygon identity

October 31, 2026, America/New_York. This is a source/data-ingestion milestone,
not a production deployment, complete regional rebuild or location conclusion.

## What changed

The earlier importer used a count and offset pagination. A nonempty page could
lose a record or repeat an earlier page while the total retained polygons still
exceeded the existing 10,000-polygon floor. It did not verify the returned IDs,
transfer-limit flag, SFHA filter or final membership. It also used `GFID` as an
identity fallback, even though different flood polygons can share that value.

The importer now validates the layer contract, obtains count and object-ID
manifests for each of the 30 county envelopes, and requests explicit ID chunks.
Every page must contain exactly those distinct IDs and `SFHA_TF='T'`. After the
last geometry response it rechecks every envelope's IDs and count, including
empty envelopes. An error, omission, duplicate, unexpected ID, changed membership
or unsupported control value prevents publication. Raw responses and failed runs
remain available for inspection; active facts and successful freshness do not
advance on these failures.

Geometry chunks use only the selected IDs and output-format parameters. The
count/ID selection and final checks still use the county envelope and SFHA
filter; page validation also checks every returned SFHA flag. This avoids
repeating spatial predicates for a known member set. It follows Esri's documented
ID-subset retrieval workflow without relaxing the local checks.

Counts, IDs and page shapes use strict checks, including boolean-versus-integer
distinctions, duplicate JSON keys, non-finite tokens and explicit transfer-limit
flags. The shared `source_response` module also serves the existing Socrata and
TIGERweb checks, preserving their existing error categories and limits.

FEMA pages are read once with a byte limit and verified against the captured
size/hash. GeoPandas constructs geometry from those same parsed feature objects,
instead of reopening a path after validating another read. Repeated objects from
overlapping envelopes must have identical properties/geometry. Distinct objects
cannot silently collapse into the same source identity. Explicit non-WGS84 CRS
declarations are rejected.

Normal `GlobalID` identities remain unchanged. Without one, use the flood-area ID
scoped by community when available, then community/object ID or layer object ID.
`GFID` is never an individual polygon key. A later accepted refresh can therefore
correct identities previously collapsed by that fallback. Old raw evidence,
lineage and snapshot-specific exports remain historical; reports are not rewritten.

## Scope and limits

- At most 250,000 memberships per county envelope and 1,000,000 across the run.
  Overlap counts toward that retrieval budget. These are implementation limits,
  not claims about current FEMA coverage.
- Each request uses the smaller of the service limit and 500 IDs, with at most
  3,200 characters in the ID argument to bound GET URL length with 64-bit IDs.
- Parsed controls are limited to 8 MB, metadata to 2 MB and pages to 128 MB.
  These checks run after the existing downloader saves a response; they are not
  a transport/disk quota or a bound on all ingestion CPU/memory.
- Retain the exact CSA union clipping, geometry repair, SFHA-only filter,
  original 30-county requirement and 10,000 retained-polygon minimum.

The result and derived publication manifest add `response_coverage`, including
envelope memberships and unique observed object IDs. Input roles now include
`membership`; only `geometry` inputs are row provenance. A locally derived
manifest is not an independent source or authorization to publish a service.

Unchanged IDs do not establish unchanged content across all times. The service
does not offer an atomic snapshot through this workflow. Same-ID edits outside
repeated observations, changes after final checks, source study gaps and variable
effective dates remain possible. A response membership check does not establish
full regional coverage, current insurance treatment or parcel-level flood risk.

## Official interface check and small real observation

The [Esri Map Service query documentation](https://developers.arcgis.com/rest/services-reference/enterprise/query-map-service-layer/)
was inspected on October 31, 2026. It describes short/empty spatial-query pages,
transfer-limit flags, unrestricted ID-array responses and requesting records by
ID subsets. This motivates the membership checks; it does not prove a service
returns a transactionally stable dataset.

The [FEMA layer metadata](https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28?f=json)
returned HTTP 200 and advertised polygon/GeoJSON query support, an `OBJECTID`
field and a 2,000-record service limit. One metadata request and five bounded
queries over a small Lower Manhattan envelope retained five real polygons, with
matching before/after membership. Their five distinct `GlobalID` values shared
one `GFID`; this observation exposed the unsafe fallback assumption.

Those exact retained bytes were replayed through the candidate's response
validators and GeoPandas. This verifies a small real HTTP observation plus
validator replay, not a full regional adapter run, map/report acceptance or a
real site recommendation. No model or private service was called. No real
customer data or private database was used.

Two additional official Census downloads (2023 OMB delineation and 2024 TIGER
counties) were retained with their original HTTP receipts and replayed through
the existing region/county adapters in fresh, isolated catalogs. They produced
30 region members and 31 county/union boundaries. This is real public data;
replay counters are not additional network calls. The approximately 84 MB county
archive and the resulting databases remain local, outside Git.

Regional FEMA observations retain their failures:

- First attempt on `f62e3ac`: request 59 reached the observation harness's
  30-second read timeout, after 35,443,908 downloaded bytes.
- Second attempt on the same runtime, with the existing product's 300-second
  timeout: request 4 returned HTTP 500, after 73,683 bytes. Neither attempt
  published FEMA polygons; accepted Census prerequisites stayed intact.
- A single diagnostic requested the exact 355 IDs from that HTTP-500 query,
  omitting only redundant spatial/filter parameters. It returned HTTP 200,
  355 exact members and 622,990 bytes, all accepted by the existing validator.
  The calls occurred at different times, so this is not proof of a causal
  latency improvement or a regional success.

The revised runtime `67b1ee3` was observed separately:

- Third attempt: the geometry request returned HTTP 200, then the local file
  write failed before body consumption. An isolated no-network diagnostic
  reproduced the Windows path issue: a 263-character ordinary path failed,
  while an extended path and a short configured home succeeded. No system
  settings were changed. This was a local validation environment failure,
  not another upstream HTTP error.
- Fourth attempt, same runtime and request parameters with a short isolated
  `NYC_LENS_HOME`: 13 requests completed; request 14 returned HTTP 500. The run
  downloaded 5,633,310 bytes, retained 31 Census boundaries and published zero
  FEMA polygons. Full regional FEMA acceptance remains incomplete.

The campaign used 82 FEMA requests and 41,847,574 decoded response bytes,
including all four attempts and the single-ID diagnostic, against limits of
512 requests and 2,000,000,000 bytes. The earlier six-request tiny probe and two
Census downloads are accounted for separately. There were no automatic retries,
model calls, private data or production writes. Repeated unchanged retries were
stopped; a later regional trial needs a fresh recorded observation and budget.

## Reproduction and evidence

Use the existing source environment, configuration and data home:

```powershell
python -m pytest tests/unit/test_fema_membership.py tests/unit/test_fema_source_identity.py tests/unit/test_fema_response_contract.py tests/unit/test_fema_id_retrieval.py
python -m pytest tests/unit/test_geojson_completeness.py tests/unit/test_geojson_completeness_recovery.py tests/unit/test_geojson_audit_failure.py
python -m pytest tests/unit/test_multisnapshot_publication.py tests/unit/test_multisnapshot_publication_recovery.py tests/unit/test_multisnapshot_commit_ack.py tests/unit/test_multisnapshot_cli_outcome.py tests/unit/test_multisnapshot_metric_binding.py
```

The small official responses and their original size/hash receipts are included
as an offline reproduction, with upstream attribution and planning-only limits:

```powershell
python scripts/verify_fema_observations.py
```

This makes zero network requests and changes no catalog. It verifies the retained
five-record before/after sequence and the separate 355-ID diagnostic with the
current candidate validators. It does not turn the failed regional observations
into a completed import. CI runs this offline replay on Windows and Linux.

Actual refresh uses the existing command after the official region definition
and county boundaries have been prepared. It can download substantial public data:

```powershell
nyc-lens refresh --source omb_2023_cbsa_delineation
nyc-lens refresh --source tiger_county_2024
nyc-lens refresh --source fema_nfhl_special_flood_hazard_areas
```

On Windows without working long-path support, configure a short local
`NYC_LENS_HOME` with the repository's `config/` files before initializing a new
catalog. Existing homes require a consistent backup/restore of the catalog and
all referenced files; changing the environment variable alone does not migrate
them. The short-home workaround was observed here. General long-path support
across Python, DuckDB and geospatial libraries is not established by this work.

The [verification record](../reports/evaluations/2026-10-31/fema-membership/verification.json)
binds code, tests, original failures, logs and public-response observations.
The six initial membership cases were frozen before implementation: five bad
responses incorrectly published, while the valid shuffled case lacked the new
coverage receipt. A separately frozen identity case retained only one of three
distinct synthetic polygons. All seven unchanged cases pass after the change.
These are six exposed correctness gaps and one new observability contract,
not seven failed real regional refreshes.

Another frozen workflow models a service rejecting combined ID/spatial queries:
it fails before the request change and passes afterward, while all 120 spatial
count/membership control calls still run. The revised runtime passes all 58 FEMA
tests in 55.71 seconds. A full local run already in progress remains pinned to
`f62e3ac`; it is not a full run of the later request change. Hosted checks for the
final draft separately cover its exact merge tree.

That initial full run ended with **460 passed, 1 failed, 3 full-data skips** in
1457.95 seconds. The failure was the existing MCP cancellation case for mode
`2026-07-28`: the worker stopped, but database cleanup was not confirmed. Both
unchanged MCP cancellation modes passed in a separate 33.45-second diagnostic;
this does not explain or resolve the original failure. Its assertion remains.
The final request runtime separately passes 29 publication/recovery checks in
433.98 seconds and 35 configuration/analysis checks in 35.27 seconds. Final full
regression with exception observation and exact-head hosted checks remain
separate requirements; no whole-suite pass is claimed at draft creation.

Existing publication fixtures now provide metadata and pre/post ID responses.
Assertions still cover rollback, reader visibility, old lineage/export retention,
commit-acknowledgement loss and actual owned-process interruption. The floor test
supplies a consistent 9,999-ID response so the original regional minimum still
fails independently of the new membership checks. Extra retained control
responses increase the expected receipt count; no original gate is removed.

Current complete regression and hosted outcomes are recorded separately as they
finish. No model-quality gain, faster ingestion, reduced user effort or business
outcome has been measured; extra control requests add retrieval work.

## Recovery and remaining work

On rejection, inspect the recorded error and retained inputs. Retry as an explicit
new ingestion run after resolving the source condition; do not relabel an old
failure or delete its evidence. Consume the returned `standardized_parquet` path.
Keep catalog, raw snapshots, publication manifests and referenced exports together
in a backup. A reviewed code revert restores the earlier downloader, including its
known gaps, and does not undo accepted data. Restoring data requires a matching,
trusted complete backup. No schema migration or production deployment is part of
this draft.

Full regional ingestion, source effective-date coverage, all-adapter publication
recovery, transport/retention budgets, the complete local database and original
scenario/independent-user acceptance remain open. Composite scoring remains
blocked pending calibration.
