# Comparison and evidence boundaries — 2026-10-29

This milestone improves correctness in the existing two-address workflow. It does not replace the database, geospatial engine, UI or model adapter. Source baseline: `c4138ba5676c932160e9e036dfe557eafd04eb4f`, on the unmerged natural-language comparison branch. No production deployment or database migration occurred.

## Findings and changes

Previously, numeric values could receive relative ranks despite different units, years, source snapshots or calculation methods. Equal values used dense ranks and an ambiguous percentile. Duplicate addresses survived planning until execution. Evidence lookup joined all retained lineage for an entity, so a later snapshot could appear as evidence for an older analysis.

The shared comparison-basis function now requires at least two finite numeric values on the same documented basis: unit, source ID, snapshot ID, source year, measure type and calculation version. Missing or mismatched fields suppress ranks and decision differences. Available raw values, provenance and limits remain visible. A conservative rejection does not prove the values could never be harmonized; a future explicit harmonization method would need its own validated provenance.

Competition ranks preserve gaps after ties: `[200, 200, 100]` gives `[1, 1, 3]`. Percentiles count strictly smaller other sites plus half of equal other sites; the same example gives `[75, 75, 0]`. Equal pairs give `[50, 50]`. These are within-request descriptions, not regional benchmarks or desirability scores. Composite scoring remains disabled.

`evidence.py` is a read-only domain service used directly by both FastAPI and MCP. It binds direct metrics to the exact saved source/snapshot and derived metrics to the analysis snapshot set. Later versions of an entity cannot silently replace that evidence. Missing lineage remains unresolved, and current snapshot catalog status is exposed separately. Raw-file references are explicitly snapshot-level candidates, not verified entity-to-file links. This preserves a limitation of the existing schema rather than claiming stronger provenance.

CSV and XLSX exports retain comparability, reasons, calculation version and percentile meaning. Existing stored JSON reports are not rewritten. The Web UI withholds ranks from legacy comparisons lacking the new basis metadata and asks for recalculation. Changed calculation code participates in the calculation-version hash; there is no schema migration.

## Frozen cases and before/after result

The [protocol](../evaluations/comparison-boundaries-plan-2026-10-29.json) was saved before product edits. The [initial result](../evaluations/comparison-boundaries-before-2026-10-29.json) preserves five passing and nine failing cases on the baseline. The [fixture addendum](../evaluations/comparison-boundaries-fixture-correction-2026-10-29.json) discloses a missing required `addresses=[]` argument in a test helper, revealed once the earlier ranking assertions passed. No expected result, input value, case or denominator was removed. This is a corrected fixture, not an untouched blind evaluation.

| Cases | Initial | Final behavior |
| --- | --- | --- |
| English, Chinese, ambiguous three addresses, unsupported business | 4/4 passed | 4/4 passed; unresolved requirements retained |
| Duplicate addresses | Failed | Planning asks for distinct addresses |
| Different unit, year, source, missing provenance, calculation method | 0/5 passed | 5/5 passed; no rank or subtraction |
| Tied values | Failed | Defined competition ranks and other-site percentiles |
| Missing source | Passed | Null, not zero; no fabricated difference |
| Later ingestion and saved report | Failed | Original snapshot and original report retained |
| Unconfirmed/oversized request | Failed | Rejected before analysis writes |

Final boundary result: **14/14 passed**. Four additional contracts cover invalidated/missing catalog lineage, evidence pagination bounds, CSV/XLSX consistency and non-finite/boolean values. The original full suite had 70 passed and three skipped. The final suite had **88 passed, three skipped**, with no failures/errors; the skips require the unavailable populated local catalog. Existing Shapely deprecation warnings remain recorded. UI-only copy, layout and resource-version changes followed that full run and were checked with the existing UI contracts and actual browser interactions.

These are authored synthetic tests using the real local DuckDB, spatial, API and report code. They do not measure general language interpretation, live model quality or commercial decision success. No new model request was made and no model latency/cost benefit is claimed. The earlier real Hermes model workflow remains historical evidence in [its dated record](natural-language-comparison-2026-10-27.md), not a new run here.

## Browser observations

The isolated demo was started from an empty synthetic database. English and Chinese requirements went through editable confirmation and actual analysis. Two map markers appeared; direct competitor counts were 1 and 3, with a first-minus-second difference of -2. Population and asking rent stayed missing. The source link opened the original synthetic snapshot, year, license, calculation version and unresolved count. A JSON report was downloaded through the page and its values/provenance checked separately.

The initial success copy incorrectly described every output row as comparable; it now says metric rows and directs the user to comparability/missingness. Narrow Chinese columns initially wrapped one character at a time. Minimum metric-column widths and versioned assets correct this while retaining local horizontal table scrolling.

Edge executed the flow at observed desktop width 1,600 CSS pixels; the final layout was also measured at 1,280 pixels and narrow width 390 pixels. The settled document stayed within those viewports. At the emulated narrow size, a pointer click did not activate the comparison button; keyboard Enter completed the calculation. Pointer/touch acceptance at that size therefore remains unconfirmed. The in-app browser did not apply requested viewport overrides, so it was recorded at its actual 499-pixel width. Edge screenshots timed out; this limits visual acceptance for those exact sizes. Saved in-app screenshots verify the compact view only. Browser automation is not independent human acceptance. See the [layout observations](../evaluations/comparison-boundaries-browser-layout-2026-10-29.json) and [machine-readable verification](../evaluations/comparison-boundaries-verification-2026-10-29.json) for measured dimensions, download and screenshot hashes.

## Run and validate

From this checkout, with Python 3.11–3.13:

```sh
python -m pip install -e ".[dev,agent]"
python -m pytest
python -m nyc_lens.demo --port 8878
```

Open `http://127.0.0.1:8878/`. The demo refuses an existing `NYC_LENS_HOME`/`NYC_LENS_DATABASE`, creates a disposable local database and labels all results synthetic. Use the quoted Demo Avenue addresses shown by the UI. Stop with Ctrl+C. No extra account, credential or new configuration is required. AI remains disabled by default. Existing opt-in model data-sending configuration and MCP 2.3.0 dependency are unchanged; no new claim of latest protocol support is made.

## Remaining work and rollback

- Full source-data analysis is still unverified. Earlier targeted local/WSL searches did not find the expected complete catalog; that search was not a claim about every disk or account.
- Cancellation, interruption/resume, cache invalidation after source changes, freshness policy and broader resource limits still require further implementation/acceptance. This milestone is not the whole modernization goal.
- Unknown metadata intentionally withholds ranks. There is no validated cross-source harmonization or calibrated composite score.
- No new Hermes-host or MCP Apps interactive-map acceptance occurred. Existing stdio and protocol contracts run against the shared service, but do not substitute for each real client environment.
- Mobile browser interaction and compact-view visual evidence do not replace independent human use or a physical-phone acceptance run.

This is a draft source change stacked on the existing natural-language branch. No release approval is implied. Before any eventual deployment, preserve the current application revision and back up the catalog and outputs with writers stopped. Since no schema changes occur, rollback is redeploying the previous application revision while preserving data. Reports produced by this version should retain their method/version metadata; do not reinterpret them as results from the old comparison algorithm.
