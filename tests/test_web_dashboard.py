"""Unit tests for the private research dashboard; no Gemini calls are made."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pytest

from webapp import server


@pytest.mark.unit
def test_validate_reliance_full_analysis():
    parsed = server.validate_run({
        "ticker": "reliance.ns",
        "date": "2026-11-03",
        "analysts": ["fundamentals", "news", "market", "social"],
        "quick_model": "gemini-3.1-flash-lite",
        "deep_model": "gemini-3.1-flash-lite",
        "rounds": 1,
    })
    assert parsed["ticker"] == "RELIANCE.NS"
    assert parsed["analysts"] == list(server.ANALYSTS)


@pytest.mark.unit
@pytest.mark.parametrize("ticker", ["", "../../secrets", "AAPL; rm -rf /", "AAPL\nX"])
def test_rejects_unsafe_tickers(ticker):
    with pytest.raises(server.RequestError):
        server.validate_run({"ticker": ticker, "date": "2026-11-03"})


@pytest.mark.unit
@pytest.mark.parametrize("changes", [
    {"date": "2099-01-01"},
    {"analysts": []},
    {"analysts": ["market", "market"]},
    {"analysts": ["unknown"]},
    {"rounds": True},
    {"rounds": 99},
    {"deep_model": "arbitrary-provider-model"},
])
def test_rejects_unsafe_or_expensive_requests(changes):
    payload = {"ticker": "AAPL", "date": "2026-11-03", "analysts": ["market"]}
    payload.update(changes)
    with pytest.raises(server.RequestError):
        server.validate_run(payload)


@pytest.mark.unit
def test_prevents_concurrent_runs(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-test-key")
    started = threading.Event()
    finish = threading.Event()

    def fake_execute(self, options):
        started.set()
        finish.wait(timeout=3)

    monkeypatch.setattr(server.ResearchManager, "_execute", fake_execute)
    manager = server.ResearchManager()
    try:
        response = manager.start({"ticker": "AAPL", "date": "2026-11-03"})
        assert response["status"] == "running"
        assert started.wait(timeout=2)
        with pytest.raises(server.RequestError, match="already running"):
            manager.start({"ticker": "AAPL", "date": "2026-11-03"})
    finally:
        finish.set()


@pytest.mark.unit
def test_report_history_cannot_escape_reports_folder(monkeypatch, tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    legit = reports / "AAPL_20261105_120000"
    legit.mkdir()
    (legit / "complete_report.md").write_text("Rating: Hold", encoding="utf-8")
    monkeypatch.setattr(server, "results_root", lambda: reports)
    assert server.report_directory(legit.name) == legit.resolve()
    assert server.report_directory("../outside") is None
    assert server.report_directory("not-found") is None
    assert server.recent_reports()[0]["ticker"] == "AAPL"


@pytest.mark.unit
def test_status_endpoint_does_not_reveal_api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "testing-secret-never-return")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    httpd.manager = server.ResearchManager()
    runner = threading.Thread(target=httpd.serve_forever, daemon=True)
    runner.start()
    try:
        with urlopen("http://127.0.0.1:%s/api/status" % httpd.server_port, timeout=3) as result:
            body = result.read().decode("utf-8")
            data = json.loads(body)
        assert data["configured"] is True
        assert "testing-secret-never-return" not in body
    finally:
        httpd.shutdown()
        httpd.server_close()
        runner.join(timeout=3)


@pytest.mark.unit
def test_google_rate_limiter_shared_for_model_tiers():
    from tradingagents.llm_clients.factory import build_llm_kwargs
    from tradingagents.llm_clients.google_client import _shared_rate_limiter

    kwargs = build_llm_kwargs({
        "llm_provider": "google",
        "google_thinking_level": "low",
        "google_requests_per_minute": 6,
    })
    assert kwargs["requests_per_minute"] == 6
    assert _shared_rate_limiter(6) is _shared_rate_limiter(6)
    assert _shared_rate_limiter(6) is not _shared_rate_limiter(12)
    with pytest.raises(ValueError):
        _shared_rate_limiter(0)
