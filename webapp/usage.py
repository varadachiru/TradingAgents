"""Per-analysis Gemini token accounting for the local research web console.

Uses model-reported LangChain usage metadata. The paid-tier estimate is not
Google billing: it excludes grounding/search, cache storage, tools, failed
requests without reported usage, and any provider-side retries not surfaced
to callbacks. Free-tier usage can cost $0 even when the paid-tier estimate is
positive.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from langchain_core.callbacks.base import BaseCallbackHandler

# USD per million tokens, Gemini Developer API Standard / paid tier.
# Keep other models *unpriced* rather than inventing or silently substituting
# model rates. Date/references travel with saved reports.
PRICING_AS_OF = "2026-11-05"
PRICING_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
MODEL_RATES = {
    "gemini-3.1-flash-lite": {
        "input": 0.25,
        "output": 1.50,
        "cached_input": 0.025,
    },
}


def _count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _model_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.removeprefix("models/").strip()


def _message_and_model(result: Any) -> tuple[Any | None, str]:
    """Pull the first chat generation and its provider-identified model."""
    output = getattr(result, "llm_output", None)
    model = _model_name(output.get("model_name") or output.get("model")) if isinstance(output, dict) else ""
    first_message = None
    for generation_group in (getattr(result, "generations", None) or []):
        if generation_group:
            first_message = getattr(generation_group[0], "message", None)
            break
    meta = getattr(first_message, "response_metadata", None)
    if not model and isinstance(meta, dict):
        model = _model_name(meta.get("model_name") or meta.get("model"))
    return first_message, model


def _usage_from_result(result: Any, message: Any) -> tuple[dict, bool]:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        details = usage.get("input_token_details") or {}
        if not isinstance(details, dict):
            details = {}
        return {
            "input_tokens": _count(usage.get("input_tokens")),
            "output_tokens": _count(usage.get("output_tokens")),
            "cached_input_tokens": _count(details.get("cache_read")),
        }, True

    output = getattr(result, "llm_output", None)
    token_usage = output.get("token_usage") if isinstance(output, dict) else None
    if isinstance(token_usage, dict):
        return {
            "input_tokens": _count(token_usage.get("prompt_tokens")),
            "output_tokens": _count(token_usage.get("completion_tokens")),
            "cached_input_tokens": 0,
        }, True

    return {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}, False


def _cost_usd(model: str, counts: dict) -> float | None:
    rate = MODEL_RATES.get(model)
    if not rate:
        return None
    prompt = counts["input_tokens"]
    cached = min(counts["cached_input_tokens"], prompt)
    output = counts["output_tokens"]
    return ((prompt - cached) * rate["input"] + cached * rate["cached_input"]
            + output * rate["output"]) / 1_000_000


class GeminiUsageTracker(BaseCallbackHandler):
    """Thread-safe, per-run LLM callback shared by all analyst model clients."""

    def __init__(self, default_model: str | None = None):
        super().__init__()
        self.default_model = default_model
        self._lock = threading.Lock()
        self._seen_runs: set[str] = set()
        self._models: dict[str, dict] = {}

    def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        message, model = _message_and_model(response)
        model = model or self.default_model or "unknown"
        counts, reported = _usage_from_result(response, message)
        key = str(run_id) if run_id is not None else None
        with self._lock:
            if key and key in self._seen_runs:
                return
            if key:
                self._seen_runs.add(key)
            row = self._models.setdefault(model, {
                "calls": 0, "reported_calls": 0, "unreported_calls": 0,
                "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
            })
            row["calls"] += 1
            row["reported_calls" if reported else "unreported_calls"] += 1
            for k, n in counts.items():
                row[k] += n

    def snapshot(self) -> dict:
        with self._lock:
            models = {name: dict(counts) for name, counts in sorted(self._models.items())}
        estimated_usd = 0.0
        pricing_complete = True
        totals = {
            "calls": 0, "reported_calls": 0, "unreported_calls": 0,
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
        }
        for name, counts in models.items():
            for k in totals:
                totals[k] += counts[k]
            cost = _cost_usd(name, counts)
            counts["estimated_paid_usd"] = round(cost, 6) if cost is not None else None
            if cost is None:
                pricing_complete = False
            else:
                estimated_usd += cost
        if totals["unreported_calls"]:
            pricing_complete = False
        return {
            **totals,
            "models": models,
            "estimated_paid_usd": round(estimated_usd, 6),
            "pricing_complete": pricing_complete,
            "pricing_as_of": PRICING_AS_OF,
            "pricing_source": PRICING_SOURCE,
            "note": (
                "Estimate uses reported model tokens and reference Standard paid-tier prices, "
                "not actual Google charges. Excludes some retries, unreported usage, "
                "grounding, tool/data fees and other billing adjustments."
            ),
            "recorded_at": datetime.now(UTC).isoformat(),
        }
