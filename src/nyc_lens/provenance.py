from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import duckdb
import httpx

from .config import get_source
from .paths import data_dir
from .util import json_dumps, new_id, safe_component, sha256_file, utc_iso, utc_now


SNAPSHOT_REFERENCE_TABLES = (
    "address",
    "building",
    "business_location",
    "census_estimate",
    "environment_indicator",
    "geo_boundary",
    "geo_cell",
    "health_indicator",
    "hotel",
    "inspection",
    "license",
    "mobility_proxy",
    "parcel",
    "poi",
    "property_record",
    "regulatory_record",
    "region_definition",
    "region_member",
    "short_term_rental",
    "traffic_observation",
    "transit_ridership",
    "transit_stop",
    "workforce_estimate",
)


def referenced_snapshot_set(conn: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """Return all snapshots referenced by the current analysis input facts."""
    referenced: dict[str, set[str]] = {}
    available = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }
    for table in SNAPSHOT_REFERENCE_TABLES:
        if table not in available:
            continue
        rows = conn.execute(
            f"SELECT DISTINCT source_id, snapshot_id FROM {table} "
            "WHERE source_id IS NOT NULL AND snapshot_id IS NOT NULL"
        ).fetchall()
        for source_id, snapshot_id in rows:
            referenced.setdefault(str(source_id), set()).add(str(snapshot_id))
    return {
        source_id: sorted(snapshot_ids)
        for source_id, snapshot_ids in sorted(referenced.items())
    }


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    source_id: str
    path: Path
    metadata_path: Path
    sha256: str
    byte_size: int
    retrieved_at: datetime
    vintage: str


def _windows_path_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if len(drive) != 1 or not drive.isalpha():
        raise ValueError(f"WSL fallback requires a drive-letter path: {resolved}")
    relative = resolved.as_posix().split(":", 1)[1].lstrip("/")
    return f"/mnt/{drive}/{relative}"


def _is_windows_ephemeral_port_exhaustion(exc: Exception) -> bool:
    message = str(exc).lower()
    return "winerror 10048" in message or "only one usage of each socket address" in message


def _download_with_wsl_curl(
    *,
    url: str,
    temporary: Path,
    params: dict[str, Any] | None,
    headers: dict[str, str],
    timeout_seconds: float,
) -> int:
    command = [
        "wsl.exe",
        "curl",
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--get",
        url,
        "--output",
        _windows_path_to_wsl(temporary),
        "--write-out",
        "%{http_code}",
    ]
    for key, value in headers.items():
        command.extend(["--header", f"{key}: {value}"])
    for key, value in (params or {}).items():
        command.extend(["--data-urlencode", f"{key}={value}"])
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(60.0, timeout_seconds),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"WSL curl fallback failed with exit {completed.returncode}: {completed.stderr.strip()}"
        )
    status_text = completed.stdout.strip()[-3:]
    if not status_text.isdigit():
        raise RuntimeError(f"WSL curl fallback did not return an HTTP status: {completed.stdout!r}")
    status = int(status_text)
    if status < 200 or status >= 300 or not temporary.is_file():
        raise RuntimeError(f"WSL curl fallback returned HTTP {status}")
    return status


def _snapshot_dir(source_id: str, vintage: str, retrieved_at: datetime) -> Path:
    stamp = retrieved_at.strftime("%Y%m%dT%H%M%S.%fZ")
    path = data_dir() / "raw" / safe_component(source_id) / safe_component(vintage) / stamp
    path.mkdir(parents=True, exist_ok=False)
    return path


def _record_snapshot(
    conn: duckdb.DuckDBPyConnection,
    *,
    snapshot: Snapshot,
    source_url: str,
    original_filename: str,
    run_id: str | None,
    dataset_id: str | None,
    content_type: str | None,
    query: Any,
    license_text: str | None,
    status: str,
    valid_from: date | None,
    valid_to: date | None,
    warnings: Any,
    http_status: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO dataset_snapshot(
            snapshot_id, source_id, run_id, dataset_id, vintage, retrieved_at,
            valid_from, valid_to, source_url, query_json, content_type, byte_size,
            sha256, license, status, raw_path, metadata_path, warnings_json,
            http_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON, ?, ?, ?, ?, ?, ?, ?, ?::JSON, ?)
        """,
        [
            snapshot.snapshot_id,
            snapshot.source_id,
            run_id,
            dataset_id,
            snapshot.vintage,
            snapshot.retrieved_at,
            valid_from,
            valid_to,
            source_url,
            json_dumps(query),
            content_type,
            snapshot.byte_size,
            snapshot.sha256,
            license_text,
            status,
            str(snapshot.path),
            str(snapshot.metadata_path),
            json_dumps(warnings),
            http_status,
        ],
    )
    conn.execute(
        "INSERT INTO raw_file VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            new_id("raw"),
            snapshot.snapshot_id,
            original_filename,
            str(snapshot.path),
            snapshot.byte_size,
            snapshot.sha256,
            content_type,
            snapshot.retrieved_at,
        ],
    )


def save_bytes_snapshot(
    conn: duckdb.DuckDBPyConnection,
    *,
    source_id: str,
    vintage: str,
    filename: str,
    content: bytes,
    source_url: str,
    run_id: str | None = None,
    content_type: str | None = None,
    query: Any = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    warnings: Any = None,
    http_status: int | None = None,
) -> Snapshot:
    source = get_source(source_id)
    retrieved_at = utc_now()
    directory = _snapshot_dir(source_id, vintage, retrieved_at)
    path = directory / safe_component(filename)
    path.write_bytes(content)
    digest = sha256_file(path)
    metadata = {
        "snapshot_id": new_id("snap"),
        "source_id": source_id,
        "dataset_id": source.get("dataset_id"),
        "source_url": source_url,
        "original_filename": filename,
        "retrieved_at": utc_iso(retrieved_at),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "byte_size": path.stat().st_size,
        "sha256": digest,
        "content_type": content_type or mimetypes.guess_type(filename)[0],
        "license": source.get("license"),
        "query": query,
        "status": "SUCCEEDED",
        "http_status": http_status,
        "warnings": warnings or [],
    }
    metadata_path = directory / "manifest.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    snapshot = Snapshot(
        snapshot_id=metadata["snapshot_id"],
        source_id=source_id,
        path=path,
        metadata_path=metadata_path,
        sha256=digest,
        byte_size=path.stat().st_size,
        retrieved_at=retrieved_at,
        vintage=vintage,
    )
    _record_snapshot(
        conn,
        snapshot=snapshot,
        source_url=source_url,
        original_filename=filename,
        run_id=run_id,
        dataset_id=source.get("dataset_id"),
        content_type=metadata["content_type"],
        query=query,
        license_text=source.get("license"),
        status="SUCCEEDED",
        valid_from=valid_from,
        valid_to=valid_to,
        warnings=warnings or [],
        http_status=http_status,
    )
    return snapshot


def save_file_snapshot(
    conn: duckdb.DuckDBPyConnection,
    *,
    source_id: str,
    vintage: str,
    filename: str,
    source_path: Path,
    source_url: str,
    run_id: str | None = None,
    content_type: str | None = None,
    query: Any = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    warnings: Any = None,
    http_status: int | None = None,
) -> Snapshot:
    """Copy an extractor-produced file into the append-only Raw layer and register it."""
    source = get_source(source_id)
    retrieved_at = utc_now()
    directory = _snapshot_dir(source_id, vintage, retrieved_at)
    path = directory / safe_component(filename)
    shutil.copyfile(source_path, path)
    digest = sha256_file(path)
    snapshot_id = new_id("snap")
    media_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    metadata = {
        "snapshot_id": snapshot_id,
        "source_id": source_id,
        "dataset_id": source.get("dataset_id"),
        "source_url": source_url,
        "original_filename": filename,
        "retrieved_at": utc_iso(retrieved_at),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "byte_size": path.stat().st_size,
        "sha256": digest,
        "content_type": media_type,
        "license": source.get("license"),
        "query": query,
        "status": "SUCCEEDED",
        "http_status": http_status,
        "warnings": warnings or [],
    }
    metadata_path = directory / "manifest.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    snapshot = Snapshot(
        snapshot_id=snapshot_id,
        source_id=source_id,
        path=path,
        metadata_path=metadata_path,
        sha256=digest,
        byte_size=path.stat().st_size,
        retrieved_at=retrieved_at,
        vintage=vintage,
    )
    _record_snapshot(
        conn,
        snapshot=snapshot,
        source_url=source_url,
        original_filename=filename,
        run_id=run_id,
        dataset_id=source.get("dataset_id"),
        content_type=media_type,
        query=query,
        license_text=source.get("license"),
        status="SUCCEEDED",
        valid_from=valid_from,
        valid_to=valid_to,
        warnings=warnings or [],
        http_status=http_status,
    )
    return snapshot


def download_snapshot(
    conn: duckdb.DuckDBPyConnection,
    *,
    source_id: str,
    vintage: str,
    url: str,
    filename: str | None = None,
    run_id: str | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    timeout_seconds: float = 180.0,
    client: httpx.Client | None = None,
) -> Snapshot:
    source = get_source(source_id)
    retrieved_at = utc_now()
    directory = _snapshot_dir(source_id, vintage, retrieved_at)
    candidate = filename or Path(urlparse(url).path).name or f"{source_id}.bin"
    path = directory / safe_component(candidate)
    temporary = path.with_suffix(path.suffix + ".part")
    request_headers = {"User-Agent": "NYC-Lens/0.1 local-research"}
    request_headers.update(headers or {})
    response_headers: dict[str, str] = {}
    response_status: int | None = None
    transport = "httpx"
    def _download(active_client: httpx.Client) -> None:
        nonlocal response_headers, response_status
        with active_client.stream("GET", url, params=params, headers=request_headers) as response:
            response.raise_for_status()
            response_headers = dict(response.headers)
            response_status = response.status_code
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)

    try:
        if client is None:
            with httpx.Client(follow_redirects=True, timeout=timeout_seconds) as owned_client:
                _download(owned_client)
        else:
            _download(client)
        temporary.replace(path)
    except Exception as exc:
        if temporary.exists():
            temporary.unlink()
        if _is_windows_ephemeral_port_exhaustion(exc):
            try:
                response_status = _download_with_wsl_curl(
                    url=url,
                    temporary=temporary,
                    params=params,
                    headers=request_headers,
                    timeout_seconds=timeout_seconds,
                )
                transport = "wsl-curl-fallback"
                temporary.replace(path)
            except Exception:
                if temporary.exists():
                    temporary.unlink()
                if not any(directory.iterdir()):
                    directory.rmdir()
                raise
        else:
            if not any(directory.iterdir()):
                directory.rmdir()
            raise
    digest = sha256_file(path)
    snapshot_id = new_id("snap")
    metadata = {
        "snapshot_id": snapshot_id,
        "source_id": source_id,
        "dataset_id": source.get("dataset_id"),
        "source_url": url,
        "final_url": response_headers.get("content-location", url),
        "original_filename": candidate,
        "retrieved_at": utc_iso(retrieved_at),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "byte_size": path.stat().st_size,
        "sha256": digest,
        "content_type": response_headers.get("content-type") or mimetypes.guess_type(candidate)[0],
        "etag": response_headers.get("etag"),
        "last_modified": response_headers.get("last-modified"),
        "license": source.get("license"),
        "query": params,
        "status": "SUCCEEDED",
        "http_status": response_status,
        "transport": transport,
        "warnings": (
            ["Transport used WSL curl after Windows ephemeral-port exhaustion."]
            if transport == "wsl-curl-fallback"
            else []
        ),
    }
    metadata_path = directory / "manifest.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    snapshot = Snapshot(
        snapshot_id=snapshot_id,
        source_id=source_id,
        path=path,
        metadata_path=metadata_path,
        sha256=digest,
        byte_size=path.stat().st_size,
        retrieved_at=retrieved_at,
        vintage=vintage,
    )
    _record_snapshot(
        conn,
        snapshot=snapshot,
        source_url=url,
        original_filename=candidate,
        run_id=run_id,
        dataset_id=source.get("dataset_id"),
        content_type=metadata["content_type"],
        query=params,
        license_text=source.get("license"),
        status="SUCCEEDED",
        valid_from=valid_from,
        valid_to=valid_to,
        warnings=metadata["warnings"],
        http_status=response_status,
    )
    return snapshot


def latest_snapshot(conn: duckdb.DuckDBPyConnection, source_id: str) -> Snapshot | None:
    row = conn.execute(
        """
        SELECT snapshot_id, source_id, raw_path, metadata_path, sha256,
               byte_size, retrieved_at, vintage
        FROM dataset_snapshot
        WHERE source_id=? AND status='SUCCEEDED'
        ORDER BY retrieved_at DESC LIMIT 1
        """,
        [source_id],
    ).fetchone()
    if not row:
        return None
    return Snapshot(
        snapshot_id=row[0],
        source_id=row[1],
        path=Path(row[2]),
        metadata_path=Path(row[3]),
        sha256=row[4],
        byte_size=row[5],
        retrieved_at=row[6],
        vintage=row[7],
    )
