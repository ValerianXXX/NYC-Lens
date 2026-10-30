# Changelog

## Unreleased — 2026-10-30

- Bind failure cleanup to one preallocated site ID, share terminal-state handling with comparisons, preserve propagated cancellation and the original exception when cleanup cannot persist. Do not overwrite terminal rows or store arbitrary exception messages.
- Withhold unsuccessful site results and transaction lookups; retain partial records for inspection and completed child sites after comparison interruption. Verify a separate manual retry against the real synthetic engine. Ten frozen contracts improve from 1/10 to 10/10; four additional checks pass after repairing the exposed transaction endpoint. This is a prerequisite for broader cancellation/resource work, not its completion. See [evidence and rollback](docs/analysis-failure-isolation-2026-10-30.md).

## Unreleased — 2026-10-29

- Capture bounded configuration bytes for each analysis/comparison, use the same bytes for calculation provenance, and reload changed files on the next request. Return independent parsed mappings so caller mutations cannot affect later reads. Include configuration loading/versioning code in calculation hashes. Preserve prior reports and schema. Frozen checks: 1/19 before, 19/19 after; six additional integration/boundary checks; full suite: 113 passed, three full-data skips. See the dated configuration snapshot record.
- Require matching units, source snapshots, years, measure types and calculation versions before ranking or subtracting site metrics; retain raw values and explicit missingness.
- Use competition ranks for ties and define within-batch percentiles against other comparable sites. Reject non-finite and boolean numeric values.
- Share bounded evidence lookup between Web and MCP, bound to the stored analysis snapshot. Expose unresolved lineage and snapshot status rather than silently substituting later data.
- Preserve comparison basis and calculation versions in CSV/XLSX exports; leave old stored results unchanged and suppress unverified legacy ranks in the Web UI.
- Reject duplicate and oversized natural-language requests before analysis. Keep unsupported requirements for confirmation.
- Improve bilingual comparison copy and narrow-screen column readability; version changed assets so cached styles refresh.
- Preserve the original 5/14 boundary result, a disclosed test-helper correction and the final 14/14 result. Full local check: 88 passed, three data-dependent skips. This is synthetic verification, not new model or customer evidence.

## Unreleased — 2026-10-28

- Preserve original requirements, requested priorities and unsupported conditions through MCP comparisons and exported reports.
- Match the interpreter's 4,000-character requirement limit so a valid long request survives unchanged when forwarded to the comparison tool.
- Default MCP comparisons to requested metrics, explicit gaps and exact evidence/report arguments; retain complete stored analyses and an explicit `response_view=full` option.
- Add an opt-in disposable Hermes acceptance runner using the operator's existing model and authorization. Preserve the initial failed attempt and the successful bounded synthetic model-selected workflow; no real-data or general quality claim.
- Revalidate all 70 available tests; three populated-database checks remain skipped because the full catalogue is unavailable.
- Install the optional MCP dependency in CI so protocol checks run there rather than being skipped for a missing SDK.

## Unreleased — 2026-10-27

- Add confirmed bilingual two-address planning, sourced decision differences, map markers and existing report exports.
- Keep map labels and markers aligned when switching between single-site and comparison results or languages; translate all standard comparison warnings while retaining unknown warnings.
- Keep missing source data null; retain source lineage, original requirements and the uncalibrated-score gate.
- Add an isolated synthetic demo and optional MCP SDK 2.3.0 stdio tools.
- Add opt-in local/cloud structured interpretation with explicit data-sending configuration; default remains offline.
- Validate synthetic engine and protocol workflows; full-data and individual host acceptance remain pending. See the dated validation record.
