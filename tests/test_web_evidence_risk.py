"""No-network tests for source provenance, date guards and trade calculations."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from webapp.audit_report import audit_appendix
from webapp.evidence import (
    EvidenceCollector,
    extract_sources,
    parse_verified_snapshot,
    safe_source_url,
)
from webapp.risk import parse_price, proposal_risk


@pytest.mark.unit
def test_tool_link_with_real_publication_date_is_recorded():
    collector = EvidenceCollector("2026-11-03")
    collector.on_tool_start({"name": "get_news"}, "", run_id="a")
    collector.on_tool_end(SimpleNamespace(content=(
        "### Big company announcement (source: Yahoo Finance)\n"
        "Published: 2026-11-03T10:00:00+00:00\n"
        "Link: https://news.example.com/story/1\n"
    )), run_id="a")
    snapshot = collector.snapshot()
    assert snapshot["source_count"] == 1
    assert snapshot["dated_source_count"] == 1
    item = snapshot["sources"][0]
    assert item["title"] == "Big company announcement"
    assert item["publisher"] == "Yahoo Finance"
    assert item["published"] == "2026-11-03"
    assert item["tool"] == "get_news"
    assert snapshot["tool_calls"]["get_news"] == 1


@pytest.mark.unit
def test_future_publication_excluded_from_historical_run():
    text = (
        "### Future article (source: Yahoo Finance)\n"
        "Published: 2026-11-04T03:01:00Z\n"
        "Link: https://news.example.com/future\n"
    )
    collector = EvidenceCollector("2026-11-03")
    collector.on_tool_start({"name": "get_news"}, "", run_id="future")
    collector.on_tool_end(SimpleNamespace(content=text), run_id="future")
    assert collector.snapshot()["future_dated_excluded"] == 1
    assert collector.snapshot()["source_count"] == 0
    assert "after the analysis date" in " ".join(collector.snapshot()["warnings"])


@pytest.mark.unit
def test_undated_article_is_flagged_not_invented():
    sources = extract_sources("### Unknown date (source: Vendor)\nLink: https://example.com/article", "get_news", "2026-11-03")
    assert sources[0]["published"] is None
    assert sources[0]["date_status"] == "undated"


@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "file:///secrets",
    "https://localhost/secret",
    "http://127.0.0.1/private",
    "http://192.168.1.4/",
    "https://user:pass@example.com/",
    "https://example.com/" + "x" * 2000,
])
def test_source_url_validation_blocks_unsafe_targets(url):
    assert safe_source_url(url) is None


@pytest.mark.unit
def test_market_snapshot_point_in_time_parses_only_tool_result():
    message = (
        "## Verified market data snapshot for RELIANCE.NS\n"
        "- Requested analysis date: 2026-11-03\n"
        "- Latest trading row used: 2026-11-03\n"
        "| Field | Value |\n|---|---:|\n| Close | 1,307.90 |\n"
    )
    snapshot = parse_verified_snapshot(message, "2026-11-03")
    assert snapshot["date"] == "2026-11-03"
    assert snapshot["close"] == 1307.90
    assert parse_verified_snapshot(message, "2026-11-02") is None
    collector = EvidenceCollector("2026-11-03")
    collector.on_tool_start({"name": "get_verified_market_snapshot"}, "", run_id="m")
    collector.on_tool_end(message, run_id="m")
    assert collector.snapshot()["market_snapshot"]["close"] == 1307.90


@pytest.mark.unit
def test_model_written_citations_do_not_enter_evidence_ledger():
    collector = EvidenceCollector("2026-11-03")
    # Only approved data tools, never agent prose, count as evidence.
    collector.on_tool_start({"name": "Portfolio Manager"}, "", run_id="fake")
    collector.on_tool_end("Link: https://example.com/hallucinated", run_id="fake")
    assert collector.snapshot()["sources"] == []


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["1300-1400", "15%", "around 100", "1,000 extra", "-30", "0", "NaN"])
def test_risk_rejects_unusable_levels(raw):
    assert parse_price(raw) is None


@pytest.mark.unit
def test_risk_without_target_is_partial_not_fabricated():
    state = {
        "trader_investment_plan": "**Action**: Buy\n**Entry Price**: ₹1,307.90\n**Stop Loss**: 1190.0",
        "final_trade_decision": "**Rating**: Overweight\n**Price Target**: not provided",
    }
    risk = proposal_risk(state, "Overweight")
    assert risk["entry"] == 1307.9
    assert risk["stop_loss"] == 1190
    assert risk["price_target"] is None
    assert risk["risk_pct"] == pytest.approx(9.01, abs=0.02)
    assert risk["reward_pct"] is None
    assert risk["reward_to_risk"] is None


@pytest.mark.unit
def test_valid_long_target_produces_deterministic_risk_reward():
    state = {
        "trader_investment_plan": "**Action**: Buy\n**Entry Price**: 100\n**Stop Loss**: 95",
        "final_trade_decision": "**Rating**: Buy\n**Price Target**: 115",
    }
    risk = proposal_risk(state, "Buy")
    assert risk["risk_pct"] == 5.0
    assert risk["reward_pct"] == 15.0
    assert risk["reward_to_risk"] == 3.0


@pytest.mark.unit
def test_valid_short_target_produces_deterministic_risk_reward():
    state = {
        "trader_investment_plan": "**Action**: Sell\n**Entry Price**: 100\n**Stop Loss**: 110",
        "final_trade_decision": "**Rating**: Sell\n**Price Target**: 80",
    }
    risk = proposal_risk(state, "Sell")
    assert risk["risk_pct"] == 10
    assert risk["reward_pct"] == 20
    assert risk["reward_to_risk"] == 2


@pytest.mark.unit
def test_bad_stop_and_wrong_way_target_not_silently_accepted():
    state = {
        "trader_investment_plan": "**Action**: Buy\n**Entry Price**: 100\n**Stop Loss**: 101",
        "final_trade_decision": "**Rating**: Overweight\n**Price Target**: 90",
    }
    risk = proposal_risk(state, "Overweight")
    assert risk["reward_to_risk"] is None
    assert risk["risk_pct"] is None
    assert any("wrong side" in w for w in risk["warnings"])


@pytest.mark.unit
def test_appendix_includes_missing_target_and_unverified_source():
    evidence = {
        "analysis_date": "2026-11-03",
        "source_count": 1,
        "dated_source_count": 0,
        "future_dated_excluded": 0,
        "market_snapshot": None,
        "sources": [{
            "title": "Breaking ](https://attacker.invalid",
            "publisher": "Yahoo Finance",
            "url": "https://example.com/news",
            "published": None,
            "tool": "get_news",
        }],
        "warnings": ["Undated article"],
    }
    risk = proposal_risk({
        "trader_investment_plan": "**Action**: Buy\n**Entry Price**: 100\n**Stop Loss**: 90",
        "final_trade_decision": "**Price Target**: not provided",
    }, "Buy")
    markdown = audit_appendix(evidence, risk)
    assert "## VI. Evidence and Trade-Risk Audit" in markdown
    assert "https://example.com/news" in markdown
    assert "not calculated" in markdown
    assert "\\]" in markdown
    assert "Undated article" in markdown



@pytest.mark.unit
def test_conflicting_trader_action_withholds_risk_reward():
    state = {
        "trader_investment_plan": "**Action**: Buy\n**Entry Price**: 100\n**Stop Loss**: 110",
        "final_trade_decision": "**Price Target**: 80",
    }
    risk = proposal_risk(state, "Sell")
    assert risk["reward_to_risk"] is None
    assert risk["risk_pct"] is None
    assert any("conflicts" in warning for warning in risk["warnings"])


@pytest.mark.unit
def test_stale_snapshot_is_explicitly_flagged():
    collector = EvidenceCollector("2026-11-03")
    collector.on_tool_start({"name": "get_verified_market_snapshot"}, "", run_id="m")
    collector.on_tool_end(
        "- Latest trading row used: 2026-10-20\n| Close | 1,234.50 |",
        run_id="m",
    )
    snapshot = collector.snapshot()
    assert snapshot["market_snapshot"]["age_days"] == 14
    assert any("14 calendar days" in warning for warning in snapshot["warnings"])



@pytest.mark.unit
def test_technical_only_uses_market_snapshot_without_source_url_warning():
    collector = EvidenceCollector("2026-11-05", analysts=["market"])
    collector.on_tool_start({"name": "get_verified_market_snapshot"}, "", run_id="market")
    collector.on_tool_end(
        "- Latest trading row used: 2026-11-05\n| Close | 1,313.40 |",
        run_id="market",
    )
    result = collector.snapshot()
    assert result["source_count"] == 0
    assert result["article_links_expected"] is False
    assert result["market_snapshot"]["close"] == 1313.40
    assert not any("No article or document URLs" in w for w in result["warnings"])


@pytest.mark.unit
def test_news_run_without_article_links_warns_about_missing_sources():
    collector = EvidenceCollector("2026-11-05", analysts=["market", "news"])
    collector.on_tool_start({"name": "get_news"}, "", run_id="news")
    collector.on_tool_end("No news found for RELIANCE.NS", run_id="news")
    result = collector.snapshot()
    assert result["source_count"] == 0
    assert result["article_links_expected"] is True
    assert any("No article or document URLs" in w for w in result["warnings"])


@pytest.mark.unit
def test_hold_rating_is_an_explicit_no_trade_and_not_missing_rating():
    result = proposal_risk(
        {
            "trader_investment_plan": "**Action**: Hold\n**Entry Price**: 1324\n**Stop Loss**: 1260",
            "final_trade_decision": "**Rating**: Hold\n**Price Target**: not provided",
        },
        "Hold",
    )
    assert result["direction"] is None
    assert result["risk_pct"] is None
    assert result["reward_to_risk"] is None
    assert any("Hold" in w for w in result["warnings"])
    assert not any("No directional final rating" in w for w in result["warnings"])
