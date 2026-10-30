"""Frozen configuration freshness and per-analysis provenance contracts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from threading import Event

import duckdb
import pytest

from nyc_lens import config, db


LOADERS = {
    "sources_config": "sources.yml",
    "geographies_config": "geographies.yml",
    "metrics_config": "metrics.yml",
    "taxonomy_config": "business_taxonomy.yml",
    "test_sites_config": "test_sites.yml",
}


def clear_caches():
    for name in LOADERS:
        clear = getattr(getattr(config, name), "cache_clear", None)
        if clear:
            clear()
    clear = getattr(config, "clear_configuration_cache", None)
    if clear:
        clear()


@pytest.fixture
def configuration(tmp_path, monkeypatch):
    root = tmp_path / "config"
    root.mkdir()
    for name in LOADERS.values():
        (root / name).write_text("value: first\nnested:\n  list: [kept]\n", encoding="utf-8")
    monkeypatch.setattr(config, "config_dir", lambda: root)
    # The old implementation imports this function directly. Preserve that
    # baseline binding while the revised code obtains its captured fingerprint.
    if hasattr(db, "config_dir"):
        monkeypatch.setattr(db, "config_dir", lambda: root)
    clear_caches()
    yield root
    clear_caches()


@pytest.mark.parametrize("loader,filename", LOADERS.items())
def test_changed_bytes_reload_even_when_size_and_timestamp_are_equal(configuration, loader, filename):
    read = getattr(config, loader)
    assert read()["value"] == "first"
    path = configuration / filename
    original = path.stat()
    path.write_text("value: later\nnested:\n  list: [kept]\n", encoding="utf-8")
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert path.stat().st_size == original.st_size
    assert read()["value"] == "later"


@pytest.mark.parametrize("loader", LOADERS)
def test_callers_cannot_mutate_another_read(configuration, loader):
    read = getattr(config, loader)
    read()["nested"]["list"].append("caller mutation")
    assert read()["nested"]["list"] == ["kept"]


def test_configuration_directory_switch_cannot_reuse_another_project(configuration, tmp_path, monkeypatch):
    assert config.sources_config()["value"] == "first"
    other = tmp_path / "other"
    other.mkdir()
    (other / "sources.yml").write_text("value: other\n", encoding="utf-8")
    monkeypatch.setattr(config, "config_dir", lambda: other)
    assert config.sources_config()["value"] == "other"


def test_invalid_update_does_not_silently_serve_old_content(configuration):
    assert config.sources_config()["value"] == "first"
    path = configuration / "sources.yml"
    path.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config.sources_config()
    path.write_text("value: repaired\n", encoding="utf-8")
    assert config.sources_config()["value"] == "repaired"


def test_same_content_reuses_parse_without_exposing_cached_mutable_object(configuration, monkeypatch):
    original = config.yaml.safe_load
    calls = []
    def counted(value):
        calls.append(1)
        return original(value)
    monkeypatch.setattr(config.yaml, "safe_load", counted)
    assert config.sources_config() == config.sources_config()
    assert len(calls) == 1


def test_snapshot_pins_values_and_fingerprint_then_next_request_refreshes(configuration):
    expected_before = db._content_hash(list(configuration.glob("*.yml")), root=configuration)
    with config.configuration_snapshot():
        assert config.sources_config()["value"] == "first"
        (configuration / "sources.yml").write_text("value: after\n", encoding="utf-8")
        assert config.sources_config()["value"] == "first"
        assert config.configuration_fingerprint() == expected_before
    assert config.sources_config()["value"] == "after"
    assert config.configuration_fingerprint() != expected_before


def test_nested_snapshot_and_exception_restore_caller_context(configuration):
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        with config.configuration_snapshot():
            before = config.configuration_fingerprint()
            (configuration / "sources.yml").write_text("value: after\n", encoding="utf-8")
            with config.configuration_snapshot():
                assert config.sources_config()["value"] == "first"
                assert config.configuration_fingerprint() == before
            raise RuntimeError("synthetic interruption")
    with config.configuration_snapshot():
        assert config.sources_config()["value"] == "after"


def test_simultaneous_requests_keep_their_own_configuration_snapshot(configuration):
    captured, release = Event(), Event()
    def first_request():
        with config.configuration_snapshot():
            captured.set()
            assert release.wait(5)
            return config.sources_config()["value"]
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first_request)
        try:
            assert captured.wait(5)
            (configuration / "sources.yml").write_text("value: later\n", encoding="utf-8")
            with config.configuration_snapshot():
                assert config.sources_config()["value"] == "later"
        finally:
            release.set()
        assert future.result(timeout=5) == "first"


def test_calculation_version_hashes_the_bytes_actually_used_in_the_snapshot(configuration):
    with closing(duckdb.connect()) as conn:
        conn.execute("CREATE TABLE calculation_version(id VARCHAR, name VARCHAR, version VARCHAR, code_hash VARCHAR, config_hash VARCHAR, created_at TIMESTAMP, notes VARCHAR)")
        conn.execute("ALTER TABLE calculation_version RENAME id TO calculation_version_id")
        with config.configuration_snapshot():
            expected = config.configuration_fingerprint()
            (configuration / "metrics.yml").write_text("value: later\n", encoding="utf-8")
            version_id = db.calculation_version(conn)
            saved = conn.execute("SELECT config_hash FROM calculation_version WHERE calculation_version_id=?", [version_id]).fetchone()[0]
            assert saved == expected
            assert config.metrics_config()["value"] == "first"
        assert db.calculation_version(conn) != version_id


def test_config_loader_itself_is_part_of_calculation_code_version(configuration):
    assert {"config.py", "db.py"} <= set(db.CALCULATION_CODE_FILES)


def test_oversized_configuration_is_rejected_before_parse(configuration):
    (configuration / "sources.yml").write_text("value: " + "a" * 2_000_001, encoding="utf-8")
    with pytest.raises(ValueError, match="CONFIGURATION_FILE_LIMIT"):
        config.sources_config()
