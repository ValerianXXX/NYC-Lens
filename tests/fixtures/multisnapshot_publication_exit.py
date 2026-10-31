"""Owned synthetic process: exit after completion SQL, before atomic commit."""
import os
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
kind = sys.argv[2]
os.environ["NYC_LENS_HOME"] = str(root)
os.environ["NYC_LENS_DATABASE"] = str(root / "synthetic.duckdb")
repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo / "src"))
sys.path.insert(0, str(repo / "tests/unit"))

import duckdb
import pytest
from test_multisnapshot_publication import ProbeConnection, install_downloads, refresh


def exit_after_freshness(statement):
    if "UPDATE source_registry" in statement:
        os._exit(83)


with pytest.MonkeyPatch.context() as patch:
    install_downloads(patch, kind, 2)
    with duckdb.connect(str(root / "synthetic.duckdb")) as conn:
        refresh(ProbeConnection(conn, after=exit_after_freshness), kind)
raise AssertionError("Expected owned process exit was not reached")
