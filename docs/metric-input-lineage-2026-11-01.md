# Saved metric inputs, November 1, 2026

A comparison could calculate from several active source parts but label every
site with the newest source snapshot. The evidence endpoint then looked only in
that snapshot. The original disposable two-address probe calculated counts of
one and three, yet resolved only **one of the four declared input records**.
The first address also inherited the other source part's year.

This change binds each declared evidence ID to its active typed record at
calculation time and saves the source/snapshot identities in the existing result
JSON. It does not rewrite calculations, replace the database, or backfill old
analyses from today's data.

## Behavior

- A direct metric with one actual primary-source snapshot names that snapshot.
  Multiple snapshots are listed explicitly instead of borrowing one latest date.
- Catalogue-derived year labels follow the actual primary inputs. Separately
  computed periods, such as transaction date ranges, retain their own meaning.
  Missing source metadata remains missing.
- Evidence lookup uses the saved record type, identity, source and snapshot.
  Retained history for the same identity cannot become another current input.
  Later refreshes cannot silently rebind a saved analysis. Deleted lineage remains
  unresolved and still identifies its original bound source/snapshot.
- Secondary source IDs already declared by the calculation are retained, including
  population weighting evidence. Undeclared inputs remain an explicit limitation.
- Missing active identities or unverified completed ingestion prevent ranks.
  Different source parts from one verified completed ingestion may still be
  compared when units, year, measure and calculation version agree. A different
  source set without that proof cannot share a rank.
- English and Chinese comparison cells expose input-evidence links even when a
  metric is missing or incomparable. A missing value does not become zero.
  Multiple saved source dates remain visible in the UI and site HTML reports.
  Comparison JSON, CSV and XLSX retain the input basis.

## Evidence and validation

The [original nine contracts](metric-input-lineage-protocol-2026-11-01.md) were
committed at `22b1dd7` before the runtime repair. Eight initially failed together;
the ninth same-ingestion comparability contract also failed separately. All nine
then passed unchanged. The original 13 related checks passed before modification.
An initial test-authoring syntax error is retained separately, not counted as a
product failure or erased.

Browser review of the first implementation exposed the missing evidence-link
entry point. A subsequent ordinary-data-path check found that catalogue vintage
alignment only worked in the synthetic-display branch. Its separate frozen test,
commit `c30c948`, failed with two `2026-10` labels instead of `2024-06` and
`2024-06 / 2026-10`. It now passes. The ordinary-data code path is exercised with
fictional fixture records; this is **not a real-location observation**.

| Observation | Before | Repaired candidate |
| --- | --- | --- |
| Declared records resolved in the two-site probe | 1 / 4 | 4 / 4 |
| Calculated competitor counts | 1 and 3 | 1 and 3 |
| First site's snapshot/year | Unrelated newer source part | Its actual input source part |
| Original nine frozen contracts | 0 / 9 | 9 / 9 |
| Ordinary-data catalogue-vintage contract | Failed | Passed |
| Related regression, failure, legacy and UI checks | Original baseline retained | 46 passed, 140 existing Shapely deprecation warnings, 61.05 seconds |

The first incomplete 527-test invocation was deliberately stopped after the new
counterexample was identified, before further runtime edits. Its nonzero exit,
partial log and reason are retained. It is not a full-suite pass. Final complete
regression on the repaired runtime passed **525 tests, with 3 unchanged skips and
201 warnings, in 694.86 seconds**. The skipped integration checks require the
separate populated database and public sample analyses. No skip or success
condition was changed. [Hash-bound verification and retained logs](../reports/evaluations/2026-11-01/metric-input-lineage/verification.json)
record the exact runtime, frozen contracts and artifact bytes.

Actual English and Chinese browser journeys confirmed the addresses and business
type, ran the normal owned worker, displayed two map markers, opened the formerly
hidden evidence link and preserved the same completed job after refresh. Actual
JSON, CSV and XLSX downloads preserve counts 1/3, all four declared input records,
both source parts, withheld ranks and missing population/rent. The initial export
verifier mistakenly read the workbook overview sheet; its failure is retained,
and the corrected verifier reads the existing metric sheet without changing the
application or downloaded files. CSV and XLSX each contain 252 metric rows.

Direct browser navigation to the HTML export returned `ERR_BLOCKED_BY_CLIENT`.
The ordinary HTTP export passed, and the exact downloaded file was visually
checked through a separate read-only loopback preview. The wide report table
requires horizontal scrolling; both source dates were inspected. This does not
relabel the blocked direct navigation as passed. [Browser receipt](../reports/evaluations/2026-11-01/metric-input-lineage/browser-verification.json)
distinguishes these levels. Desktop browser automation is the tested environment;
mobile devices and independent humans were not tested in this milestone.

The repaired runtime is `c0d720a2de7fc20fda439074ac719c02af386ceb`. No model call,
provider migration or purchased service is involved. These are provenance and
workflow improvements, not measured model-quality, latency or cost gains.

## Reproduce

Install the existing environment with `pip install -e ".[dev,agent]"`. The focused
contracts are:

```text
python -m pytest tests/unit/test_metric_input_lineage.py tests/unit/test_metric_lineage_compatibility.py tests/unit/test_metric_source_vintage.py tests/unit/test_evidence_snapshot_contract.py
```

For a new, explicitly synthetic browser database, leave `NYC_LENS_HOME` and
`NYC_LENS_DATABASE` unset and choose a path that does not exist:

```text
python tests/fixtures/browser_analysis_server.py --home /path/to/new-synthetic-home --port 8790 --split-source
```

The server binds only `127.0.0.1`, disables AI, and refuses an existing home. Use
the quoted Demo Avenue addresses, confirm the restaurant type and 500 m scope,
then inspect competition counts, missing population/rent, input evidence, map
and report exports. The two artificial source vintages deliberately make the
competition values incomparable; this is expected and must not be bypassed.

## Interface compatibility

No database schema, dependency or configuration migration is required. New saved
metric objects add `evidence_binding` (`METRIC_INPUT_BINDINGS_V1`). Existing saved
results retain the old bounded lookup (`LEGACY_SNAPSHOT_SCOPE`); they do not gain
retrospective guarantees.

Evidence pagination retains `total_evidence_ids`, `limit` and `offset`, and adds
`total_evidence_records` and `next_offset`. One ID may have multiple typed bindings;
clients should follow `next_offset`, not infer row counts from unique IDs. Queries
remain bounded to 250 rows per page and the existing maximum offset. Unresolved
rows add `bound_source_id` and `bound_snapshot_id`; a failed lookup still has a
null resolved `snapshot_id`.

CSV/XLSX retain existing columns and add `source_snapshots_json`,
`evidence_bound_records`, `evidence_unresolved_ids`, `source_observations_json` and
`source_snapshot_dates_text`. Column-name readers remain compatible; positional
consumers must account for additive columns. Source retrieval date is not evidence
of currentness or expiry. Raw file links remain snapshot-level candidates, not
verified per-entity file bindings.

## Limits and rollback

Bindings cover the evidence IDs declared by the metric engine. They do not prove
complete lineage for every implicit geometry, ACS weight or benchmark input, nor
that every declared ID is a minimal causal input. Full-scale performance on the
separate regional database remains unmeasured. The lookup reuses the existing
fixed typed-table allowlist, caches IDs per analysis and batches lookups; it adds
no vector service or unrestricted model SQL.

The separate full regional data, independent user acceptance and all-metric input
audit remain outstanding. Browser automation with synthetic data is not an
independent human or a real commercial-location decision. Composite scoring stays
disabled.

For a reviewed release, finish active jobs and preserve the catalog, referenced
generations and task receipts together. A normal code revert restores the prior
lookup, including its missing multi-snapshot evidence and date limitations. Keep
saved JSON and exports; do not erase them to downgrade. Older code can read the
additive result data but does not enforce the new per-record binding. No production
migration, source refresh, release or deployment was performed.
