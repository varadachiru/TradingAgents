"""Conservative, deterministic research-integrity diagnostics.

A source returned by a tool can support an audit trail but cannot establish
that the model's claim is true. These checks do not contact the internet,
re-run models, or claim independent financial verification.
"""
from __future__ import annotations

import math
import re
from urllib.parse import urlsplit

from webapp.evidence import safe_source_url
from webapp.quality import _reported_field

REPORT_KEYS = {
    "market": "market_report",
    "news": "news_report",
    "social": "sentiment_report",
    "fundamentals": "fundamentals_report",
}
NEWS_TOOLS = frozenset({"get_news", "get_global_news"})
FUNDAMENTAL_TOOLS = frozenset({
    "get_fundamentals", "get_balance_sheet", "get_cashflow",
    "get_income_statement", "get_insider_transactions",
})
URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+", flags=re.I)
# Only statements explicitly describing today's/current/latest price are
# comparable with a single as-of OHLCV close. Other historical prices, support
# lines, analyst targets and time-varying metrics are not compared.
CURRENT_PRICE_RE = re.compile(
    r"\b(?:current|latest|as[- ]of|verified)\s+"
    r"(?:(?:share|stock|market)\s+)?"
    r"(?:closing\s+price|price|close)\s*"
    r"(?::|is|was|at|of|around|=)?\s*"
    r"(?:₹|Rs\.?\s*|INR\s*|\$\s*|USD\s*)?"
    r"(\d[\d,]*(?:\.\d+)?)\b",
    flags=re.I,
)


def _normalized_url(value: str) -> str | None:
    url = safe_source_url(value.rstrip(".,;:!?)"))
    if url is None:
        return None
    parts = urlsplit(url)
    # Ignore fragments and tracking query parameters for exact content identity
    # only when the query is empty. Other query params may identify the article.
    return parts._replace(fragment="").geturl().rstrip("/")


def _report_urls(text: str) -> set[str]:
    seen: set[str] = set()
    for match in URL_RE.finditer(text or ""):
        normalized = _normalized_url(match.group(0))
        if normalized:
            seen.add(normalized)
    return seen


def _price_claims(text: str) -> list[float]:
    prices = []
    for m in CURRENT_PRICE_RE.finditer(text or ""):
        price = float(m.group(1).replace(",", ""))
        if math.isfinite(price) and price > 0 and price not in prices:
            prices.append(price)
    return prices[:12]


def integrity_review(
    state: dict, evidence: dict, analysts: list[str], rating: str, risk: dict,
) -> dict:
    """Report corroboration gaps without fabricating citations or missing data."""
    sources = evidence.get("sources") or []
    known_links = {
        url for item in sources
        if (url := _normalized_url(str(item.get("url") or ""))) is not None
    }
    checks = []
    warnings = []

    for analyst in analysts:
        report = str(state.get(REPORT_KEYS[analyst]) or "")
        found = _report_urls(report)
        matched = found & known_links
        untraced = found - known_links
        checks.append({
            "analyst": analyst,
            "report_present": bool(report.strip()),
            "cited_urls": len(found),
            "tool_traced_urls": len(matched),
            "untraced_urls": sorted(untraced)[:12],
            "note": (
                "Tool traceability only; even matching URLs do not verify individual claims."
                if found else "No explicit article/document URLs in this analyst's report."
            ),
        })
        if untraced:
            warnings.append(
                f"{analyst.capitalize()} report cites {len(untraced)} URL(s) not found "
                "in the captured tool evidence; verify them manually."
            )

    # News coverage requires that we can see some dated source material; otherwise
    # do not promote an ungrounded news story to 'verified'.
    tool_calls = evidence.get("tool_calls") or {}
    news_calls = sum(int(tool_calls.get(k) or 0) for k in NEWS_TOOLS)
    financial_calls = sum(int(tool_calls.get(k) or 0) for k in FUNDAMENTAL_TOOLS)
    dated_news_links = sum(
        1 for item in sources
        if item.get("tool") in NEWS_TOOLS and item.get("published")
    )
    undated_news_links = sum(
        1 for item in sources
        if item.get("tool") in NEWS_TOOLS and not item.get("published")
    )
    gaps = []
    tool_outcomes = evidence.get("tool_outcomes") or {}
    for group, names in (
        ("news", NEWS_TOOLS), ("fundamentals", FUNDAMENTAL_TOOLS),
    ):
        if group not in analysts:
            continue
        for name in sorted(names):
            outcomes = tool_outcomes.get(name) or {}
            if outcomes.get("error") or outcomes.get("unavailable"):
                gaps.append(
                    f"{name} returned {outcomes.get('unavailable', 0)} unavailable "
                    f"and {outcomes.get('error', 0)} errored response(s); "
                    "this is a data-availability limitation, not bearish evidence."
                )
    if "news" in analysts:
        if not news_calls:
            gaps.append("No company/global news tool calls were captured.")
        if not dated_news_links:
            gaps.append("No dated news URLs were captured for the News analyst.")
        if undated_news_links:
            gaps.append(
                f"{undated_news_links} news link(s) lack a publication date, "
                "so historical availability is not established."
            )
    if "fundamentals" in analysts:
        if not financial_calls:
            gaps.append("No financial-statement/fundamentals tool calls were captured.")
        if not any(item.get("tool") in FUNDAMENTAL_TOOLS and item.get("published")
                   for item in sources):
            gaps.append(
                "No dated fundamental-document URLs were captured; fiscal dates "
                "must not be mistaken for original publication dates."
            )
    warnings.extend(gaps)

    market = evidence.get("market_snapshot")
    if "market" in analysts and not market:
        warnings.append(
            "No dated market-tool snapshot was captured; exact current-price "
            "claims could not be compared with historical vendor data."
        )
    if market and (market.get("age_days") or 0) > 7:
        warnings.append(
            "The market-tool snapshot predates the analysis by more than "
            "seven calendar days; confirm exchange-session coverage."
        )
    price_flags = []
    checked_prices = []
    if market and market.get("point_in_time"):
        close = market.get("close")
        if isinstance(close, int | float) and math.isfinite(close) and close > 0:
            for analyst in analysts:
                report = str(state.get(REPORT_KEYS[analyst]) or "")
                for candidate in _price_claims(report):
                    if abs(candidate - close) > max(1.0, 0.005 * close):
                        price_flags.append({
                            "analyst": analyst,
                            "claimed_price": candidate,
                            "snapshot_close": close,
                            "snapshot_date": market.get("date"),
                            "difference_pct": round((candidate / close - 1) * 100, 2),
                        })
                    else:
                        checked_prices.append(candidate)
    if price_flags:
        warnings.append(
            f"{len(price_flags)} current-price mention(s) differ from the "
            "market-tool snapshot by over 0.5% (or 1 quote unit). Inspect dates "
            "and context; this is a review flag, not proof of a false claim."
        )

    # Changes between research-team and final portfolio views may be
    # deliberate; show them for transparency rather than call them "errors".
    research_call = _reported_field(
        str(state.get("investment_plan") or ""), "Recommendation"
    )
    trader_action = _reported_field(
        str(state.get("trader_investment_plan") or ""), "Action"
    )
    decision_change = bool(
        research_call and research_call.lower() != rating.lower()
    )
    # The price target should be justified in the report; never create one.
    target = risk.get("price_target")
    if rating in {"Buy", "Overweight", "Sell", "Underweight"}:
        if target is None:
            warnings.append(
                "Directional recommendation has no explicit numeric target; "
                "potential return and reward-to-risk cannot be evaluated."
            )
        elif risk.get("reward_to_risk") is None:
            warnings.append(
                "Directional recommendation has a target, but the entry/stop/"
                "target levels do not support a coherent reward-to-risk calculation."
            )
    # Hold is a legitimate conclusion; a missing price target on Hold is not
    # inherently a research error.
    return {
        "analysis_date": evidence.get("analysis_date"),
        "selected_analysts": list(analysts),
        "citation_checks": checks,
        "source_coverage": {
            "news_tool_calls": news_calls,
            "fundamental_tool_calls": financial_calls,
            "dated_news_links": dated_news_links,
            "undated_news_links": undated_news_links,
            "future_dated_links_excluded": evidence.get("future_dated_excluded", 0),
            "tool_outcomes": {
                name: dict(counts) for name, counts in tool_outcomes.items()
                if name in NEWS_TOOLS | FUNDAMENTAL_TOOLS
            },
        },
        "decision_alignment": {
            "research_team": research_call,
            "trader_action": trader_action,
            "portfolio_rating": rating,
            "research_to_portfolio_changed": decision_change,
            "note": (
                "Different recommendations can be justified as the risk team weighs "
                "evidence; divergence alone does not establish an error."
            ),
        },
        "market_comparison": {
            "has_snapshot": bool(market),
            "snapshot_date": (market or {}).get("date"),
            "snapshot_close": (market or {}).get("close"),
            "matching_current_price_mentions": len(checked_prices),
            "review_flags": price_flags,
        },
        "warnings": warnings,
        "status": "needs_review" if warnings else "limited_checks_passed",
        "disclaimer": (
            "Heuristic, limited audit only. A matching link does not prove a claim; "
            "an unmatched link might be valid. A financial report without dated "
            "source documents is not point-in-time verified. Validate all material "
            "statements against original dated disclosures and exchange data."
        ),
    }
