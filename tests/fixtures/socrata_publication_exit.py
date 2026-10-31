"""Owned subprocess fault injection using only the parent's synthetic home."""
from pathlib import Path
import os
import sys

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_socrata_publication import ProbeConnection, install_downloads
from nyc_lens.ingest.socrata import refresh_dohmh


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("NYC_LENS_HOME", str(root))
        patch.delenv("NYC_LENS_DATABASE", raising=False)
        install_downloads(patch, "restaurants", 2)
        conn = duckdb.connect(str(root / "synthetic.duckdb"))
        def exit_inside_publication(statement):
            if "SELECT * FROM _inspection" in statement:
                os._exit(79)
        refresh_dohmh(ProbeConnection(conn, before=exit_inside_publication))
        raise AssertionError("Expected owned child exit before publication completion")
