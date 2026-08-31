from contextlib import nullcontext
from importlib import import_module

import httpx
import pytest
from fastapi.testclient import TestClient


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
    monkeypatch.setattr(api_module, function_name, unavailable)

    response = TestClient(api_module.app).request(method, path, json=payload)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    detail = response.json()["detail"]
    assert detail["code"] == "EXTERNAL_SERVICE_UNAVAILABLE"
    assert "本地数据库没有损坏" in detail["message"]
    assert detail["message_zh"] == detail["message"]
    assert "local database is not damaged" in detail["message_en"]
