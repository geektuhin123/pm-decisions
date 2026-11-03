"""PM decision sets.

Each function turns one PM judgment into several narrow, typed questions, asks them in
a single call, and combines the answers in plain code. That split is the point:
the model answers small questions it is good at, and the rules a PM may want to
change (weights, thresholds, what counts as "ready") live here or in config.

Every result carries:
  decision  - the combined outcome
  action    - "auto", "escalate_llm" or "human", from the confidence gates
  answers   - the raw normalized answers, for audit
  backend   - "jev" or "mock"
  redacted  - counts of what local redaction removed before sending
"""

from __future__ import annotations

from typing import Any

from .backends import Backend, Hints, Question, get_backend
from .config import load_config
from .redact import redact

SECRET_TYPES = {"PRIVATE_KEY", "JWT", "API_KEY", "SECRET_ASSIGNMENT", "CONNECTION_STRING", "CARD_NUMBER"}


# ----------------------------------------------------------------------- helpers


def gate(confidence: float, cfg: dict[str, Any] | None = None) -> str:
    gates = (cfg or load_config())["gates"]
    if confidence >= gates["auto"]:
        return "auto"
    if confidence >= gates["review"]:
        return "escalate_llm"
    return "human"


def _worst(actions: list[str]) -> str:
    order = {"auto": 0, "escalate_llm": 1, "human": 2}
    return max(actions, key=order.__getitem__) if actions else "human"


def _prepare(text: str, force: bool = False) -> tuple[str, dict[str, int]]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    if force or load_config().get("redact_before_send", True):
        result = redact(text)
        return result.text, result.findings
    return text, {}


def _result(backend: Backend, decision: dict[str, Any], action: str, answers: dict[str, Any],
            redacted: dict[str, int]) -> dict[str, Any]:
    return {"decision": decision, "action": action, "answers": answers,
            "backend": backend.name, "redacted": redacted}


def _noul(instructions: str) -> Question:
    return {"type": "noul", "instructions": instructions}


def _yes(ans: dict[str, Any], threshold: float = 0.5) -> bool:
    return ans["value"] >= threshold


# ---------------------------------------------------------------- 1. triage

SENTIMENT_LEVELS = ["Very negative or angry", "Negative", "Neutral", "Positive", "Very positive"]


def triage_feedback(text: str, backend: Backend | None = None) -> dict[str, Any]:
    """Classify one piece of customer feedback: type, product area, mood, churn risk, urgency."""
    backend = backend or get_backend()
    cfg = load_config()
    state, redacted = _prepare(text)
    questions: dict[str, Question] = {
        "category": {"type": "choice", "instructions": "What kind of feedback is this?", "criteria": {
            "bug": "Something is broken or behaves differently than documented",
            "feature_request": "Asks for a capability that does not exist yet",
            "how_to_question": "Asks how to do something the product already supports",
            "praise": "Positive feedback with no request",
            "complaint": "Dissatisfaction that is not a specific bug or request",
            "not_actionable": "Spam, off-topic, or too vague to act on",
        }},
        "product_area": {"type": "choice", "instructions": "Which product area does this concern?",
                         "criteria": dict(cfg["product_areas"])},
        "sentiment": {"type": "score", "instructions": "How does the customer feel?", "criteria": SENTIMENT_LEVELS},
        "churn_risk": _noul("The customer signals they may cancel, downgrade, stop using the product, or switch to a competitor"),
        "urgent": _noul("Needs a response within one business day: production is down, data is wrong, or a customer deadline is at risk"),
    }
    hints: dict[str, Hints] = {
        "category": {"bug": ["error", "broken", "fails", "failing", "failed", "crash", "bug", "wrong", "stale", "doesn't work", "not working"],
                     "feature_request": ["would love", "please add", "feature", "wish", "support for", "can you add", "it would be great"],
                     "how_to_question": ["how do i", "how to", "is there a way", "where can i"],
                     "praise": ["love", "great", "amazing", "thank you", "awesome", "helpful", "impressed"],
                     "complaint": ["frustrated", "disappointed", "unhappy", "terrible", "messy", "again", "third"],
                     "_default": "not_actionable"},
        "product_area": {"dashboards": ["dashboard", "dashboards", "chart", "report", "visual", "pdf", "export"],
                         "data_connections": ["refresh", "connector", "sync", "source", "connection", "pipeline"],
                         "ai_assistant": ["assistant", "ai", "copilot"],
                         "admin_and_billing": ["invoice", "billing", "plan", "permission", "seat", "charge",
                                               "access", "contractor", "workspace"],
                         "performance": ["slow", "timeout", "lag", "loading", "load", "minute", "takes"],
                         "_default": "other"},
        "sentiment": {"levels": [["furious", "unacceptable", "terrible", "angry"],
                                 ["frustrated", "annoying", "disappointed", "messy", "failed", "wrong"],
                                 [], ["good", "nice", "thanks", "would love"],
                                 ["love", "amazing", "awesome", "great"]], "_default": 2},
        "churn_risk": {"yes": ["cancel", "switch", "competitor", "leaving", "downgrade", "renewal", "alternatives"]},
        "urgent": {"yes": ["asap", "urgent", "production", "down", "today", "deadline", "blocking",
                           "board meeting", "leadership", "friday"]},
    }
    a = backend.ask(state, questions, hints)
    gates = cfg["gates"]
    action = _worst([gate(a["category"]["confidence"], cfg), gate(a["product_area"]["confidence"], cfg)])
    sentiment_idx = min(round(a["sentiment"]["value"]), len(SENTIMENT_LEVELS) - 1)
    decision = {
        "category": a["category"]["value"],
        "product_area": a["product_area"]["value"],
        "sentiment": SENTIMENT_LEVELS[sentiment_idx],
        "churn_risk": _yes(a["churn_risk"]),
        "urgent": _yes(a["urgent"]),
        "route_to": "account_manager" if _yes(a["churn_risk"], gates["review"]) else a["product_area"]["value"],
    }
    if decision["churn_risk"] or decision["urgent"]:
        action = _worst([action, "escalate_llm"])  # a person-facing reply is likely; let Claude draft it
    return _result(backend, decision, action, a, redacted)


# ------------------------------------------------------------- 2. duplicates


def find_duplicates(text: str, backlog: list[dict[str, str]], backend: Backend | None = None) -> dict[str, Any]:
    """Check whether a new request matches an existing backlog item.

    backlog: [{"id": "PROD-12", "title": "...", "summary": "..."}, ...]
    """
    backend = backend or get_backend()
    cfg = load_config()
    if not backlog:
        raise ValueError("backlog must contain at least one item")
    limit = int(cfg["max_choice_options"]) - 1  # leave room for "none"
    truncated = len(backlog) > limit
    items = backlog[:limit]
    state, redacted = _prepare(text)
    criteria: dict[str, str] = {}
    for item in items:
        if not item.get("id"):
            raise ValueError("every backlog item needs an 'id'")
        desc = item.get("title", "")
        if item.get("summary"):
            desc = f"{desc}. {item['summary']}"
        criteria[str(item["id"])] = desc.strip() or str(item["id"])
    criteria["none"] = "None of the listed items describes the same underlying need"
    questions = {"match": {"type": "choice",
                           "instructions": "Which existing backlog item describes the same underlying need as this request? "
                                           "Same need matters, not just shared words.",
                           "criteria": criteria}}
    hints = {"match": {k: [w for w in v.lower().split() if len(w) > 4] for k, v in criteria.items() if k != "none"}}
    hints["match"]["_default"] = "none"
    a = backend.ask(state, questions, hints)
    probs = a["match"]["probabilities"]
    top = sorted(((k, v) for k, v in probs.items() if k != "none"), key=lambda kv: kv[1], reverse=True)[:3]
    match = a["match"]["value"]
    decision = {"duplicate_of": None if match == "none" else match,
                "candidates": [{"id": k, "probability": round(v, 3)} for k, v in top],
                "truncated_backlog": truncated}
    return _result(backend, decision, gate(a["match"]["confidence"], cfg), a, redacted)


# ---------------------------------------------------------- 3. story readiness

READINESS_CHECKS = {
    "has_user": "Names who the user or persona is",
    "has_problem": "Explains the problem or job to be done, not only the proposed solution",
    "has_acceptance_criteria": "Includes specific, testable acceptance criteria",
    "has_success_metric": "States how success will be measured after release",
    "single_scope": "Describes one shippable piece of work rather than several bundled together",
}
CLARITY_LEVELS = [
    "Unclear: an engineer could not start",
    "Partly clear: major questions remain",
    "Mostly clear: only minor questions remain",
    "Clear: an engineer could start today",
]


def check_story_ready(story: str, backend: Backend | None = None) -> dict[str, Any]:
    """Check a user story or ticket against a definition of ready."""
    backend = backend or get_backend()
    cfg = load_config()
    state, redacted = _prepare(story)
    questions: dict[str, Question] = {k: _noul(v) for k, v in READINESS_CHECKS.items()}
    questions["clarity"] = {"type": "score", "instructions": "How ready is this for an engineer to pick up?",
                            "criteria": CLARITY_LEVELS}
    hints: dict[str, Hints] = {
        "has_user": {"yes": ["as a", "user", "admin", "analyst", "persona", "customer"]},
        "has_problem": {"yes": ["so that", "because", "problem", "today", "currently", "pain"]},
        "has_acceptance_criteria": {"yes": ["acceptance criteria", "given", "when", "then", "should", "must"]},
        "has_success_metric": {"yes": ["metric", "measure", "%", "reduce", "increase", "kpi", "success"]},
        "single_scope": {"no": ["and also", "plus", "additionally", "as well as"], "_default": 0.6},
        "clarity": {"levels": [["tbd", "somehow", "etc"], ["maybe", "not sure"], [], ["acceptance criteria"]], "_default": 1},
    }
    a = backend.ask(state, questions, hints)
    gaps = [k for k in READINESS_CHECKS if not _yes(a[k])]
    clarity_idx = min(round(a["clarity"]["value"]), len(CLARITY_LEVELS) - 1)
    ready = not gaps and clarity_idx >= 2
    uncertain = [k for k in [*READINESS_CHECKS, "clarity"] if gate(a[k]["confidence"], cfg) != "auto"]
    decision = {"ready": ready, "gaps": gaps, "clarity": CLARITY_LEVELS[clarity_idx], "uncertain_checks": uncertain}
    action = "auto" if not uncertain else ("escalate_llm" if len(uncertain) <= 2 else "human")
    return _result(backend, decision, action, a, redacted)


# ---------------------------------------------------------------- 4. risk flags

RISK_CHECKS = {
    "billing": "Changes how customers are charged, invoiced, or what their plan includes",
    "security": "Touches authentication, authorization, secrets, encryption, or exposes an attack surface",
    "privacy": "Collects, stores, shares, or changes the handling of personal data",
    "compliance": "Affects audit logs, data residency, retention, or regulatory obligations",
    "data_migration": "Requires migrating, backfilling, or deleting existing customer data",
    "breaking_change": "Removes or changes behaviour that existing customers, APIs, or integrations rely on",
}


def flag_risks(change: str, backend: Backend | None = None) -> dict[str, Any]:
    """Flag which review areas a proposed change touches, and who should look at it."""
    backend = backend or get_backend()
    cfg = load_config()
    state, redacted = _prepare(change)
    questions = {k: _noul(f"This change {v[0].lower()}{v[1:]}") for k, v in RISK_CHECKS.items()}
    hints: dict[str, Hints] = {
        "billing": {"yes": ["billing", "invoice", "pricing", "plan", "charge", "subscription", "seat"]},
        "security": {"yes": ["auth", "login", "token", "permission", "sso", "encryption", "secret", "role"]},
        "privacy": {"yes": ["personal", "pii", "email", "gdpr", "user data", "tracking"]},
        "compliance": {"yes": ["audit", "retention", "residency", "soc", "hipaa", "regulation"]},
        "data_migration": {"yes": ["migrate", "migration", "backfill", "delete", "schema change"]},
        "breaking_change": {"yes": ["deprecate", "remove", "rename", "breaking", "v2", "no longer"]},
    }
    a = backend.ask(state, questions, hints)
    review_at = cfg["gates"]["review"]
    flags = []
    for k in RISK_CHECKS:
        p = a[k]["value"]
        if p >= review_at:
            flags.append({"area": k, "probability": round(p, 3),
                          "reviewer": cfg["risk_reviewers"].get(k, "Product owner"),
                          "certain": p >= cfg["gates"]["auto"]})
    action = "auto" if all(gate(a[k]["confidence"], cfg) == "auto" for k in RISK_CHECKS) else "escalate_llm"
    if any(not f["certain"] for f in flags):
        action = "human"  # a possible but unconfirmed risk should be looked at by a person
    return _result(backend, {"flags": flags, "clean": not flags}, action, a, redacted)


# ---------------------------------------------------------- 5. prioritization

PRIORITY_FACTORS: dict[str, list[str]] = {
    "reach": ["A few users or one account", "Some users in one segment",
              "Most users in a segment, or several key accounts", "Most or all users"],
    "impact": ["Minimal", "Low: nice to have", "Medium: noticeably improves a key workflow",
               "High: unblocks revenue, retention, or a critical workflow"],
    "confidence": ["Gut feel, no evidence", "Anecdotal: one or two requests",
                   "Some data: repeated requests or usage data", "Strong: usage data plus several customers"],
    "effort": ["Small: a few days", "Medium: one to two weeks", "Large: a full sprint or more",
               "Very large: several sprints or several teams"],
}


def score_priority(item: str, overrides: dict[str, int] | None = None, backend: Backend | None = None) -> dict[str, Any]:
    """Score reach, impact, confidence and effort separately, then combine them in code.

    overrides: factor -> level (0-3) the PM already knows, e.g. {"effort": 1}. Overridden
    factors are not sent to the model.
    """
    backend = backend or get_backend()
    cfg = load_config()
    overrides = dict(overrides or {})
    for k, v in overrides.items():
        if k not in PRIORITY_FACTORS or not 0 <= int(v) <= 3:
            raise ValueError(f"invalid override {k}={v}; factors are {list(PRIORITY_FACTORS)} with levels 0-3")
    state, redacted = _prepare(item)
    ask = {k: v for k, v in PRIORITY_FACTORS.items() if k not in overrides}
    questions = {k: {"type": "score", "instructions": f"Estimate the {k} of this backlog item", "criteria": v}
                 for k, v in ask.items()}
    hints: dict[str, Hints] = {
        "reach": {"levels": [["one customer"], ["some"], ["several accounts", "enterprise"], ["all users", "everyone"]], "_default": 1},
        "impact": {"levels": [[], ["nice to have"], ["workflow", "time"], ["revenue", "churn", "blocker", "renewal"]], "_default": 1},
        "confidence": {"levels": [["i think"], ["one customer"], ["requests", "tickets"], ["data", "usage", "survey"]], "_default": 1},
        "effort": {"levels": [["small", "quick"], ["week"], ["sprint"], ["quarter", "multiple teams", "rewrite"]], "_default": 1},
    }
    a = backend.ask(state, questions, hints) if questions else {}
    levels: dict[str, float] = {k: float(v) for k, v in overrides.items()}
    levels.update({k: a[k]["value"] for k in ask})
    w = cfg["priority_weights"]
    value = {k: levels[k] + 1.0 for k in PRIORITY_FACTORS}  # 1..4
    rice = (value["reach"] ** w["reach"] * value["impact"] ** w["impact"] * value["confidence"] ** w["confidence"]
            / value["effort"] ** w["effort"])
    best = 4.0 ** w["reach"] * 4.0 ** w["impact"] * 4.0 ** w["confidence"]  # max with effort = 1
    to_confirm = [k for k in ask if gate(a[k]["confidence"], cfg) != "auto"]
    decision = {
        "priority_score": round(100.0 * rice / best, 1),
        "factors": {k: {"level": round(levels[k], 2), "label": PRIORITY_FACTORS[k][min(round(levels[k]), 3)],
                        "source": "pm" if k in overrides else backend.name} for k in PRIORITY_FACTORS},
        "pm_to_confirm": to_confirm,
    }
    action = "auto" if not to_confirm else "human"  # prioritization is a PM call; flag guesses openly
    return _result(backend, decision, action, a, redacted)


# ---------------------------------------------------------------- 6. status

STATUS_OPTIONS = {
    "on_track": "Work is progressing as planned with no open blockers",
    "at_risk": "Work is progressing but a deadline, dependency, or scope issue may cause a miss",
    "blocked": "Work cannot proceed until something outside the team is resolved",
}


def read_status(update: str, backend: Backend | None = None) -> dict[str, Any]:
    """Read a standup or status message as on track, at risk, or blocked."""
    backend = backend or get_backend()
    cfg = load_config()
    state, redacted = _prepare(update)
    questions: dict[str, Question] = {
        "status": {"type": "choice", "instructions": "What is the status of the work described?", "criteria": STATUS_OPTIONS},
        "asks_for_help": _noul("The writer asks someone for help, a decision, or an unblock"),
        "date_slip": _noul("Mentions a deadline moving or likely to be missed"),
        "scope_change": _noul("Mentions new scope being added or scope being cut"),
    }
    hints: dict[str, Hints] = {
        "status": {"blocked": ["blocked", "waiting on", "can't proceed", "stuck"],
                   "at_risk": ["risk", "slip", "delay", "behind", "tight"],
                   "on_track": ["done", "on track", "merged", "shipped", "progress"], "_default": "on_track"},
        "asks_for_help": {"yes": ["need", "help", "can someone", "please review", "decision"]},
        "date_slip": {"yes": ["slip", "push", "delay", "next week", "won't make"]},
        "scope_change": {"yes": ["added", "new requirement", "cut", "descoped", "also need"]},
    }
    a = backend.ask(state, questions, hints)
    decision = {"status": a["status"]["value"], "asks_for_help": _yes(a["asks_for_help"]),
                "date_slip": _yes(a["date_slip"]), "scope_change": _yes(a["scope_change"])}
    return _result(backend, decision, gate(a["status"]["confidence"], cfg), a, redacted)


# --------------------------------------------------------- 7. meeting notes

NOTE_LABELS = {
    "decision": "A decision that was made",
    "action_item": "A task someone needs to do",
    "open_question": "An unresolved question or something to follow up on",
    "fyi": "Context or information with nothing to do",
}


def label_meeting_notes(notes: str, backend: Backend | None = None) -> dict[str, Any]:
    """Label each line of meeting notes or a transcript as decision, action item, open question or FYI.

    All lines in a batch share one call: the whole meeting is the state, and each
    line is its own question, so context from other lines is available.
    """
    backend = backend or get_backend()
    cfg = load_config()
    lines = [ln.strip(" -*\t") for ln in notes.splitlines() if ln.strip(" -*\t")]
    if not lines:
        raise ValueError("notes must contain at least one non-empty line")
    batch = max(1, int(cfg["meeting_batch_size"]))
    out_lines: list[dict[str, Any]] = []
    all_answers: dict[str, Any] = {}
    redacted_total: dict[str, int] = {}
    actions: list[str] = []
    for start in range(0, len(lines), batch):
        chunk = lines[start:start + batch]
        numbered = "\n".join(f"{start + i + 1}. {ln}" for i, ln in enumerate(chunk))
        state, redacted = _prepare(numbered)
        for k, v in redacted.items():
            redacted_total[k] = redacted_total.get(k, 0) + v
        questions: dict[str, Question] = {}
        hints: dict[str, Hints] = {}
        for i, ln in enumerate(chunk):
            n = start + i + 1
            questions[f"line_{n}"] = {"type": "choice", "instructions": f"Classify line {n}", "criteria": NOTE_LABELS}
            questions[f"owner_{n}"] = _noul(f"Line {n} names a specific person or team as owner")
            hints[f"line_{n}"] = {"_text": ln, "decision": ["decided", "agreed", "we will", "going with", "approved"],
                                  "action_item": ["will", "todo", "to do", "action", "by friday", "follow up", "send", "owner"],
                                  "open_question": ["?", "unclear", "tbd", "not sure", "open question"],
                                  "_default": "fyi"}
            hints[f"owner_{n}"] = {"_text": ln, "yes": ["@", "owner", "will", "assigned"]}
        a = _ask_per_line(backend, state, questions, hints)
        all_answers.update(a)
        for i, ln in enumerate(chunk):
            n = start + i + 1
            lab = a[f"line_{n}"]
            act = gate(lab["confidence"], cfg)
            actions.append(act)
            entry = {"line": n, "text": ln, "label": lab["value"], "confidence": round(lab["confidence"], 3), "action": act}
            if lab["value"] == "action_item":
                entry["has_owner"] = _yes(a[f"owner_{n}"])
            out_lines.append(entry)
    summary = {lab: sum(1 for e in out_lines if e["label"] == lab) for lab in NOTE_LABELS}
    missing_owner = [e["line"] for e in out_lines if e.get("has_owner") is False]
    decision = {"lines": out_lines, "summary": summary, "action_items_without_owner": missing_owner}
    worst = "auto" if all(x == "auto" for x in actions) else "escalate_llm"
    return _result(backend, decision, worst, all_answers, redacted_total)


def _ask_per_line(backend: Backend, state: str, questions: dict[str, Question], hints: dict[str, Hints]) -> dict[str, Any]:
    """Jev sees the whole meeting; the mock backend needs each line's own text."""
    if backend.name != "mock":
        return backend.ask(state, questions, hints)
    out: dict[str, Any] = {}
    for name, q in questions.items():
        h = dict(hints.get(name, {}))
        line_text = h.pop("_text", state)
        out.update(backend.ask(line_text, {name: q}, {name: h}))
    return out


# --------------------------------------------------------- 8. release notes

RELEASE_KINDS = {
    "feature": "A new capability customers can use",
    "improvement": "Makes an existing capability better, faster, or easier",
    "fix": "Corrects a bug customers may have noticed",
    "internal": "Refactoring, tests, tooling, or infrastructure with no visible change",
}


def classify_release_note(title: str, body: str = "", labels: list[str] | None = None,
                          backend: Backend | None = None) -> dict[str, Any]:
    """Decide whether a merged change belongs in release notes, and under which heading."""
    backend = backend or get_backend()
    cfg = load_config()
    text = f"Title: {title}\nLabels: {', '.join(labels or []) or 'none'}\n\n{body}".strip()
    state, redacted = _prepare(text)
    questions: dict[str, Question] = {
        "kind": {"type": "choice", "instructions": "What kind of change is this?", "criteria": RELEASE_KINDS},
        "customer_facing": _noul("Customers would notice this change in the product"),
        "needs_docs": _noul("Help docs or guides need to be written or updated because of this change"),
        "worth_announcing": _noul("This is notable enough to announce to customers on its own"),
    }
    hints: dict[str, Hints] = {
        "kind": {"feature": ["add", "new", "introduce", "feature", "feat"],
                 "improvement": ["improve", "faster", "better", "optimize", "perf"],
                 "fix": ["fix", "bug", "resolve", "correct", "crash"],
                 "internal": ["refactor", "test", "ci", "chore", "bump", "deps", "lint"], "_default": "internal"},
        "customer_facing": {"yes": ["ui", "user", "customer", "dashboard", "new", "fix"],
                            "no": ["refactor", "test", "ci", "chore", "internal"]},
        "needs_docs": {"yes": ["new", "setting", "option", "api", "workflow"]},
        "worth_announcing": {"yes": ["launch", "new", "introduce", "major"]},
    }
    a = backend.ask(state, questions, hints)
    include = a["kind"]["value"] != "internal" and _yes(a["customer_facing"])
    decision = {"include_in_release_notes": include, "section": a["kind"]["value"],
                "needs_docs": _yes(a["needs_docs"]), "worth_announcing": _yes(a["worth_announcing"])}
    return _result(backend, decision, _worst([gate(a["kind"]["confidence"], cfg),
                                              gate(a["customer_facing"]["confidence"], cfg)]), a, redacted)


# ------------------------------------------------------ 9. sensitive content

SENSITIVE_CHECKS = {
    "names_customer": "Names a specific customer company or an identifiable customer contact",
    "unreleased_plans": "Reveals unreleased features, roadmap dates, or internal plans",
    "pricing_or_contract": "Mentions specific prices, discounts, contract terms, or deal values",
    "security_details": "Describes vulnerabilities, security incidents, internal infrastructure, or credentials",
    "people_matters": "Discusses an individual's performance, hiring, compensation, or other HR matters",
    "financials": "Includes non-public company financials such as revenue, margins, burn, or forecasts",
}
AUDIENCES = ("internal", "customer", "public")


def check_sensitive(text: str, audience: str = "customer", backend: Backend | None = None) -> dict[str, Any]:
    """Check text before it is shared (a Teams post, a customer email, a public update).

    Local redaction always runs first, whatever the config says, so structured
    secrets never reach the model. The verdict is "safe", "review" or "block".
    """
    if audience not in AUDIENCES:
        raise ValueError(f"audience must be one of {AUDIENCES}")
    backend = backend or get_backend()
    cfg = load_config()
    th = cfg["sensitive_thresholds"][audience]
    state, redacted = _prepare(text, force=True)
    questions = {k: _noul(f"The text {v[0].lower()}{v[1:]}") for k, v in SENSITIVE_CHECKS.items()}
    hints: dict[str, Hints] = {
        "names_customer": {"yes": ["customer", "client", "inc", "ltd", "corp", "account"]},
        "unreleased_plans": {"yes": ["roadmap", "next quarter", "unreleased", "launching", "q1", "q2", "q3", "q4", "planned"]},
        "pricing_or_contract": {"yes": ["price", "discount", "contract", "$", "per seat", "deal", "renewal"]},
        "security_details": {"yes": ["vulnerability", "cve", "breach", "password", "incident", "exploit"]},
        "people_matters": {"yes": ["performance review", "salary", "hiring", "fired", "pip", "promotion"]},
        "financials": {"yes": ["revenue", "arr", "margin", "burn", "forecast", "ebitda"]},
    }
    a = backend.ask(state, questions, hints)
    hits = {k: round(a[k]["value"], 3) for k in SENSITIVE_CHECKS}
    blocking = [k for k, p in hits.items() if p >= th["block"]]
    reviewing = [k for k, p in hits.items() if th["review"] <= p < th["block"]]
    secrets_found = sorted(set(redacted) & SECRET_TYPES)
    pii_found = sorted(set(redacted) - SECRET_TYPES)
    if secrets_found and audience != "internal":
        blocking.append("secrets_in_text")
    elif secrets_found:
        reviewing.append("secrets_in_text")
    if pii_found and audience == "public":
        reviewing.append("personal_contact_details")
    verdict = "block" if blocking else ("review" if reviewing else "safe")
    decision = {"verdict": verdict, "audience": audience, "blocking": blocking, "needs_review": reviewing,
                "redacted_text": state, "redactions": redacted, "probabilities": hits}
    action = {"safe": "auto", "review": "human", "block": "human"}[verdict]
    if verdict == "safe" and any(gate(a[k]["confidence"], cfg) != "auto" for k in SENSITIVE_CHECKS):
        action = "escalate_llm"
    return _result(backend, decision, action, a, redacted)
