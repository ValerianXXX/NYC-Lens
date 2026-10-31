"""A derived receipt or count response cannot become a geometry metric's source."""
import pytest

from nyc_lens.metrics.engine import _latest_snapshot, _latest_vintage
from test_multisnapshot_publication import SOURCES, ProbeConnection, database, refresh, seed


@pytest.mark.parametrize("kind", SOURCES)
def test_metric_uses_active_geometry_input_before_and_after_failed_refresh(database, monkeypatch, kind):
    conn, _ = database
    seed(database, monkeypatch, kind)
    parents = {row[0] for row in conn.execute("SELECT DISTINCT snapshot_id FROM geo_boundary WHERE source_id=?", [SOURCES[kind]]).fetchall()}
    snapshot = _latest_snapshot(conn, SOURCES[kind])
    assert snapshot in parents
    assert "derived-publication" not in _latest_vintage(conn, SOURCES[kind])
    def fail(statement):
        if "UPDATE source_registry" in statement:
            raise OSError("synthetic failed candidate publication")
    with pytest.raises(OSError, match="synthetic failed candidate publication"):
        refresh(ProbeConnection(conn, before=fail), kind)
    assert _latest_snapshot(conn, SOURCES[kind]) == snapshot
    assert conn.execute("SELECT source_url FROM dataset_snapshot WHERE snapshot_id=?", [snapshot]).fetchone()[0].startswith("https://")
