"""Offline tests for Gemini usage, report completeness and full NSE workflow."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from webapp import quality, server, usage


def result(*, model="gemini-3.1-flash-lite", input_tokens=10000, output_tokens=2000,
           cached_input_tokens=2000, reported=True):
    message = SimpleNamespace(
        response_metadata={"model_name": model},
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_token_details": {"cache_read": cached_input_tokens},
        } if reported else None,
    )
    return SimpleNamespace(generations=[[SimpleNamespace(message=message)]], llm_output={})


@pytest.mark.unit
def test_gemini_cost_uses_reported_tokens_and_deduplicates_run_ids():
    tracker = usage.GeminiUsageTracker()
    tracker.on_llm_end(result(), run_id="first")
    tracker.on_llm_end(result(), run_id="first")
    snapshot = tracker.snapshot()
    assert snapshot["calls"] == 1
    assert snapshot["reported_calls"] == 1
    assert snapshot["input_tokens"] == 10000
    assert snapshot["output_tokens"] == 2000
    assert snapshot["cached_input_tokens"] == 2000
    assert snapshot["estimated_paid_usd"] == 0.00505
    assert snapshot["pricing_complete"] is True
    assert snapshot["models"]["gemini-3.1-flash-lite"]["calls"] == 1


@pytest.mark.unit
def test_unknown_model_is_unpriced_instead_of_mispriced():
    tracker = usage.GeminiUsageTracker()
    tracker.on_llm_end(result(model="gemini-3.8-flash"), run_id="one")
    tracker.on_llm_end(result(model="gemini-3.1-flash-lite", reported=False), run_id="two")
    snapshot = tracker.snapshot()
    assert snapshot["calls"] == 2
    assert snapshot["unreported_calls"] == 1
    assert snapshot["pricing_complete"] is False
    assert snapshot["models"]["gemini-3.8-flash"]["estimated_paid_usd"] is None


@pytest.mark.unit
def test_fallback_legacy_llm_usage_counts():
    tracker = usage.GeminiUsageTracker(default_model="gemini-3.1-flash-lite")
    raw = SimpleNamespace(generations=[], llm_output={
        "token_usage": {"prompt_tokens": 250, "completion_tokens": 100},
    })
    tracker.on_llm_end(raw, run_id="legacy")
    snapshot = tracker.snapshot()
    assert snapshot["calls"] == 1
    assert snapshot["input_tokens"] == 250
    assert snapshot["output_tokens"] == 100
    assert snapshot["pricing_complete"] is True


@pytest.mark.unit
def test_report_quality_flags_missing_fundamentals_and_target():
    state = {
        "market_report": "Market overview",
        "news_report": "Recent headlines",
        "sentiment_report": "Sentiment",
        "fundamentals_report": "",
        "final_trade_decision": "**Rating**: Hold\n**Price Target**: not provided\n**Time Horizon**: 3-6 months",
        "risk_debate_state": {"neutral_history": "Risk warning"},
    }
    summary = quality.report_quality(state, list(server.ANALYSTS))
    assert summary["sections_present"] == 3
    assert summary["sections_requested"] == 4
    assert summary["explicit_price_target"] is None
    assert summary["explicit_time_horizon"] == "3-6 months"
    assert summary["risk_debate_present"] is True
    assert any("Fundamentals" in w for w in summary["warnings"])


@pytest.mark.unit
def test_report_quality_handles_explicit_target_and_missing_risk():
    summary = quality.report_quality({
        "market_report": "Market analysis",
        "final_trade_decision": "Rating: Buy\nPrice Target: ₹1,500\nTime Horizon: 1 year",
        "risk_debate_state": {},
    }, ["market"])
    assert summary["sections_present"] == 1
    assert summary["explicit_price_target"] == "₹1,500"
    assert summary["explicit_time_horizon"] == "1 year"
    assert summary["risk_debate_present"] is False
    assert len(summary["warnings"]) == 1


@pytest.mark.unit
def test_full_reliance_execution_saves_usage_and_quality_without_api_calls(
    monkeypatch, tmp_path,
):
    # Simulate a complete LangGraph run, verify integration and persistence.
    from tradingagents.graph import trading_graph

    report_dir = tmp_path / "reports" / "RELIANCE.NS_20261105_120000"
    answer = {
        "market_report": "RELIANCE.NS technical report",
        "sentiment_report": "RELIANCE.NS sentiment report",
        "news_report": "RELIANCE.NS news report",
        "fundamentals_report": "RELIANCE.NS fundamentals report",
        "investment_plan": "Bull/Bear decision",
        "trader_investment_plan": "Trader plan",
        "risk_debate_state": {"neutral_history": "Risk debate"},
        "final_trade_decision": "Rating: Hold\nPrice Target: not provided\nTime Horizon: 3-6 months",
    }
    captured = {}

    class FakeGraph:
        def __init__(self, selected_analysts, *, debug, config, callbacks):
            captured["analysts"] = selected_analysts
            captured["config"] = config
            captured["callbacks"] = callbacks
            self.propagator = SimpleNamespace(get_graph_args=lambda callbacks=None: {"config": {"callbacks": callbacks or []}})
            self._resuming = False

        def create_run_state(self, ticker, date):
            return {"company_of_interest": ticker, "trade_date": date}

        def begin_checkpoint(self, ticker, date):
            return "fake-checkpoint-id"

        def checkpoint_input(self, state):
            return state

        def stream_run(self, state, **kwargs):
            # Simulate nested analyst tool callbacks, no actual network requests.
            collector = kwargs["config"]["callbacks"][0]
            collector.on_tool_start({"name": "get_news"}, "", run_id="mock-news")
            collector.on_tool_end(
                "### Test article (source: Example)\nPublished: 2026-11-03T08:00:00Z\n"
                "Link: https://example.com/test-story",
                run_id="mock-news",
            )
            # Mimic the four analyst completions followed by decision.
            for key in ("market_report", "sentiment_report", "news_report", "fundamentals_report"):
                yield [], {key: answer[key]}
            yield [], {key: value for key, value in answer.items()
                       if key not in ("market_report", "sentiment_report", "news_report", "fundamentals_report")}

        def record_decision(self, ticker, date, state):
            captured["recorded"] = state

        def clear_checkpoint_on_success(self, ticker, date):
            captured["cleared"] = True

        def end_checkpoint(self):
            captured["ended"] = True

        def save_reports(self, state, ticker):
            report_dir.mkdir(parents=True)
            report = report_dir / "complete_report.md"
            report.write_text("Simulated RELIANCE report", encoding="utf-8")
            return report

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", FakeGraph)
    options = server.validate_run({
        "ticker": "RELIANCE.NS",
        "date": "2026-11-03",
        "analysts": ["market", "social", "news", "fundamentals"],
        "quick_model": "gemini-3.1-flash-lite",
        "deep_model": "gemini-3.1-flash-lite",
        "rounds": 1,
    })
    manager = server.ResearchManager()
    manager.current = {"status": "running", "sections": {}, "completed": [],
                       "events": [], "phase": "Preparing"}
    manager._execute(options)
    output = manager.snapshot()
    assert output["status"] == "completed", output.get("error")
    assert output["quality"]["sections_present"] == 4
    assert output["usage"]["calls"] == 0
    assert captured["analysts"] == tuple(server.ANALYSTS)
    assert captured["config"]["google_requests_per_minute"] == 6
    assert len(captured["callbacks"]) == 1
    assert captured["cleared"] is True
    assert captured["ended"] is True
    assert (report_dir / "usage_summary.json").is_file()
    assert (report_dir / "evidence_summary.json").is_file()
    assert json.loads((report_dir / "evidence_summary.json").read_text())["source_count"] == 1
    assert (report_dir / "risk_summary.json").is_file()
    assert (report_dir / "integrity_summary.json").is_file()
    integrity = json.loads((report_dir / "integrity_summary.json").read_text())
    assert integrity["selected_analysts"] == list(server.ANALYSTS)
    assert integrity["source_coverage"]["dated_news_links"] == 1
    assert "integrity" in manager.snapshot()
    assert (report_dir / "audit_appendix.md").is_file()
    assert "Evidence and Trade-Risk Audit" in (report_dir / "complete_report.md").read_text()
    assert json.loads((report_dir / "quality_summary.json").read_text())["sections_requested"] == 4

    monkeypatch.setattr(server, "results_root", lambda: report_dir.parent)
    recent = server.recent_reports()
    assert recent[0]["ticker"] == "RELIANCE.NS"
    assert recent[0]["quality"]["sections_requested"] == 4
    assert recent[0]["usage"]["calls"] == 0
    assert recent[0]["integrity"]["source_coverage"]["news_tool_calls"] == 1
