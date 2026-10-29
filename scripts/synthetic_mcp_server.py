"""Disposable synthetic MCP server for client acceptance; never opens a real catalogue."""
from pathlib import Path
import tempfile

from nyc_lens.demo import seed_demo
from nyc_lens.mcp_server import server

with tempfile.TemporaryDirectory(prefix="nyc-hermes-synthetic-") as directory:
    seed_demo(Path(directory))
    server.run(transport="stdio")
