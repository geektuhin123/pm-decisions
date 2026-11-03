"""Every question this plugin sends must be valid for the official TypeSafe SDK.

The SDK's pydantic question models are the real validators, so this catches wire
format mistakes without needing an API key or network access.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typesafe_sdk import Choice, Noul, Score

from pm_jev import engine
from pm_jev.backends import JevBackend, normalize_answers

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SDK_TYPES = {"choice": Choice, "score": Score, "noul": Noul}


def _all_calls(stub_cls):
    backend = stub_cls()
    backlog = json.loads((EXAMPLES / "backlog.json").read_text())
    engine.triage_feedback("refresh failing", backend=backend)
    engine.find_duplicates("schedule pdf emails", backlog, backend=backend)
    engine.check_story_ready((EXAMPLES / "story.md").read_text(), backend=backend)
    engine.flag_risks("change seat billing", backend=backend)
    engine.score_priority("guest access", backend=backend)
    engine.read_status("blocked on payments", backend=backend)
    engine.label_meeting_notes((EXAMPLES / "meeting-notes.txt").read_text(), backend=backend)
    engine.classify_release_note("Add guest access", "new invite flow", ["feature"], backend=backend)
    engine.check_sensitive("hello team", "customer", backend=backend)
    return backend.calls


def test_every_question_validates_against_sdk(stub):
    calls = _all_calls(stub)
    assert len(calls) == 9
    for _state, questions in calls:
        assert questions, "a call must ask at least one question"
        for name, q in questions.items():
            assert name.replace("_", "").isalnum(), name
            SDK_TYPES[q["type"]](**q)  # raises on any wire-format mistake
            if q["type"] == "choice":
                assert 1 <= len(q["criteria"]) <= 255


def test_normalize_sdk_answer_objects():
    answers = {
        "c": SimpleNamespace(type="choice", choice="bug", confidence=0.91, probabilities={"bug": 0.91, "praise": 0.09}),
        "s": SimpleNamespace(type="score", score=2.4, confidence=0.7, probabilities={0: 0.1, 2: 0.6, 3: 0.3}),
        "n": SimpleNamespace(type="noul", noul=0.2),
        "b": SimpleNamespace(type="box"),
    }
    out = normalize_answers(answers)
    assert out["c"] == {"type": "choice", "value": "bug", "confidence": 0.91, "probabilities": {"bug": 0.91, "praise": 0.09}}
    assert out["s"]["value"] == 2.4 and out["s"]["probabilities"] == {"0": 0.1, "2": 0.6, "3": 0.3}
    assert out["n"]["value"] == 0.2 and out["n"]["confidence"] == pytest.approx(0.8)
    assert "b" not in out


def test_jev_backend_passes_model_and_questions():
    seen = {}

    class FakeClient:
        def system_one(self, state, questions, model):
            seen.update(state=state, questions=questions, model=model)
            return SimpleNamespace(answers={"urgent": SimpleNamespace(type="noul", noul=0.97)})

    backend = JevBackend(model="jev-1.13.0", client=FakeClient())
    out = backend.ask("prod is down", {"urgent": {"type": "noul", "instructions": "Is it urgent?"}})
    assert seen["model"] == "jev-1.13.0"
    assert seen["state"] == "prod is down"
    assert out["urgent"]["value"] == 0.97
