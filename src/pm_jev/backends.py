"""Decision backends.

``JevBackend`` calls TypeSafe's System One API through the official SDK.
``MockBackend`` answers the same questions offline with keyword heuristics, so the
plugin can be installed, demoed and tested without an API key. Mock answers are
clearly labelled and deliberately low-confidence; never use them for real triage.

Both backends take questions in TypeSafe's wire format (plain dicts) and return the
same normalized shape:

    {name: {"type": "choice" | "score" | "noul",
            "value": <label | expected score | probability of yes>,
            "confidence": float in [0, 1],
            "probabilities": {...}}}
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Protocol

Question = dict[str, Any]
Hints = dict[str, Any]
Normalized = dict[str, dict[str, Any]]


class Backend(Protocol):
    name: str

    def ask(self, state: Any, questions: dict[str, Question], hints: dict[str, Hints] | None = None) -> Normalized: ...


# --------------------------------------------------------------------------- Jev


class JevBackend:
    """Thin wrapper over ``typesafe_sdk.TypeSafeClient.system_one``."""

    name = "jev"

    def __init__(self, api_key: str | None = None, model: str | None = None, client: Any = None) -> None:
        self.model = model or os.environ.get("PM_JEV_MODEL") or os.environ.get("TYPESAFE_DEFAULT_MODEL") or "jev-latest"
        if client is not None:
            self._client = client
            return
        try:
            from typesafe_sdk import TypeSafeClient
        except ImportError as exc:  # pragma: no cover - exercised only without the SDK
            raise RuntimeError("typesafe-sdk is not installed. Run: pip install typesafe-sdk") from exc
        self._client = TypeSafeClient(api_key=api_key) if api_key else TypeSafeClient()

    def ask(self, state: Any, questions: dict[str, Question], hints: dict[str, Hints] | None = None) -> Normalized:
        response = self._client.system_one(state=state, questions=questions, model=self.model)
        return normalize_answers(response.answers)


def normalize_answers(answers: dict[str, Any]) -> Normalized:
    """Convert SDK answer objects into plain dicts with a uniform confidence field."""
    out: Normalized = {}
    for name, ans in answers.items():
        kind = getattr(ans, "type", None)
        if kind == "choice":
            probs = {str(k): float(v) for k, v in (ans.probabilities or {}).items()}
            out[name] = {"type": "choice", "value": ans.choice, "confidence": float(ans.confidence), "probabilities": probs}
        elif kind == "score":
            probs = {str(k): float(v) for k, v in (ans.probabilities or {}).items()}
            out[name] = {"type": "score", "value": float(ans.score), "confidence": float(ans.confidence), "probabilities": probs}
        elif kind == "noul":
            p = float(ans.noul)
            out[name] = {"type": "noul", "value": p, "confidence": max(p, 1.0 - p), "probabilities": {"yes": p, "no": 1.0 - p}}
        # Box answers and future answer types are ignored: this plugin never asks for them.
    return out


# -------------------------------------------------------------------------- Mock

_WORD = re.compile(r"[a-z0-9']+")


def _hits(text: str, keywords: list[str]) -> int:
    low = text.lower()
    words = set(_WORD.findall(low))
    count = 0
    for kw in keywords:
        kw = kw.lower()
        # Single plain words match whole words ("ai" must not match "said"); phrases and
        # symbols such as "?" or "$" match as substrings.
        count += (kw in words) if _WORD.fullmatch(kw) else (kw in low)
    return count


def _softmax(scores: dict[str, float]) -> dict[str, float]:
    m = max(scores.values())
    exps = {k: math.exp(v - m) for k, v in scores.items()}
    total = sum(exps.values())
    return {k: v / total for k, v in exps.items()}


class MockBackend:
    """Offline stand-in that uses keyword hints. Confidence is capped at 0.75."""

    name = "mock"
    MAX_CONFIDENCE = 0.75

    def ask(self, state: Any, questions: dict[str, Question], hints: dict[str, Hints] | None = None) -> Normalized:
        text = state if isinstance(state, str) else str(state)
        hints = hints or {}
        out: Normalized = {}
        for name, q in questions.items():
            h = hints.get(name, {})
            kind = q["type"]
            if kind == "choice":
                out[name] = self._choice(text, list(q["criteria"].keys()), h)
            elif kind == "score":
                out[name] = self._score(text, len(q["criteria"]), h)
            elif kind == "noul":
                out[name] = self._noul(text, h)
        return out

    def _choice(self, text: str, labels: list[str], h: Hints) -> dict[str, Any]:
        default = h.get("_default", labels[-1])
        raw = {lab: 1.5 * _hits(text, h.get(lab, [])) for lab in labels}
        if all(v == 0 for v in raw.values()):
            raw[default] = 1.0
        probs = _softmax(raw)
        best = max(probs, key=probs.get)
        return {"type": "choice", "value": best, "confidence": min(probs[best], self.MAX_CONFIDENCE), "probabilities": probs}

    def _score(self, text: str, levels: int, h: Hints) -> dict[str, Any]:
        # hints: {"levels": [[keywords for level 0], [level 1], ...], "_default": int}
        level_kw: list[list[str]] = h.get("levels", [])
        raw = {str(i): 1.5 * _hits(text, level_kw[i]) if i < len(level_kw) else 0.0 for i in range(levels)}
        if all(v == 0 for v in raw.values()):
            raw[str(h.get("_default", levels // 2))] = 1.0
        probs = _softmax(raw)
        expected = sum(int(k) * v for k, v in probs.items())
        conf = min(max(probs.values()), self.MAX_CONFIDENCE)
        return {"type": "score", "value": expected, "confidence": conf, "probabilities": probs}

    def _noul(self, text: str, h: Hints) -> dict[str, Any]:
        yes, no = _hits(text, h.get("yes", [])), _hits(text, h.get("no", []))
        if yes == no:
            p = float(h.get("_default", 0.5 if yes else 0.15))
        else:
            p = 0.8 if yes > no else 0.15
        # The probability may be low (so "not present" stays below every threshold), but the
        # reported confidence is capped so mock answers never pass the "auto" gate.
        conf = min(max(p, 1 - p), self.MAX_CONFIDENCE)
        return {"type": "noul", "value": p, "confidence": conf, "probabilities": {"yes": p, "no": 1 - p}}


# ------------------------------------------------------------------------ Select


def get_backend() -> Backend:
    """Pick the backend from the environment.

    ``PM_JEV_BACKEND`` can force ``jev`` or ``mock``. Otherwise Jev is used when
    ``TYPESAFE_API_KEY`` is set, and the mock backend is used when it is not.
    """
    forced = (os.environ.get("PM_JEV_BACKEND") or "").strip().lower()
    key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if forced == "mock":
        return MockBackend()
    if forced == "jev" or key:
        if not key:
            raise RuntimeError("PM_JEV_BACKEND=jev but TYPESAFE_API_KEY is empty")
        return JevBackend(api_key=key)
    return MockBackend()
