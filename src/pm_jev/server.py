"""MCP server exposing the PM decision sets as tools.

Runs over stdio. Nothing may be printed to stdout except MCP protocol messages, so
all diagnostics go to stderr. Works with the mcp Python SDK 2.x (MCPServer) and 1.x
(FastMCP).
"""

from __future__ import annotations

import os
import sys
from typing import Any

from . import engine
from .backends import get_backend
from .config import load_config

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

INSTRUCTIONS = (
    "Fast, typed PM decisions backed by TypeSafe's Jev model. These tools DECIDE; they never write prose. "
    "Use them for bounded calls (classify, score, flag, check) and write any human-facing text yourself. "
    "Every result has an 'action': 'auto' means the answer is confident enough to act on, 'escalate_llm' "
    "means you should double-check it with your own reasoning, 'human' means the PM must decide. "
    "If 'backend' is 'mock', no API key is configured and answers are keyword heuristics: say so to the user "
    "and do not treat them as real judgments. Run check_sensitive before posting or sending anything."
)

mcp = _Server("pm-jev", instructions=INSTRUCTIONS)


@mcp.tool()
def backend_status() -> dict[str, Any]:
    """Report which backend is active (jev or mock), the model, and the confidence gates. Call this first in a session."""
    cfg = load_config()
    backend = get_backend()
    return {
        "backend": backend.name,
        "model": getattr(backend, "model", None),
        "config_file": os.environ.get("PM_JEV_CONFIG") or "packaged defaults",
        "gates": cfg["gates"],
        "redact_before_send": cfg.get("redact_before_send", True),
        "product_areas": list(cfg["product_areas"]),
        "note": None if backend.name == "jev" else "Mock mode: set the Jev API key in the plugin settings for real answers.",
    }


@mcp.tool()
def triage_feedback(text: str) -> dict[str, Any]:
    """Classify one piece of customer feedback (ticket, review, survey answer, Teams message): category, product area, sentiment, churn risk, urgency, and where to route it."""
    return engine.triage_feedback(text)


@mcp.tool()
def find_duplicates(text: str, backlog: list[dict[str, str]]) -> dict[str, Any]:
    """Check whether a new request duplicates an existing backlog item. backlog is a list of {"id", "title", "summary"} objects (up to 254 are compared)."""
    return engine.find_duplicates(text, backlog)


@mcp.tool()
def check_story_ready(story: str) -> dict[str, Any]:
    """Check a user story or ticket against a definition of ready: user, problem, acceptance criteria, success metric, single scope, and clarity. Returns the gaps."""
    return engine.check_story_ready(story)


@mcp.tool()
def flag_risks(change: str) -> dict[str, Any]:
    """Flag whether a proposed change touches billing, security, privacy, compliance, data migration, or breaking changes, and who should review it."""
    return engine.flag_risks(change)


@mcp.tool()
def score_priority(item: str, overrides: dict[str, int] | None = None) -> dict[str, Any]:
    """Score a backlog item on reach, impact, confidence and effort (levels 0-3 each) and combine them into a 0-100 priority. overrides sets factors the PM already knows, e.g. {"effort": 1}."""
    return engine.score_priority(item, overrides)


@mcp.tool()
def read_status(update: str) -> dict[str, Any]:
    """Read a standup or status update as on_track, at_risk or blocked, and flag help requests, date slips and scope changes."""
    return engine.read_status(update)


@mcp.tool()
def label_meeting_notes(notes: str) -> dict[str, Any]:
    """Label each line of meeting notes or a transcript as decision, action_item, open_question or fyi, and list action items with no owner."""
    return engine.label_meeting_notes(notes)


@mcp.tool()
def classify_release_note(title: str, body: str = "", labels: list[str] | None = None) -> dict[str, Any]:
    """Decide whether a merged PR or change belongs in release notes, under which section, and whether it needs docs or an announcement."""
    return engine.classify_release_note(title, body, labels)


@mcp.tool()
def check_sensitive(text: str, audience: str = "customer") -> dict[str, Any]:
    """Check text before it is shared. audience is internal, customer or public. Redacts structured secrets locally, then flags customer names, unreleased plans, pricing, security details, HR matters and financials. Returns verdict safe/review/block and the redacted text."""
    return engine.check_sensitive(text, audience)


def main() -> None:
    try:
        backend = get_backend()
        print(f"pm-jev: starting with backend={backend.name}", file=sys.stderr)
    except Exception as exc:  # surface misconfiguration on stderr, keep serving so tools can report it
        print(f"pm-jev: backend problem: {exc}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
