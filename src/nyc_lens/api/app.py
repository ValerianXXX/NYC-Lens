from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from shapely import from_wkt

from ..analyze import get_site
from ..assistant import PlanningRequest, ConfirmedComparison, interpret
from ..adapters.commercial_mobility import inspect_commercial_mobility_adapters
from ..adapters.commercial_real_estate import inspect_commercial_real_estate_adapters
from ..comparison import get_comparison
from ..db import db_session, initialize_database
from ..evidence import query_metric_evidence
from ..geocoding.service import geocode_address
from ..paths import project_home
from ..reporting.export import export_csv, export_html, export_json, export_xlsx
from ..reporting.comparison_export import (
    export_comparison_csv,
    export_comparison_json,
    export_comparison_xlsx,
)
from ..scoring import ScoringNotCalibratedError, score_site, scoring_status
from ..analysis_jobs import AnalysisFailedError, AnalysisJobs, JobUnavailableError, run_analysis_async, stop_active_jobs
from ..analysis_contracts import SiteRequest as SiteAnalysisRequest, ComparisonRequest as SiteComparisonRequest
from ..database_admission import DatabaseBusyError
from .request_limits import AnalysisBodyLimit


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    try:
        yield
    finally:
        import anyio
        await anyio.to_thread.run_sync(stop_active_jobs)


app = FastAPI(title="NYC Lens", version="0.2.0", docs_url="/docs", lifespan=lifespan)
app.add_middleware(AnalysisBodyLimit)
UI_DIR = project_home() / "ui"
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


@app.exception_handler(DatabaseBusyError)
async def database_busy(_request, _error):
    return JSONResponse(status_code=503, headers={"Retry-After": "1"}, content={"detail": {
        "code": "DATABASE_BUSY", "message_en": "Another local operation is using the database. Wait for it to stop.",
        "message_zh": "另一项本地任务正在使用数据库，请等待它停止。"}})


@app.exception_handler(JobUnavailableError)
async def job_unavailable(_request, error):
    if isinstance(error, AnalysisFailedError):
        receipt = error.receipt
        status, code = receipt["status"], receipt["error_code"]
        http_status = 504 if status == "TIMED_OUT" else 409 if status == "CANCELLED" else 422 if code == "ANALYSIS_INPUT_OR_DATA_UNAVAILABLE" else 503
        detail = {"code": code, "status": status, "job_id": receipt["job_id"],
                  "database_cleanup_confirmed": receipt["database_cleanup_confirmed"],
                  "message_en": "Analysis did not complete. Inspect the saved task before starting another request.",
                  "message_zh": "分析未完成。请先检查已保存的任务，再决定是否发起新的请求。"}
        if code == "EXTERNAL_SERVICE_UNAVAILABLE" and receipt["database_cleanup_confirmed"]:
            detail.update(message="外部公共数据服务暂时无法连接，请稍后重试；本地数据库没有损坏。",
                          message_zh="外部公共数据服务暂时无法连接，请稍后重试；本地数据库没有损坏。",
                          message_en="An external public-data service is temporarily unavailable. Please try again; the local database is not damaged.")
        return JSONResponse(status_code=http_status, headers={"X-Analysis-Job-Id": receipt["job_id"]}, content={"detail": detail})
    return JSONResponse(status_code=503, content={"detail": {"code": str(error),
        "message_en": "Analysis is unavailable. Keep the task identifier and check its status before retrying.",
        "message_zh": "分析暂不可用。请保留任务标识，并在重试前检查原任务状态。"}})


async def _compatibility_analysis(operation: str, payload: dict, response: Response, request_id: str | None):
    identifier = request_id if request_id is not None else uuid4().hex
    response.headers["X-Analysis-Job-Id"] = "analysis_" + identifier
    try:
        return await run_analysis_async(operation, payload, request_id=identifier)
    except ValueError:
        raise HTTPException(422, "Invalid bounded analysis input or request identifier already used",
                            headers={"X-Analysis-Job-Id": "analysis_" + identifier}) from None


class AnalysisJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["site", "comparison", "assistant_comparison"]
    request: dict
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@app.post("/api/analysis-jobs", status_code=202)
def start_analysis_job(request: AnalysisJobRequest):
    try:
        return AnalysisJobs().start(request.operation, request.request, request_id=request.request_id)
    except ValueError:
        raise HTTPException(422, "Invalid bounded analysis input or request identifier already used") from None
    except DatabaseBusyError:
        return JSONResponse(status_code=503, content={"detail": {"code": "DATABASE_BUSY", "started": False,
            "message_en": "Another local task is active. This new task was not started.",
            "message_zh": "另一项本地任务仍在运行，本次新任务未启动。"}})
    except JobUnavailableError as error:
        if str(error) not in {"RESOURCE_GUARD_UNAVAILABLE", "JOB_HISTORY_CAPACITY_REQUIRES_ARCHIVE"}:
            raise
        return JSONResponse(status_code=503, content={"detail": {"code": str(error), "started": False,
            "message_en": "The resource guard or job history is unavailable. No task was started.",
            "message_zh": "资源保护或任务历史不可用，本次任务未启动。"}})


@app.get("/api/analysis-jobs/{job_id}")
def analysis_job(job_id: str):
    try:
        return AnalysisJobs().get(job_id)
    except KeyError:
        raise HTTPException(404, "Analysis job not found") from None


@app.post("/api/analysis-jobs/{job_id}/cancel")
def cancel_analysis_job(job_id: str):
    try:
        return AnalysisJobs().cancel(job_id)
    except KeyError:
        raise HTTPException(404, "Analysis job not found") from None


@app.get("/api/analysis-jobs/{job_id}/result")
def analysis_job_result(job_id: str):
    try:
        return AnalysisJobs().result(job_id)
    except KeyError:
        raise HTTPException(404, "Analysis job not found") from None


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
async def assistant_compare(request: ConfirmedComparison, response: Response,
                            request_id: str | None = Header(default=None, alias="Idempotency-Key", pattern=r"^[0-9a-f]{32}$")):
    return await _compatibility_analysis("assistant_comparison", request.model_dump(), response, request_id)


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
async def site_analysis(request: SiteAnalysisRequest, response: Response,
                        request_id: str | None = Header(default=None, alias="Idempotency-Key", pattern=r"^[0-9a-f]{32}$")):
    return await _compatibility_analysis("site", request.model_dump(), response, request_id)


@app.post("/api/site-comparison")
async def site_comparison(request: SiteComparisonRequest, response: Response,
                          request_id: str | None = Header(default=None, alias="Idempotency-Key", pattern=r"^[0-9a-f]{32}$")):
    return await _compatibility_analysis("comparison", request.model_dump(), response, request_id)


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
            "SELECT c.geom_wkt FROM catchment c JOIN site_query q ON q.site_id=c.site_id "
            "WHERE c.site_id=? AND c.catchment_type=? AND q.status='SUCCEEDED' "
            "ORDER BY c.created_at DESC LIMIT 1",
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
    metric_id: str = Query(min_length=2, max_length=100),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0, le=1_000_000),
):
    with db_session(read_only=True) as conn:
        try:
            return query_metric_evidence(conn, site_id=site_id, catchment=catchment,
                                         metric_id=metric_id, limit=limit, offset=offset)
        except KeyError:
            raise HTTPException(404, "Site metric not found") from None
        except ValueError:
            raise HTTPException(422, "Invalid bounded evidence query") from None


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
