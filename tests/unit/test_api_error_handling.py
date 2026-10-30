from contextlib import nullcontext
from importlib import import_module

import httpx
import pytest
from fastapi.testclient import TestClient
from nyc_lens.analysis_jobs import AnalysisFailedError


api_module = import_module("nyc_lens.api.app")


@pytest.mark.parametrize(
    ("function_name", "method", "path", "payload"),
    [
        ("geocode_address", "get", "/api/geocode?address=502%20W%2034th%20St", None),
        (
            "analyze_site",
            "post",
            "/api/site-analysis",
            {
                "address": "502 W 34th St., New York, NY 10001",
                "business_type": "full_service_restaurant",
                "catchments": ["500m"],
            },
        ),
        (
            "compare_sites",
            "post",
            "/api/site-comparison",
            {
                "addresses": [
                    "502 W 34th St., New York, NY 10001",
                    "1501 Broadway, New York, NY 10036",
                ],
                "business_type": "full_service_restaurant",
                "catchments": ["500m"],
                "comparison_catchment": "500m",
            },
        ),
    ],
)
def test_external_service_failures_return_readable_json(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
    method: str,
    path: str,
    payload: dict | None,
) -> None:
    def unavailable(*_args, **_kwargs):
        raise httpx.RemoteProtocolError("upstream disconnected")

    monkeypatch.setattr(api_module, "db_session", lambda **_kwargs: nullcontext(object()))
    if function_name == "geocode_address":
        monkeypatch.setattr(api_module, function_name, unavailable)
    else:
        # Analysis now crosses a process/job boundary. Preserve the original
        # readable error assertions while injecting at that transport seam.
        async def unavailable_job(*_args, **_kwargs):
            raise AnalysisFailedError({"status": "FAILED", "error_code": "EXTERNAL_SERVICE_UNAVAILABLE",
                "job_id": "analysis_" + "a" * 32, "database_cleanup_confirmed": True})
        monkeypatch.setattr(api_module, "run_analysis_async", unavailable_job)

    response = TestClient(api_module.app).request(method, path, json=payload)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    detail = response.json()["detail"]
    assert detail["code"] == "EXTERNAL_SERVICE_UNAVAILABLE"
    assert "本地数据库没有损坏" in detail["message"]
    assert detail["message_zh"] == detail["message"]
    assert "local database is not damaged" in detail["message_en"]
