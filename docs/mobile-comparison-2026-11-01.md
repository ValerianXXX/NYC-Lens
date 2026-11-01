# Mobile comparison and actual browser acceptance — 2026-11-01

At 390 CSS pixels, the original comparison placed its seven metrics in a
1,131-pixel table inside a 329-pixel result area. The population and missing-input
details were outside the initial horizontal view. The new narrow presentation
groups each metric with its addresses, values, input evidence and limitations.
Desktop retains the table. Both call the same rendering helper, so the change
does not add a second calculation, rank or source-selection implementation.

The language button now keeps its label on one line, relevant narrow-screen
controls have a minimum 44-pixel height, and long task IDs wrap without losing
characters. Evidence-link accessible names include the metric and address.
Only the presentation active at the breakpoint is displayed; the hidden copy
does not add duplicate visible/keyboard targets. This is not a complete
accessibility audit or a claim that every control meets a particular standard.

## Frozen comparison and actual observations

Base: `dfaf74139ba4c4f05adc4ec1575916b06025b024`, draft #19.
Initial protocol/probe: `99904fb`; first UI implementation: `e8bed51`.
The separate long-task-ID probe was frozen in `c1be581` before its repair.
Final runtime: `5f729ae65dfd4f3ace08b53ce3837c06eb74a8d9`.
The [initial protocol](mobile-comparison-protocol-2026-11-01.md), both read-only
probes and their failed observations remain unchanged.

| Observed case | Before | After |
| --- | --- | --- |
| 390-pixel result area | 329-pixel area / 1,131-pixel content | 329 / 329; both values visible together |
| 320-pixel page with complete task ID | 305-pixel page / 334-pixel content | 305 / 305; all 41 ID characters retained |
| Existing English display | 14 metric/address items in table | All 56 value/rank/evidence/detail fields preserved |
| Original saved JSON download | Saved before editing UI | Identical bytes after UI reload |
| Fresh Chinese task, addresses reversed | First minus second competitor count: −2 | +2; all 252 address-mapped metric values preserved |

The two fictional addresses are `100 Demo Avenue, Newark, NJ` and
`200 Demo Avenue, Newark, NJ`. Both journeys used the existing Census fixture,
500 m catchments and full-service restaurant classification. AI was disabled;
the real bounded analysis worker ran to `SUCCEEDED`. The first address has a
synthetic population of 300; the second lacks a required population input and
remains unavailable. Missing rent, incomparable values, unmodeled requirements
and the absence of a composite score remain visible.

Actual browser actions included:

- Preparing an English request, confirming it, waiting for analysis and later
  reloading that same completed job with the changed UI.
- Preparing a Chinese request, editing/reversing both addresses, changing the
  radius to 1 km and back to 500 m, then explicitly confirming a fresh task.
- Inspecting the map's two markers and synthetic-data label, using Zoom in/out,
  opening income evidence, and activating a missing-data disclosure by keyboard.
- Switching language/theme and checking 390 × 844, 390 × 640, 320 × 640,
  320 × 480 dark and 1280 × 900 desktop views. These are browser CSS dimensions,
  not a physical mobile device or touch-input test.
- Clicking all three visible export links for each language: six actual files.
  Each CSV and XLSX detail sheet contains 252 rows and 32 columns, checked against
  its saved JSON without invoking the application's exporter in the verifier.
  XLSX numerical serialization uses an absolute `1e-8` tolerance and zero relative
  tolerance; missingness and metadata compare exactly. CSV numeric values compare
  exactly. The 252 address-mapped values also agree across the two tasks.

The income evidence page resolves six declared records across synthetic Census
and boundary sources, with zero unresolved record bindings. Its identities agree
with the saved report. Raw-file binding is explicitly `UNAVAILABLE`: this
synthetic fixture has no claimed official raw files. Retrieval dates and source
identities remain unchanged; a new analysis records a new observation time and
retrieval age. Neither date implies currentness or expiry.

## Validation and retained failures

- Original three UI contracts passed on the base and on the final runtime;
  JavaScript syntax checks passed. No dependency or existing test was changed.
- Complete Windows/Python 3.11 regression: **548 passed, 3 skipped**, zero errors
  or failures, 738.354 seconds in the JUnit receipt. The unchanged skips require
  a populated local database and generated samples. Geometry deprecation warnings
  remain visible. Hosted checks are recorded separately on the draft PR.
- The first six-check mobile probe passed only its report-link check before the
  change. Afterward all six pass at each recorded layout. These checks describe
  this layout contract, not six new business tasks or a general quality score.
- The first 320-pixel follow-up still found page overflow despite the corrected
  comparison. Its separate page/identity probe failed two of three checks. The
  task-ID wrapping repair passes all three without shortening the ID.
- Two initial browser probe invocation attempts failed (trailing semicolon and
  an uninvoked function expression). The same frozen functions were then invoked
  correctly; expected values were not changed.
- A first display-parity helper compared JSON key insertion order and reported
  false despite identical fields. Its failed record is retained alongside the
  successful comparison of 56 explicitly named fields.
- The demo copies UI/config into its isolated home when seeded. An initial
  reload therefore served the old UI. Only the three owned UI copies were updated;
  hash receipts show configuration, database and saved reports unchanged at each
  copy step. The original English result was reused, not replaced with a new run.
- Browser evidence navigation reported a favicon 404; the evidence request itself
  succeeded. No claim of a complete network trace or zero browser errors is made.

See the [verification receipt](../reports/mobile-comparison-2026-11-01/verification.json),
[baseline view](../reports/mobile-comparison-2026-11-01/baseline.png),
[final Chinese view](../reports/mobile-comparison-2026-11-01/final-zh.png), and
[short dark view](../reports/mobile-comparison-2026-11-01/short-zh-dark.png).
Published text logs redact local workspace/host identities and normalize line
endings; original local logs and full downloads are retained with their hashes.

## Reproduce and operate

From a source checkout in its existing Python environment:

```powershell
pip install -e ".[dev,agent]"
# Use a new empty directory; do not point this fixture at a real data home.
python tests/fixtures/browser_analysis_server.py --home <new-synthetic-directory> --port 8835 --census-inputs
```

Open `http://127.0.0.1:8835/` and enter, for example:
`Compare "100 Demo Avenue, Newark, NJ" and "200 Demo Avenue, Newark, NJ" for a full-service restaurant within 500m, focusing on competition, population and rent.`
Prepare, inspect/edit the confirmed inputs, run analysis, open the multi-address
section and inspect evidence/downloads. Switch to Chinese and reverse the
addresses for the second task. Inspect the recorded probes in
`scripts/probe_mobile_comparison.js` and `scripts/probe_mobile_page.js`; they read
the DOM and do not mutate the application or initiate model/data requests.
The ordinary `python -m nyc_lens.demo` also uses the responsive UI, but the
`--census-inputs` fixture is required for these particular 300/missing examples.

There are no new environment variables, credentials, model calls, database
migrations, packages or external services. Existing map resources retain their
existing network dependency. Start a fresh synthetic home after changing UI;
reusing a seeded home keeps its copied assets until deliberately updated.

## Limits and rollback

This validates a real browser journey over synthetic data and the existing local
engine. It does not establish complete Greater New York coverage, a calibrated
site score, a customer decision, a new model improvement, MCP Apps compatibility,
a fresh Hermes session or independent-human acceptance. Some original calculation
notes remain English in Chinese mode. Physical phones, touch and screen readers
remain untested. Desktop and mobile references are the same source payload,
not independent corroborating data.

No production deployment or public release occurred. Roll back by reverting the
reviewed UI commits and their cache-query versions, and restoring those UI files
in any deliberately copied deployment. Preserve all databases, snapshots and
saved results. No recalculation or data rollback is required.
