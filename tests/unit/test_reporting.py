from __future__ import annotations

import duckdb

from nyc_lens.reporting.export import _register_report
from nyc_lens.util import sha256_file


def test_register_report_replaces_hash_for_stable_export_path(tmp_path) -> None:
    conn = duckdb.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE site_report(
            report_id VARCHAR PRIMARY KEY,
            site_id VARCHAR NOT NULL,
            format VARCHAR NOT NULL,
            local_path VARCHAR NOT NULL,
            sha256 VARCHAR NOT NULL,
            generated_at TIMESTAMP NOT NULL,
            calculation_version_id VARCHAR NOT NULL
        )
        """
    )
    result = {
        "site_id": "site_test",
        "calculation_version_id": "calc_test",
    }
    path = tmp_path / "site_test.xlsx"
    path.write_bytes(b"first export")
    _register_report(conn, result, path, "xlsx")

    path.write_bytes(b"second export")
    _register_report(conn, result, path, "xlsx")

    rows = conn.execute(
        """
        SELECT local_path, sha256
        FROM site_report
        WHERE site_id='site_test' AND format='xlsx'
        """
    ).fetchall()
    assert rows == [(str(path), sha256_file(path))]
