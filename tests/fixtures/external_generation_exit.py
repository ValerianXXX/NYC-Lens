"""Owned synthetic child: die after publishing the candidate catalog, before commit."""
import os
import sys

import duckdb
from nyc_lens import storage

conn = duckdb.connect(sys.argv[1])
conn.execute("CREATE TEMP TABLE incoming AS SELECT * REPLACE ('interrupted' AS building_id) FROM building")
original = storage.rebuild_external_catalog

def exit_before_commit(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(23)

storage.rebuild_external_catalog = exit_before_commit
storage.replace_source_from_relation(conn, table_name="building", source_id="fixture_buildings", relation="incoming")
raise SystemExit("Expected the publication fault boundary")
