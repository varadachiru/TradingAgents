"""No-network PR5 checks for optional, authenticated FreeLLMAPI routing."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.factory import create_tier_client
from tradingagents.llm_clients.openai_client import is_openai_compatible
from webapp import gateway, server
from webapp.usage import GatewayUsageTracker


@pytest.mark.unit
def test_existing_gemini_mode_remains_default(monkeypatch):
    monkeypatch.delenv("FREELLMAPI_API_KEY", raising=False)
    options = server.validate_run({"ticker": "AAPL", "date": "2026-11-03"})
    assert options["provider"] == "google"
    assert options["quick_model"] == server.MODELS[0]
    assert server.public_provider_status()["freellmapi"]["configured"] is False


@pytest.mark.unit
def test_gateway_registered_with_keyed_openai_compatible_factory():
    assert is_openai_compatible("freellmapi")
    assert get_api_key_env("freellmapi") == "FREELLMAPI_API_KEY"
    cfg = {
        "llm_provider": "freellmapi",
        "backend_url": gateway.DEFAULT_URL,
        "quick_think_llm": "auto:trading-quick",
        "deep_think_llm": "auto:trading-deep",
    }
    client = create_tier_client(cfg, "quick")
    assert client.provider == "freellmapi"
    assert client.model == "auto:trading-quick"
    assert client.base_url == gateway.DEFAULT_URL


@pytest.mark.unit
@pytest.mark.parametrize("base", [
    "https://127.0.0.1:3001/v1",
    "http://localhost.evil.com:3001/v1",
    "http://192.168.0.2:3001/v1",
    "http://example.com:3001/v1",
    "http://user:password@127.0.0.1:3001/v1",
    "http://127.0.0.1:3001/v1?token=abc",
    "http://127.0.0.1:3001/v1#anything",
    "http://127.0.0.1:3001/admin",
    "http://127.0.0.1/v1",
])
def test_gateway_never_accepts_remote_or_malformed_endpoint(monkeypatch, base):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", base)
    with pytest.raises(gateway.GatewayConfigError):
        gateway.gateway_url()


@pytest.mark.unit
def test_valid_loopback_url_and_operator_model_profiles(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "http://localhost:3001/v1")
    monkeypatch.setenv("FREELLMAPI_QUICK_MODEL", "auto:trading-quick")
    monkeypatch.setenv("FREELLMAPI_DEEP_MODEL", "auto:trading-deep")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "gateway-test-secret")
    assert gateway.gateway_url() == "http://localhost:3001/v1"
    assert gateway.configured_models() == ("auto:trading-quick", "auto:trading-deep")
    assert gateway.gateway_is_configured() is True


@pytest.mark.unit
def test_gateway_request_uses_server_approved_model_names(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_QUICK_MODEL", "auto:trading-quick")
    monkeypatch.setenv("FREELLMAPI_DEEP_MODEL", "auto:trading-deep")
    options = server.validate_run({
        "ticker": "RELIANCE.NS", "date": "2026-11-03",
        "provider": "freellmapi", "analysts": ["market"],
        "quick_model": "auto:trading-quick", "deep_model": "auto:trading-deep",
    })
    assert options["provider"] == "freellmapi"
    assert options["quick_model"] == "auto:trading-quick"
    bad = dict(options, quick_model="auto:untrusted")
    with pytest.raises(server.RequestError, match="server-approved"):
        server.validate_run(bad)
    with pytest.raises(server.RequestError, match="supported AI provider"):
        server.validate_run(dict(options, provider="unknown"))


@pytest.mark.unit
@pytest.mark.parametrize("value", [
    "../other", "a" * 101, "x key", "http://host?q=secret",
])
def test_gateway_model_id_cannot_be_injected(monkeypatch, value):
    monkeypatch.setenv("FREELLMAPI_QUICK_MODEL", value)
    with pytest.raises(gateway.GatewayConfigError):
        gateway.configured_models()


@pytest.mark.unit
def test_gateway_health_probe_uses_bearer_token_without_network(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_API_KEY", "gateway-test-secret")
    monkeypatch.setenv("FREELLMAPI_BASE_URL", gateway.DEFAULT_URL)
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit):
            assert limit <= 131_073
            return json.dumps({"data": [
                {"id": "model1", "execution_status": "ready"},
                {"id": "model2", "execution_status": "exhausted"},
                {"id": "auto"},
            ]}).encode("utf-8")

    class Opener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(gateway, "build_opener", lambda *_args: Opener())
    status = gateway.test_gateway_connection()
    assert status["reachable"] is True
    assert status["ready_models"] == 1
    assert captured == {
        "url": gateway.DEFAULT_URL + "/models?available=true",
        "authorization": "Bearer gateway-test-secret",
        "timeout": 5,
    }
    assert "gateway-test-secret" not in json.dumps(status)
    assert gateway._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.com") is None


@pytest.mark.unit
def test_server_status_does_not_leak_provider_keys_or_urls(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-secret")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "freellmapi-secret")
    monkeypatch.setenv("FREELLMAPI_BASE_URL", gateway.DEFAULT_URL)
    handler = object.__new__(server.DashboardHandler)
    handler.path = "/api/status"
    handler.server = SimpleNamespace(manager=server.ResearchManager())
    data = []
    handler._json = lambda status, payload: data.append((status, payload))
    handler.do_GET()
    assert data[0][0] == 200
    body = json.dumps(data[0][1])
    assert data[0][1]["configured"] is True
    assert data[0][1]["providers"]["freellmapi"]["configured"] is True
    assert "gemini-secret" not in body
    assert "freellmapi-secret" not in body
    assert gateway.DEFAULT_URL not in body


@pytest.mark.unit
def test_connection_probe_requires_browser_header(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "test_gateway_connection", lambda: calls.append(True))
    handler = object.__new__(server.DashboardHandler)
    handler.path = "/api/gateway/check"
    handler.headers = {}
    responses = []
    handler._json = lambda status, payload: responses.append((status, payload))
    handler.do_GET()
    assert responses[0][0] == 403
    assert calls == []


@pytest.mark.unit
def test_gateway_token_tracker_never_uses_direct_gemini_prices():
    tracker = GatewayUsageTracker(default_model="gemini-3.1-flash-lite")
    message = SimpleNamespace(
        response_metadata={"model_name": "gemini-3.1-flash-lite"},
        usage_metadata={"input_tokens": 10000, "output_tokens": 1000},
    )
    tracker.on_llm_end(
        SimpleNamespace(generations=[[SimpleNamespace(message=message)]], llm_output={}),
        run_id="test",
    )
    result = tracker.snapshot()
    assert result["calls"] == 1
    assert result["input_tokens"] == 10000
    assert result["provider"] == "freellmapi"
    assert result["estimated_paid_usd"] is None
    assert result["pricing_complete"] is False
    assert result["models"]["gemini-3.1-flash-lite"]["estimated_paid_usd"] is None


@pytest.mark.unit
def test_dashboard_contains_local_gateway_option_and_no_browser_secret_field():
    page = (Path(server.STATIC_DIR) / "index.html").read_text(encoding="utf-8")
    js = (Path(server.STATIC_DIR) / "app.js").read_text(encoding="utf-8")
    assert 'value="freellmapi"' in page
    assert 'id="checkGateway"' in page
    assert 'id="providerChoice"' in page
    assert "FREELLMAPI_API_KEY" not in js
    assert "FREELLMAPI_API_KEY" not in page


@pytest.mark.unit
def test_gateway_full_simulated_research_saves_unpriced_usage(monkeypatch, tmp_path):
    from tradingagents.graph import trading_graph

    monkeypatch.setenv("FREELLMAPI_API_KEY", "integration-test-secret")
    monkeypatch.setenv("FREELLMAPI_BASE_URL", gateway.DEFAULT_URL)
    report_dir = tmp_path / "reports" / "AAPL_20261105_190000"
    answer = {
        "market_report": "AAPL technical report",
        "final_trade_decision": "Rating: Hold\nPrice Target: not provided",
        "risk_debate_state": {"neutral_history": "Risk debate"},
    }
    captured = {}

    class FakeGraph:
        def __init__(self, selected_analysts, *, debug, config, callbacks):
            captured["config"] = config
            captured["callbacks"] = callbacks
            self.propagator = SimpleNamespace(
                get_graph_args=lambda callbacks=None: {
                    "config": {"callbacks": callbacks or []}
                }
            )
            self._resuming = False

        def create_run_state(self, ticker, date):
            return {"company_of_interest": ticker, "trade_date": date}

        def begin_checkpoint(self, ticker, date):
            return None

        def checkpoint_input(self, state):
            return state

        def stream_run(self, state, **kwargs):
            yield [], {"market_report": answer["market_report"]}
            yield [], {
                "final_trade_decision": answer["final_trade_decision"],
                "risk_debate_state": answer["risk_debate_state"],
            }

        def record_decision(self, ticker, date, state):
            return None

        def clear_checkpoint_on_success(self, ticker, date):
            return None

        def end_checkpoint(self):
            return None

        def save_reports(self, state, ticker):
            report_dir.mkdir(parents=True)
            file = report_dir / "complete_report.md"
            file.write_text("Mock analysis", encoding="utf-8")
            return file

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", FakeGraph)
    options = server.validate_run({
        "ticker": "AAPL", "date": "2026-11-03", "analysts": ["market"],
        "provider": "freellmapi",
    })
    manager = server.ResearchManager()
    manager.current = {
        **options, "status": "running", "phase": "Preparing",
        "sections": {}, "completed": [], "events": [],
    }
    manager._execute(options)
    completed = manager.snapshot()
    assert completed["status"] == "completed", completed.get("error")
    assert completed["provider"] == "freellmapi"
    assert captured["config"]["llm_provider"] == "freellmapi"
    assert captured["config"]["backend_url"] == gateway.DEFAULT_URL
    assert captured["config"]["google_requests_per_minute"] is None
    assert captured["config"]["llm_max_retries"] == 1
    assert isinstance(captured["callbacks"][0], GatewayUsageTracker)
    metadata = json.loads((report_dir / "usage_summary.json").read_text())
    assert metadata["estimated_paid_usd"] is None
    assert "integration-test-secret" not in json.dumps(metadata)
