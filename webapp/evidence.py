"""Point-in-time evidence ledger from actual tool outputs, not AI-written citations.

Only URLs and dates present in a tool result become evidence entries. These
entries show what an analyst *could access*, not which claims they prove.
"""
from __future__ import annotations

import math
import re
import threading
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from langchain_core.callbacks.base import BaseCallbackHandler

LINK_RE = re.compile(r"(?im)^\s*(?:Link|URL|Source URL)\s*:\s*(https?://[^\s<>)]+)")
HEADER_RE = re.compile(r"(?m)^###\s+(.+?)(?:\s+\(source:\s*([^)\n]+)\))?\s*$")
PUBLISHED_RE = re.compile(r"(?im)^\s*(?:Published|Publication date|Pub date)\s*:\s*(\d{4}-\d{2}-\d{2})(?:[T ][^\n]*)?$")
MARKET_DATE_RE = re.compile(r"(?m)^-\s*Latest trading row used:\s*(\d{4}-\d{2}-\d{2})\s*$")
MARKET_CLOSE_RE = re.compile(r"(?m)^\|\s*Close\s*\|\s*([\d,]+(?:\.\d+)?)\s*\|$")
SOURCE_TOOLS = frozenset({"get_news", "get_global_news", "get_fundamentals", "get_balance_sheet", "get_income_statement", "get_cashflow", "get_insider_transactions", "get_macro_indicators"})
MARKET_TOOL = "get_verified_market_snapshot"


def safe_source_url(raw: str) -> str | None:
    """Allow external http(s) links only; no credentials or localhost URLs."""
    raw = raw.rstrip(".,;]").strip()
    if len(raw) > 1800:
        return None
    try:
        url = urlsplit(raw)
        host = (url.hostname or "").lower().rstrip(".")
        if url.scheme not in {"https", "http"} or not host or url.username or url.password:
            return None
        if host in {"localhost", "0.0.0.0", "::1"} or host.endswith(".localhost"):
            return None
        if host.startswith(("127.", "10.", "192.168.", "169.254.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
            return None
        return raw
    except ValueError:
        return None


def _text(output: Any) -> str:
    value = getattr(output, "content", output)
    if isinstance(value, str):
        return value[:200_000]
    if isinstance(value, list):
        return "\n".join(str(item.get("text", "")) for item in value if isinstance(item, dict))[:200_000]
    return ""


def _published(value: str, as_of_date: str) -> tuple[str | None, str]:
    try:
        date_value = date.fromisoformat(value)
        if date_value.isoformat() != value:
            return None, "undated"
        if date_value > date.fromisoformat(as_of_date):
            return value, "after_analysis_date"
        return value, "dated_on_or_before_analysis"
    except (ValueError, TypeError):
        return None, "undated"


def extract_sources(text: str, tool: str, as_of_date: str) -> list[dict]:
    """Parse article/tool lines, never infer article dates from the query window."""
    sections = list(HEADER_RE.finditer(text))
    blocks = []
    if sections:
        for i, match in enumerate(sections):
            end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
            blocks.append((match.group(1)[:180].strip(), (match.group(2) or "").strip()[:80], text[match.end():end]))
    else:
        blocks.append(("", "", text))
    sources = []
    for title, publisher, block in blocks:
        published = PUBLISHED_RE.search(block)
        timestamp, date_status = _published(published.group(1) if published else "", as_of_date)
        for match in LINK_RE.finditer(block):
            url = safe_source_url(match.group(1))
            if url:
                sources.append({
                    "title": title or urlsplit(url).netloc,
                    "publisher": publisher or "Unspecified",
                    "url": url,
                    "published": timestamp,
                    "date_status": date_status,
                    "tool": tool,
                    # Tool origin is the only defensible classification:
                    # never claim a global article is relevant to the ticker
                    # merely because it appeared in the same research run.
                    "scope": (
                        "company_feed" if tool == "get_news"
                        else "global_context" if tool == "get_global_news"
                        else "other_source"
                    ),
                })
    return sources


def parse_verified_snapshot(text: str, as_of_date: str) -> dict | None:
    """Read a verified *tool result*, never an AI paraphrase of market prices."""
    match_date = MARKET_DATE_RE.search(text)
    match_close = MARKET_CLOSE_RE.search(text)
    if not match_date or not match_close:
        return None
    stamp, status = _published(match_date.group(1), as_of_date)
    if status != "dated_on_or_before_analysis":
        return None
    close = float(match_close.group(1).replace(",", ""))
    if not (math.isfinite(close) and close > 0):
        return None
    return {
        "date": stamp,
        "close": close,
        "source": "Yahoo Finance OHLCV via get_verified_market_snapshot",
        "analysis_date": as_of_date,
        "point_in_time": True,
    }


class EvidenceCollector(BaseCallbackHandler):
    """Thread-safe audit of tool results, passed through LangGraph callbacks."""

    def __init__(self, as_of_date: str, analysts: list[str] | tuple[str, ...] | None = None):
        super().__init__()
        self.as_of_date = date.fromisoformat(as_of_date).isoformat()
        # None means analyst coverage is unknown (e.g. direct callback use).
        # A technical-only run collects prices/indicators, not article links.
        self.analysts = frozenset(analysts) if analysts is not None else None
        self._lock = threading.Lock()
        self._pending: dict[str, str] = {}
        self._tool_counts: dict[str, int] = {}
        self._tool_outcomes: dict[str, dict[str, int]] = {}
        self._sources: dict[str, dict] = {}
        self._snapshot: dict | None = None
        self._future_sources = 0

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: Any, **kwargs: Any) -> None:
        del input_str, kwargs
        name = serialized.get("name") if isinstance(serialized, dict) else None
        if isinstance(name, str):
            with self._lock:
                self._pending[str(run_id)] = name

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        del error, kwargs
        with self._lock:
            name = self._pending.pop(str(run_id), None)
            if name:
                self._tool_counts[name] = self._tool_counts.get(name, 0) + 1
                outcomes = self._tool_outcomes.setdefault(name, {})
                outcomes["error"] = outcomes.get("error", 0) + 1

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        content = _text(output)
        with self._lock:
            name = self._pending.pop(str(run_id), None)
        if not name:
            name = getattr(output, "name", None)
        if not isinstance(name, str) or name not in SOURCE_TOOLS | {MARKET_TOOL}:
            return
        sources = extract_sources(content, name, self.as_of_date) if name in SOURCE_TOOLS else []
        snapshot = parse_verified_snapshot(content, self.as_of_date) if name == MARKET_TOOL else None
        lower = content.lower()
        # Classify only explicit placeholders or empty/no-result responses.
        # 'returned' does not establish source reliability or correctness.
        if not content.strip():
            outcome = "empty"
        elif (
            re.search(r"<[^>]{0,220}unavailable[^>]{0,220}>", lower)
            or "vendor unavailable" in lower
        ):
            outcome = "unavailable"
        elif re.search(r"\bno (?:news|data|articles|results) found\b", lower):
            outcome = "no_results"
        else:
            outcome = "returned"
        with self._lock:
            self._tool_counts[name] = self._tool_counts.get(name, 0) + 1
            tool_outcomes = self._tool_outcomes.setdefault(name, {})
            tool_outcomes[outcome] = tool_outcomes.get(outcome, 0) + 1
            for item in sources:
                if item["date_status"] == "after_analysis_date":
                    self._future_sources += 1
                    continue
                self._sources.setdefault(item["url"], item)
            if snapshot and (self._snapshot is None or snapshot["date"] > self._snapshot["date"]):
                self._snapshot = snapshot

    def snapshot(self) -> dict:
        with self._lock:
            sources = sorted(self._sources.values(), key=lambda s: (s["published"] or "", s["title"]), reverse=True)
            prices = dict(self._snapshot) if self._snapshot else None
            calls = dict(self._tool_counts)
            outcomes = {key: dict(value) for key, value in self._tool_outcomes.items()}
            future_count = self._future_sources
        dated = sum(s["published"] is not None for s in sources)
        warnings = []
        links_expected = self.analysts is None or bool(
            self.analysts.intersection({"news", "social", "fundamentals"})
        )
        if not sources and links_expected:
            warnings.append("No article or document URLs were captured from tool results. AI prose is not a substitute for source citations.")
        source_note = (
            "Technical-only analysis uses the dated market snapshot for price provenance; "
            "no article or document links were expected."
            if not sources and not links_expected
            else "No article or document URLs were returned by the selected source-oriented research tools."
            if not sources else None
        )
        if len(sources) != dated:
            warnings.append("Some source links have no publication date; their point-in-time availability is not established.")
        if future_count:
            warnings.append(f"{future_count} source link(s) dated after the analysis date were excluded.")
        if prices is None:
            if self.analysts is None or "market" in self.analysts:
                warnings.append("No verified historical OHLCV snapshot was captured; quoted prices have not been checked against market tool output.")
        else:
            age = (date.fromisoformat(self.as_of_date) - date.fromisoformat(prices["date"])).days
            prices["age_days"] = age
            if age > 7:
                warnings.append(f"Latest available market-data row is {age} calendar days before the analysis date; confirm data coverage.")
        return {
            "analysis_date": self.as_of_date,
            "tool_calls": calls,
            "tool_outcomes": outcomes,
            "sources": sources[:60],
            "source_count": len(sources),
            "dated_source_count": dated,
            "article_links_expected": links_expected,
            "source_note": source_note,
            "market_snapshot_expected": self.analysts is None or "market" in self.analysts,
            "future_dated_excluded": future_count,
            "market_snapshot": prices,
            "warnings": warnings,
            "disclaimer": "These are tool-returned source links and prices, not proof that the report's specific claims are correct. Check original publications and vintage before using a recommendation.",
        }
