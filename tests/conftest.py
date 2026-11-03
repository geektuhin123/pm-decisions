from __future__ import annotations

from typing import Any

import pytest

from pm_jev.config import load_config


class StubBackend:
    """Deterministic backend: returns preset answers and records what it was asked."""

    name = "stub"

    def __init__(self, answers: dict[str, Any] | None = None, default_noul: float = 0.05,
                 default_conf: float = 0.95) -> None:
        self.answers = answers or {}
        self.default_noul = default_noul
        self.default_conf = default_conf
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def ask(self, state, questions, hints=None):
        self.calls.append((state, questions))
        out = {}
        for name, q in questions.items():
            preset = self.answers.get(name)
            kind = q["type"]
            if kind == "noul":
                p = self.default_noul if preset is None else float(preset)
                out[name] = {"type": "noul", "value": p, "confidence": max(p, 1 - p), "probabilities": {"yes": p, "no": 1 - p}}
            elif kind == "choice":
                labels = list(q["criteria"])
                label, conf = preset if isinstance(preset, tuple) else (preset or labels[0], self.default_conf)
                probs = {lab: (conf if lab == label else (1 - conf) / max(len(labels) - 1, 1)) for lab in labels}
                out[name] = {"type": "choice", "value": label, "confidence": conf, "probabilities": probs}
            elif kind == "score":
                value, conf = preset if isinstance(preset, tuple) else (preset if preset is not None else 0, self.default_conf)
                out[name] = {"type": "score", "value": float(value), "confidence": conf, "probabilities": {}}
        return out


@pytest.fixture
def stub():
    return StubBackend


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for var in ("TYPESAFE_API_KEY", "PM_JEV_BACKEND", "PM_JEV_CONFIG", "PM_JEV_MODEL"):
        monkeypatch.delenv(var, raising=False)
    load_config.cache_clear()
    yield
    load_config.cache_clear()
