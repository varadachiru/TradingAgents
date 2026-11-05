"""Human-readable deterministic evidence and proposed-risk appendix.

An appendix describes evidence coverage and trade arithmetic; it does not
validate the agents' reasoning or guarantee that underlying facts are accurate.
"""
from __future__ import annotations


def _escape(text: object) -> str:
    """Prevent untrusted title text from inserting arbitrary Markdown links."""
    return str(text or "").replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")[:240]


def audit_appendix(evidence: dict, risk: dict) -> str:
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
            lines.append(
                f"- [{label}]({item['url']}) — {publisher}; published: {stamp}; "
                f"retrieved via {_escape(item.get('tool'))}"
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
    return "\n".join(lines) + "\n"
