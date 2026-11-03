import asyncio

from pm_jev import server

EXPECTED = {
    "backend_status", "triage_feedback", "find_duplicates", "check_story_ready", "flag_risks",
    "score_priority", "read_status", "label_meeting_notes", "classify_release_note", "check_sensitive",
}


def test_all_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == EXPECTED
    assert all(t.description for t in tools)


def test_backend_status_reports_mock():
    status = server.backend_status()
    assert status["backend"] == "mock" and status["note"]
