# FreeLLMAPI — optional private gateway integration (PR #5)

The research dashboard now offers **Direct Gemini** (existing default) and **FreeLLMAPI**. The latter is an OpenAI-compatible inference gateway. **This PR is not yet a demonstrated working end-to-end gateway analysis.** Models must be explicitly approved for financial-tool calls and structured outputs.

## Start the gateway in the SAME TradingAgents Codespace

Separate Codespaces do **not** share localhost. Open another terminal inside your TradingAgents Codespace:

```bash
cd /workspaces
git clone https://github.com/varadachiru/freellmapi.git
cd freellmapi
npm install
umask 077
printf 'ENCRYPTION_KEY=%s\nPORT=3001\nHOST=127.0.0.1\n' \
  "$(node -e "console.log(require('crypto').randomBytes(32).toString('hex'))")" > .env
npm run build
node server/dist/index.js
```

FreeLLMAPI requires Node >=20.18 and npm >=10. Its encryption key must persist with the SQLite database; **never commit .env or discard the key**. Its dashboard is served on port **3001**. Open this port via Codespaces **Ports**, keeping visibility **Private**. Complete first-run setup, add one or more provider keys under **Keys**, inspect the actual eligible models, and copy FreeLLMAPI's **unified API token**. Provider credentials remain in the encrypted gateway store; the TradingAgents browser never sees them.

## Configure TradingAgents

Store `FREELLMAPI_API_KEY` as a GitHub **Codespaces secret** for the TradingAgents repository, then restart/rebuild the Codespace to load it. Use a new terminal *in the same Codespace*:

```bash
cd /workspaces/TradingAgents
python3 -m pip install -e '.[dev]'
# Only after creating these named, restricted fallback profiles
# in the FreeLLMAPI dashboard:
export FREELLMAPI_QUICK_MODEL='auto:trading-quick'
export FREELLMAPI_DEEP_MODEL='auto:trading-deep'
export FREELLMAPI_BASE_URL='http://127.0.0.1:3001/v1'
python3 -m webapp.server
```

The named profiles are **examples to configure, not preexisting profiles**. Alternatively set each model environment variable to an explicit model ID supported by your gateway. Choose models with sufficient context, stable tool calling and structured output support. The server intentionally disallows unrestricted global `auto`, `auto:fast`, `auto:smart`, `auto:cheap`, `auto:balanced`, `auto:reliable` for financial research; use only explicit models or constrained, tested named chains.

The gateway URL defaults to `http://127.0.0.1:3001/v1`, is configured **only** on the server, and requires HTTPS for remote servers. Never forward an unauthenticated gateway or its admin UI publicly.

## Dashboard validation

Open private Codespaces port **8765**, choose **FreeLLMAPI** in AI Provider, and click **Test connection**. This sends an authenticated `GET /v1/models?execution_status=ready` to your gateway. It tests connectivity and authorization *without inference requests*. It does not establish that models support the required tools, that named profiles will succeed, or that quotas will cover a full research run.

For an isolated, free-of-inference connection check:

```bash
python3 - <<'PY'
from webapp.gateway import test_gateway_connection
print(test_gateway_connection())
PY
```

**Before a live analysis**, explicitly run this capability probe, which sends
real inference requests to both model tiers and checks basic completion, a
model-initiated tool call and tool-result round-trip, and structured output:

```bash
python3 -m webapp.gateway_smoke
```

The command **consumes provider API quota** and fails if the selected models
lack these capabilities. Passing it is necessary but not sufficient for
full-agent compatibility. Only after it passes run one conservative
AAPL / Technical-only / 1-round analysis and compare with Gemini.

The provider selection is *manual*: a failed gateway run does **not** silently reroute to Gemini. Switching back to Direct Gemini preserves the existing workflow.

## Usage, price and reproducibility

The app saves `execution_metadata.json` alongside usage, quality, evidence and integrity records and appends an inference-configuration footer to `complete_report.md`. It stores requested quick/deep IDs and callback-reported model counts. Model aliases are not proof of which upstream model actually served the request.

Gateway cost is displayed as **Not available**, never as zero. FreeLLMAPI may substitute models, and LangChain may not expose gateway routing headers or upstream retries. Use FreeLLMAPI's private request logs for actual model, fallback, latency, usage and provider billing. Catalog-wide free-token totals are *not* guaranteed usable for your keys. Verify commercial/free-tier terms per provider.

## Offline validation before merging

```bash
ruff check webapp tradingagents/llm_clients/openai_client.py \
  tradingagents/llm_clients/api_key_env.py \
  tradingagents/llm_clients/model_catalog.py \
  tradingagents/llm_clients/validators.py cli/prompts.py tests/test_web_freellmapi.py
pytest -q tests/test_web_dashboard.py tests/test_web_usage_and_quality.py \
  tests/test_web_evidence_risk.py tests/test_web_integrity.py \
  tests/test_web_freellmapi.py
```

Do not merge until Codespaces Ruff, pytest, a private browser smoke test, and at least one gateway-backed real analysis are confirmed. This feature neither changes broker execution nor deploys to Cloudflare or Render.
