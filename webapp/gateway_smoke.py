"""Optional LIVE gateway compatibility smoke test. Sends real inference requests.

Usage:
    python3 -m webapp.gateway_smoke

This must be run explicitly with a private gateway configured. It verifies
basic chat, actual tool-call + tool-result round-trip, and a Pydantic
structured-output response. Tests both approved model tiers. Do not confuse
success with financial reasoning accuracy or provider-free-tier eligibility.
"""
from __future__ import annotations

import os
import sys

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tradingagents.llm_clients import create_llm_client

from .gateway import approved_models, gateway_url, test_gateway_connection


class ProbeResult(BaseModel):
    signal: str = Field(description="The exact word OK")
    confidence: int = Field(description="Integer 1 to 5")


@tool
def test_quote_tool(ticker: str) -> str:
    """Return a synthetic numeric stock quote for a test ticker only."""
    return "100.00" if ticker.upper() == "DEMO" else "No data"


def probe_model(model: str) -> None:
    """Live smoke test of model suitability for TradingAgents agent nodes."""
    client = create_llm_client(
        "freellmapi", model, base_url=gateway_url(), max_retries=0
    ).get_llm()

    plain = client.invoke("Reply with exactly the single word OK.")
    if not str(getattr(plain, "content", "")).strip():
        raise ValueError("Basic completion produced empty content.")
    print(f"  [PASS] Basic completion: {model}")

    tool_llm = client.bind_tools([test_quote_tool])
    request = HumanMessage(
        content="Call test_quote_tool with ticker DEMO. Do not invent a price."
    )
    tool_answer = tool_llm.invoke([request])
    calls = getattr(tool_answer, "tool_calls", None) or []
    if not calls or calls[0].get("name") != "test_quote_tool":
        raise ValueError("Model did not return a valid function/tool call.")
    arguments = calls[0].get("args") or {}
    if not isinstance(arguments, dict) or arguments.get("ticker", "").upper() != "DEMO":
        raise ValueError("Model returned incorrect tool arguments.")
    follow_up = tool_llm.invoke([
        request,
        tool_answer,
        ToolMessage(content=test_quote_tool.invoke({"ticker": "DEMO"}),
                    tool_call_id=calls[0]["id"]),
    ])
    if not str(getattr(follow_up, "content", "")).strip():
        raise ValueError("Model failed to respond after a tool result.")
    print(f"  [PASS] Tool calling and round-trip: {model}")

    structured = client.with_structured_output(ProbeResult)
    parsed = structured.invoke(
        "Reply as structured output with signal exactly OK and confidence 3."
    )
    if not isinstance(parsed, ProbeResult) or parsed.signal != "OK" or parsed.confidence != 3:
        raise ValueError("Model did not produce correctly parsed structured output.")
    print(f"  [PASS] Structured output: {model}")


def main() -> int:
    """Only execute the paid/free-provider calls after explicit CLI invocation."""
    if not os.environ.get("FREELLMAPI_API_KEY"):
        print("Missing FREELLMAPI_API_KEY. Configure a Codespaces secret.", file=sys.stderr)
        return 1
    try:
        models = approved_models()
        if not models:
            raise ValueError("Configure approved quick and deep model IDs first.")
        check = test_gateway_connection()
        print(
            "Gateway authenticated model discovery: OK; "
            f"{check['ready_models_listed']} catalog entries listed"
        )
        for tier, model in models.items():
            print(f"Testing {tier} tier model/profile: {model}")
            probe_model(model)
    except Exception as exc:
        # Never print upstream credentials, even if an SDK exception embeds them.
        message = str(exc)
        for key in ("FREELLMAPI_API_KEY", "GOOGLE_API_KEY"):
            secret = os.environ.get(key)
            if secret:
                message = message.replace(secret, "[redacted]")
        print(f"Gateway compatibility test FAILED: {type(exc).__name__}: {message[:450]}", file=sys.stderr)
        return 1
    print("Gateway capability tests passed; actual TradingAgents analysis is still unverified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
