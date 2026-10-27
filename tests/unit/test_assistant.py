import asyncio
import json
import sys
from importlib import import_module

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from nyc_lens.assistant import ComparisonIntent, ConfirmedComparison, PlanningRequest, interpret, execute_comparison
from nyc_lens.db import db_session
from nyc_lens.demo import seed_demo, DEMO_ADDRESSES


@pytest.fixture
def demo_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NYC_LENS_HOME", str(tmp_path))
    monkeypatch.delenv("NYC_LENS_DATABASE", raising=False)
    monkeypatch.setenv("NYC_LENS_AI_PROVIDER", "disabled")
    seed_demo(tmp_path)
    return tmp_path


def test_bilingual_intent_does_not_execute_and_requires_missing_fields():
    for text in [f'Compare "{DEMO_ADDRESSES[0]}" and "{DEMO_ADDRESSES[1]}" for a restaurant, rent and competition',
                 f'为餐厅比较“{DEMO_ADDRESSES[0]}”和“{DEMO_ADDRESSES[1]}”，关注租金与竞争']:
        result = interpret(PlanningRequest(text=text))
        assert result["status"] == "AWAITING_CONFIRMATION"
        assert result["automatic_execution"] is False
        assert result["intent"]["addresses"] == DEMO_ADDRESSES
        assert "rent" in result["intent"]["priorities"]
    assert interpret(PlanningRequest(text="Find a good place for a cafe"))["status"] == "NEEDS_INPUT"


def test_model_cannot_invent_an_address_or_sql_tool():
    request = PlanningRequest(text='A restaurant near "100 Original Street"')
    with pytest.raises(ValueError, match="quote"):
        interpret(request, model_call=lambda _: ComparisonIntent(addresses=["900 Invented Street"]).model_dump())
    with pytest.raises(ValidationError):
        interpret(request, model_call=lambda _: {"addresses": [], "sql": "DROP TABLE address"})
    result = interpret(request, model_call=lambda _: ComparisonIntent(addresses=["100 Original Street"]).model_dump())
    assert result["interpretation"] == "MODEL_PROPOSAL"
    assert result["status"] == "NEEDS_INPUT"
    with pytest.raises(ValidationError):
        ConfirmedComparison(intent=ComparisonIntent(), confirmed="true")


def test_model_http_contract_and_explicit_cloud_consent(monkeypatch):
    import httpx
    import nyc_lens.assistant as assistant
    request = PlanningRequest(text=f'A restaurant at "{DEMO_ADDRESSES[0]}" and "{DEMO_ADDRESSES[1]}"')
    monkeypatch.setenv("NYC_LENS_AI_PROVIDER", "openai")
    monkeypatch.setenv("NYC_LENS_AI_MODEL", "test-contract-model")
    monkeypatch.delenv("NYC_LENS_ALLOW_CLOUD", raising=False)
    with pytest.raises(ValueError, match="consent"):
        assistant._bounded_model(request)
    monkeypatch.setenv("NYC_LENS_ALLOW_CLOUD", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-only")
    captured = []
    real_client = httpx.Client
    proposal = ComparisonIntent(addresses=DEMO_ADDRESSES, business_type="full_service_restaurant").model_dump()
    def respond(req):
        captured.append(json.loads(req.content))
        assert str(req.url) == "https://api.openai.com/v1/responses"
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(proposal)}]}]})
    monkeypatch.setattr(assistant.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    assert interpret(request)["intent"]["addresses"] == DEMO_ADDRESSES
    assert captured[0]["store"] is False and "tools" not in captured[0]
    assert captured[0]["input"] == request.text
    monkeypatch.setenv("NYC_LENS_AI_PROVIDER", "loopback")
    monkeypatch.setenv("NYC_LENS_AI_URL", "https://untrusted.example/api")
    with pytest.raises(ValueError, match="loopback"):
        assistant._bounded_model(request)


def test_actual_duckdb_comparison_evidence_export_and_missing_data(demo_home):
    api = import_module("nyc_lens.api.app")
    client = TestClient(api.app)
    intent = ComparisonIntent(addresses=DEMO_ADDRESSES, business_type="full_service_restaurant", priorities=["competition", "rent", "population"])
    denied = client.post("/api/assistant/compare", json={"intent": intent.model_dump(), "confirmed": False})
    assert denied.status_code == 422
    with db_session() as conn:
        assert conn.execute("SELECT count(*) FROM site_query").fetchone()[0] == 0
    response = client.post("/api/assistant/compare", json={"intent": intent.model_dump(), "confirmed": True})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["data_mode"] == "SYNTHETIC_DEMO"
    values = result["metric_comparisons"]["direct_competitor_count"]["values"]
    assert [row["value"] for row in values] == [1, 3]
    brief = result["assistant"]["brief"]
    assert brief["differences"][0]["first_minus_second"] == -2
    assert {row["priority"] for row in brief["missing"]} == {"rent", "population"}
    assert brief["composite_score"] is None
    evidence = client.get(brief["differences"][0]["evidence_urls"][0])
    assert evidence.status_code == 200
    assert evidence.json()["evidence"][0]["source_url"] == "synthetic://nyc-lens/demo-v1"
    for format in ["json", "csv", "xlsx"]:
        report = client.get(f"/api/comparison/{result['comparison_id']}/report?format={format}")
        assert report.status_code == 200
        assert len(report.content) > 100
        if format == "json":
            assert report.json()["data_mode"] == "SYNTHETIC_DEMO"
    assert client.get(f"/api/site/{result['sites'][0]['site_id']}/score").status_code == 409
    unknown = intent.model_copy(update={"addresses": [DEMO_ADDRESSES[0], "Unknown real address"]})
    with db_session() as conn, pytest.raises(ValueError, match="Synthetic demo"):
        execute_comparison(conn, ConfirmedComparison(intent=unknown, confirmed=True))
    # Delete the fixture source marker, then repeat: absent data must be null, never zero.
    with db_session() as conn:
        conn.execute("UPDATE dataset_snapshot SET status='FAILED'")
        missing = execute_comparison(conn, ConfirmedComparison(intent=intent, confirmed=True))
    assert missing["metric_comparisons"]["direct_competitor_count"]["values"][0]["value"] is None
    assert not missing["assistant"]["brief"]["differences"]


def test_mcp_current_and_legacy_client_business_flow(demo_home):
    mcp = pytest.importorskip("mcp")
    from nyc_lens.mcp_server import server

    async def scenario(mode):
        async with mcp.Client(server, mode=mode) as client:
            listed = await client.list_tools()
            assert {"analyze_address", "compare_addresses", "query_evidence", "generate_report"} <= {tool.name for tool in listed.tools}
            denied = await client.call_tool("compare_addresses", {"addresses": DEMO_ADDRESSES})
            assert denied.is_error
            compared = await client.call_tool("compare_addresses", {"addresses": DEMO_ADDRESSES, "confirmed": True})
            assert not compared.is_error
            result = compared.structured_content
            assert result["data_mode"] == "SYNTHETIC_DEMO"
            evidence = await client.call_tool("query_evidence", {"site_id": result["sites"][0]["site_id"], "metric_id": "direct_competitor_count"})
            assert not evidence.is_error
            assert evidence.structured_content["evidence"][0]["source_url"].startswith("synthetic:")
            report = await client.call_tool("generate_report", {"comparison_id": result["comparison_id"]})
            assert not report.is_error
            assert report.structured_content["uploaded"] is False
            invalid = await client.call_tool("analyze_address", {"address": DEMO_ADDRESSES[0], "catchment": "SELECT * FROM address"})
            assert invalid.is_error
    for mode in ["2026-07-28", "legacy"]:
        asyncio.run(scenario(mode))


def test_mcp_stdio_process_protocol_and_report(demo_home):
    mcp = pytest.importorskip("mcp")
    from mcp.client.stdio import StdioServerParameters
    params = StdioServerParameters(command=sys.executable, args=["-m", "nyc_lens.mcp_server"],
        env={"NYC_LENS_HOME": str(demo_home), "NYC_LENS_AI_PROVIDER": "disabled"})

    async def scenario(mode):
        async with mcp.Client(params, mode=mode) as client:
            assert client.protocol_version == ("2026-07-28" if mode != "legacy" else "2025-11-25")
            result = await client.call_tool("compare_addresses", {"addresses": DEMO_ADDRESSES, "confirmed": True})
            assert not result.is_error
            report = await client.call_tool("generate_report", {"comparison_id": result.structured_content["comparison_id"]})
            assert not report.is_error
            assert report.structured_content["bytes"] > 100
    for mode in ["2026-07-28", "legacy"]:
        asyncio.run(scenario(mode))
