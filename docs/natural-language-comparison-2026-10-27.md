# Natural-language comparison milestone — 2026-10-27

## Scope and evidence

Base: `46239288de18e2562b234229c5b2ea746c7fa3d8`. The existing engine, 62 source definitions, DuckDB schema, MapLibre map, comparisons and exports remain in use. The roughly 17 GB local dataset was unavailable in this checkout. No real trade-area conclusion is claimed from this validation.

Follow-up on 2026-10-28: a targeted search of likely Windows project/download locations and the existing WSL workspace did not locate the full NYC-Lens DuckDB/Parquet catalogue. Unrelated datasets were not reused. This does not prove the data is absent from every drive or backup. Real-data acceptance still requires the matching catalogue directory, configured through `NYC_LENS_HOME` / `NYC_LENS_DATABASE`, or a separately reviewed source refresh.

The new flow is: enter requirements → review/edit two addresses, restaurant type and radial scope → explicitly confirm → run existing spatial calculations → compare sourced differences and missing requirements → inspect evidence → download JSON/CSV/XLSX. English and Chinese UI are supported. No generated SQL or composite score is exposed.

Absent source snapshots now produce nulls and warnings instead of misleading zero counts. Differences require matching source year, unit and measure type. The original request and confirmed intent remain in the JSON report; unsupported requirements remain visible. The original uncalibrated-scoring gate still returns HTTP 409.

## Run without the full dataset

```sh
python -m pip install -e ".[dev,agent]"
python -m nyc_lens.demo --port 8766
```

Open `http://127.0.0.1:8766`. The demo creates an isolated temporary database and refuses unseeded addresses and walking areas. Compare the fictional `100 Demo Avenue, Newark, NJ` and `200 Demo Avenue, Newark, NJ` for a restaurant within 500 m. It runs the real engine on explicitly synthetic fixtures: one versus three competitors, source `synthetic://nyc-lens/demo-v1`, vintage `SYNTHETIC 2026`, confidence unmeasured. Rent and population are deliberately missing. The application does not fetch real data for these fictional addresses. Existing databases are not modified.

For actual data use the existing `nyc-lens init`, `refresh`, `qa` and `serve` commands. Full coverage depends on successful source ingestion and source-specific access; registry entries are not a claim of complete data availability.

## AI configuration and comparison

The default explicit-text helper recognizes quoted addresses and known bilingual keywords. It is labelled as a helper, not semantic AI. It preserves the full request for review and requires no account.

Optional `NYC_LENS_AI_PROVIDER=loopback` accepts an operator-configured local OpenAI-compatible model on a loopback HTTP endpoint. Optional `openai` requires both `NYC_LENS_ALLOW_CLOUD=true` and `OPENAI_API_KEY`, plus an explicit `NYC_LENS_AI_MODEL`. Only entered requirements/addresses are sent, with `store=false`, no tools, a strict bounded schema and output limits. Query results and the working database are not transmitted. API charges and provider retention terms remain applicable. See `.env.example`; do not commit populated credentials.

An existing Hermes installation was used for **one real synthetic planning call**: configured `gpt-5.6-sol` / `openai-codex`, 11,828 ms, zero tool calls. Both addresses, 500 m, restaurant type and competition/population/rent priorities were preserved. Cost and token usage were unavailable, represented as null in `reports/hermes-planner.json`. This is planning-only evidence; it did not execute a Hermes MCP business flow and does not establish statistical quality or a speed/cost benefit. Cloud HTTP wiring has a mock transport contract test, not a live API-key call. A local HTTP model has not been exercised.

## Standard tool interface

Install `[agent]` and configure a compatible host with the absolute Python path from this virtual environment:

```json
{"mcpServers":{"nyc-lens":{"command":"/absolute/path/to/.venv/bin/python","args":["-m","nyc_lens.mcp_server"],"env":{"NYC_LENS_HOME":"/absolute/path/to/NYC-Lens","NYC_LENS_AI_PROVIDER":"disabled"}}}}
```

On Windows use `.venv\\Scripts\\python.exe`. The SDK owns stdio framing, discovery, protocol negotiation and structured results. Tools: `interpret_requirements`, `analyze_address`, `compare_addresses`, `query_evidence`, `generate_report`. Hosts must obtain user confirmation before passing `confirmed=true`; API flags cannot prove human consent. There is no unrestricted SQL, arbitrary filesystem output or upload tool. Geocoding/parcel lookups in the real-data engine may call its configured public sources.

Official references verified 2026-10-27: [MCP versioning](https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning), [Python SDK](https://github.com/modelcontextprotocol/python-sdk), [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview). SDK 2.3.0 is pinned in the optional dependency. Current 2026-07-28 discovery and legacy 2025-11-25 initialization are tested both in process and over real Windows stdio subprocesses. No MCP Apps resource or host map integration is claimed; the existing Web UI supplies the verified interactive map.

Follow-up actual-host validation used the existing Hermes revision `afe53708ee7400919ae3e17a6dde9b02629e5c87` and its MCP client 1.26.0 in WSL to launch the Windows server. Hermes discovered the tools, interpreted the synthetic request, rejected an unconfirmed comparison, calculated one versus three competitors, retrieved the synthetic source and generated a local report. The same Hermes tool registry/dispatch used by the Agent performed these calls; no model was asked to choose the tools and no persistent host configuration was changed. This verifies a real host tool flow, not a real-data business outcome or full model-driven conversation. [Receipt](../reports/hermes-mcp-host.json).

### Model-selected Hermes acceptance — 2026-10-28 America/New_York

The next check used the same installed Hermes revision, client MCP 1.26.0, server
MCP 2.3.0 and configured `gpt-5.6-sol` / `openai-codex` model. No model upgrade,
new account or service purchase was involved. A disposable Hermes home exposed
exactly the five NYC-Lens tools, with a seven-model-iteration/eight-tool-call
budget. The fixture explicitly confirmed the fictional addresses, restaurant
type and 500 m scope. This is synthetic acceptance consent, not customer consent.

The first real model-selected attempt failed. The engine calculated the fixture
correctly, but the model reported truncated tool output, guessed two incorrect
metric IDs, exhausted the tool budget and did not generate a report. It returned
null counts instead of inventing numbers. A separate deterministic measurement
found 126 metrics and 222,796 JSON characters in the full response. Requirements
also mixed the task text with acceptance instructions; the exact original-text
check correctly failed. [Unmodified failure receipt](../reports/hermes-agent-attempt-1.json).

MCP now accepts bounded `original_request`, `priorities` and
`unmodeled_requirements`, preserving them in the saved comparison/report. Its
default `response_view=summary` returns requested metric rows with all source,
year, unit, confidence, missingness and warning fields intact, plus canonical
`evidence_queries` and `report_arguments`. Missing rent remains explicitly
unavailable. For the identical offline comparison, the summary has 8,893 JSON
characters (96.01% fewer); the complete 126-metric analysis remains in storage
and report exports. `response_view=full` returns it inline. Consumers requiring
every metric should request that option. No numerical calculation changed.

The second real attempt completed interpretation → comparison → evidence for
both sites → JSON report through model-selected calls. All 12 acceptance checks
passed, including source lineage, exact original request, missing population/rent,
null composite score and a SHA-256 check of the generated report bytes. The model
returned the actual fixture counts `[1, 3]`. The report was generated inside the
disposable test directory and removed during cleanup; it was not uploaded.
[Success receipt](../reports/hermes-agent-attempt-2.json).
The [source/environment receipt](../reports/hermes-agent-source.json) records the
tested file hashes, server versions and offline payload-size comparison.

| Observation | Initial attempt | After tool/prompt repair |
|---|---:|---:|
| Bounded task completed | No | Yes |
| Model API calls | 7 | 4 |
| Recorded tool calls | 8, then budget rejection | 5, no rejection |
| Total reported tokens | 38,448 | 27,030 |
| End-to-end elapsed time | 52.439 s | 25.236 s |
| Report generated and bytes verified | No | Yes |

Both used the same task, fixture, model and call limits. Protocol revision 2 also
separates the original request from test instructions and specifies final JSON
field types; this is **not a controlled test isolating the summary change**.
One attempt per version does not estimate general completion rate or latency.
Hermes reported `cost_status=included`, `cost_source=none` and estimated incremental
cost `$0` for both calls; there is no independent dollar-cost measurement or claim
that the model is free. No real catalogue, paid API-key endpoint, customer workflow
or MCP Apps map was tested. UTC receipt timestamps fall on October 29.

To reproduce with an existing Hermes installation, copy
[`scripts/hermes-synthetic-server.example.json`](../scripts/hermes-synthetic-server.example.json)
to a local file and replace the two absolute paths. Use the Hermes installation's
own Python to run the probe, and the NYC-Lens virtual environment for the server:

```sh
/path/to/hermes/venv/bin/python -I scripts/probe_hermes_agent.py \
  --hermes-root /path/to/hermes < /path/to/local-server.json
# Explicit opt-in to a bounded call using the existing configured model/account:
/path/to/hermes/venv/bin/python -I scripts/probe_hermes_agent.py \
  --hermes-root /path/to/hermes --execute-model < /path/to/local-server.json
```

The first command discovers tools without model calls. The second sends only the
synthetic task and real synthetic tool results to the already configured provider;
ordinary Hermes/model account terms still apply. It does not change persistent
Hermes configuration. A Windows server launched from WSL needs its executable
path in WSL form and script argument in Windows form. Exit status is nonzero for
failed acceptance. The standalone runner is opt-in and excluded from offline CI.

## Validation and operational limits

- Baseline: 64 passed, 3 skipped. Final milestone: 70 passed, 3 skipped. Populated-database integration checks require the unavailable full dataset.
- New checks cover bilingual interpretation, invented addresses/extra SQL rejection, cloud consent/transport contract, explicit confirmation, real DuckDB comparison, missing snapshots, source trace, scoring rejection and all three report formats. The October 28 follow-up still passes 70 tests with three data-dependent skips; both protocol modes also verify requirement preservation, compact/full response equivalence, unavailable rent and unchanged full report contents. CI now installs `[agent]` so these MCP checks run instead of being skipped for a missing optional SDK.
- Browser: English and Chinese two-address flows completed; both map markers and comparison data appeared. JSON was downloaded through its button and checked. Rent/population gaps were visible, not invented.
- Browser follow-up: fixed the stale “awaiting analysis” comparison badge, cleared previous single/comparison markers and catchment geometry, and retained all standard warnings in both languages. Actual page actions verified comparison → single-site analysis → comparison with marker counts 2 → 1 → 2; English/Chinese switching retained the two-site map state. The exact synthetic addresses are required; an incomplete address was correctly rejected without external geocoding. [Browser capture](../reports/comparison-browser-2026-10-27.png). JavaScript syntax checks and three existing UI contract tests passed after this UI-only fix.
- Python 3.11.3 on Windows; `pip check` passed. Existing Shapely deprecation warnings remain.

No production deployment or database migration is needed for review. New tables exist only inside the isolated synthetic demo. Roll back by returning to the base revision and stopping the new MCP/demo process; saved comparison JSON adds an optional `assistant` field readable by existing storage. Back up a real local catalog before any deployment or source refresh. Full-data acceptance and other host versions remain separate from these synthetic and protocol checks.
