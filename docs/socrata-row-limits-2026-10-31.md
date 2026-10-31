# Socrata response row limits — 2026-10-31

The shared `download_socrata_csv` previously sent the caller's `$limit` and
accepted the response without checking whether more records existed. A capped
response could proceed into calculations as if the download were complete.
The existing count reconciliation in some specialized importers did not provide
a common check for every caller of this helper.

The helper now requests `limit + 1` and checks logical CSV records before returning
the raw snapshot to its caller. More than `limit` records fails explicitly. An
exact fit is accepted only after reaching CSV EOF; quoted newlines, blank lines,
BOMs and numeric aggregate values do not inflate record counts. A header-only
response can legitimately contain zero records, while zero-byte or unparseable
CSV cannot pass as an empty dataset. This is a data-input correction for the
existing analysis engine; no model, source, metric or composite score was added.

## What is recorded and what it proves

Raw CSV and the original download manifest remain unchanged. A new, exclusively
created `row-limit-check.json` beside the CSV records its snapshot ID and SHA-256,
source, requested/allowed limits, rows read, EOF observation, check time, method
version and one of:

| Status | Meaning |
| --- | --- |
| `WITHIN_REQUESTED_LIMIT` | This parsed response fits the row budget and reached EOF. |
| `ROW_LIMIT_EXCEEDED` | This response contains more records than the caller accepts. |
| `CSV_UNVERIFIED` | Parsing/reading failed; the raw download is retained. |

Every check explicitly records `full_upstream_snapshot_verified: false`.
A server that silently enforces a smaller cap, omitted upstream records, changing
source data, or incomplete pagination cannot be ruled out by this method.
`csv_eof_observed` describes the local CSV parser, not an upstream consistency
token. Records counted before a parser error may be only a partial count.

The raw `dataset_snapshot.status=SUCCEEDED` continues to mean the HTTP download
was retained. It is not permission to publish facts. The caller receives an
exception on overflow, parse failure or inability to save the check. An existing
check is never overwritten. Invalid row limits fail before network access;
booleans, strings, non-integral values, zero and negatives are rejected.

The check reaches all callers of this helper in the existing ingestion modules.
It does **not** retrofit transaction atomicity into every multi-stage importer or
cover downloads that call `download_snapshot` or other transports directly.
Previous-publication preservation was exercised for exactly the restaurant,
hotel and air/health adapters covered by [the preceding milestone](socrata-publication-2026-10-31.md).
The existing stricter MTA partition and specialized count checks remain intact.

## Observed comparison

The 20 new contracts were frozen in
`177465d1b71ad02ce0087d762eb91377316d2b4a`, based on prior delivery
`78ef184329d1ebae29a58c7c85f7cf772bb8b58d`. They produced **20 failures** with the
old runtime. The same unchanged contracts pass after runtime
`f9b151392890c04fb291868fa82d535ab17d77f6`. Together with the 23 existing source
publication/recovery checks, **43 tests passed in 14.39 seconds**.

These include a deliberately capped five-record source, exact-cap grouped
results, CSV record boundaries, aggregate values, invalid limits, malformed CSV,
occupied audit evidence, empty hotel publication and failure preserving previous
facts, lineage, source freshness and export bytes for each of the three adapters.
Downloads are synthetic in these contracts; CSV parsing, DuckDB, Parquet and
publication transactions execute for real. Original failure logs are retained.

Two separate live calls to the configured NYC Open Data restaurant endpoint on
2026-10-31 returned HTTP 200 through `httpx`:

| Probe | Allowed / requested records | Actual response | Result |
| --- | --- | --- | --- |
| Scalar `count(*)` | 1 / 2 | 1 record, 24 bytes | Accepted, EOF observed; 0.5674 s |
| `camis` records | 1 / 2 | 2 records, 30 bytes | Expected overflow rejection; 0.2123 s |

These are live interface checks, not synthetic results. They ran in a newly
created isolated catalog, imported zero facts and make no neighborhood/business
claims. The timings are single observations, not a performance comparison.
The aggregate's numeric value is not treated as its CSV record count. Raw files
remain local; the committed probe receipt contains request/check metadata and
hashes, not public restaurant record values. No model calls, paid service
activation, production data change or full-city refresh occurred.

The [verification receipt](../reports/evaluations/2026-10-31/socrata-row-limits/verification.json)
contains full regression results, log/source hashes, installed versions and the
creation-time hosted status. Its adjacent live receipt records both real calls.
The preceding baseline had 256 passes and three full-data skips; new results are
reported separately rather than replacing that historical run. Hosted CI tests
Ubuntu's full suite and adds these contracts to the existing Windows suite.

The full local suite passed **276 tests**, with **three unchanged full-data
skips** and 90 existing deprecation warnings, in **285.76 seconds** on Windows,
Python 3.11.3, DuckDB 1.5.6 and Pandas 2.3.3. All three existing JavaScript syntax
checks passed. The probe's offline and existing-output refusal paths were also
exercised without additional network calls. No test or assertion was weakened;
the source runtime remained unchanged during the full run and delivery preparation.

## Run and recover

No new configuration, credential, dependency or schema migration is required.
Existing refresh commands use the check automatically for helper-based downloads.
`limit` remains the maximum accepted record count; the outgoing HTTP `$limit`
increases by one. Source-specific filters, groups, order and validity dates remain.
Older endpoints with their own maximum may reject the enlarged request; no
automatic cap increase, pagination, retry or truncation is introduced.

```powershell
pip install -e ".[dev,agent]"
python -m pytest tests/unit/test_socrata_row_limits.py tests/unit/test_socrata_publication.py tests/unit/test_socrata_publication_recovery.py
python -m pytest

# Optional: two real public requests, never part of automated tests.
# Choose a new directory for every observation; existing evidence is not replaced.
python scripts/probe_socrata_row_limits.py --online --output outputs/socrata-row-limit-probe
```

On `SOCRATA_ROW_LIMIT_EXCEEDED`, retain the failed attempt and inspect its raw
manifest plus check. Review upstream count/version/partition requirements before
choosing a new budget or implementing verified pagination; simply increasing
the cap is not proof of completeness. On `SOCRATA_CSV_UNVERIFIED`, inspect the
retained file locally. Audit path/permission failures also stop the helper;
recover storage permissions without replacing existing evidence. Retry explicitly
after addressing the cause, generating a new snapshot. Do not rewrite failed
attempts or pretend their raw download status is successful publication.

Before adopting this change on a populated installation, close writers and make
a verified matching catalog/data/configuration backup. Review and merge the
stacked PRs in dependency order; this branch builds on PR #9. A normal reviewed
revert needs no database migration but removes the common guard. Retain all raw
checks and previous generations; restore a matching closed backup if a separate
data rollback is required. No release, deployment or data restoration has occurred.

## Remaining acceptance and references

- Complete upstream snapshots, safe pagination and consistent multi-snapshot
  sources remain open; this check cannot detect omissions below the configured cap.
- Direct-download adapters and all-adapter atomic publication remain open.
- CSV verification uses chunks of at most 10,000 records, but downloading still
  precedes verification. This is not a total byte, cell-size, time, memory or disk
  quota. It adds a sequential parse pass; full-data overhead is not measured.
- Every metric's actual-record provenance, retained-file policy and full-data
  restore/performance still need acceptance. The approximately 17 GB data is absent.
- There is no new UI, model, Hermes/MCP Apps or independent-user acceptance in this
  milestone, and no measured AI quality gain. The full NYC-Lens GOAL remains open.

Official documentation rechecked on 2026-10-31:
[Socrata LIMIT](https://dev.socrata.com/docs/queries/limit.html) applies the limit
after aggregation and documents endpoint-specific maxima;
[OFFSET](https://dev.socrata.com/docs/queries/offset.html) describes pagination;
[count](https://dev.socrata.com/docs/functions/count.html) describes the scalar/group
aggregate. These support the query semantics, while the local and live observations
above establish this implementation's tested behavior. No third-party code was
copied and no endpoint or API version was migrated.
