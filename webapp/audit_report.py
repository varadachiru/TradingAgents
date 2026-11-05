"""Human-readable deterministic evidence and proposed-risk appendix.

An appendix describes evidence coverage and trade arithmetic; it does not
validate the agents' reasoning or guarantee that underlying facts are accurate.
"""
from __future__ import annotations


def _escape(text: object) -> str:
    """Prevent untrusted title text from inserting arbitrary Markdown links."""
    return str(text or "").replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")[:240]


def audit_appendix(evidence: dict, risk: dict, integrity: dict | None = None) -> str:
    lines = [
        "## VI. Evidence and Trade-Risk Audit (deterministic)",
        "",
        "This appendix lists URLs returned by data tools. A listed link is **not**"
        " proof it supports every claim in the AI report. Sources are not independently fact-checked.",
        "",
        f"- Analysis date: {evidence.get('analysis_date', 'unknown')}",
        f"- Captured source links: {evidence.get('source_count', 0)}",
        f"- Dated links: {evidence.get('dated_source_count', 0)}",
        f"- Links excluded for publication after analysis date: {evidence.get('future_dated_excluded', 0)}",
        "",
        "### Verified market-tool snapshot",
        "",
    ]
    snapshot = evidence.get("market_snapshot")
    if snapshot:
        lines.append(
            f"- Last available trading row: **{snapshot['date']}**, "
            f"close **{snapshot['close']:,.2f}** "
            f"({snapshot['source']}). This is a historical vendor tool value, not exchange confirmation."
        )
    else:
        lines.append("- Not captured from a historical market-data tool result.")
    lines.extend(["", "### Tool-returned source links", ""])
    if not evidence.get("sources"):
        lines.append("- None collected. Do not treat unsupported AI claims as verified.")
    else:
        for item in evidence["sources"]:
            stamp = item.get("published") or "Publication date unavailable"
            label = _escape(item.get("title") or "Source")
            publisher = _escape(item.get("publisher") or "Unspecified")
            scope = (
                "ticker-scoped company-feed candidate (relevance unverified)"
                if item.get("tool") == "get_news"
                else "global/macro context (not company-specific)"
                if item.get("tool") == "get_global_news"
                else "other data source"
            )
            lines.append(
                f"- [{label}]({item['url']}) — {publisher}; published: {stamp}; "
                f"{scope}; retrieved via {_escape(item.get('tool'))}"
            )
    lines.extend(["", "### Trade proposal and risk arithmetic", ""])
    direction = risk.get("direction") or "N/A"
    lines.append(f"- Final trade direction: **{direction}**")
    for name, key in (
        ("Trader entry", "entry"), ("Trader stop loss", "stop_loss"),
        ("Portfolio Manager target", "price_target"),
    ):
        value = risk.get(key)
        lines.append(f"- {name}: **{value:,.2f}**" if value is not None else f"- {name}: **not provided**")
    for name, key, suffix in (
        ("Downside risk to stop", "risk_pct", "%"),
        ("Potential move to target", "reward_pct", "%"),
        ("Reward-to-risk ratio", "reward_to_risk", ":1"),
    ):
        value = risk.get(key)
        lines.append(f"- {name}: **{value:.2f}{suffix}**" if value is not None else f"- {name}: **not calculated**")
    for warning in [*evidence.get("warnings", []), *risk.get("warnings", [])]:
        lines.append(f"- **Gap:** {_escape(warning)}")
    lines.extend([
        "",
        "The trade levels are AI proposals, not verified market quotes or orders. "
        "Risk/reward ignores slippage, overnight gaps, fees and target probability. "
        "Verify documents, dates, prices and reasoning against original sources before investing.",
    ])
    if integrity is not None:
        lines.extend([
            "",
            "### Research integrity checks (heuristic)",
            "",
            f"- Review status: **{_escape(integrity.get('status', 'unknown'))}**",
            f"- Dated news links captured: {integrity.get('source_coverage', {}).get('dated_news_links', 0)}",
            f"- Dated ticker-scoped company-feed candidates: {integrity.get('source_coverage', {}).get('dated_company_feed_links', 0)}",
            f"- Global/macro-context links (not company evidence): {integrity.get('source_coverage', {}).get('global_context_links', 0)}",
            f"- News tool calls observed: {integrity.get('source_coverage', {}).get('news_tool_calls', 0)}",
            f"- Financial tool calls observed: {integrity.get('source_coverage', {}).get('fundamental_tool_calls', 0)}",
        ])
        for item in integrity.get("citation_checks", []):
            lines.append(
                "- " + _escape(item.get("analyst", "Analyst")) + ": "
                + str(item.get("tool_traced_urls", 0)) + " of "
                + str(item.get("cited_urls", 0)) + " cited URL(s) matched tool-returned links"
            )
        for discrepancy in integrity.get("market_comparison", {}).get("review_flags", []):
            lines.append(
                "- **Review price mention (" + _escape(discrepancy.get("analyst", "analyst"))
                + "):** claimed " + str(discrepancy.get("claimed_price"))
                + ", snapshot " + str(discrepancy.get("snapshot_close"))
                + " as of " + _escape(discrepancy.get("snapshot_date")) + "."
            )
        decision = integrity.get("decision_alignment") or {}
        if decision.get("research_team") or decision.get("trader_action"):
            lines.append(
                "- **Decision trail:** Research team "
                + _escape(decision.get("research_team") or "not stated")
                + ", Trader " + _escape(decision.get("trader_action") or "not stated")
                + ", Final " + _escape(decision.get("portfolio_rating") or "not stated") + "."
            )
        for warning in integrity.get("warnings", []):
            lines.append(f"- **Research check:** {_escape(warning)}")
        lines.extend([
            "",
            "A URL match only demonstrates traceability to a tool result, "
            "not article authenticity, factual verification or claim-level support. "
            "These rules do not identify every inaccurate financial claim.",
        ])
    return "\n".join(lines) + "\n"
