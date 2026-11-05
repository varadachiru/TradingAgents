"""Local-only FreeLLMAPI configuration and authenticated status probe.

All secrets stay on the Python server. Browser requests may select only the
model IDs configured by the operator; they cannot supply endpoint URLs, keys,
or arbitrary models. PR #5 deliberately supports loopback-only connections.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_URL = "http://127.0.0.1:3001/v1"
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,99}$")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class GatewayConfigError(ValueError):
    """A locally configured setting needs operator attention."""


class _NoRedirect(HTTPRedirectHandler):
    """Never forward the bearer token to an HTTP redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def gateway_url() -> str:
    """Validate a local endpoint and avoid leaking bearer tokens to remote hosts."""
    value = os.environ.get("FREELLMAPI_BASE_URL", DEFAULT_URL).strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise GatewayConfigError("Invalid FreeLLMAPI local gateway URL.") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in LOCAL_HOSTS
        or not port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/") != "/v1"
        or parsed.query
        or parsed.fragment
    ):
        raise GatewayConfigError(
            "PR #5 only allows an HTTP FreeLLMAPI endpoint on localhost "
            "with an explicit port and /v1 path. Keep the gateway private."
        )
    return value.rstrip("/")


def configured_models() -> tuple[str, str]:
    quick = os.environ.get("FREELLMAPI_QUICK_MODEL", "auto:trading-quick").strip()
    deep = os.environ.get("FREELLMAPI_DEEP_MODEL", "auto:trading-deep").strip()
    if not MODEL_ID.fullmatch(quick) or not MODEL_ID.fullmatch(deep):
        raise GatewayConfigError(
            "FreeLLMAPI model names must be 1–100 characters using letters, "
            "numbers, hyphens, underscores, dots, slashes or colons."
        )
    return quick, deep


def gateway_is_configured() -> bool:
    if not os.environ.get("FREELLMAPI_API_KEY"):
        return False
    try:
        gateway_url()
        configured_models()
    except GatewayConfigError:
        return False
    return True


def public_provider_status() -> dict:
    """Return only non-secret configuration; never expose gateway URLs or keys."""
    quick, deep = ("auto:trading-quick", "auto:trading-deep")
    try:
        quick, deep = configured_models()
    except GatewayConfigError:
        pass
    return {
        "google": {"configured": bool(os.environ.get("GOOGLE_API_KEY"))},
        "freellmapi": {
            "configured": gateway_is_configured(),
            "quick_model": quick,
            "deep_model": deep,
        },
    }


def test_gateway_connection() -> dict:
    """Test actual authenticated /v1/models, with bounded response and timeout.

    Model listing proves gateway connectivity, not that fallback profiles,
    tools or structured outputs work; those require separate smoke tests.
    """
    url = gateway_url()
    token = os.environ.get("FREELLMAPI_API_KEY")
    if not token:
        raise GatewayConfigError("Add FREELLMAPI_API_KEY as a Codespaces secret.")
    request = Request(
        url + "/models?available=true",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        # Avoid outbound HTTP_PROXY settings for authenticated localhost calls.
        with build_opener(ProxyHandler({}), _NoRedirect).open(request, timeout=5) as response:
            if response.status != 200:
                raise GatewayConfigError(
                    f"FreeLLMAPI returned HTTP {response.status}."
                )
            payload = json.loads(response.read(131_073))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise GatewayConfigError(
                "FreeLLMAPI authentication failed. Check the unified gateway key."
            ) from None
        raise GatewayConfigError(
            f"FreeLLMAPI model-list request returned HTTP {exc.code}."
        ) from None
    except (URLError, OSError, TimeoutError, ValueError) as exc:
        raise GatewayConfigError(
            "Could not reach FreeLLMAPI at the local gateway URL. "
            "Start the gateway in this Codespace and check port 3001."
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise GatewayConfigError("FreeLLMAPI returned an unexpected model-list response.")
    entries = payload["data"]
    ready = [
        item for item in entries
        if isinstance(item, dict)
        and item.get("execution_status") == "ready"
    ]
    ids = {
        item["id"] for item in entries
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    quick, deep = configured_models()
    approved_profiles_listed = quick in ids and deep in ids
    return {
        "reachable": True,
        "ready_models": len(ready),
        "approved_models_listed": approved_profiles_listed,
        "note": (
            "Both approved model/profile identifiers are listed. "
            "Tool calling and structured output still require a smoke test."
            if approved_profiles_listed else
            "Gateway authenticated, but one or both approved model/profile IDs "
            "are missing from the available model list. Configure the chains "
            "or choose approved models before running an analysis."
        ),
    }
