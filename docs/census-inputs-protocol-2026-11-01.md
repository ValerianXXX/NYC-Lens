# Frozen Census aggregation contracts

Baseline: `71f1760fc91046a2e52e57a7ee9e194aef1241aa`, draft #18. All inputs are
fictional in-memory records. This protocol is fixed before runtime changes.

Exercise the existing ACS/decennial/health aggregation helpers with hand-computed
weights and values. Check complete valid arithmetic, source isolation, exact
geometry/population evidence, tract fallback denominators outside the intersection,
missing observations, duplicates and missing age components. A source with the
same geography/variable name must not enter another source's calculation. A
partial input set is not a complete population total or neighborhood summary.

The twelve contracts in `tests/unit/test_census_input_contract.py` preserve the
four-value ACS/health and two-value decennial interfaces. Existing methods and
gold cases stay unchanged. Freeze actual failures before extracting aggregation
from the metric engine. Bind any new calculation module into the existing code
fingerprint, and keep saved analyses immutable.

After repair, run related regressions, a same-input before/after probe, full
necessary tests and an actual synthetic two-address workflow with evidence/export
checks. Do not overwrite existing observations or use the regional database.
If UI behavior changes, inspect the actual page and interactions.

This is not a new Census methodology, a validated health or commercial-location
conclusion, all-metric lineage certification, independent-human acceptance or
full-regional-data testing. No new provider calls, model budget, data download,
dependency or production migration is authorized by this protocol. Keep benchmark,
other geometry inputs and the original seven-project acceptance requirements open.
