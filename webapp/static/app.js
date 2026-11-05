"use strict";

const $ = (id) => document.getElementById(id);
const REPORTS = [
  ["final_trade_decision", "Final decision"],
  ["market_report", "Technical"],
  ["news_report", "News"],
  ["sentiment_report", "Sentiment"],
  ["fundamentals_report", "Fundamentals"],
  ["investment_plan", "Research debate"],
  ["trader_investment_plan", "Trader proposal"],
  ["full", "Full saved report"],
];
const state = {
  configured: false,
  providers: {},
  job: null,
  selectedReport: "job",
  selectedTab: "final_trade_decision",
  archive: [],
  historicalText: "",
  savedText: "",
  viewedReportId: null,
  historicalQuality: null,
  historicalEvidence: null,
  historicalRisk: null,
  historicalIntegrity: null,
};

function formatDate(value) {
  if (!value) return "Not started";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
function selectedAnalysts() {
  return Array.from(document.querySelectorAll('input[name="analyst"]:checked'))
    .map((input) => input.value);
}
function selectedJobSections() {
  if (!state.job || state.selectedReport !== "job") return {};
  return state.job.sections || {};
}
function setPreset(preset) {
  const full = preset === "full";
  document.querySelectorAll('input[name="analyst"]').forEach((input) => {
    input.checked = full || input.value === "market";
  });
  refreshPreset();
}
function refreshPreset() {
  const selected = selectedAnalysts();
  document.querySelectorAll("[data-preset]").forEach((el) => {
    const target = el.dataset.preset === "full"
      ? ["market", "social", "news", "fundamentals"]
      : ["market"];
    el.classList.toggle("selected", selected.length === target.length &&
      selected.every((name) => target.includes(name)));
  });
}
function setFormError(message) {
  const error = $("formError");
  error.hidden = !message;
  error.textContent = message || "";
}
function statusClass(status) {
  return status === "completed" ? "completed" : status === "failed" ? "failed" :
    status === "running" ? "running" : "idle";
}

function num(value) {
  return Number(value || 0).toLocaleString();
}
function dollar(value) {
  const amount = Number(value || 0);
  return "$" + amount.toFixed(6);
}

const GEMINI_MODELS = [
  ["gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite · Lower cost"],
  ["gemini-3.8-flash", "Gemini 3.8 Flash · Stronger"]
];
let providerOptionsSignature = "";
function selectedProvider() {
  return $("aiProvider").value;
}
function fillModelOptions(control, options, fallbackLabel) {
  const oldValue = control.value;
  control.replaceChildren();
  if (!options.length) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = fallbackLabel;
    control.append(placeholder);
    return;
  }
  for (const [value, label] of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    control.append(option);
  }
  if (options.some(([value]) => value === oldValue)) control.value = oldValue;
}
function updateProviderControls() {
  const provider = selectedProvider();
  const gateway = provider === "freellmapi";
  const info = state.providers[provider] || {};
  const signature = JSON.stringify({
    provider, quick: info.quick_model, deep: info.deep_model, configured: info.configured
  });
  if (signature !== providerOptionsSignature) {
    providerOptionsSignature = signature;
    if (gateway) {
      fillModelOptions($("quickModel"), info.quick_model
        ? [[info.quick_model, info.quick_model + " · Approved quick tier"]] : [],
        "Configure gateway quick model on server");
      fillModelOptions($("deepModel"), info.deep_model
        ? [[info.deep_model, info.deep_model + " · Approved deep tier"]] : [],
        "Configure gateway deep model on server");
    } else {
      fillModelOptions($("quickModel"), GEMINI_MODELS, "Gemini model unavailable");
      fillModelOptions($("deepModel"), GEMINI_MODELS, "Gemini model unavailable");
    }
  }
  state.configured = !!info.configured;
  $("gatewaySettings").hidden = !gateway;
  $("modelProviderLabel").textContent = gateway ? "FreeLLMAPI · Server-approved models" : "Google Gemini";
  $("gatewayEndpoint").textContent = info.endpoint || "Configure private gateway on server";
  if (gateway && (!info.configured || info.configuration_error)) {
    $("gatewayConnectionStatus").textContent =
      info.configuration_error ||
      "Set FREELLMAPI_API_KEY, FREELLMAPI_QUICK_MODEL and FREELLMAPI_DEEP_MODEL in Codespaces, then restart the server.";
  } else if (gateway) {
    $("gatewayConnectionStatus").textContent =
      "Server settings are present. Test the connection; tool-calling and model compatibility remain unverified.";
  }
  $("providerQuotaNote").textContent = gateway
    ? "FreeLLMAPI controls provider quotas and fallback. Only operator-approved models/profiles are used. Do not expose either dashboard publicly; one analysis at a time."
    : "Full coverage starts analysts concurrently. Gemini calls are paced at up to 6/min within this server; API retries and other projects can still exceed quota. One run at a time.";
  $("providerLabel").textContent = state.configured
    ? (gateway ? "FreeLLMAPI configured" : "Gemini configured")
    : (gateway ? "FreeLLMAPI setup needed" : "Gemini not configured");
  $("providerPulse").classList.toggle("off", !state.configured);
  renderProgress();
}
async function testGateway() {
  $("gatewayTest").disabled = true;
  $("gatewayConnectionStatus").textContent = "Checking authenticated gateway model discovery...";
  try {
    const response = await fetch("/api/gateway/test", {
      method: "POST", headers: {"X-Requested-With": "TradingAgentsWeb"}
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Gateway test failed.");
    $("gatewayConnectionStatus").textContent =
      "Authenticated connection passed. " + data.ready_models_listed +
      " ready model entries; quick profile listed: " +
      (data.quick_model_listed ? "yes" : "no") +
      "; deep profile listed: " +
      (data.deep_model_listed ? "yes" : "no") +
      ". Tool calling and financial analysis still need separate tests.";
  } catch (error) {
    $("gatewayConnectionStatus").textContent = error.message || "Gateway connection failed.";
  } finally {
    $("gatewayTest").disabled = false;
  }
}

function renderUsage() {
  const job = state.job || {};
  const usage = job.usage || {};
  $("inputTokens").textContent = num(usage.input_tokens);
  $("outputTokens").textContent = num(usage.output_tokens);
  $("modelCalls").textContent = num(usage.calls);
  const gateway = job.provider
    ? job.provider === "freellmapi" : selectedProvider() === "freellmapi";
  $("usageProviderTitle").textContent = gateway ? "GATEWAY TOKEN USAGE" : "GEMINI TOKEN USAGE";
  $("estimatedCost").textContent = gateway || usage.estimated_paid_usd === null
    ? "Not available" : dollar(usage.estimated_paid_usd);
  $("usageStatus").textContent = job.status === "running" ? "Live usage" :
    usage.calls ? "Latest run" : "No usage yet";
  $("costIndicator").textContent = gateway ? "Provider billing unknown" :
    usage.pricing_complete && usage.calls ? "Reference only" : "Partial / unverified";
  const unreported = usage.unreported_calls || 0;
  $("usageNote").textContent = gateway
    ? (usage.note || "Check FreeLLMAPI upstream logs for model routing, fallback and actual cost.")
    : usage.calls
      ? "Standard paid-tier reference only; actual charges may differ. " +
        (unreported ? unreported + " model call(s) had no token usage metadata. " : "") +
        (!usage.pricing_complete ? "Not all model usage was priced. " : "") +
        "Does not include some retries, external data or grounding fees."
      : "Token counts appear as Gemini calls finish. A $0 estimate is not proof of no charges.";
  const usageLink = $("providerUsageLink");
  usageLink.href = gateway
    ? "https://github.com/varadachiru/freellmapi"
    : "https://ai.google.dev/gemini-api/docs/pricing";
  usageLink.textContent = gateway
    ? "FreeLLMAPI gateway and usage documentation ↗"
    : "Official Gemini pricing ↗";
}
function renderQuality() {
  const jobReport = state.selectedReport === "job";
  const q = jobReport ? (state.job || {}).quality : state.historicalQuality;
  const panel = $("qualityPanel");
  panel.hidden = !q;
  if (!q) return;
  $("qualityCoverage").textContent = q.sections_present + " of " +
    q.sections_requested + " requested analyst reports";
  const details = $("qualityDetails");
  details.replaceChildren();
  for (const report of (q.analysts || [])) {
    const line = document.createElement("span");
    line.className = "quality-pill" + (report.present ? " present" : " missing");
    line.textContent = (report.present ? "✓ " : "! ") + report.label;
    details.append(line);
  }
  const fields = [
    ["Target", q.explicit_price_target || "Not provided"],
    ["Horizon", q.explicit_time_horizon || "Not provided"],
    ["Risk debate", q.risk_debate_present ? "Recorded" : "Not found"],
  ];
  const row = document.createElement("div");
  row.className = "quality-facts";
  for (const [label, value] of fields) {
    const item = document.createElement("span");
    const title = document.createElement("small");
    title.textContent = label;
    const content = document.createElement("strong");
    content.textContent = value;
    item.append(title, content);
    row.append(item);
  }
  details.append(row);
  if (q.warnings && q.warnings.length) {
    const warning = document.createElement("p");
    warning.className = "quality-warning";
    warning.textContent = q.warnings.join(" ");
    details.append(warning);
  }
}

function _make(tag, textValue, className) {
  const element = document.createElement(tag);
  if (textValue !== undefined && textValue !== null) element.textContent = String(textValue);
  if (className) element.className = className;
  return element;
}
function renderAudit() {
  const current = state.selectedReport === "job";
  const evidence = current ? (state.job || {}).evidence : state.historicalEvidence;
  const risk = current ? (state.job || {}).risk : state.historicalRisk;
  const selected = current
    ? ((state.job || {}).analysts || [])
    : ((state.historicalQuality || {}).analysts || []).map((a) => a.analyst);
  const technicalOnly = selected.length === 1 && selected[0] === "market";
  const evidencePanel = $("auditPanel");
  evidencePanel.hidden = !evidence;
  if (evidence) {
    const sourceList = evidence.sources || [];
    const companyLinks = sourceList.filter((item) => item.tool === "get_news").length;
    const globalLinks = sourceList.filter((item) => item.tool === "get_global_news").length;
    $("auditSourceCount").textContent = companyLinks + " company-feed candidates · " +
      globalLinks + " global-context links · " +
      (evidence.dated_source_count || 0) + " dated";
    const snapshot = evidence.market_snapshot;
    $("auditMarket").textContent = snapshot
      ? "Verified tool snapshot: " + snapshot.date + " close " +
        Number(snapshot.close).toLocaleString(undefined, {minimumFractionDigits: 2}) +
        " (Yahoo Finance historical OHLCV; not exchange-confirmed)"
      : (evidence.market_snapshot_expected === false ||
          (state.selectedReport === "job"
            ? !((state.job || {}).analysts || []).includes("market")
            : !((state.historicalQuality || {}).analysts || [])
                .some((item) => item.analyst === "market"))
        ? "A market-price snapshot is not expected when Technical analysis is not selected."
        : "Market-price snapshot not captured — quoted prices are not independently checked.");
    const rows = $("auditSources");
    rows.replaceChildren();
    // Ticker-scoped tool results first; global headlines are context, not
    // company evidence. Neither classification verifies article relevance.
    const sortedSources = [...sourceList].sort((a, b) =>
      Number(b.tool === "get_news") - Number(a.tool === "get_news"));
    for (const entry of sortedSources.slice(0, 12)) {
      // Never render model/tool HTML. Permit only external http(s) sources.
      const url = String(entry.url || "");
      let valid = false;
      try {
        const parsed = new URL(url);
        valid = ["https:", "http:"].includes(parsed.protocol) &&
          !parsed.username && !parsed.password &&
          !["localhost", "127.0.0.1"].includes(parsed.hostname);
      } catch (_) {
        valid = false;
      }
      if (!valid) continue;
      const line = _make("div", null, "audit-source");
      const anchor = _make("a", entry.title || parsedHostname(url));
      anchor.href = url;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      const origin = entry.tool === "get_news"
        ? "Ticker-scoped feed (relevance unverified)"
        : entry.tool === "get_global_news"
          ? "Global context · not company-specific"
          : "Other data source";
      const subtitle = _make("small",
        origin + " · " + (entry.publisher || "Unspecified") + " · " +
        (entry.published || "publication date unavailable"));
      line.append(anchor, subtitle);
      rows.append(line);
    }
    if (!(evidence.sources || []).length) {
      rows.append(_make("p", technicalOnly
        ? "Technical-only analysis uses the dated market snapshot; article links are not expected."
        : (evidence.source_note || "No source URLs were returned by tools during this run.")));
    }
    const warnings = (evidence.warnings || []).filter((warning) =>
      !technicalOnly || !warning.startsWith("No article or document URLs were captured"));
    const filteredWarnings = warnings.filter((warning) =>
      evidence.market_snapshot_expected !== false ||
      !warning.startsWith("No verified historical OHLCV"));
    $("auditWarnings").textContent = filteredWarnings.join(" ");
  }
  const riskPanel = $("riskPanel");
  riskPanel.hidden = !risk;
  if (risk) {
    $("auditDirection").textContent = risk.direction || "No directional position";
    const grid = $("auditRiskGrid");
    grid.replaceChildren();
    const data = [
      ["Entry", risk.entry, ""],
      ["Stop loss", risk.stop_loss, ""],
      ["Target", risk.price_target, ""],
      ["Risk to stop", risk.risk_pct, "%"],
      ["Reward to target", risk.reward_pct, "%"],
      ["Reward : risk", risk.reward_to_risk, ":1"],
    ];
    for (const [label, value, unit] of data) {
      const cell = _make("div", null, "audit-risk-cell");
      cell.append(_make("small", label));
      cell.append(_make("strong", value === null || value === undefined ? "Not available" :
        Number(value).toLocaleString(undefined, {maximumFractionDigits: 2}) + unit));
      grid.append(cell);
    }
    $("auditRiskWarnings").textContent = (risk.warnings || []).join(" ");
  }
}
function parsedHostname(url) {
  try { return new URL(url).hostname; }
  catch (_) { return "Source"; }
}


function renderIntegrity() {
  const current = state.selectedReport === "job";
  const integrity = current
    ? (state.job || {}).integrity
    : state.historicalIntegrity;
  const panel = $("integrityPanel");
  panel.hidden = !integrity;
  if (!integrity) return;

  const coverage = integrity.source_coverage || {};
  const oldNewsAudit = (integrity.selected_analysts || []).includes("news") &&
    coverage.company_feed_links === undefined;
  $("integrityStatus").textContent = oldNewsAudit
    ? "Older audit · company news not checked"
    : (integrity.status === "needs_review" ? "Review needed" : "Limited checks passed");
  $("integrityStatus").className =
    "integrity-status " + (oldNewsAudit || integrity.status === "needs_review" ? "review" : "limited");

  const metrics = $("integrityMetrics");
  metrics.replaceChildren();
  const sourceEvidence = current
    ? (state.job || {}).evidence : state.historicalEvidence;
  const savedSources = (sourceEvidence || {}).sources || [];
  const countTool = (name, datedOnly) => savedSources.filter((item) =>
    item.tool === name && (!datedOnly || !!item.published)).length;
  for (const [label, value] of [
    ["Ticker-feed candidates", coverage.company_feed_links ?? countTool("get_news", false)],
    ["Dated ticker-feed", coverage.dated_company_feed_links ?? countTool("get_news", true)],
    ["Global-context links", coverage.global_context_links ?? countTool("get_global_news", false)],
    ["Company news calls", coverage.company_news_tool_calls || 0],
    ["Financial tool calls", coverage.fundamental_tool_calls || 0],
    ["Future links excluded", coverage.future_dated_links_excluded || 0],
  ]) {
    const box = _make("div", null, "integrity-metric");
    box.append(_make("small", label), _make("strong", value));
    metrics.append(box);
  }

  const checks = $("integrityChecks");
  checks.replaceChildren();
  for (const item of (integrity.citation_checks || [])) {
    const row = _make("div", null, "integrity-check");
    row.append(_make("strong", item.analyst + " · " +
      item.tool_traced_urls + " of " + item.cited_urls + " URLs traced to tools"));
    row.append(_make("small",
      item.cited_urls
        ? "Tool traceability does not prove factual support for a claim."
        : "No explicit URLs were cited by this analyst."));
    checks.append(row);
  }
  const price = integrity.market_comparison || {};
  if (price.has_snapshot) {
    const line = _make("div", null, "integrity-check");
    line.append(_make("strong", "Historical close comparison"));
    line.append(_make("small", (price.matching_current_price_mentions || 0) +
      " matching current-price statement(s); " +
      (price.review_flags || []).length + " potential discrepancy flag(s)."));
    checks.append(line);
  }
  const alignment = integrity.decision_alignment || {};
  if (alignment.research_team || alignment.trader_action) {
    const line = _make("div", null, "integrity-check");
    line.append(_make("strong", "Decision trail"));
    const recommendation = alignment.research_team || "Not stated";
    const action = alignment.trader_action || "Not stated";
    const final = alignment.portfolio_rating || "Not stated";
    line.append(_make("small", "Research: " + recommendation +
      " · Trader: " + action + " · Final: " + final +
      (alignment.research_to_portfolio_changed ?
        ". The final position differs from the research recommendation; review the risk rationale." : ".")));
    checks.append(line);
  }
  const issues = $("integrityWarnings");
  issues.replaceChildren();
  const warnings = integrity.warnings || [];
  if (oldNewsAudit) {
    issues.append(_make("p",
      "This saved run predates the company/global news distinction. Its old integrity result did not assess ticker-scoped news coverage; open a newly generated report to see that check."));
  }
  if (!warnings.length && !oldNewsAudit) {
    issues.append(_make("p",
      "No discrepancies detected by these limited checks. This is not an accuracy guarantee."));
  } else {
    for (const warning of warnings.slice(0, 15)) {
      issues.append(_make("p", "• " + warning));
    }
  }
}

function renderMarketNotice() {
  $("marketNotice").hidden = !/\.(NS|BO)$/i.test($("ticker").value.trim());
}

function renderProgress() {
  const job = state.job || {};
  const status = job.status || "idle";
  const badge = $("runBadge");
  badge.className = "status-badge " + statusClass(status);
  badge.textContent = status === "idle" ? "Not started" :
    status === "running" ? "Running" : status === "completed" ? "Completed" : "Failed";
  $("runTicker").textContent = job.ticker || "No active company";
  $("runDate").textContent = job.date ? "Analysis date: " + job.date : "Choose a ticker to begin";
  $("runStatusIcon").textContent = status === "completed" ? "✓" :
    status === "failed" ? "!" : status === "running" ? "◉" : "◌";

  const nAnalysts = (job.analysts || []).length;
  const count = (job.completed || []).length;
  const total = nAnalysts + 3;
  const ratio = status === "completed" ? 1 : Math.min(0.96, count / Math.max(1, total));
  const percent = Math.round(ratio * 100);
  $("progressFill").style.width = percent + "%";
  $("progressText").textContent = status === "running" ?
    (job.phase || "Analyzing company") :
    status === "failed" ? "Analysis interrupted" :
    status === "completed" ? "Research report saved" : "Ready for analysis";
  $("progressPercent").textContent = percent + "%";

  const sections = job.sections || {};
  const stages = [
    ["Analyst research", nAnalysts && (job.analysts || []).every((name) => !!sections[({
      market: "market_report", social: "sentiment_report", news: "news_report",
      fundamentals: "fundamentals_report"
    })[name]]), "Technical, news, sentiment and financial research"],
    ["Research debate", !!sections.investment_plan, "Bull and bear cases, investment thesis"],
    ["Trader proposal", !!sections.trader_investment_plan, "Proposed positioning"],
    ["Risk & final decision", !!sections.final_trade_decision, "Portfolio manager reviews risk"],
    ["Report saved", status === "completed", "Markdown file stored in Codespaces"],
  ];
  const timeline = $("timeline");
  timeline.replaceChildren();
  stages.forEach((step, i) => {
    const item = document.createElement("div");
    const isActive = status === "running" && !step[1] &&
      stages.slice(0, i).every((earlier) => earlier[1]);
    item.className = "timeline-item " + (step[1] ? "done" : isActive ? "active" : "");
    const marker = document.createElement("span");
    marker.className = "timeline-marker";
    marker.textContent = step[1] ? "✓" : isActive ? "•" : String(i + 1);
    const body = document.createElement("div");
    const label = document.createElement("strong");
    label.textContent = step[0];
    const detail = document.createElement("small");
    detail.textContent = step[2];
    body.append(label, detail);
    item.append(marker, body);
    timeline.append(item);
  });
  const decision = $("decisionBox");
  decision.hidden = status !== "completed" || !job.rating;
  $("decisionText").textContent = job.rating || "";
  const error = $("errorBox");
  error.hidden = status !== "failed";
  error.textContent = job.error || "Analysis failed. Inspect your Codespaces terminal.";
  $("runButton").disabled = !state.configured || status === "running";
  $("runLabel").textContent = !state.configured
    ? (selectedProvider() === "freellmapi" ? "Configure FreeLLMAPI gateway" : "Configure Gemini API key")
    : status === "running" ? "Analysis running..." : "Run AI analysis";
  renderUsage();
}
function activeReportTabs(job) {
  if (!job.analysts || !job.analysts.length) return [["full", "Full saved report"]];
  const analystKeys = {
    market_report: "market",
    news_report: "news",
    sentiment_report: "social",
    fundamentals_report: "fundamentals",
  };
  return REPORTS.filter(([key]) => !analystKeys[key] || job.analysts.includes(analystKeys[key]));
}

// Render only a small safe Markdown subset. Generated research is untrusted:
// create text nodes and semantic elements, never put model output in innerHTML.
function appendInlineReportText(parent, text) {
  const pieces = text.split(/(\*\*[^*\n]+\*\*)/g);
  for (const piece of pieces) {
    if (!piece) continue;
    if (piece.startsWith("**") && piece.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = piece.slice(2, -2);
      parent.append(strong);
    } else {
      parent.append(document.createTextNode(piece));
    }
  }
}
function tableCells(line) {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|")) text = text.slice(0, -1);
  return text.split("|").map((cell) => cell.trim());
}
function isTableDivider(line, columns) {
  if (!line || !line.includes("|")) return false;
  const cells = tableCells(line);
  return cells.length === columns &&
    cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}
function renderReportText(container, rawText) {
  container.replaceChildren();
  let paragraph = null;
  const lines = rawText.split(/\r?\n/);
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (!line.trim()) {
      paragraph = null;
      continue;
    }
    // Safe Markdown tables: generated values are text nodes, never HTML.
    if (line.includes("|")) {
      const headers = tableCells(line);
      if (headers.length > 1 && headers.length <= 12 &&
          isTableDivider(lines[i + 1], headers.length)) {
        const wrap = document.createElement("div");
        wrap.className = "report-table-wrap";
        const table = document.createElement("table");
        table.className = "report-table";
        const thead = document.createElement("thead");
        const headRow = document.createElement("tr");
        for (const value of headers) {
          const th = document.createElement("th");
          appendInlineReportText(th, value);
          headRow.append(th);
        }
        thead.append(headRow);
        table.append(thead);
        const tbody = document.createElement("tbody");
        i += 2; // Header plus divider consumed.
        while (i < lines.length && lines[i].includes("|")) {
          const cells = tableCells(lines[i]);
          if (cells.length !== headers.length) break;
          const row = document.createElement("tr");
          for (const value of cells) {
            const td = document.createElement("td");
            appendInlineReportText(td, value);
            row.append(td);
          }
          tbody.append(row);
          i += 1;
        }
        table.append(tbody);
        wrap.append(table);
        container.append(wrap);
        paragraph = null;
        i -= 1;
        continue;
      }
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const title = document.createElement(heading[1].length < 3 ? "h3" : "h4");
      appendInlineReportText(title, heading[2]);
      container.append(title);
      paragraph = null;
      continue;
    }
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      const item = document.createElement("p");
      item.className = "report-bullet";
      item.append("• ");
      appendInlineReportText(item, bullet[1]);
      container.append(item);
      paragraph = null;
      continue;
    }
    if (!paragraph) {
      paragraph = document.createElement("p");
      container.append(paragraph);
    } else {
      paragraph.append(document.createTextNode(" "));
    }
    appendInlineReportText(paragraph, line.trim());
  }
}

function renderReports() {
  const job = state.job || {};
  renderQuality();
  renderAudit();
  renderIntegrity();
  const jobReport = state.selectedReport === "job";
  const sections = selectedJobSections();
  const tabs = jobReport ? activeReportTabs(job) : [["full", "Saved report"]];
  if (!tabs.some(([key]) => key === state.selectedTab)) state.selectedTab = tabs[0][0];
  const tabsEl = $("reportTabs");
  tabsEl.replaceChildren();
  tabs.forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "report-tab" + (state.selectedTab === key ? " selected" : "");
    btn.textContent = label;
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", state.selectedTab === key ? "true" : "false");
    btn.addEventListener("click", () => {
      state.selectedTab = key;
      renderReports();
    });
    tabsEl.append(btn);
  });

  const label = jobReport
    ? (job.ticker ? job.ticker + " · " + (job.status || "Pending") : "No report selected")
    : (state.selectedReport || "Saved report");
  $("reportStatus").textContent = label;
  const savedReport = jobReport ? state.savedText : state.historicalText;
  let content = state.selectedTab === "full" ? savedReport : sections[state.selectedTab];
  if (!content) {
    content = state.selectedTab === "full"
      ? "The completed report will be available when the run finishes."
      : "This section has not completed yet. The live research output will appear here.";
  }
  renderReportText($("reportContent"), content);
  const id = jobReport ? job.report_id : state.selectedReport;
  const link = $("downloadReport");
  link.hidden = !id;
  if (id) link.href = "/api/reports/" + encodeURIComponent(id);
}
function renderHistory() {
  const root = $("historyList");
  root.replaceChildren();
  if (!state.archive.length) {
    const msg = document.createElement("span");
    msg.className = "muted";
    msg.textContent = "No completed reports yet. Run an analysis to create one.";
    root.append(msg);
    return;
  }
  state.archive.forEach((item) => {
    const button = document.createElement("button");
    button.className = "history-item";
    button.type = "button";
    const left = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = item.ticker || item.id;
    const date = document.createElement("small");
    date.textContent = formatDate(item.created_at);
    left.append(title, date);
    if (item.usage && item.usage.calls) {
      const cost = document.createElement("small");
      const costText = item.usage.estimated_paid_usd === null
        ? "cost not available" : dollar(item.usage.estimated_paid_usd) + " reference cost";
      cost.textContent = num(item.usage.input_tokens + item.usage.output_tokens) +
        " tokens · " + costText;
      left.append(cost);
    }
    const arrow = document.createElement("span");
    arrow.className = "right";
    arrow.textContent = "↗";
    button.append(left, arrow);
    button.addEventListener("click", async () => {
      await openReport(item.id, false);
      $("reports").scrollIntoView({behavior: "smooth"});
    });
    root.append(button);
  });
}
async function openReport(id, isCurrent) {
  try {
    const response = await fetch("/api/reports/" + encodeURIComponent(id), {cache: "no-store"});
    if (!response.ok) throw new Error("The saved report could not be loaded.");
    const text = await response.text();
    if (isCurrent) {
      state.savedText = text;
      if (state.selectedReport === "job") renderReports();
    } else {
      state.selectedReport = id;
      state.selectedTab = "full";
      state.historicalText = text;
      const item = state.archive.find((item) => item.id === id) || {};
      state.historicalQuality = item.quality || null;
      state.historicalEvidence = item.evidence || null;
      state.historicalRisk = item.risk || null;
      state.historicalIntegrity = item.integrity || null;
      renderReports();
    }
  } catch (err) {
    setFormError(err.message);
  }
}
async function refreshHistory() {
  try {
    const res = await fetch("/api/reports", {cache: "no-store"});
    if (res.ok) state.archive = await res.json();
    renderHistory();
  } catch (_) {
    $("historyList").textContent = "Could not load saved reports.";
  }
}
async function refreshStatus() {
  try {
    const res = await fetch("/api/status", {cache: "no-store"});
    if (!res.ok) throw new Error("Cannot reach research server");
    const payload = await res.json();
    state.providers = payload.providers || {
      google: {configured: !!payload.configured, models: GEMINI_MODELS.map(([id]) => id)}
    };
    state.job = payload.job && Object.keys(payload.job).length ? payload.job : null;
    updateProviderControls();
    if (state.job && state.job.status === "completed" && state.job.report_id &&
      state.viewedReportId !== state.job.report_id) {
      state.viewedReportId = state.job.report_id;
      await openReport(state.job.report_id, true);
      await refreshHistory();
    }
    renderProgress();
    renderReports();
  } catch (_) {
    state.configured = false;
    $("providerLabel").textContent = "Server disconnected";
    $("providerPulse").classList.add("off");
    $("runButton").disabled = true;
    $("runLabel").textContent = "Server disconnected";
  }
}
async function startAnalysis(event) {
  event.preventDefault();
  setFormError("");
  const analysts = selectedAnalysts();
  if (!analysts.length) {
    setFormError("Select at least one analyst.");
    return;
  }
  const payload = {
    ticker: $("ticker").value.trim().toUpperCase(),
    date: $("analysisDate").value,
    analysts,
    quick_model: $("quickModel").value,
    deep_model: $("deepModel").value,
    rounds: Number($("rounds").value),
    provider: selectedProvider(),
  };
  $("runButton").disabled = true;
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Requested-With": "TradingAgentsWeb"},
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not start analysis.");
    state.job = data.job;
    state.selectedReport = "job";
    state.selectedTab = "final_trade_decision";
    state.savedText = "";
    state.viewedReportId = null;
    state.historicalQuality = null;
    state.historicalEvidence = null;
    state.historicalRisk = null;
    state.historicalIntegrity = null;
    renderProgress();
    renderReports();
    $("progress").scrollIntoView({behavior: "smooth", block: "nearest"});
  } catch (err) {
    setFormError(err.message);
    $("runButton").disabled = !state.configured;
  }
}

document.querySelectorAll("[data-preset]").forEach((button) => {
  button.addEventListener("click", () => setPreset(button.dataset.preset));
});
document.querySelectorAll('input[name="analyst"]').forEach((input) => {
  input.addEventListener("change", refreshPreset);
});
$("researchForm").addEventListener("submit", startAnalysis);
$("aiProvider").addEventListener("change", () => {
  updateProviderControls();
  setFormError("");
});
$("gatewayTest").addEventListener("click", testGateway);
$("refreshHistory").addEventListener("click", refreshHistory);
$("ticker").addEventListener("input", renderMarketNotice);
$("analysisDate").value = [
  new Date().getFullYear(),
  String(new Date().getMonth() + 1).padStart(2, "0"),
  String(new Date().getDate()).padStart(2, "0"),
].join("-");
$("analysisDate").max = $("analysisDate").value;
refreshPreset();
renderMarketNotice();
renderProgress();
renderReports();
refreshHistory();
refreshStatus();
setInterval(refreshStatus, 3000);
