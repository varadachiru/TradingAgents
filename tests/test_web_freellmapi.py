"""PR #5: offline FreeLLMAPI adapter and dashboard isolation tests.

No server sockets are opened and no external provider is contacted.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from tradingagents.llm_clients import create_tier_client
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.openai_client import (
    LocalCompatibleChatOpenAI,
    is_openai_compatible,
)
from webapp import gateway, server
from webapp.usage import GatewayUsageTracker

QUICK = "auto:trading-quick"
DEEP = "auto:trading-deep"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "http://127.0.0.1:3001/v1")
    monkeypatch.setenv("FREELLMAPI_API_KEY", "fake-gateway-secret-not-for-browser")
    monkeypatch.setenv("FREELLMAPI_QUICK_MODEL", QUICK)
    monkeypatch.setenv("FREELLMAPI_DEEP_MODEL", DEEP)


@pytest.mark.unit
def test_named_provider_reuses_openai_compatible_client(configured):
    assert is_openai_compatible("freellmapi")
    assert get_api_key_env("freellmapi") == "FREELLMAPI_API_KEY"
    cfg = {
        "llm_provider": "freellmapi",
        "backend_url": "http://127.0.0.1:3001/v1",
        "quick_think_llm": QUICK,
        "deep_think_llm": DEEP,
        "temperature": None,
        "max_tokens": None,
        "llm_max_retries": 1,
    }
    quick = create_tier_client(cfg, "quick").get_llm()
    deep = create_tier_client(cfg, "deep").get_llm()
    assert isinstance(quick, LocalCompatibleChatOpenAI)
    assert isinstance(deep, LocalCompatibleChatOpenAI)
    assert quick.model_name == QUICK
    assert deep.model_name == DEEP
    assert "127.0.0.1:3001" in str(quick.openai_api_base)
    # Model creation never verifies upstream tool support or makes an API call.


@pytest.mark.unit
def test_configured_gateway_does_not_publish_api_key(configured):
    settings = gateway.gateway_settings()
    assert settings["configured"] is True
    assert settings["key_configured"] is True
    assert settings["quick_model"] == QUICK
    assert settings["deep_model"] == DEEP
    assert "fake-gateway-secret" not in json.dumps(settings)
    assert "http://127.0.0.1" not in json.dumps(settings)


@pytest.mark.unit
@pytest.mark.parametrize("bad_url", [
    "http://example.com/v1",
    "http://127.0.0.1:3001/v1?secret=yes",
    "http://user:password@127.0.0.1:3001/v1",
    "http://127.0.0.1:3001/api/admin",
    "file:///tmp/gateway",
    "https://example.com/v1#token",
])
def test_rejects_unsafe_gateway_endpoint(monkeypatch, bad_url):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", bad_url)
    with pytest.raises(ValueError):
        gateway.gateway_url()


@pytest.mark.unit
def test_remote_tls_allowed_only_from_operator_env(monkeypatch):
    monkeypatch.setenv("FREELLMAPI_BASE_URL", "https://my-gateway.example.com/v1")
    assert gateway.gateway_url() == "https://my-gateway.example.com/v1"


@pytest.mark.unit
@pytest.mark.parametrize("quick", ["auto", "auto:fast", "auto:smart", "bad model id"])
def test_unrestricted_auto_or_malformed_model_is_not_approved(configured, monkeypatch, quick):
    monkeypatch.setenv("FREELLMAPI_QUICK_MODEL", quick)
    with pytest.raises(ValueError):
        gateway.approved_models()


@pytest.mark.unit
def test_gateway_must_be_explicitly_configured_for_both_tiers(monkeypatch):
    monkeypatch.delenv("FREELLMAPI_QUICK_MODEL", raising=False)
    monkeypatch.delenv("FREELLMAPI_DEEP_MODEL", raising=False)
    assert gateway.approved_models() == {}
    assert not gateway.gateway_settings()["configured"]


@pytest.mark.unit
def test_validate_run_preserves_direct_gemini(configured):
    parsed = server.validate_run({
        "ticker": "AAPL", "date": "2026-11-03",
        "provider": "google",
        "quick_model": "gemini-3.1-flash-lite",
        "deep_model": "gemini-3.1-flash-lite",
    })
    assert parsed["provider"] == "google"
    assert parsed["quick_model"] == "gemini-3.1-flash-lite"


@pytest.mark.unit
def test_gateway_accepts_only_server_approved_tier_models(configured):
    parsed = server.validate_run({
        "ticker": "RELIANCE.NS", "date": "2026-11-03",
        "provider": "freellmapi",
        "quick_model": QUICK,
        "deep_model": DEEP,
        "analysts": ["market"],
    })
    assert parsed["provider"] == "freellmapi"
    assert parsed["quick_model"] == QUICK
    assert parsed["deep_model"] == DEEP
    for changes in (
        {"quick_model": DEEP},
        {"deep_model": "other-model"},
        {"provider": "unknown"},
    ):
        with pytest.raises(server.RequestError):
            server.validate_run({
                "ticker": "AAPL", "date": "2026-11-03",
                "provider": "freellmapi", "quick_model": QUICK,
                "deep_model": DEEP, **changes,
            })


@pytest.mark.unit
def test_gateway_start_requires_key_before_launching_thread(configured, monkeypatch):
    monkeypatch.delenv("FREELLMAPI_API_KEY")
    manager = server.ResearchManager()
    with pytest.raises(server.RequestError, match="Configure"):
        manager.start({
            "ticker": "AAPL", "date": "2026-11-03",
            "provider": "freellmapi",
            "quick_model": QUICK, "deep_model": DEEP,
        })
    assert manager.snapshot() == {}


class MockResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _max_bytes):
        return json.dumps({
            "object": "list",
            "data": [{"id": QUICK}, {"id": DEEP}, {"id": "gemini-3.1-flash-lite"}],
        }).encode("utf-8")


@pytest.mark.unit
def test_authenticated_gateway_probe_uses_server_secret_not_browser(configured, monkeypatch):
    outgoing = {}

    def fake_urlopen(request, timeout):
        outgoing["url"] = request.full_url
        outgoing["auth"] = request.get_header("Authorization")
        outgoing["timeout"] = timeout
        return MockResponse()

    monkeypatch.setattr(gateway, "urlopen", fake_urlopen)
    response = gateway.test_gateway_connection()
    assert response["connected"] is True
    assert response["quick_model_listed"]
    assert response["deep_model_listed"]
    assert outgoing["url"] == "http://127.0.0.1:3001/v1/models?execution_status=ready"
    assert outgoing["auth"] == "Bearer fake-gateway-secret-not-for-browser"
    assert "fake-gateway-secret" not in json.dumps(response)
    assert outgoing["timeout"] <= 10


@pytest.mark.unit
def test_gateway_auth_errors_are_safe(configured, monkeypatch):
    def rejected(request, timeout):
        raise HTTPError(request.full_url, 401, "secret=leak-me", None, None)

    monkeypatch.setattr(gateway, "urlopen", rejected)
    with pytest.raises(ValueError, match="HTTP 401") as exc:
        gateway.test_gateway_connection()
    assert "secret=" not in str(exc.value)


@pytest.mark.unit
def test_network_failure_is_safe(configured, monkeypatch):
    def failed(_request, timeout):
        raise URLError("contains super-private host and token")

    monkeypatch.setattr(gateway, "urlopen", failed)
    with pytest.raises(ValueError, match="Could not reach"):
        gateway.test_gateway_connection()


@pytest.mark.unit
def test_browser_status_is_keyless_and_backward_compatible(configured):
    handler = object.__new__(server.DashboardHandler)
    handler.path = "/api/status"
    handler.server = SimpleNamespace(manager=server.ResearchManager())
    responses = []
    handler._json = lambda status, payload: responses.append((status, payload))
    handler.do_GET()
    status, data = responses[0]
    assert status == 200
    assert "configured" in data  # Original Gemini-compatible field.
    assert data["providers"]["freellmapi"]["configured"] is True
    assert data["providers"]["freellmapi"]["quick_model"] == QUICK
    assert "fake-gateway-secret" not in json.dumps(data)


@pytest.mark.unit
def test_gateway_pricing_unknown_is_not_replaced_with_zero():
    tracker = GatewayUsageTracker(default_model=QUICK)
    tracker.on_llm_end(SimpleNamespace(
        generations=[[SimpleNamespace(message=SimpleNamespace(
            response_metadata={"model_name": "upstream-model-1"},
            usage_metadata={"input_tokens": 1000, "output_tokens": 500},
        ))]],
        llm_output={},
    ), run_id="sample")
    usage = tracker.snapshot()
    assert usage["calls"] == 1
    assert usage["input_tokens"] == 1000
    assert usage["output_tokens"] == 500
    assert usage["models"]["upstream-model-1"]["calls"] == 1
    assert usage["models"]["upstream-model-1"]["estimated_paid_usd"] is None
    assert usage["estimated_paid_usd"] is None
    assert usage["pricing_complete"] is False


@pytest.mark.unit
def test_gateway_simulated_langgraph_run_saves_provider_metadata(configured, tmp_path, monkeypatch):
    from tradingagents.graph import trading_graph

    captured = {}
    report_dir = tmp_path / "reports" / "AAPL_20261105_150000"

    class FakeGraph:
        def __init__(self, selected_analysts, *, debug, config, callbacks):
            captured["config"] = config
            captured["callbacks"] = callbacks
            self.propagator = SimpleNamespace(
                get_graph_args=lambda callbacks=None: {"config": {"callbacks": callbacks or []}}
            )
            self._resuming = False

        def create_run_state(self, ticker, day):
            return {"company_of_interest": ticker, "trade_date": day}

        def begin_checkpoint(self, ticker, day):
            return None

        def checkpoint_input(self, state):
            return state

        def stream_run(self, state, **kwargs):
            yield [], {"market_report": "AAPL technical review"}
            yield [], {
                "investment_plan": "Recommendation: Hold",
                "trader_investment_plan": "Action: Hold",
                "risk_debate_state": {"neutral_history": "Risk discussion"},
                "final_trade_decision": "Rating: Hold\nTime Horizon: 3 months",
            }

        def record_decision(self, ticker, day, state):
            pass

        def clear_checkpoint_on_success(self, ticker, day):
            pass

        def end_checkpoint(self):
            pass

        def save_reports(self, state, ticker):
            report_dir.mkdir(parents=True)
            report = report_dir / "complete_report.md"
            report.write_text("Simulated AAPL report", encoding="utf-8")
            return report

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", FakeGraph)
    options = server.validate_run({
        "ticker": "AAPL", "date": "2026-11-03",
        "provider": "freellmapi",
        "quick_model": QUICK,
        "deep_model": DEEP,
        "analysts": ["market"],
        "rounds": 1,
    })
    manager = server.ResearchManager()
    manager.current = {"status": "running", "sections": {}, "completed": [],
                       "events": [], "phase": "Preparing"}
    manager._execute(options)
    job = manager.snapshot()
    assert job["status"] == "completed", job.get("error")
    assert captured["config"]["llm_provider"] == "freellmapi"
    assert captured["config"]["quick_think_llm"] == QUICK
    assert captured["config"]["deep_think_llm"] == DEEP
    assert captured["config"]["google_requests_per_minute"] is None
    assert captured["config"]["backend_url"] == "http://127.0.0.1:3001/v1"
    assert job["usage"]["estimated_paid_usd"] is None
    assert job["execution"]["provider"] == "freellmapi"
    assert json.loads((report_dir / "execution_metadata.json").read_text())["provider"] == "freellmapi"
