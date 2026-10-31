# Changelog

## Unreleased — 2026-10-31

- Publish NYC boundaries, ZCTAs, H3 grids, hazard layers and TLC taxi zones with one transaction for facts, lineage, snapshot details, successful run and freshness. Prepare snapshot-specific exports first, retain historical lineage (including the legacy H3 alias), and roll back propagated interrupts. Refresh results add `standardized_parquet`; old fixed paths remain historical files. Thirty-eight frozen contracts improve from 12/38 to 38/38, with 11 supplementary coverage/recovery checks including an actual owned process exit. No minimum coverage, privacy, geographic or planning-only constraint is reduced. See [verification and compatibility](docs/boundary-publication-2026-10-31.md).

- Reject Socrata CSV responses over the caller's row budget by requesting one sentinel record; reject invalid limits before downloading and malformed/empty-byte CSV before returning a snapshot. Count logical CSV records, preserve aggregate values and exact-limit/header-only responses, and retain an exclusive hash-linked check beside each raw receipt. Twenty frozen contracts improve from 0/20 to 20/20; two tiny official-endpoint probes separately verify a scalar aggregate and expected overflow rejection. Windows CI includes the new contracts. This does not establish complete upstream data, all-adapter atomicity or a resource quota. See [evidence, compatibility and recovery](docs/socrata-row-limits-2026-10-31.md).

- Commit restaurant, hotel and air/health facts, lineage, successful ingestion status and source freshness together. Roll back failed publication, release temporary frame registrations and retain prior exports in snapshot-specific paths; refresh results now provide `standardized_parquet`. Preserve failed/indeterminate attempts and raw evidence. Twenty frozen contracts improve from 1/20 to 20/20; owned-process recovery and retained-source checks supplement them. Other importers and full-data acceptance remain open. See [behavior, export-path compatibility and recovery](docs/socrata-publication-2026-10-31.md).

- Publish external facts in retained immutable generations and atomically switch the DuckDB view plus both catalogs. Preserve old readers, empty replacements, other sources and legacy adoption; reject incomplete v2 source-run prerequisites before analysis. Roll back caught nested failures and refused commits, ignore abandoned candidates, and extend Windows CI with storage/worker/restore contracts. No full-data migration or deployment was performed. See [verification, recovery and unfinished pipeline work](docs/external-generations-2026-10-31.md).

- Coordinate task receipt readers and the existing writer lease with a short cross-process I/O lock. Retry transient sharing/access failures within a one-second window, retain the previous complete receipt on failed replacement, and expose persistent unavailability without restarting analysis or inventing success. Five frozen failure contracts, the original failed MCP workflow and a separate-process held-reader check verify the repair; full results are recorded with the CLI milestone.

## Unreleased — 2026-10-30

- Route CLI `analyze`, `compare`, `samples` and `regional-samples` through the existing bounded durable jobs. Add a synchronous library entry point, optional stable request IDs for single analysis/comparison and CLI status/result/cancel commands. Keep successful calculation/export formats, preserve completed child results on cancellation and withhold incomplete comparisons. See [verification, platform changes and recovery](docs/bounded-cli-analysis-2026-10-30.md).
- Move the three legacy HTTP analysis endpoints onto existing bounded durable jobs; retain success bodies, add optional idempotent request keys and task response headers, and return saved timeout/cancellation/failure states without automatic retries. Bound all analysis/planning bodies before JSON parsing and share strict worker request schemas. Preserve readable redacted upstream-error categories across the worker boundary. See [verification and client changes](docs/bounded-legacy-http-2026-10-30.md).
- Run Web task analysis and MCP address/comparison tools in owned processes with OS memory limits, deadlines, actual cancellation and bounded DuckDB settings. Persist task receipts and exact attempt IDs; refresh reconnects without resubmitting. Recover interrupted database states after supervisor exit without killing guessed PIDs or automatically rerunning work. Keep completed child results and distinguish database cleanup from observed process exit.
- Add idempotent job start/status/cancel/result HTTP endpoints, a pre-JSON request limit and bilingual task controls. Restore saved addresses along with results after refresh. The initial worker milestone left legacy HTTP/CLI direct; subsequent changes above extend coverage. Low-level connection-based library calls, ingestion and exports remain direct. See [initial validation and operating limits](docs/bounded-analysis-2026-10-30.md).
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
