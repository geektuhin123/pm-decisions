import json
from pathlib import Path

import pytest

from pm_jev import engine
from pm_jev.backends import JevBackend, MockBackend, get_backend
from pm_jev.config import load_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


# ------------------------------------------------------------------ gating

def test_gate_thresholds():
    assert engine.gate(0.85) == "auto"
    assert engine.gate(0.80) == "auto"
    assert engine.gate(0.6) == "escalate_llm"
    assert engine.gate(0.3) == "human"


def test_config_override_changes_gates(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("gates: {auto: 0.95, review: 0.7}\nproduct_areas: {only: 'the only area'}\n")
    monkeypatch.setenv("PM_JEV_CONFIG", str(cfg))
    load_config.cache_clear()
    assert engine.gate(0.9) == "escalate_llm"
    assert list(load_config()["product_areas"]) == ["only"]  # replaced, not merged
    assert load_config()["priority_weights"]["reach"] == 1.0  # untouched default kept


def test_missing_config_file_is_an_error(monkeypatch):
    monkeypatch.setenv("PM_JEV_CONFIG", "/nope/missing.yaml")
    load_config.cache_clear()
    with pytest.raises(FileNotFoundError):
        load_config()


# -------------------------------------------------------- backend selection

def test_backend_selection(monkeypatch):
    assert isinstance(get_backend(), MockBackend)
    monkeypatch.setenv("PM_JEV_BACKEND", "jev")
    with pytest.raises(RuntimeError):
        get_backend()
    monkeypatch.setenv("TYPESAFE_API_KEY", "tsk_test")
    assert isinstance(get_backend(), JevBackend)
    monkeypatch.setenv("PM_JEV_BACKEND", "mock")
    assert isinstance(get_backend(), MockBackend)


# ------------------------------------------------------------------- triage

def test_triage_combines_answers(stub):
    b = stub({"category": ("bug", 0.93), "product_area": ("data_connections", 0.88), "sentiment": (0.4, 0.9),
              "churn_risk": 0.1, "urgent": 0.92})
    r = engine.triage_feedback("refresh failing, board meeting friday", backend=b)
    d = r["decision"]
    assert d == {"category": "bug", "product_area": "data_connections", "sentiment": "Very negative or angry",
                 "churn_risk": False, "urgent": True, "route_to": "data_connections"}
    assert r["action"] == "escalate_llm"  # urgent -> a reply is likely, let Claude draft it


def test_triage_churn_routes_to_account_manager(stub):
    b = stub({"category": ("complaint", 0.9), "product_area": ("admin_and_billing", 0.9), "churn_risk": 0.6})
    assert engine.triage_feedback("wrong invoices again", backend=b)["decision"]["route_to"] == "account_manager"


def test_redaction_happens_before_sending(stub):
    b = stub()
    r = engine.triage_feedback("mail me at a.person@example.com", backend=b)
    assert "example.com" not in b.calls[0][0]
    assert r["redacted"] == {"EMAIL": 1}


def test_empty_text_rejected(stub):
    with pytest.raises(ValueError):
        engine.triage_feedback("   ", backend=stub())


# --------------------------------------------------------------- duplicates

def test_duplicates_match_and_none(stub):
    backlog = json.loads((EXAMPLES / "backlog.json").read_text())
    r = engine.find_duplicates("email dashboards weekly", backlog, backend=stub({"match": ("PROD-101", 0.9)}))
    assert r["decision"]["duplicate_of"] == "PROD-101" and r["action"] == "auto"
    r = engine.find_duplicates("totally new", backlog, backend=stub({"match": ("none", 0.85)}))
    assert r["decision"]["duplicate_of"] is None


def test_duplicates_truncates_to_choice_limit(stub):
    backlog = [{"id": f"P-{i}", "title": f"item {i}"} for i in range(400)]
    b = stub()
    r = engine.find_duplicates("x", backlog, backend=b)
    assert r["decision"]["truncated_backlog"] is True
    assert len(b.calls[0][1]["match"]["criteria"]) == 255  # 254 items + "none"


def test_duplicates_require_ids(stub):
    with pytest.raises(ValueError):
        engine.find_duplicates("x", [{"title": "no id"}], backend=stub())


# ---------------------------------------------------------------- readiness

def test_story_ready(stub):
    yes = {k: 0.95 for k in engine.READINESS_CHECKS}
    r = engine.check_story_ready("story", backend=stub({**yes, "clarity": (3, 0.9)}))
    assert r["decision"]["ready"] is True and r["decision"]["gaps"] == [] and r["action"] == "auto"


def test_story_gaps(stub):
    ans = {k: 0.95 for k in engine.READINESS_CHECKS}
    ans["has_success_metric"] = 0.05
    r = engine.check_story_ready("story", backend=stub({**ans, "clarity": (2, 0.9)}))
    assert r["decision"]["ready"] is False and r["decision"]["gaps"] == ["has_success_metric"]


# -------------------------------------------------------------------- risks

def test_risk_flags_and_reviewers(stub):
    r = engine.flag_risks("change seat pricing", backend=stub({"billing": 0.97}))
    assert r["decision"]["flags"] == [{"area": "billing", "probability": 0.97,
                                       "reviewer": "Finance / billing owner", "certain": True}]
    assert r["action"] == "auto"


def test_uncertain_risk_goes_to_human(stub):
    r = engine.flag_risks("touches auth maybe", backend=stub({"security": 0.6}))
    assert r["action"] == "human"


# ----------------------------------------------------------------- priority

def test_priority_math_and_overrides(stub):
    b = stub({"reach": (3, 0.9), "impact": (3, 0.9), "confidence": (3, 0.9)})
    r = engine.score_priority("sso", overrides={"effort": 0}, backend=b)
    assert r["decision"]["priority_score"] == 100.0
    assert r["decision"]["factors"]["effort"]["source"] == "pm"
    assert "effort" not in b.calls[0][1]  # overridden factor is not asked
    low = engine.score_priority("x", overrides={"effort": 3}, backend=stub({"reach": (0, 0.9), "impact": (0, 0.9),
                                                                              "confidence": (0, 0.9)}))
    assert low["decision"]["priority_score"] == pytest.approx(100 * (1 / 4) / 64, abs=0.1)


def test_priority_flags_guesses(stub):
    r = engine.score_priority("x", backend=stub({"reach": (2, 0.5)}))
    assert "reach" in r["decision"]["pm_to_confirm"] and r["action"] == "human"


def test_priority_rejects_bad_override(stub):
    with pytest.raises(ValueError):
        engine.score_priority("x", overrides={"effort": 7}, backend=stub())


# ------------------------------------------------------------------- status

def test_status(stub):
    r = engine.read_status("waiting on payments", backend=stub({"status": ("blocked", 0.9), "asks_for_help": 0.9}))
    assert r["decision"]["status"] == "blocked" and r["decision"]["asks_for_help"] is True


# ------------------------------------------------------------ meeting notes

def test_meeting_notes_batches_and_numbers(stub, tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("meeting_batch_size: 3\n")
    monkeypatch.setenv("PM_JEV_CONFIG", str(cfg))
    load_config.cache_clear()
    notes = (EXAMPLES / "meeting-notes.txt").read_text()
    b = stub({"line_2": ("action_item", 0.9), "owner_2": 0.9, "line_3": ("action_item", 0.9), "owner_3": 0.1})
    r = engine.label_meeting_notes(notes, backend=b)
    assert len(b.calls) == 3  # 7 lines in batches of 3
    assert [e["line"] for e in r["decision"]["lines"]] == list(range(1, 8))
    assert r["decision"]["action_items_without_owner"] == [3]


def test_meeting_notes_mock_uses_each_line():
    notes = "- We agreed to launch on the 20th\n- Ravi will send the plan by Friday\n- Do we need legal review?"
    labels = [e["label"] for e in engine.label_meeting_notes(notes, backend=MockBackend())["decision"]["lines"]]
    assert labels == ["decision", "action_item", "open_question"]


# ------------------------------------------------------------ release notes

def test_release_note_internal_excluded(stub):
    r = engine.classify_release_note("Refactor CI", backend=stub({"kind": ("internal", 0.95), "customer_facing": 0.05}))
    assert r["decision"]["include_in_release_notes"] is False


def test_release_note_feature_included(stub):
    r = engine.classify_release_note("Add guest access", backend=stub({"kind": ("feature", 0.95), "customer_facing": 0.95}))
    assert r["decision"]["include_in_release_notes"] is True and r["decision"]["section"] == "feature"


# ---------------------------------------------------------------- sensitive

def test_sensitive_safe(stub):
    r = engine.check_sensitive("The team shipped the new export flow.", "public", backend=stub())
    assert r["decision"]["verdict"] == "safe" and r["action"] == "auto"


def test_sensitive_thresholds_depend_on_audience(stub):
    b = stub({"pricing_or_contract": 0.8})
    assert engine.check_sensitive("discount", "internal", backend=b)["decision"]["verdict"] == "review"
    assert engine.check_sensitive("discount", "customer", backend=b)["decision"]["verdict"] == "block"


def test_sensitive_secrets_always_redacted_and_blocked_externally(stub, tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("redact_before_send: false\n")  # sharing checks redact regardless
    monkeypatch.setenv("PM_JEV_CONFIG", str(cfg))
    load_config.cache_clear()
    fake_token = "gh" + "p_" + "x" * 24  # built at runtime so secret scanners don't flag the repo
    b = stub()
    r = engine.check_sensitive(f"use token {fake_token}", "customer", backend=b)
    assert fake_token not in b.calls[0][0]
    assert r["decision"]["verdict"] == "block" and "secrets_in_text" in r["decision"]["blocking"]
    assert engine.check_sensitive(f"use token {fake_token}", "internal", backend=stub())[
        "decision"]["verdict"] == "review"


def test_sensitive_bad_audience(stub):
    with pytest.raises(ValueError):
        engine.check_sensitive("x", "everyone", backend=stub())


# -------------------------------------------------------------------- mock

def test_mock_clean_text_is_safe_even_for_public():
    r = engine.check_sensitive("The team shipped a faster export flow this week.", "public", backend=MockBackend())
    assert r["decision"]["verdict"] == "safe"
    assert r["action"] == "escalate_llm"  # mock is never confident enough for auto


def test_mock_confidence_is_capped():
    r = engine.triage_feedback("refresh failing error broken crash", backend=MockBackend())
    assert all(a["confidence"] <= MockBackend.MAX_CONFIDENCE for a in r["answers"].values())
    assert r["backend"] == "mock" and r["action"] != "auto"
