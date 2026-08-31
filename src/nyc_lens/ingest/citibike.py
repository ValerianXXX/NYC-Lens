from __future__ import annotations

import io
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pandas as pd
from pypdf import PdfReader

from ..config import get_source
from ..db import begin_run, finish_run
from ..provenance import Snapshot, download_snapshot
from ..util import json_dumps
from .common import register_standardized_records, stable_id, standardized_path, update_snapshot_details


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
REGIONAL_BOUNDS = (-74.35, -73.65, 40.45, 41.0)
NYC_BOUNDS = (-74.25909, -73.700272, 40.477399, 40.917577)


def _load_json(snapshot: Snapshot) -> dict[str, Any]:
    value = json.loads(snapshot.path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {snapshot.path}")
    return value


def _feed_urls(root: dict[str, Any]) -> dict[str, str]:
    data = root.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("GBFS root data is not an object")
    language = data.get("en") or next(iter(data.values()), {})
    feeds = language.get("feeds", []) if isinstance(language, dict) else []
    result = {
        str(item.get("name")): str(item.get("url"))
        for item in feeds
        if item.get("name") and item.get("url")
    }
    required = {"system_information", "station_information", "station_status"}
    missing = sorted(required - set(result))
    if missing:
        raise ValueError(f"GBFS root is missing feeds: {', '.join(missing)}")
    return result


def _feed_timestamp(payload: dict[str, Any]) -> datetime:
    value = pd.to_numeric(payload.get("last_updated"), errors="coerce")
    if pd.isna(value):
        raise ValueError("GBFS payload has no valid last_updated timestamp")
    return datetime.fromtimestamp(int(value), tz=UTC)


def _stations(payload: dict[str, Any]) -> pd.DataFrame:
    rows = (payload.get("data") or {}).get("stations", [])
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("GBFS station feed is empty")
    return frame


def _station_proxy_frame(
    station_frame: pd.DataFrame,
    *,
    column: str,
    proxy_type: str,
    unit: str,
    snapshot: Snapshot,
    period: str,
    measure_type: str,
) -> pd.DataFrame:
    values = pd.to_numeric(station_frame.get(column), errors="coerce").clip(lower=0)
    return pd.DataFrame(
        {
            "mobility_proxy_id": station_frame["station_id"].astype(str).map(
                lambda station_id: stable_id("mobility", "citibike_gbfs_current", station_id, proxy_type)
            ),
            "proxy_type": proxy_type,
            "value": values,
            "unit": unit,
            "period": period,
            "longitude": pd.to_numeric(station_frame["lon"], errors="coerce"),
            "latitude": pd.to_numeric(station_frame["lat"], errors="coerce"),
            "source_id": "citibike_gbfs_current",
            "snapshot_id": snapshot.snapshot_id,
            "retrieved_at": snapshot.retrieved_at,
            "valid_from": pd.Timestamp(period).date(),
            "valid_to": pd.Timestamp(period).date(),
            "geography_type": "bike_share_station",
            "geography_id": station_frame["station_id"].astype(str),
            "measure_type": measure_type,
            "confidence": 0.95,
            "method_version": "0.1.0",
        }
    )


def refresh_citibike_gbfs(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "citibike_gbfs_current"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh current GBFS station information and station status")
    request_count = 0
    downloaded_bytes = 0
    snapshots: list[Snapshot] = []
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=120,
            limits=httpx.Limits(max_keepalive_connections=2, max_connections=4),
        ) as client:
            root_snapshot = download_snapshot(
                conn,
                source_id=source_id,
                vintage="current-root",
                url=source["api_url"],
                filename="gbfs.json",
                run_id=run_id,
                client=client,
            )
            snapshots.append(root_snapshot)
            request_count += 1
            downloaded_bytes += root_snapshot.byte_size
            root = _load_json(root_snapshot)
            urls = _feed_urls(root)
            named: dict[str, Snapshot] = {}
            for name in ("system_information", "station_information", "station_status"):
                snapshot = download_snapshot(
                    conn,
                    source_id=source_id,
                    vintage=f"current-{name.replace('_', '-')}",
                    url=urls[name],
                    filename=f"{name}.json",
                    run_id=run_id,
                    client=client,
                )
                named[name] = snapshot
                snapshots.append(snapshot)
                request_count += 1
                downloaded_bytes += snapshot.byte_size

        system_payload = _load_json(named["system_information"])
        information_payload = _load_json(named["station_information"])
        status_payload = _load_json(named["station_status"])
        information = _stations(information_payload)
        status = _stations(status_payload)
        if information["station_id"].duplicated().any() or status["station_id"].duplicated().any():
            raise ValueError("GBFS station feeds contain duplicate station_id values")
        information["lon"] = pd.to_numeric(information["lon"], errors="coerce")
        information["lat"] = pd.to_numeric(information["lat"], errors="coerce")
        west, east, south, north = REGIONAL_BOUNDS
        information = information.loc[
            information["lon"].between(west, east) & information["lat"].between(south, north)
        ].copy()
        status_columns = [
            "station_id", "num_bikes_available", "num_docks_available", "is_installed",
            "is_renting", "is_returning", "last_reported",
        ]
        merged = information.merge(
            status[[column for column in status_columns if column in status]],
            on="station_id",
            how="left",
            validate="one_to_one",
        )
        information_time = _feed_timestamp(information_payload)
        status_time = _feed_timestamp(status_payload)
        rental_status = pd.to_numeric(merged.get("is_renting"), errors="coerce").map(
            {1.0: "renting", 0.0: "not_renting"}
        )
        transit = pd.DataFrame(
            {
                "transit_stop_id": merged["station_id"].astype(str).map(
                    lambda station_id: stable_id("stop", source_id, station_id)
                ),
                "source_stop_id": merged["station_id"].astype(str),
                "complex_id": merged["station_id"].astype(str),
                "name": merged["name"].fillna("Unnamed Citi Bike station"),
                "borough": None,
                "routes_json": merged.apply(
                    lambda row: json_dumps(
                        {
                            "short_name": row.get("short_name"),
                            "region_id": row.get("region_id"),
                            "capacity": row.get("capacity"),
                        }
                    ),
                    axis=1,
                ),
                "ada_status": rental_status,
                "longitude": merged["lon"],
                "latitude": merged["lat"],
                "source_id": source_id,
                "snapshot_id": named["station_information"].snapshot_id,
                "retrieved_at": named["station_information"].retrieved_at,
                "valid_from": information_time.date(),
                "valid_to": information_time.date(),
                "geography_type": "point",
                "geography_id": merged["station_id"].astype(str),
                "measure_type": "published_bikeshare_station",
                "confidence": 0.98,
                "method_version": "0.1.0",
            }
        )
        proxy_frames = [
            _station_proxy_frame(
                merged,
                column="num_bikes_available",
                proxy_type="citibike_bikes_available",
                unit="bikes",
                snapshot=named["station_status"],
                period=status_time.isoformat(),
                measure_type="real_time_station_status",
            ),
            _station_proxy_frame(
                merged,
                column="num_docks_available",
                proxy_type="citibike_docks_available",
                unit="docks",
                snapshot=named["station_status"],
                period=status_time.isoformat(),
                measure_type="real_time_station_status",
            ),
            _station_proxy_frame(
                merged,
                column="capacity",
                proxy_type="citibike_station_capacity",
                unit="docks",
                snapshot=named["station_information"],
                period=information_time.isoformat(),
                measure_type="published_station_capacity",
            ),
        ]
        proxies = pd.concat(proxy_frames, ignore_index=True)
        previous_stops = int(
            conn.execute("SELECT count(*) FROM transit_stop WHERE source_id=?", [source_id]).fetchone()[0]
        )
        previous_proxies = int(
            conn.execute("SELECT count(*) FROM mobility_proxy WHERE source_id=?", [source_id]).fetchone()[0]
        )
        conn.execute("DELETE FROM transit_stop WHERE source_id=?", [source_id])
        conn.execute("DELETE FROM mobility_proxy WHERE source_id=?", [source_id])
        conn.register("_citibike_stations", transit)
        conn.register("_citibike_proxies", proxies)
        conn.execute("INSERT INTO transit_stop BY NAME SELECT * FROM _citibike_stations")
        conn.execute("INSERT INTO mobility_proxy BY NAME SELECT * FROM _citibike_proxies")
        conn.unregister("_citibike_stations")
        conn.unregister("_citibike_proxies")
        register_standardized_records(
            conn,
            transit,
            entity_type="transit_stop",
            entity_id_column="transit_stop_id",
            source_id=source_id,
            snapshot_id=named["station_information"].snapshot_id,
            source_record_id_column="source_stop_id",
        )
        for snapshot_id, subset in proxies.groupby("snapshot_id"):
            register_standardized_records(
                conn,
                subset,
                entity_type="mobility_proxy",
                entity_id_column="mobility_proxy_id",
                source_id=source_id,
                snapshot_id=str(snapshot_id),
            )
        root_rows = pd.DataFrame(
            [{"name": name, "url": url} for name, url in sorted(urls.items())]
        )
        system_rows = pd.DataFrame([system_payload.get("data") or {}])
        update_snapshot_details(conn, root_snapshot.snapshot_id, root_rows)
        update_snapshot_details(conn, named["system_information"].snapshot_id, system_rows)
        update_snapshot_details(conn, named["station_information"].snapshot_id, information)
        update_snapshot_details(
            conn,
            named["station_status"].snapshot_id,
            status,
            warnings=["Point-in-time station availability changes continuously."],
        )
        transit.to_parquet(standardized_path(source_id, "current", "stations.parquet"), index=False)
        proxies.to_parquet(
            standardized_path(source_id, "current", "station_status_proxies.parquet"), index=False
        )
        nyc_west, nyc_east, nyc_south, nyc_north = NYC_BOUNDS
        nyc_stations = int(
            (
                transit["longitude"].between(nyc_west, nyc_east)
                & transit["latitude"].between(nyc_south, nyc_north)
            ).sum()
        )
        missing_status = int(merged["num_bikes_available"].isna().sum())
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS" if missing_status else "SUCCEEDED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(transit) + len(proxies),
            deleted_records=previous_stops + previous_proxies,
            warnings=[
                "Only station-level GBFS information and current status were retained; no trip history or rider trajectory was downloaded.",
                "Availability is a point-in-time operational state and can change within seconds.",
                f"Stations without matching current status: {missing_status}.",
            ],
        )
        return {
            "run_id": run_id,
            "root_snapshot_id": root_snapshot.snapshot_id,
            "station_information_snapshot_id": named["station_information"].snapshot_id,
            "station_status_snapshot_id": named["station_status"].snapshot_id,
            "regional_stations": len(transit),
            "nyc_bbox_stations": nyc_stations,
            "station_status_rows": len(status),
            "station_proxy_rows": len(proxies),
            "stations_without_status": missing_status,
            "status_as_of": status_time,
            "individual_trips_downloaded": False,
            "downloaded_bytes_this_run": downloaded_bytes,
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
            warnings=[f"Completed Raw snapshots remain reusable: {len(snapshots)}"],
        )
        raise


def _report_links(html: str) -> list[tuple[date, str]]:
    pattern = re.compile(
        r"https?://[^\"'<>\s]+/([A-Za-z]+)-(\d{4})-Citi-Bike-Monthly-Report\.pdf",
        re.IGNORECASE,
    )
    found: dict[tuple[int, int], str] = {}
    for url, month_name, year_text in (
        (match.group(0), match.group(1), match.group(2)) for match in pattern.finditer(html)
    ):
        month = MONTHS.get(month_name.lower())
        if month:
            found[(int(year_text), month)] = url.replace("&amp;", "&")
    return [(date(year, month, 1), url) for (year, month), url in sorted(found.items())]


def _pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"\s+", " ", text).strip()


def _number(text: str) -> float:
    return float(text.replace(",", "").replace("$", "").replace("%", ""))


def _extract_monthly_report_metrics(text: str) -> dict[str, tuple[float, str]]:
    patterns: dict[str, tuple[str, str]] = {
        "monthly_trips": (r"There were ([\d,]+) trips in", "trips"),
        "average_daily_trips": (r"average ([\d,]+) trips per day", "trips_per_day"),
        "monthly_trip_miles": (r"distance traveled for all trips was ([\d,]+) miles", "miles"),
        "annual_member_trips": (r"Annual members completed.*?recording ([\d,]+) trips", "trips"),
        "casual_member_trips": (r"compared to ([\d,]+) trips by casual members", "trips"),
        "active_stations_end_of_month": (r"There were ([\d,]+) active (?:Citi Bike )?stations at the end", "stations"),
        "average_bike_fleet": (r"average bike fleet last month was ([\d,]+)", "bikes"),
        "annual_membership_total": (r"Total annual membership stands at ([\d,]+)", "memberships"),
        "station_uptime": (r"Station uptime for the month was ([\d.]+)%", "percent"),
        "monthly_rebalancing_actions": (r"rebalanced a total of ([\d,]+) bicycles", "bike_actions"),
    }
    result: dict[str, tuple[float, str]] = {}
    for metric_id, (pattern, unit) in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            raise ValueError(f"Citi Bike monthly report is missing expected metric: {metric_id}")
        result[metric_id] = (_number(match.group(1)), unit)
    return result


def refresh_citibike_monthly_report(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_id = "citibike_monthly_operating_report"
    source = get_source(source_id)
    run_id = begin_run(conn, source_id, "refresh latest published monthly operating report aggregates")
    downloaded_bytes = 0
    request_count = 0
    try:
        page = download_snapshot(
            conn,
            source_id=source_id,
            vintage="current-index",
            url=source["api_url"],
            filename="operating_reports.html",
            run_id=run_id,
        )
        request_count += 1
        downloaded_bytes += page.byte_size
        html = page.path.read_text(encoding="utf-8", errors="replace")
        links = _report_links(html)
        if not links:
            raise ValueError("No Citi Bike monthly operating-report PDF links were discovered")
        period_start, report_url = links[-1]
        period = period_start.strftime("%Y-%m")
        report = download_snapshot(
            conn,
            source_id=source_id,
            vintage=period,
            url=report_url,
            filename=f"citibike_monthly_report_{period}.pdf",
            run_id=run_id,
            valid_from=period_start,
            valid_to=(pd.Timestamp(period_start) + pd.offsets.MonthEnd()).date(),
        )
        request_count += 1
        downloaded_bytes += report.byte_size
        text = _pdf_text(report.path.read_bytes())
        metrics = _extract_monthly_report_metrics(text)
        period_end = (pd.Timestamp(period_start) + pd.offsets.MonthEnd()).date()
        rows = []
        for metric_id, (value, unit) in metrics.items():
            rows.append(
                {
                    "mobility_proxy_id": stable_id("mobility", source_id, period, metric_id),
                    "proxy_type": f"citibike_{metric_id}",
                    "value": value,
                    "unit": unit,
                    "period": period,
                    "longitude": None,
                    "latitude": None,
                    "source_id": source_id,
                    "snapshot_id": report.snapshot_id,
                    "retrieved_at": report.retrieved_at,
                    "valid_from": period_start,
                    "valid_to": period_end,
                    "geography_type": "bike_share_system",
                    "geography_id": "lyft_nyc",
                    "measure_type": "published_system_aggregate",
                    "confidence": 0.9,
                    "method_version": "0.1.0",
                }
            )
        frame = pd.DataFrame(rows)
        previous = int(
            conn.execute("SELECT count(*) FROM mobility_proxy WHERE source_id=?", [source_id]).fetchone()[0]
        )
        conn.execute("DELETE FROM mobility_proxy WHERE source_id=?", [source_id])
        conn.register("_citibike_monthly", frame)
        conn.execute("INSERT INTO mobility_proxy BY NAME SELECT * FROM _citibike_monthly")
        conn.unregister("_citibike_monthly")
        register_standardized_records(
            conn,
            frame,
            entity_type="mobility_proxy",
            entity_id_column="mobility_proxy_id",
            source_id=source_id,
            snapshot_id=report.snapshot_id,
        )
        link_frame = pd.DataFrame([{"period": item[0], "url": item[1]} for item in links])
        update_snapshot_details(conn, page.snapshot_id, link_frame)
        update_snapshot_details(
            conn,
            report.snapshot_id,
            pd.DataFrame(
                [
                    {"metric_id": metric_id, "value": value, "unit": unit}
                    for metric_id, (value, unit) in metrics.items()
                ]
            ),
            warnings=[
                "Metrics are system-wide published aggregates and must not be assigned to a station, neighborhood or person.",
                "Revenue figures are not standardized here; the report describes them as preliminary and unaudited.",
            ],
        )
        frame.to_parquet(
            standardized_path(source_id, period, "system_aggregates.parquet"), index=False
        )
        finish_run(
            conn,
            run_id,
            "SUCCEEDED_WITH_WARNINGS",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            inserted_records=len(frame),
            deleted_records=previous,
            warnings=[
                "Only system-wide monthly aggregates were standardized; no individual ride rows were downloaded.",
                "System-wide values are context only and are not neighborhood-level demand measures.",
            ],
        )
        return {
            "run_id": run_id,
            "index_snapshot_id": page.snapshot_id,
            "report_snapshot_id": report.snapshot_id,
            "period": period,
            "report_url": report_url,
            "metrics": {metric_id: value for metric_id, (value, _) in metrics.items()},
            "individual_trips_downloaded": False,
            "downloaded_bytes_this_run": downloaded_bytes,
        }
    except Exception as exc:
        finish_run(
            conn,
            run_id,
            "FAILED",
            request_count=request_count,
            downloaded_bytes=downloaded_bytes,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise
