"""Real synthetic DuckDB/spatial calculations; no model or live source data."""
from importlib import import_module
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from nyc_lens import config, db, analysis_execution
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo


def test_real_comparison_retains_configuration_then_refreshes_and_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    monkeypatch.delenv("NYC_LENS_DATABASE", raising=False)
    monkeypatch.setenv("NYC_LENS_AI_PROVIDER", "disabled")
    seed_demo(tmp_path)
    taxonomy_path = tmp_path / "config/business_taxonomy.yml"
    original_bytes = taxonomy_path.read_bytes()
    changed_bytes = original_bytes.replace(b"overture_basic_categories: [restaurant]", b"overture_basic_categories: [cafe]", 1)
    assert changed_bytes != original_bytes
    source_hash = config.configuration_fingerprint()
    # The HTTP engine is now in a child. Move the same between-site edit to a
    # private test worker instead of changing the original expected outcomes.
    monkeypatch.setattr(analysis_execution, "WORKER_SCRIPT",
                        Path(__file__).parents[1] / "fixtures/configuration_edit_worker.py")
    client = TestClient(import_module("nyc_lens.api.app").app)
    request = {"confirmed": True, "language": "en", "original_request": "Synthetic configuration refresh test",
               "intent": {"addresses": DEMO_ADDRESSES, "business_type": "full_service_restaurant",
                          "catchment": "500m", "priorities": ["competition", "population"]}}
    first_response = client.post("/api/assistant/compare", json=request)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    edit = json.loads((tmp_path / "synthetic-config-edit.json").read_text())
    assert edit["worker_pid"] != os.getpid()
    assert edit["completed_first_site"] == first["sites"][0]["site_id"]
    assert taxonomy_path.read_bytes() == changed_bytes
    assert [row["value"] for row in first["metric_comparisons"]["direct_competitor_count"]["values"]] == [1.0, 3.0]
    assert first["assistant"]["brief"]["composite_score"] is None
    assert first["assistant"]["brief"]["missing"]
    with db.db_session() as conn:
        actual_hash = conn.execute("SELECT config_hash FROM calculation_version WHERE calculation_version_id=?", [first["calculation_version_id"]]).fetchone()[0]
        assert actual_hash == source_hash

    second_response = client.post("/api/assistant/compare", json=request)
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    assert [row["value"] for row in second["metric_comparisons"]["direct_competitor_count"]["values"]] == [0.0, 0.0]
    assert second["calculation_version_id"] != first["calculation_version_id"]
    assert first["snapshot_set"] == second["snapshot_set"]
    # Dataset identity did not change; only the explicitly edited rule changed.
    saved = client.get(f"/api/comparison/{first['comparison_id']}/report?format=json")
    assert saved.status_code == 200
    historical = saved.json()
    assert [row["value"] for row in historical["metric_comparisons"]["direct_competitor_count"]["values"]] == [1.0, 3.0]

    taxonomy_path.write_bytes(original_bytes)
    restored_response = client.post("/api/assistant/compare", json=request)
    assert restored_response.status_code == 200, restored_response.text
    restored = restored_response.json()
    assert restored["calculation_version_id"] == first["calculation_version_id"]
    assert [row["value"] for row in restored["metric_comparisons"]["direct_competitor_count"]["values"]] == [1.0, 3.0]
    with db.db_session() as conn:
        assert conn.execute("SELECT count(*) FROM site_comparison WHERE status='RUNNING'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM site_query WHERE status='RUNNING'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM site_comparison WHERE status='SUCCEEDED'").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM site_query WHERE status='SUCCEEDED'").fetchone()[0] == 6


def test_deleted_file_is_retained_only_for_the_inflight_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    path = tmp_path / "sources.yml"
    path.write_text("value: original\n", encoding="utf-8")
    with config.configuration_snapshot():
        path.unlink()
        config.clear_configuration_cache()
        assert config.sources_config()["value"] == "original"
    with pytest.raises(FileNotFoundError):
        config.sources_config()


def test_snapshot_file_count_and_total_bytes_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    for index in range(65):
        (tmp_path / f"file-{index}.yml").write_text("value: small\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CONFIGURATION_FILE_COUNT_LIMIT"):
        with config.configuration_snapshot():
            pytest.fail("oversized file set was accepted")
    for path in tmp_path.glob("*.yml"):
        path.unlink()
    for index in range(5):
        (tmp_path / f"large-{index}.yml").write_bytes(b"value: " + b"a" * 1_600_001)
    with pytest.raises(ValueError, match="CONFIGURATION_SNAPSHOT_LIMIT"):
        with config.configuration_snapshot():
            pytest.fail("oversized snapshot was accepted")


def test_continuously_changing_capture_fails_instead_of_using_old_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    captures = []
    def changing(_root):
        captures.append(1)
        return {"sources.yml": f"value: {len(captures)}".encode()}
    monkeypatch.setattr(config, "_capture_files", changing)
    with pytest.raises(ValueError, match="CONFIGURATION_CHANGED_DURING_CAPTURE"):
        with config.configuration_snapshot():
            pytest.fail("changing configuration was accepted")
    assert 2 <= len(captures) <= 4


@pytest.mark.parametrize("content", [b"value: [unclosed", b"value: \xff"])
def test_invalid_yaml_or_encoding_never_falls_back_to_old_parse(tmp_path, monkeypatch, content):
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    path = tmp_path / "sources.yml"
    path.write_bytes(b"value: first")
    assert config.sources_config()["value"] == "first"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="CONFIGURATION_YAML_INVALID"):
        config.sources_config()
