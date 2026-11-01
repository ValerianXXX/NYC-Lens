# Census input correctness and traceability — 2026-11-01

A selected block group could contribute another survey's population merely
because its geography and variable names matched. Missing geographies and age or
education components could also become apparently complete totals. The existing
metric record bindings did not include several population weights or spatial
denominators actually used by Census and health calculations.

The aggregation helpers now live in `metrics/census.py`, while the engine keeps
the existing four-item ACS/health and two-item decennial interfaces. This is a
bounded extraction of one calculation family, not a replacement of DuckDB,
Parquet, the analysis workers or the reporting interfaces.

## Resulting behavior

- ACS observations and population weights use the declared `acs_2024_5yr`
  source and geography type. Tract area denominators use `tiger_bg_2024`;
  unrelated surveys and geometry sources cannot enter those calculations.
- Known intersecting geographies must each have exactly one finite, nonnegative
  observation. Missing, invalid or duplicate observations withhold the complete
  aggregate. Age totals and education/occupation shares require every requested
  component. An explicitly observed zero remains different from missing data.
- Population-weighted summaries require complete population weights and a
  positive total weight. They no longer silently substitute area-only weighting.
  Missing published uncertainty remains unavailable instead of becoming zero.
- Evidence includes intersecting boundary records, population weights and the
  full set of boundary/population records used by tract denominators, including
  records outside the intersection. Known inputs remain inspectable when an
  aggregate is unavailable. Health summaries include both geometry layers.
- Saved results, comparison exports and expandable Web details retain calculation
  warnings. The new unavailable-input explanations are shown in English/Chinese.
  The existing health-release fallback is explicit; neither missing current nor
  missing fallback data is described as a successful current estimate.
- The calculation fingerprint includes the extracted module and the existing
  input-binding helpers. Existing saved results are not silently recalculated.

## Fixed before/after observations

Baseline: `71f1760fc91046a2e52e57a7ee9e194aef1241aa` (draft #18).
Runtime: `32ca46710edf16c55210530000547785e3876575`.
The [initial protocol](census-inputs-protocol-2026-11-01.md), twelve contracts
(`ae8ccdc`) and eight-scenario evaluator (`8f29634`) were frozen before runtime
changes. Six further invalid-input checks (`810ae3f`) preceded semantic changes.
One real-engine education-share counterexample (`a021849`) was separately frozen
before repairing the engine's partial-component summation; the helper repair was
already underway at that point. Original labels and assertions are retained.

| Synthetic scenario | Before | After | Evidence records before → after |
| --- | --- | --- | --- |
| Complete area-weighted count | 100 | 100 | 2 → 4 |
| Same geography/variable from another survey | 550 | 100 | 3 → 4 |
| Complete population-weighted summary | 50,000 | 50,000 | 2 → 6 |
| Tract denominator with an unrelated geometry source | 93.181818… | 175 | 2 → 5 |
| Tract population outside the intersection | 30,000 | 30,000 | 2 → 8 |
| Missing selected population geography | 50 | Unavailable | 1 → 3 |
| Missing age component | 15 | Unavailable | 2 → 4 |
| Complete health summary | 30 | 30 | 4 → 8 |

Value-only checks improve from **4/8 to 8/8**; combined value and complete declared
evidence checks improve from **0/8 to 8/8**. The evaluator and fixture hashes are
identical before/after. This is a fixed development comparison, not an estimate
of correctness across all regional data or all 126 metric definitions.

## Validation and observed failures

- Related regression: **49 passed**, 141 warnings, 49.29 seconds; the three
  existing UI contracts also pass. JavaScript syntax passed.
- Full Windows/Python 3.11 suite at the runtime commit: **548 passed, 3 skipped**,
  232 warnings, 706.57 seconds. The three unchanged populated-database tests
  require local refresh/sample data. Existing geometry deprecation warnings are
  retained; no check, assertion, deadline or skip was weakened.
- The original twelve contracts and six additional invalid-input tests failed
  before their repair. The separate partial-education test observed `0.1` where
  data was incomplete. Their original logs are retained.
- The first new workflow test used exact floating-point equality for a synthetic
  weighted income of 60,000 and observed `59999.99999999999`. The numerical check
  now uses an absolute `1e-8` tolerance, with zero relative tolerance; missing
  values still require exactly `None`. The failed observation is retained.
- Actual desktop browser journeys used English and Chinese requests, explicit
  confirmation, the real bounded analysis worker, a two-marker map, source links,
  refresh recovery and JSON/CSV/XLSX downloads. The final Chinese journey ran
  after the runtime commit. Each downloaded table contains 252 metric/address
  rows. One site's population is 300; the other is unavailable. Its missing-data
  explanation survives export. The complete income summary resolves six records
  across Census and geometry sources with zero unresolved record bindings.
- The first wheel attempt failed because the shared test environment lacked
  `wheel` and had old build tooling. That environment was left unchanged during
  its full test. A separate disposable build environment with `setuptools
  80.10.2`, `wheel 0.48.0` and its observed `packaging 26.3` dependency built the
  wheel successfully. The packaged Census, engine and fingerprint code match
  their source bytes. This is a package-build check, not standalone installation
  or publication acceptance.

See the [hash-linked evidence receipt](../reports/census-inputs-2026-11-01/verification.json),
[English details](../reports/census-inputs-2026-11-01/browser-en.png) and
[final Chinese details](../reports/census-inputs-2026-11-01/browser-zh.png).
Hosted checks are recorded separately on the draft PR. No hosted PASS is implied
by the local results. The Windows workflow now also runs the 23 new contracts.
Published text receipts redact local workspace identities and normalize line
endings/trailing whitespace. Original local observations remain unchanged.

## Run and configure

Use the existing source checkout and `pip install -e ".[dev,agent]"` setup.
No product dependency, protocol version, model or configuration change is needed.
For the unchanged fixed probe, select a new output file:

```text
python scripts/evaluate_census_inputs.py --output reports/new-census-inputs.json
python -m pytest tests/unit/test_census_input_contract.py tests/unit/test_census_input_failures.py tests/unit/test_census_ratio_components.py tests/unit/test_census_comparison_workflow.py
```

For the specific browser scenario, unset `NYC_LENS_HOME` and `NYC_LENS_DATABASE`,
then use a **new, nonexistent** folder:

```text
python tests/fixtures/browser_analysis_server.py --home <new-folder> --port 8833 --census-inputs
```

Open the local URL and confirm the two fictional Demo Avenue addresses. The
harness refuses an existing home, creates its own synthetic database and disables
AI. It does not read regional data or call a provider. The ordinary
`python -m nyc_lens.demo` remains the smaller original demonstration.

## Limits, deployment and rollback

These checks certify the selected synthetic cases, not completeness of a loaded
boundary layer, regional datasets or statistical methodology. Missing boundaries
outside the known selection still require ingestion/coverage validation. The
existing area allocation and weighted summary approximations are preserved.
A weighted mean of published medians is **not** a pooled neighborhood median;
the result warns about that distinction. Health values remain screening
aggregates with approximate uncertainty, not individual or clinical findings.

Other implicit geometry, benchmark and mobility inputs still need a separate
audit. Raw-file binding remains unavailable for these invented in-memory inputs;
resolved record provenance does not invent a raw file. No model benefit, lower
latency/cost, full-regional-data acceptance, independent-user acceptance, mobile
device testing or fresh Hermes-client outcome is claimed by this milestone.

No database migration, data refresh, release or production deployment was
performed. Review the stacked draft before adoption. A normal code revert
restores the preceding implementation but also its source-mixing/partial-total
risks. Preserve source generations and saved analyses; explicitly create a new
analysis to use a different calculation version. Never rewrite historical
results to make them appear newly validated. Composite scoring remains disabled.
