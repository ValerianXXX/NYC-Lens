# Geographic response completeness — 2026-10-31

## Problem and implemented behavior

The preceding publication work made updates atomic, but a partial upstream response could still satisfy a minimum coverage floor. For example, 250 synthetic NTA rows or 100 synthetic NYC-intersecting ZCTAs passed even when an upstream count or manifest declared more records. Atomic publication alone does not establish complete input.

The new shared helper changes the six NYC geography sources, the three configured Socrata planning-hazard sources, and the bounded TIGERweb ZCTA extract:

- Socrata: retain a `count(*) AS count` response, reject counts above the existing source limit, request `limit + 1` GeoJSON records, require the returned feature count to match, then retain and compare a second count. Reject invalid counts, HTTP errors, malformed JSON and transfer-limit flags. There is no automatic retry or partial publication.
- TIGERweb: retain the NYC bounding-box object-ID manifest, reject malformed/duplicate identifiers and extracts above 1,000 objects, fetch exact ID sets in chunks of at most 100, then compare a second manifest. Reject missing, unexpected or duplicate response IDs, transfer-limit flags and manifest changes. The assembled GeoJSON links all original response snapshots.
- Preserve the existing minimum/maximum geography coverage and five-borough filtering. Reject geometries that would silently disappear during normalization. Preserve valid geometry repair and the existing planning-only caveats; the response check is separate from geometry and business validation.
- Require an exclusive `completeness-check.json` audit before returning a checked response. It identifies the source, run, method, raw paths, hashes, observations and result. A failed receipt write prevents publication. Failed source attempts retain their actual request/received-byte counts and raw evidence. The audit status describes response coverage; only the ingestion run describes successful publication.

No model produces the counts, identifiers or decision. This prevents incomplete inputs from silently becoming the basis for AI explanations or deterministic analysis; it is not a measured improvement in natural-language task completion.

## Official sources and bounded live probe

Checked on **2026-10-31**:

- [Socrata count](https://dev.socrata.com/docs/functions/count.html) and [LIMIT](https://dev.socrata.com/docs/queries/limit.html) define the count and row-limit operations.
- [Esri layer queries](https://developers.arcgis.com/rest/services-reference/enterprise/query-map-service-layer/) document object-ID queries, feature subsets and transfer-limit responses.
- [The configured Census layer metadata](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/1?f=pjson) identified the 2020 ZCTA layer, service version 11.5, `OBJECTID`, supported GeoJSON output and a current maximum feature response of 100,000. The code does not assume that maximum proves completeness or cannot change.

A separate four-request, read-only probe received HTTP 200 for borough count (5), hurricane-zone count (8), the configured bounding-box manifest (409 IDs), and one requested ZCTA GeoJSON feature. The feature's `properties.OBJECTID` matched the requested ID. Requests used no credentials, model calls, production catalog or database refresh. These observations verify small interface samples; they do not verify all geometry downloads, the full real-data ingest path or commercial-location conclusions. The portable verification receipt records request metadata and response hashes; raw public responses are retained in the local task artifacts without redistributing geometry here.

## Verification

The baseline is `89a55d6a110d19c39e50d24168488d47e970eb6e`; the fixed contract commit is `5caa2be`.

Thirty-four fixed contracts failed against the prior runtime. The first implementation passed the same 34 contracts in 44.64 seconds. This count includes 31 rejection/evidence contracts and three accepted-response/audit contracts; it is not a count of 34 independent vulnerabilities. The old HTTP-200 error-body case already rejected data but did not meet the required retained-evidence contract. The old positive cases published valid data but lacked the new audit.

Supplementary verification exercises three actual synthetic geometry chunks (100/100/1), exact request and raw-byte accounting, receipt-storage failure, malformed JSON, HTTP failure, timeout without retry, and post-normalization geometry loss. Existing atomic publication, previous-export, separate-reader and owned-child-exit contracts remain in force. The two prior hard-coded snapshot counts increase to include the new two count-query receipts per source attempt; no data-preservation assertion is removed.

The initial full Windows run at `83f1f485c8bd6d416703e2635a0d3af8ce38db13` completed with **371 passed, three failed and three full-data skips** (terminal output: 1,290.32 seconds; JUnit duration is recorded separately). All three failures were outside geographic ingestion. The owner-crash setup failed separately on both the base and candidate: its single 15-second readiness window covered two processes. One isolated base-revision observation measured 9.422 seconds to owner readiness and 20.078 seconds to held-worker readiness; killing the owned supervisor then recovered the expected two queries/one comparison without reexecution. The initial two standalone diagnostic setup errors (before launching a worker) are disclosed in the receipt. The test now observes the existing owner handshake and retains a separate 15-second worker deadline and all recovery assertions.

The separate CLI cancellation setup also failed on the unchanged base. It now observes the actual durable RUNNING receipt within its original 20-second setup allowance, then retains a separate 20-second worker-readiness window. All cancellation, stopped-process, cleanup, partial-result and no-reexecution assertions remain. The product's process limits and success states are unchanged. The original 0.7-second process-deadline contract is unchanged; it separately passed on both base and candidate, so an initial full-run failure is retained rather than claimed fixed by a test relaxation.

An additional frozen combined-error case found that an unwritable rejection audit could hide the already completed request totals. It failed before the correction, then passed without changing its assertions. The outward storage error now carries the original totals; publication still fails. That case plus the repaired owner-crash test passed (2 tests, 31.50 seconds); the repaired CLI test and unchanged deadline contract passed separately (2 tests, 71.23 seconds).

The final runtime is `5c8b14faefd80e1a5b77bd90db9324681280d9ae`. Its complete local regression is running at receipt creation; exact-delivery hosted checks have not yet been observed. Neither is counted as passed. The unchanged 0.7-second check remains a timing concern until final observations; its first failure correctly reported a stopped timed-out process, but the worker had not created its `started` marker.

The [verification receipt and retained logs](../reports/evaluations/2026-10-31/geojson-completeness/verification.json) distinguish the initial full result, exact source revisions, subsequent checks and any still-running final checks. The evidence-packaging script's initial regex failure and subsequent newline normalization are disclosed; the original partial progress snapshot and terminal failed result are retained. No UI changed in this milestone, and no new browser, model, Hermes, MCP Apps or independent-user result is claimed.

## Running, inspecting and recovering

Install the existing dependencies with `pip install -e ".[dev,agent]"`; there are no new packages or configuration values. Existing `nyc-lens refresh --source ...` entry points use the helper automatically for the named sources. Keep any real refresh separate from the synthetic demo and confirm source-specific usage terms before downloading new datasets.

Refresh results retain their existing fields and add `completeness_check`, the local audit path. `standardized_parquet` remains snapshot-specific. ZCTA `snapshot_id` now identifies the assembled, checked GeoJSON; its manifest's `query.upstream_snapshot_ids` points to the original ID/page snapshots. `raw_bytes` counts retained response bytes, excluding the assembled copy. Socrata counts and metadata are included. Run request counts include attempted requests, including a timeout. Partial network bytes without a completed raw receipt are not recovered as a byte total.

On a rejected update, inspect the failed ingestion run and audit before explicitly retrying. Compare the retained expected count/ID manifest with the raw response. Restore upstream availability, investigate a changed dataset, or review an intentional source-limit change; do not mark the failed run successful or raise limits merely to bypass missing-data findings. The previous successful catalog publication, lineage and exports remain available. Interrupted attempts preserve the existing indeterminate-state recovery rules.

No database migration, release or production deployment is required to review this branch. A reviewed code revert needs no schema rollback but restores the earlier incomplete-response risk. Close writers before restoring a matching catalog/data/config backup; preserve raw receipts and historical exports. Do not delete evidence or rewrite old analysis results to emulate a rollback.

## Remaining limits

- Equal Socrata counts cannot detect same-count row replacement, equal-count duplication/omission, or source errors. This is count coverage, not exact source membership verification.
- Equal ArcGIS ID sets cannot prove geometry/attribute values stayed unchanged while chunks were fetched. Neither service check creates a transactional upstream snapshot. Audits explicitly set `stable_upstream_snapshot: false`.
- The ZCTA check covers the configured bounding box, then the existing NYC borough intersection. It does not claim completeness of the national dataset or that ZCTAs are USPS delivery boundaries.
- Feature-count/request bounds and HTTP timeouts are not a full ingestion memory, response-byte, disk-retention or cumulative deadline policy. Large geometry payloads still need resource validation.
- Other CSV/ZIP/paged adapters, source-record identity validation and all-record metric provenance, a complete 17 GB data rebuild, full-data backup/restore and performance, and independent end-user acceptance remain open. No current authorization or license gap is bypassed.

M3 remains incomplete. Successful synthetic checks and the small official probe do not certify a real neighborhood recommendation.
