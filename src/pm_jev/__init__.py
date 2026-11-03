"""pm-decisions: fast, typed PM decisions backed by TypeSafe's Jev model."""

from .backends import JevBackend, MockBackend, get_backend
from .engine import (
    check_sensitive,
    check_story_ready,
    classify_release_note,
    find_duplicates,
    flag_risks,
    label_meeting_notes,
    read_status,
    score_priority,
    triage_feedback,
)
from .redact import redact

__version__ = "0.1.0"

__all__ = [
    "JevBackend",
    "MockBackend",
    "get_backend",
    "check_sensitive",
    "check_story_ready",
    "classify_release_note",
    "find_duplicates",
    "flag_risks",
    "label_meeting_notes",
    "read_status",
    "score_priority",
    "triage_feedback",
    "redact",
]
