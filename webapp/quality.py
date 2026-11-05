"""Deterministic report-completeness checks for web research runs.

These checks confirm that requested sections were produced. They do NOT verify
underlying financial facts, data freshness, investment suitability or forecasts.
"""
from __future__ import annotations

import re

from tradingagents.agents.rating import run_rating

ANALYST_REPORTS = {
    "market": ("Technical", "market_report"),
    "social": ("Sentiment", "sentiment_report"),
    "news": ("News", "news_report"),
    "fundamentals": ("Fundamentals", "fundamentals_report"),
}
_NOT_PROVIDED = {"", "not provided", "none", "n/a", "na", "unknown", "not available", "tbd"}


def _reported_field(text: str, label: str) -> str | None:
    """Read only an explicitly labeled report field, never infer a target."""
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        normalized = line.replace("**", "").replace("__", "").lstrip(" #-\t")
        match = re.match(rf"^{re.escape(label)}\s*:\s*(.+)$", normalized, flags=re.I)
        if match:
            value = match.group(1).strip().strip("*").strip()
            if value.lower() in _NOT_PROVIDED:
                return None
            return value[:160]
    return None


def report_quality(state: dict, analysts: list[str]) -> dict:
    """Produce a transparent coverage checklist, not a confidence score."""
    selected = []
    missing = []
    for analyst in analysts:
        label, report_key = ANALYST_REPORTS[analyst]
        present = bool(str(state.get(report_key, "") or "").strip())
        selected.append({"analyst": analyst, "label": label, "present": present})
        if not present:
            missing.append(label)

    final = state.get("final_trade_decision", "") or ""
    risk = state.get("risk_debate_state") or {}
    risk_present = isinstance(risk, dict) and any(
        risk.get(key) for key in ("aggressive_history", "neutral_history", "conservative_history")
    )
    target = _reported_field(final, "Price Target")
    horizon = _reported_field(final, "Time Horizon")
    warnings = []
    if missing:
        warnings.append("Selected analyst reports missing: " + ", ".join(missing))
    if not target:
        warnings.append("No explicit price target was provided in the final decision.")
    if not horizon:
        warnings.append("No explicit investment time horizon was provided.")
    if not risk_present:
        warnings.append("Risk-team debate text was not found in the final state.")

    return {
        "rating": run_rating(state),
        "analysts": selected,
        "sections_present": len(selected) - len(missing),
        "sections_requested": len(selected),
        "explicit_price_target": target,
        "explicit_time_horizon": horizon,
        "risk_debate_present": risk_present,
        "warnings": warnings,
        "disclaimer": (
            "Completeness checks only. Do not interpret as proof of data quality "
            "or investment accuracy; verify financials, dates and sources."
        ),
    }
