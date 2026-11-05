"""Opt-in FreeLLMAPI compatibility smoke checks (real API requests, no trades).

Use only after gateway auth and approved quick/deep model profiles are set up.
This script uses a few short prompts and is NOT run by pytest or CI.
"""
from __future__ import annotations

import os
import sys

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tradingagents.llm_clients.factory import create_llm_client
from webapp.gateway import (
    GatewayConfigError,
    configured_models,
    gateway_is_configured,
    gateway_url,
)


class ResearchTag(BaseModel):
    category: str = Field(description="Exactly one of: test")
    risk_level: int = Field(description="Exactly 1")


@tool
def add_two_integers(a: int, b: int) -> int:
    """Add two whole numbers precisely."""
    return a + b


def main() -> int:
    if not gateway_is_configured():
        print("FAIL: Set the gateway URL, approved models, and FREELLMAPI_API_KEY.")
        return 2
    url = gateway_url()
    quick, deep = configured_models()
    print("Gateway: local, authenticated (credential not displayed)")
    print(f"Approved quick model/profile: {quick}")
    print(f"Approved deep model/profile: {deep}")
    for tier, model in (("quick", quick), ("deep", deep)):
        llm = create_llm_client(
            "freellmapi", model, base_url=url, timeout=45, max_retries=0
        ).get_llm()
        try:
            response = llm.invoke("Reply with exactly the word READY.")
            if not getattr(response, "content", None):
                raise ValueError("No response content")
            print(f"PASS: {tier} plain response")
            if tier == "quick":
                bound = llm.bind_tools([add_two_integers], tool_choice="required")
                result = bound.invoke("Use add_two_integers to add 17 and 25.")
                calls = getattr(result, "tool_calls", None) or []
                if not any(
                    item.get("name") == "add_two_integers"
                    and item.get("args", {}).get("a") == 17
                    and item.get("args", {}).get("b") == 25
                    for item in calls
                ):
                    raise ValueError("No valid add_two_integers tool call")
                print("PASS: quick tool-calling and JSON arguments")
            else:
                tagged = llm.with_structured_output(
                    ResearchTag, method="function_calling"
                ).invoke("Return category=test and risk_level=1.")
                if not isinstance(tagged, ResearchTag) or (
                    tagged.category != "test" or tagged.risk_level != 1
                ):
                    raise ValueError("Structured output schema was not honored")
                print("PASS: deep structured output")
        except Exception as exc:
            error = str(exc)
            for key in (os.environ.get("FREELLMAPI_API_KEY"),):
                if key:
                    error = error.replace(key, "[redacted]")
            print(f"FAIL: {tier} {type(exc).__name__}: {error[:400]}")
            print("Fix or narrow gateway model profiles before full financial research.")
            return 1
    print("PASS: basic gateway compatibility. Fallback reliability still unverified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
