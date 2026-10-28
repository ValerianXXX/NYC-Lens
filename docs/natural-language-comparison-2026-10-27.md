# Natural-language comparison milestone — 2026-10-27

## Scope and evidence

Base: `46239288de18e2562b234229c5b2ea746c7fa3d8`. The existing engine, 62 source definitions, DuckDB schema, MapLibre map, comparisons and exports remain in use. The roughly 17 GB local dataset was unavailable in this checkout. No real trade-area conclusion is claimed from this validation.

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

## Validation and operational limits

- Baseline: 64 passed, 3 skipped. Final milestone: 70 passed, 3 skipped. Populated-database integration checks require the unavailable full dataset.
- New checks cover bilingual interpretation, invented addresses/extra SQL rejection, cloud consent/transport contract, explicit confirmation, real DuckDB comparison, missing snapshots, source trace, scoring rejection and all three report formats.
- Browser: English and Chinese two-address flows completed; both map markers and comparison data appeared. JSON was downloaded through its button and checked. Rent/population gaps were visible, not invented.
- Browser follow-up: fixed the stale “awaiting analysis” comparison badge, cleared previous single/comparison markers and catchment geometry, and retained all standard warnings in both languages. Actual page actions verified comparison → single-site analysis → comparison with marker counts 2 → 1 → 2; English/Chinese switching retained the two-site map state. The exact synthetic addresses are required; an incomplete address was correctly rejected without external geocoding. [Browser capture](../reports/comparison-browser-2026-10-27.png). JavaScript syntax checks and three existing UI contract tests passed after this UI-only fix.
- Python 3.11.3 on Windows; `pip check` passed. Existing Shapely deprecation warnings remain.

No production deployment or database migration is needed for review. New tables exist only inside the isolated synthetic demo. Roll back by returning to the base revision and stopping the new MCP/demo process; saved comparison JSON adds an optional `assistant` field readable by existing storage. Back up a real local catalog before any deployment or source refresh. Full-data acceptance and other host versions remain separate from these synthetic and protocol checks.
