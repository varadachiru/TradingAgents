"""Private, optional FreeLLMAPI configuration for the Codespaces research console.

All credentials and endpoint configuration are server-side. Neither the browser
nor a request body can supply a gateway URL or a new model identifier. Only
operator-approved quick/deep model IDs may be selected for financial research.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,119}$")
DEFAULT_URL = "http://127.0.0.1:3001/v1"


def gateway_url() -> str:
    raw = os.environ.get("FREELLMAPI_BASE_URL", DEFAULT_URL).strip().rstrip("/")
    parts = urlsplit(raw)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in {"", "/v1"}):
        raise ValueError("FREELLMAPI_BASE_URL must be a plain http(s) URL ending in /v1.")
    # Local development may use http; remote access must be TLS-protected.
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Non-local FreeLLMAPI gateways must use HTTPS.")
    if parts.port is not None and not 1 <= parts.port <= 65535:
        raise ValueError("Invalid FreeLLMAPI port.")
    return raw.rstrip("/") + ("" if parts.path == "/v1" else "/v1")


def approved_models() -> dict[str, str]:
    """Return two independently approved model/profile IDs, never default to auto.

    The operator must first confirm the models' function/tool and structured
    output capabilities in FreeLLMAPI. Both can be the same verified model if
    desired. A named auto:<profile> is supported but requires a constrained
    profile in the gateway, not the unconstrained global auto pool.
    """
    quick = os.environ.get("FREELLMAPI_QUICK_MODEL", "").strip()
    deep = os.environ.get("FREELLMAPI_DEEP_MODEL", "").strip()
    if not quick or not deep:
        return {}
    if not MODEL_RE.fullmatch(quick) or not MODEL_RE.fullmatch(deep):
        raise ValueError("Configured FreeLLMAPI models contain invalid characters.")
    if quick in {"auto", "auto:smart", "auto:fast", "auto:balanced", "auto:cheap", "auto:reliable"}:
        raise ValueError("Choose an approved explicit model or a restricted auto:<profile> for the quick tier.")
    if deep in {"auto", "auto:smart", "auto:fast", "auto:balanced", "auto:cheap", "auto:reliable"}:
        raise ValueError("Choose an approved explicit model or a restricted auto:<profile> for the deep tier.")
    return {"quick": quick, "deep": deep}


def gateway_settings() -> dict:
    """Safe public status; never disclose the bearer token."""
    try:
        url = gateway_url()
        models = approved_models()
        configuration_error = None
    except ValueError as exc:
        url = None
        models = {}
        configuration_error = str(exc)
    # Do not publish arbitrary remote hostnames if a user reads /api/status.
    is_local = bool(url and urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"})
    key_present = bool(os.environ.get("FREELLMAPI_API_KEY", "").strip())
    return {
        "configured": bool(key_present and models and url),
        "key_configured": key_present,
        "endpoint": ("Local gateway · port " + str(urlsplit(url).port or 3001)
                     if url and is_local else "Private HTTPS gateway" if url else "Invalid endpoint"),
        "quick_model": models.get("quick"),
        "deep_model": models.get("deep"),
        "configuration_error": configuration_error,
        "note": "Model IDs must be approved for reliable tools and structured output. No credentials are sent to the browser.",
    }


def test_gateway_connection() -> dict:
    """Check authenticated model discovery, without consuming inference tokens.

    Not an agent compatibility check; a real tool/structured-output smoke test
    and a TradingAgents run are still necessary before production use.
    """
    url = gateway_url()
    models = approved_models()
    token = os.environ.get("FREELLMAPI_API_KEY", "").strip()
    if not models:
        raise ValueError("Set FREELLMAPI_QUICK_MODEL and FREELLMAPI_DEEP_MODEL to approved models.")
    if not token:
        raise ValueError("Set FREELLMAPI_API_KEY as a Codespaces secret.")
    request = Request(
        url + "/models?execution_status=ready",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=7) as response:
            if response.status != 200:
                raise ValueError(f"Gateway model discovery returned HTTP {response.status}.")
            body = json.loads(response.read(262_144))
    except HTTPError as exc:
        raise ValueError(f"Gateway rejected model discovery (HTTP {exc.code}).") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Could not reach the FreeLLMAPI gateway. Confirm it is running on the configured private endpoint.") from None
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Gateway returned an invalid model-list response.") from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("Gateway returned an invalid model-list response.")
    ready_ids = {
        row.get("id") for row in body["data"]
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    return {
        "connected": True,
        "model_catalog_accessible": True,
        "ready_models_listed": len(ready_ids),
        "quick_model_listed": models["quick"] in ready_ids,
        "deep_model_listed": models["deep"] in ready_ids,
        "note": "Authenticated discovery passed. Model suitability, paid/free quota, tool calls and fallback are not yet verified.",
    }
