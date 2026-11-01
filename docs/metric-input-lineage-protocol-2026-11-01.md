# Frozen metric-input lineage contracts, November 1, 2026

The current engine can count active records from several snapshots while labeling every site's metric with the latest source snapshot. A disposable two-site probe returned correct counts of one and three but assigned the newer source part to both sites. Its evidence lookup resolved only one of four actual input records. This is a source-binding defect, not a calculation-quality or real-location finding.

Freeze these tests before implementation. Retain the original 13-test baseline, initial probe and the initial test-authoring syntax failure separately. All addresses, records, snapshots and source values are synthetic; model calls and network retrieval are disabled.

Required behavior:

- Capture each metric's declared evidence identities against active typed input rows at calculation time. Retained standardized-record history alone cannot become an input.
- Preserve every contributing snapshot, including secondary weighting sources, and distinguish unresolved identities. A later ingestion must not silently rewrite a saved metric's binding.
- The one-source scalar summary must identify its actual input snapshot. Multiple snapshots need explicit structured provenance rather than the unrelated newest source date.
- Evidence lookup must honor the saved per-record binding. Missing later lineage stays visible; pagination must count the rows actually returned and provide bounded continuation.
- Comparisons and exported reports retain the full source basis. Different snapshot sets without proof of a common completed ingestion cannot acquire a rank. Different pages of one verified ingestion can remain comparable when year, measure, unit and calculation version agree.
- Preserve legacy saved results and their existing conservative lookup behavior. No schema migration or automatic backfill from today's input rows is permitted.
- Keep numeric calculations, source publication gates, calibrated-score restrictions, worker resource limits and synthetic-demo controls unchanged. Validate the normal double-address browser journey and source/report presentation after implementation.

These contracts cover identity/snapshot binding for evidence that the metric engine declares. They do not establish that every calculation declares all weighting, geometry or benchmark inputs, nor do they replace the missing full regional-data acceptance. Remaining provenance coverage must stay explicit in the goal audit.
