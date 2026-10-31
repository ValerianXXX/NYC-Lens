"""Readiness checks never treat a different successful run as authorization."""
import pytest

from nyc_lens.publication_readiness import require_ready_external_publications
from test_external_generation_contract import database


def add_metadata(conn, *, status="SUCCEEDED", run_source="fixture_buildings", completed="2026-10-31"):
    conn.execute("""INSERT INTO dataset_snapshot(snapshot_id, source_id, run_id, retrieved_at, status, vintage, source_url)
        VALUES ('snapshot_old', 'fixture_buildings', 'fixture_run', TIMESTAMP '2026-10-30', 'SUCCEEDED', 'SYNTHETIC 2026', 'synthetic://readiness')""")
    conn.execute("""INSERT INTO ingestion_run(run_id, source_id, status, completed_at, command, started_at)
        VALUES ('fixture_run', ?, ?, ?::TIMESTAMP, 'synthetic readiness', TIMESTAMP '2026-10-29')""", [run_source, status, completed])


def test_missing_metadata_blocks_then_the_exact_completed_run_allows_analysis(database):
    conn, _, _ = database
    with pytest.raises(ValueError, match="EXTERNAL_PUBLICATION_NOT_READY"):
        require_ready_external_publications(conn)
    add_metadata(conn)
    require_ready_external_publications(conn)


@pytest.mark.parametrize("options", [
    {"status": "RUNNING"}, {"status": "FAILED"}, {"run_source": "other_source"}, {"completed": "2026-10-29"}
])
def test_incomplete_or_wrong_run_does_not_authorize_the_generation(database, options):
    conn, _, _ = database
    add_metadata(conn, **options)
    conn.execute("""INSERT INTO ingestion_run(run_id, source_id, status, completed_at, command, started_at)
        VALUES ('unrelated_later_success', 'fixture_buildings', 'SUCCEEDED', TIMESTAMP '2026-11-01', 'synthetic other run', TIMESTAMP '2026-10-31')""")
    with pytest.raises(ValueError, match="EXTERNAL_PUBLICATION_NOT_READY"):
        require_ready_external_publications(conn)


def test_success_with_warnings_is_an_existing_supported_terminal_run(database):
    conn, _, _ = database
    add_metadata(conn, status="SUCCEEDED_WITH_WARNINGS")
    require_ready_external_publications(conn)


def test_changed_manifest_cannot_supply_the_snapshot_readiness_proof(database):
    conn, _, home = database
    add_metadata(conn)
    root = conn.execute("SELECT storage_root FROM external_table_catalog WHERE table_name='building'").fetchone()[0]
    (home / root / "_manifest.json").write_text('{}')
    with pytest.raises(ValueError, match="EXTERNAL_PUBLICATION_MANIFEST_INVALID"):
        require_ready_external_publications(conn)
