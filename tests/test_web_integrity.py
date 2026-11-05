"""Offline PR #4 tests: provenance, source health and conservative fact checks."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from webapp.audit_report import audit_appendix
from webapp.evidence import EvidenceCollector
from webapp.integrity import integrity_review
from webapp.quality import report_quality
from webapp.risk import proposal_risk

AS_OF = "2026-11-03"
PRICE = {
    "date": AS_OF, "close": 1307.90, "source": "Yahoo Finance OHLCV",
    "analysis_date": AS_OF, "point_in_time": True,
}


def evidence(*, sources=None, calls=None, snapshot=None, outcomes=None):
    return {
        "analysis_date": AS_OF,
        "sources": sources or [],
        "tool_calls": calls or {},
        "tool_outcomes": outcomes or {},
        "market_snapshot": snapshot,
        "future_dated_excluded": 0,
    }


def risk_for(state, rating="Hold"):
    return proposal_risk(state, rating)


def check(state, audit, analysts, rating="Hold"):
    return integrity_review(state, audit, analysts, rating, risk_for(state, rating))


@pytest.mark.unit
def test_tool_traced_article_link_matches_news_analyst_citation():
    article = {
        "url": "https://news.example.org/story?article=1",
        "title": "Article", "published": AS_OF, "tool": "get_news",
    }
    data = evidence(sources=[article], calls={"get_news": 1})
    state = {"news_report": (
        "The published article is available: "
        "[Article](https://news.example.org/story?article=1)."
    )}
    result = check(state, data, ["news"])
    assert result["citation_checks"][0]["tool_traced_urls"] == 1
    assert result["citation_checks"][0]["untraced_urls"] == []
    assert result["source_coverage"]["dated_news_links"] == 1
    assert result["status"] == "limited_checks_passed"
    assert not result["warnings"]


@pytest.mark.unit
def test_model_invented_citation_is_not_marked_verified():
    data = evidence(
        sources=[{"url": "https://news.example.org/known",
                  "published": AS_OF, "tool": "get_news"}],
        calls={"get_news": 1},
    )
    state = {"news_report": "Claim source: https://other.example.org/unseen"}
    result = check(state, data, ["news"])
    row = result["citation_checks"][0]
    assert row["tool_traced_urls"] == 0
    assert row["untraced_urls"] == ["https://other.example.org/unseen"]
    assert any("not found in the captured" in w for w in result["warnings"])
    assert result["status"] == "needs_review"


@pytest.mark.unit
def test_missing_dated_news_is_a_coverage_gap_not_market_sentiment():
    data = evidence(calls={"get_news": 1})
    result = check({"news_report": "Bullish news!"}, data, ["news"])
    assert result["source_coverage"]["dated_news_links"] == 0
    assert any("No dated news URLs" in warning for warning in result["warnings"])
    assert not any("bearish" in warning.lower() for warning in result["warnings"])


@pytest.mark.unit
def test_undated_news_is_not_point_in_time_verified():
    item = {"url": "https://news.example.org/undated", "published": None, "tool": "get_news"}
    data = evidence(sources=[item], calls={"get_news": 1})
    result = check({"news_report": "https://news.example.org/undated"}, data, ["news"])
    assert result["source_coverage"]["undated_news_links"] == 1
    assert result["source_coverage"]["dated_news_links"] == 0
    assert any("publication date" in warning for warning in result["warnings"])


@pytest.mark.unit
def test_fundamentals_needs_dated_documents_not_just_tool_calls():
    data = evidence(calls={"get_income_statement": 1, "get_fundamentals": 1})
    state = {"fundamentals_report": "Company reported EBITDA of 100 crore."}
    result = check(state, data, ["fundamentals"])
    assert result["source_coverage"]["fundamental_tool_calls"] == 2
    assert any("fiscal dates" in warning for warning in result["warnings"])


@pytest.mark.unit
def test_technical_only_requires_no_news_sources_or_financial_filings():
    state = {"market_report": "Current price is 1,307.90 as of 2026-11-03."}
    result = check(state, evidence(snapshot=PRICE), ["market"])
    assert result["source_coverage"]["news_tool_calls"] == 0
    assert result["market_comparison"]["matching_current_price_mentions"] == 1
    assert result["warnings"] == []


@pytest.mark.unit
def test_suspicious_current_price_is_review_flag_not_fraud_conclusion():
    state = {"market_report": "Current price: ₹1,340.00."}
    result = check(state, evidence(snapshot=PRICE), ["market"])
    flagged = result["market_comparison"]["review_flags"]
    assert len(flagged) == 1
    assert flagged[0]["claimed_price"] == 1340.0
    assert flagged[0]["snapshot_close"] == 1307.9
    assert any("review flag, not proof" in w for w in result["warnings"])


@pytest.mark.unit
def test_historical_levels_and_support_are_not_compared_with_current_close():
    state = {"market_report": "200-day SMA is 1,200. Support 1,190. Stop loss 1,160."}
    result = check(state, evidence(snapshot=PRICE), ["market"])
    assert result["market_comparison"]["review_flags"] == []
    assert result["market_comparison"]["matching_current_price_mentions"] == 0


@pytest.mark.unit
def test_no_market_snapshot_does_not_fabricate_comparison():
    state = {"market_report": "Current price is 1,340.00."}
    result = check(state, evidence(), ["market"])
    assert result["market_comparison"]["has_snapshot"] is False
    assert result["market_comparison"]["review_flags"] == []


@pytest.mark.unit
def test_directional_recommendation_with_no_target_is_flagged():
    state = {
        "trader_investment_plan": "Action: Buy\nEntry Price: 100\nStop Loss: 90",
        "final_trade_decision": "Rating: Overweight\nPrice Target: not provided",
    }
    result = check(state, evidence(), ["market"], rating="Overweight")
    assert any("no explicit numeric target" in w for w in result["warnings"])
    assert result["status"] == "needs_review"


@pytest.mark.unit
def test_proper_target_and_stop_allow_computable_risk_reward():
    state = {
        "trader_investment_plan": "Action: Buy\nEntry Price: 100\nStop Loss: 90",
        "final_trade_decision": "Rating: Overweight\nPrice Target: 125",
    }
    risk = risk_for(state, rating="Overweight")
    assert risk["reward_to_risk"] == 2.5
    result = check(state, evidence(), ["market"], rating="Overweight")
    assert result["warnings"] == []


@pytest.mark.unit
def test_hold_without_target_not_a_quality_or_integrity_problem():
    state = {
        "market_report": "Technical analysis.",
        "final_trade_decision": "Rating: Hold\nPrice Target: not provided\nTime Horizon: 1 month",
        "risk_debate_state": {"neutral_history": "Risk discussion"},
    }
    q = report_quality(state, ["market"])
    assert not any("price target" in w.lower() for w in q["warnings"])
    result = check(state, evidence(), ["market"], rating="Hold")
    assert not any("target" in w.lower() for w in result["warnings"])


@pytest.mark.unit
def test_explicit_vendor_unavailable_is_not_automatically_bearish():
    collector = EvidenceCollector(AS_OF, analysts=["news"])
    collector.on_tool_start({"name": "get_news"}, "", run_id="news")
    collector.on_tool_end(
        SimpleNamespace(content="<Yahoo Finance news unavailable for 2026-11-03: no response>"),
        run_id="news",
    )
    audit = collector.snapshot()
    assert audit["tool_outcomes"]["get_news"]["unavailable"] == 1
    result = check({"news_report": "Vendor data unavailable."}, audit, ["news"])
    assert any("data-availability limitation" in w for w in result["warnings"])
    assert result["status"] == "needs_review"


@pytest.mark.unit
def test_tool_error_captured_as_provider_diagnostic():
    collector = EvidenceCollector(AS_OF, analysts=["fundamentals"])
    collector.on_tool_start({"name": "get_income_statement"}, "", run_id="failed")
    collector.on_tool_error(RuntimeError("upstream vendor failed"), run_id="failed")
    audit = collector.snapshot()
    assert audit["tool_outcomes"]["get_income_statement"]["error"] == 1
    result = check({"fundamentals_report": "Data inaccessible"}, audit, ["fundamentals"])
    assert any("get_income_statement" in w for w in result["warnings"])


@pytest.mark.unit
def test_integrity_appendix_adds_diagnostics_without_breaking_existing_audit():
    state = {
        "market_report": "Current price is 1,340.00.",
        "final_trade_decision": "Rating: Hold",
    }
    audit = evidence(snapshot=PRICE)
    risk = risk_for(state)
    integrity = check(state, audit, ["market"])
    markdown = audit_appendix(audit, risk, integrity)
    assert "Research integrity checks" in markdown
    assert "Review price mention" in markdown
    assert "no explicit numeric target" not in markdown
    assert "Evidence and Trade-Risk Audit" in markdown



@pytest.mark.unit
def test_news_analyst_current_price_can_be_compared_to_market_tool_snapshot():
    state = {"news_report": "Latest share price at ₹1,360.00 according to this report."}
    result = check(state, evidence(snapshot=PRICE), ["news"])
    review = result["market_comparison"]["review_flags"]
    assert len(review) == 1
    assert review[0]["analyst"] == "news"


@pytest.mark.unit
def test_research_and_final_recommendation_difference_is_transparent_not_error():
    state = {
        "investment_plan": "**Recommendation**: Buy",
        "trader_investment_plan": "**Action**: Hold",
        "final_trade_decision": "**Rating**: Hold\n**Price Target**: not provided",
    }
    result = check(state, evidence(), ["market"], rating="Hold")
    assert result["decision_alignment"]["research_team"] == "Buy"
    assert result["decision_alignment"]["trader_action"] == "Hold"
    assert result["decision_alignment"]["research_to_portfolio_changed"] is True
    assert result["status"] == "limited_checks_passed"
    assert not result["warnings"]
