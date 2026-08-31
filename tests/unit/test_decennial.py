from datetime import UTC, datetime

import pandas as pd

from nyc_lens.ingest.decennial import DECENNIAL_VARIABLES, _to_decennial_estimates


def test_decennial_wide_rows_become_traceable_long_counts() -> None:
    wide = pd.DataFrame(
        [
            {
                "geography_id": "360610001001000",
                **{variable: str(index) for index, variable in enumerate(DECENNIAL_VARIABLES, start=10)},
            }
        ]
    )

    result = _to_decennial_estimates(
        wide,
        source_id="census_decennial_2020_pl",
        snapshot_id="snap_test",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert len(result) == len(DECENNIAL_VARIABLES)
    assert set(result["variable"]) == set(DECENNIAL_VARIABLES)
    assert result["geography_id"].eq("360610001001000").all()
    assert result["margin_of_error"].isna().all()
    assert result["measure_type"].eq("decennial_enumeration_count").all()
