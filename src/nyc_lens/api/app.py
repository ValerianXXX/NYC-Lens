from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from shapely import from_wkt

from ..analyze import analyze_site, get_site
from ..assistant import PlanningRequest, ConfirmedComparison, interpret, execute_comparison
from ..adapters.commercial_mobility import inspect_commercial_mobility_adapters
from ..adapters.commercial_real_estate import inspect_commercial_real_estate_adapters
from ..comparison import compare_sites, get_comparison
from ..db import db_session, initialize_database
from ..geocoding.service import geocode_address
from ..paths import project_home
from ..reporting.export import export_csv, export_html, export_json, export_xlsx
from ..reporting.comparison_export import (
    export_comparison_csv,
    export_comparison_json,
    export_comparison_xlsx,
)
from ..scoring import ScoringNotCalibratedError, score_site, scoring_status


class SiteAnalysisRequest(BaseModel):
    address: str = Field(min_length=5)
    business_type: str = "full_service_restaurant"
    catchments: list[str] = Field(
        default_factory=lambda: ["250m", "500m", "1km", "5min_walk", "10min_walk", "15min_walk"]
    )


class SiteComparisonRequest(BaseModel):
    addresses: list[str] = Field(min_length=2, max_length=20)
    business_type: str = "full_service_restaurant"
    catchments: list[str] = Field(default_factory=lambda: ["500m", "1km"])
    comparison_catchment: str = "500m"


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="NYC Lens", version="0.2.0", docs_url="/docs", lifespan=lifespan)
UI_DIR = project_home() / "ui"
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local"}


@app.post("/api/assistant/interpret")
def assistant_interpret(request: PlanningRequest):
    try:
        return interpret(request)
    except (ValueError, httpx.HTTPError, KeyError, TypeError):
        raise HTTPException(422, "Interpretation unavailable. Use the editable address comparison form; no analysis has run.") from None


@app.post("/api/assistant/compare")
def assistant_compare(request: ConfirmedComparison):
    try:
        with db_session() as conn:
            return execute_comparison(conn, request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except httpx.HTTPError:
        raise HTTPException(503, "Public data service unavailable; the confirmed request can be retried manually.") from None


@app.get("/api/adapters/commercial-mobility")
def commercial_mobility_adapters():
    return inspect_commercial_mobility_adapters()


@app.get("/api/adapters/commercial-real-estate")
def commercial_real_estate_adapters():
    return inspect_commercial_real_estate_adapters()


@app.get("/api/scoring/status")
def score_status():
    with db_session(read_only=True) as conn:
        return scoring_status(conn)


@app.get("/api/site/{site_id}/score")
def site_score(site_id: str):
    with db_session(read_only=True) as conn:
        try:
            return score_site(conn, site_id)
        except KeyError as exc:
            raise HTTPException(404, "Site not found") from exc
        except ScoringNotCalibratedError as exc:
            raise HTTPException(
                409,
                {
                    "status": "BLOCKED_CALIBRATION",
                    "message": str(exc),
                    "status_url": "/api/scoring/status",
                },
            ) from exc


@app.get("/api/geocode")
def geocode(address: str = Query(min_length=5)):
    with db_session() as conn:
        try:
            return geocode_address(conn, address)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                503,
                {
                    "code": "EXTERNAL_SERVICE_UNAVAILABLE",
                    "message": "官方地址服务暂时无法连接，请稍后重试；本地数据库没有损坏。",
                    "message_zh": "官方地址服务暂时无法连接，请稍后重试；本地数据库没有损坏。",
                    "message_en": "The official address service is temporarily unavailable. Please try again; the local database is not damaged.",
                },
            ) from exc


@app.post("/api/site-analysis")
def site_analysis(request: SiteAnalysisRequest):
    with db_session() as conn:
        try:
            return analyze_site(
                conn,
                address=request.address,
                business_type=request.business_type,
                catchments=request.catchments,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                503,
                {
                    "code": "EXTERNAL_SERVICE_UNAVAILABLE",
                    "message": "外部公共数据服务暂时无法连接，请稍后重试；本地数据库没有损坏。",
                    "message_zh": "外部公共数据服务暂时无法连接，请稍后重试；本地数据库没有损坏。",
                    "message_en": "An external public-data service is temporarily unavailable. Please try again; the local database is not damaged.",
                },
            ) from exc


@app.post("/api/site-comparison")
def site_comparison(request: SiteComparisonRequest):
    with db_session() as conn:
        try:
            return compare_sites(
                conn,
                addresses=request.addresses,
                business_type=request.business_type,
                catchments=request.catchments,
                comparison_catchment=request.comparison_catchment,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                503,
                {
                    "code": "EXTERNAL_SERVICE_UNAVAILABLE",
                    "message": "外部公共数据服务暂时无法连接，请稍后重试；本地数据库没有损坏。",
                    "message_zh": "外部公共数据服务暂时无法连接，请稍后重试；本地数据库没有损坏。",
                    "message_en": "An external public-data service is temporarily unavailable. Please try again; the local database is not damaged.",
                },
            ) from exc


@app.get("/api/comparison/{comparison_id}")
def comparison(comparison_id: str):
    with db_session(read_only=True) as conn:
        result = get_comparison(conn, comparison_id)
    if result is None:
        raise HTTPException(404, "Comparison not found")
    return result


@app.get("/api/site/{site_id}")
def site(site_id: str):
    with db_session(read_only=True) as conn:
        result = get_site(conn, site_id)
    if result is None:
        raise HTTPException(404, "Site not found")
    return result


def _section(site_id: str, metric_prefixes: tuple[str, ...]):
    with db_session(read_only=True) as conn:
        result = get_site(conn, site_id)
    if result is None:
        raise HTTPException(404, "Site not found")
    return {
        catchment: {
            metric_id: metric
            for metric_id, metric in payload["metrics"].items()
            if metric_id.startswith(metric_prefixes)
        }
        for catchment, payload in result["catchments"].items()
    }


@app.get("/api/site/{site_id}/competitors")
def competitors(site_id: str):
    return _section(
        site_id,
        (
            "direct_",
            "substitute_",
            "demand_complement_",
            "closed_",
            "competitors_",
            "competition_",
            "market_opportunity_",
        ),
    )


@app.get("/api/site/{site_id}/demographics")
def demographics(site_id: str):
    return _section(
        site_id,
        (
            "resident_",
            "decennial_",
            "median_",
            "age_",
            "per_capita_",
            "bachelors_",
            "foreign_",
            "family_",
            "nonfamily_",
            "civilian_",
            "non_hispanic_",
            "hispanic_",
            "english_",
            "spanish_",
            "other_indo_",
            "asian_pacific_",
            "drove_alone_",
            "public_transit_commute_",
            "walked_commute_",
            "worked_from_home_",
            "no_vehicle_",
            "owner_",
            "renter_",
            "management_business_",
            "service_occupation_",
            "sales_office_",
            "natural_resources_",
            "production_transport_",
        ),
    )


@app.get("/api/site/{site_id}/mobility")
def mobility(site_id: str):
    return _section(
        site_id,
        (
            "daytime_",
            "top_workplace_",
            "subway_",
            "bus_",
            "citibike_",
            "tlc_",
            "vehicle_",
            "pedestrian_",
            "traffic_",
            "public_transit_",
            "transit_",
            "regional_",
            "worker_",
        ),
    )


@app.get("/api/site/{site_id}/real-estate")
def real_estate(site_id: str):
    return _section(
        site_id,
        (
            "parcel_",
            "intersecting_",
            "building_",
            "residential_",
            "office_",
            "retail_",
            "rolling_",
            "acris_",
            "historical_",
            "active_dob_",
            "active_ecb_",
            "regulatory_",
            "certificate_",
            "zoning_",
        ),
    )


@app.get("/api/site/{site_id}/transactions")
def transactions(
    site_id: str,
    catchment: str = Query(default="1km", min_length=2),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    with db_session(read_only=True) as conn:
        row = conn.execute(
            "SELECT geom_wkt FROM catchment WHERE site_id=? AND catchment_type=? "
            "ORDER BY created_at DESC LIMIT 1",
            [site_id, catchment],
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Site catchment not found")
        catchment_wkt = row[0]
        target_geometry = from_wkt(catchment_wkt)
        min_x, min_y, max_x, max_y = target_geometry.bounds
        candidate_rows = conn.execute(
            """
            SELECT DISTINCT
                t.transaction_case_id, t.case_type, t.transaction_date, t.amount,
                t.geography_type, t.geography_id, t.attributes_json,
                t.source_id, t.snapshot_id, t.confidence, t.method_version,
                p.geom_wkt
            FROM transaction_case t
            JOIN parcel p ON p.bbl=t.geography_id
            WHERE t.source_id='derived_public_transaction_cases'
              AND p.max_longitude >= ? AND p.min_longitude <= ?
              AND p.max_latitude >= ? AND p.min_latitude <= ?
            """,
            [min_x, max_x, min_y, max_y],
        ).fetchall()
    fields = (
        "transaction_case_id",
        "case_type",
        "transaction_date",
        "amount",
        "geography_type",
        "geography_id",
        "attributes_json",
        "source_id",
        "snapshot_id",
        "confidence",
        "method_version",
        "geom_wkt",
    )
    matched_by_id = {
        candidate[0]: dict(zip(fields, candidate))
        for candidate in candidate_rows
        if target_geometry.intersects(from_wkt(candidate[-1]))
    }
    matched = sorted(
        matched_by_id.values(),
        key=lambda record: (
            record["transaction_date"],
            record["amount"] or 0,
            record["transaction_case_id"],
        ),
        reverse=True,
    )
    total = len(matched)
    records = matched[offset : offset + limit]
    for record in records:
        attributes = record.get("attributes_json")
        if isinstance(attributes, str):
            record["attributes"] = json.loads(attributes)
        else:
            record["attributes"] = attributes
        record.pop("attributes_json", None)
        record.pop("geom_wkt", None)
    return {
        "site_id": site_id,
        "catchment": catchment,
        "total": total,
        "offset": offset,
        "limit": limit,
        "cases": records,
        "warning": "公开房地产记录案例，不是企业并购、当前估值、法律结论或经物业类型校准的可比案例；不含姓名、当事人、街道或单元地址及联系方式。",
    }


@app.get("/api/site/{site_id}/evidence")
def evidence(
    site_id: str,
    catchment: str = Query(min_length=2),
    metric_id: str = Query(min_length=2),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
):
    with db_session(read_only=True) as conn:
        metric = conn.execute(
            """
            SELECT m.evidence_record_ids_json
            FROM site_metric m
            JOIN catchment c ON c.catchment_id=m.catchment_id
            WHERE m.site_id=? AND c.catchment_type=? AND m.metric_id=?
            ORDER BY m.created_at DESC
            LIMIT 1
            """,
            [site_id, catchment, metric_id],
        ).fetchone()
        if metric is None:
            raise HTTPException(404, "Site metric not found")
        total = conn.execute(
            "SELECT count(DISTINCT json_extract_string(value, '$')) FROM json_each(?::JSON)",
            [metric[0]],
        ).fetchone()[0]
        rows = conn.execute(
            """
            WITH evidence_ids AS (
                SELECT DISTINCT json_extract_string(value, '$') AS entity_id
                FROM json_each(?::JSON)
            ), resolved AS (
                SELECT e.entity_id, s.entity_type, s.source_record_id,
                       s.source_id, s.snapshot_id, d.vintage, d.retrieved_at,
                       d.source_url, d.license,
                       r.original_filename, r.sha256 AS raw_sha256,
                       row_number() OVER (
                           PARTITION BY e.entity_id, s.source_id, s.snapshot_id
                           ORDER BY r.original_filename NULLS LAST
                       ) AS raw_rank
                FROM evidence_ids e
                LEFT JOIN standardized_record s ON s.entity_id=e.entity_id
                LEFT JOIN dataset_snapshot d ON d.snapshot_id=s.snapshot_id
                LEFT JOIN raw_file r ON r.snapshot_id=d.snapshot_id
            )
            SELECT entity_id, entity_type, source_record_id, source_id, snapshot_id,
                   vintage, retrieved_at, source_url, license,
                   original_filename, raw_sha256
            FROM resolved
            WHERE raw_rank=1 OR raw_rank IS NULL
            ORDER BY entity_id, source_id, snapshot_id
            LIMIT ? OFFSET ?
            """,
            [metric[0], limit, offset],
        ).fetchdf().to_dict("records")
    return {
        "site_id": site_id,
        "catchment": catchment,
        "metric_id": metric_id,
        "total_evidence_ids": total,
        "offset": offset,
        "limit": limit,
        "evidence": rows,
    }


@app.get("/api/site/{site_id}/report")
def report(site_id: str, format: Literal["html", "json", "csv", "xlsx"] = "html"):
    with db_session() as conn:
        try:
            path = {
                "html": export_html,
                "json": export_json,
                "csv": export_csv,
                "xlsx": export_xlsx,
            }[format](conn, site_id)
        except KeyError as exc:
            raise HTTPException(404, "Site not found") from exc
    media = {
        "html": "text/html",
        "json": "application/json",
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[format]
    return FileResponse(path, media_type=media, filename=path.name)


@app.get("/api/comparison/{comparison_id}/report")
def comparison_report(
    comparison_id: str,
    format: Literal["json", "csv", "xlsx"] = "xlsx",
):
    with db_session() as conn:
        try:
            path = {
                "json": export_comparison_json,
                "csv": export_comparison_csv,
                "xlsx": export_comparison_xlsx,
            }[format](conn, comparison_id)
        except KeyError as exc:
            raise HTTPException(404, "Comparison not found") from exc
    media = {
        "json": "application/json",
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[format]
    return FileResponse(path, media_type=media, filename=path.name)
