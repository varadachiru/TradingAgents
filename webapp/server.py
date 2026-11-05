"""Local-only TradingAgents research dashboard.

Run with: python -m webapp.server
The browser never receives Gemini credentials. Keep the forwarded Codespaces port private.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import threading
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from tradingagents.default_config import DEFAULT_CONFIG

STATIC_DIR = Path(__file__).parent / "static"
ANALYSTS = ("market", "social", "news", "fundamentals")
MODELS = ("gemini-3.1-flash-lite", "gemini-3.8-flash")
REPORT_KEYS = {
    "market_report": "Market analyst",
    "sentiment_report": "Sentiment analyst",
    "news_report": "News analyst",
    "fundamentals_report": "Fundamentals analyst",
    "investment_plan": "Research manager",
    "trader_investment_plan": "Trader",
    "final_trade_decision": "Portfolio manager",
}
TICKER_RE = re.compile(r"^[A-Za-z0-9^.=_-]{1,32}$")
REPORT_RE = re.compile(r"^[A-Za-z0-9._=-]{1,90}$")
MAX_BODY_BYTES = 4096


class RequestError(ValueError):
    """Safe error text that may be shown in the browser."""


def validate_run(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise RequestError("Expected a JSON object.")
    ticker = str(payload.get("ticker", "")).strip().upper()
    if not TICKER_RE.fullmatch(ticker):
        raise RequestError("Enter a valid ticker (e.g. AAPL or RELIANCE.NS).")
    raw_date = payload.get("date", "")
    if not isinstance(raw_date, str):
        raise RequestError("Choose a valid YYYY-MM-DD analysis date.")
    try:
        day = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise RequestError("Choose a valid YYYY-MM-DD analysis date.") from exc
    if day.isoformat() != raw_date or day > date.today():
        raise RequestError("Analysis date must be today or earlier (YYYY-MM-DD).")
    selected = payload.get("analysts", ["market"])
    if not isinstance(selected, list) or not selected or len(selected) > len(ANALYSTS):
        raise RequestError("Select at least one analyst.")
    if any(not isinstance(x, str) or x not in ANALYSTS for x in selected):
        raise RequestError("Unknown analyst selected.")
    if len(set(selected)) != len(selected):
        raise RequestError("Select each analyst only once.")
    quick = payload.get("quick_model", MODELS[0])
    deep = payload.get("deep_model", MODELS[0])
    if quick not in MODELS or deep not in MODELS:
        raise RequestError("Choose one of the supported Gemini models.")
    rounds = payload.get("rounds", 1)
    if type(rounds) is not int or rounds not in (1, 2):
        raise RequestError("Research rounds must be 1 or 2.")
    return {
        "ticker": ticker,
        "date": raw_date,
        "analysts": [a for a in ANALYSTS if a in selected],
        "quick_model": quick,
        "deep_model": deep,
        "rounds": rounds,
    }


def results_root() -> Path:
    return Path(DEFAULT_CONFIG["results_dir"]).expanduser() / "reports"


def report_directory(report_id: str) -> Path | None:
    if not REPORT_RE.fullmatch(report_id):
        return None
    root = results_root().resolve()
    target = (root / report_id).resolve()
    if target.parent != root or not target.is_dir():
        return None
    return target


def recent_reports(limit: int = 12) -> list[dict]:
    root = results_root()
    if not root.exists():
        return []
    found = []
    for folder in root.iterdir():
        if not folder.is_dir() or not REPORT_RE.fullmatch(folder.name):
            continue
        report = folder / "complete_report.md"
        if not report.is_file():
            continue
        found.append({
            "id": folder.name,
            "created_at": datetime.fromtimestamp(report.stat().st_mtime, timezone.utc).isoformat(),
            "ticker": folder.name.split("_", 1)[0],
        })
    return sorted(found, key=lambda r: r["created_at"], reverse=True)[:limit]


class ResearchManager:
    """One active analysis, with a thread-safe snapshot for browser polling."""

    def __init__(self):
        self.lock = threading.Lock()
        self.current: dict | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.current) if self.current else {}

    def _update(self, **changes) -> None:
        with self.lock:
            if self.current is not None:
                self.current.update(changes)

    def _stage(self, label: str, report_key: str | None = None, report_text: str = "") -> None:
        with self.lock:
            if self.current is None:
                return
            state = self.current
            if report_key and report_text:
                excerpt = report_text[:20000]
                # Top-level LangGraph values are full snapshots, not deltas.
                # Do not re-emit completed stages on every later graph step.
                if state["sections"].get(report_key) == excerpt:
                    return
                state["sections"][report_key] = excerpt
                if report_key not in state["completed"]:
                    state["completed"].append(report_key)
            if label != state["phase"]:
                state["phase"] = label
                state["events"].append({
                    "at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                    "message": label,
                })
                state["events"] = state["events"][-40:]

    def start(self, payload: object) -> dict:
        options = validate_run(payload)
        if not os.environ.get("GOOGLE_API_KEY"):
            raise RequestError(
                "GOOGLE_API_KEY is missing. Add it as a Codespaces secret and restart the Codespace."
            )
        with self.lock:
            if self.current and self.current["status"] == "running":
                raise RequestError("An analysis is already running. Let it finish before starting another.")
            self.current = {
                **options,
                "status": "running",
                "phase": "Preparing data and AI agents",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "completed": [],
                "sections": {},
                "events": [{
                    "at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                    "message": "Analysis started",
                }],
                "rating": None,
                "report_id": None,
                "error": None,
            }
            snapshot = copy.deepcopy(self.current)
        threading.Thread(target=self._execute, args=(options,), daemon=True).start()
        return snapshot

    def _execute(self, options: dict) -> None:
        """Run the native LangGraph pipeline, including checkpoint and memory handling."""
        graph = None
        try:
            from tradingagents.agents.rating import run_rating
            from tradingagents.graph.trading_graph import TradingAgentsGraph

            config = copy.deepcopy(DEFAULT_CONFIG)
            config.update({
                "llm_provider": "google",
                "backend_url": None,
                "quick_think_provider": None,
                "deep_think_provider": None,
                "quick_think_llm": options["quick_model"],
                "deep_think_llm": options["deep_model"],
                "google_thinking_level": "low",
                "max_debate_rounds": options["rounds"],
                "max_risk_discuss_rounds": options["rounds"],
                "llm_max_retries": 2,
                "output_language": "English",
                "checkpoint_enabled": True,
            })
            graph = TradingAgentsGraph(
                selected_analysts=tuple(options["analysts"]), debug=False, config=config
            )
            ticker, day = options["ticker"], options["date"]
            initial = graph.create_run_state(ticker, day)
            args = graph.propagator.get_graph_args()
            checkpoint_id = graph.begin_checkpoint(ticker, day)
            try:
                if checkpoint_id is not None:
                    args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_id
                    if graph._resuming:
                        self._stage("Resuming saved checkpoint")
                final_state = {}
                for _messages, delta in graph.stream_run(graph.checkpoint_input(initial), **args):
                    if not isinstance(delta, dict):
                        continue
                    final_state.update(delta)
                    for key, label in REPORT_KEYS.items():
                        content = delta.get(key)
                        if isinstance(content, str) and content.strip():
                            self._stage(label + " completed", key, content)
                    debate = delta.get("investment_debate_state")
                    if isinstance(debate, dict) and (debate.get("bull_history") or debate.get("bear_history")):
                        self._stage("Bull / bear research debate")
                    risk = delta.get("risk_debate_state")
                    if isinstance(risk, dict) and (
                        risk.get("aggressive_history") or risk.get("neutral_history")
                        or risk.get("conservative_history")
                    ):
                        self._stage("Risk management debate")
                if not final_state.get("final_trade_decision"):
                    raise RuntimeError("Run ended without a final portfolio decision.")
                graph.record_decision(ticker, day, final_state)
                graph.clear_checkpoint_on_success(ticker, day)
            finally:
                graph.end_checkpoint()

            saved = graph.save_reports(final_state, ticker)
            self._update(
                status="completed",
                phase="Analysis complete",
                finished_at=datetime.now(timezone.utc).isoformat(),
                rating=run_rating(final_state),
                report_id=saved.parent.name,
            )
            self._stage("Analysis complete")
        except Exception as exc:
            error = str(exc)
            key = os.environ.get("GOOGLE_API_KEY")
            if key:
                error = error.replace(key, "[redacted]")
            self._update(
                status="failed",
                phase="Analysis failed",
                finished_at=datetime.now(timezone.utc).isoformat(),
                error=(type(exc).__name__ + ": " + error)[:2000],
            )
            self._stage("Analysis failed")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "TradingAgentsWeb/0.1"

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict | list) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self._file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/app.css":
            return self._file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
        if path == "/app.js":
            return self._file(STATIC_DIR / "app.js", "text/javascript; charset=utf-8")
        if path == "/api/status":
            return self._json(200, {
                "configured": bool(os.environ.get("GOOGLE_API_KEY")),
                "job": self.server.manager.snapshot(),
            })
        if path == "/api/reports":
            return self._json(200, recent_reports())
        if path.startswith("/api/reports/"):
            report_id = unquote(path.removeprefix("/api/reports/"))
            folder = report_directory(report_id)
            if folder:
                report = folder / "complete_report.md"
                if report.is_file():
                    # No user-supplied filesystem paths; serve as inert text, never raw HTML.
                    return self._send(200, report.read_bytes()[:1024 * 1024], "text/plain; charset=utf-8")
        return self._json(404, {"error": "Not found"})

    def _file(self, file: Path, mime: str) -> None:
        try:
            content = file.read_bytes()
        except OSError:
            return self._json(404, {"error": "Not found"})
        return self._send(200, content, mime)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/runs":
            return self._json(404, {"error": "Not found"})
        # Cross-site forms cannot send this custom header; do not enable CORS.
        if self.headers.get("X-Requested-With") != "TradingAgentsWeb":
            return self._json(403, {"error": "Browser request header is missing."})
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._json(415, {"error": "Expected application/json."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if not 0 < size <= MAX_BODY_BYTES:
            return self._json(413, {"error": "Invalid request size."})
        try:
            payload = json.loads(self.rfile.read(size))
            snapshot = self.server.manager.start(payload)
            return self._json(202, {"job": snapshot})
        except ValueError as exc:
            message = str(exc) if isinstance(exc, RequestError) else "Invalid request."
            return self._json(409 if "already running" in message else 400, {"error": message})

    def log_message(self, format: str, *args) -> None:
        # Avoid logging user-supplied URLs or request bodies.
        if self.path.startswith("/api"):
            return
        super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Private local TradingAgents web dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.manager = ResearchManager()
    print("TradingAgents dashboard: http://localhost:%d" % args.port, flush=True)
    print("Keep the Codespaces forwarded port PRIVATE. Do not publish without authentication.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
