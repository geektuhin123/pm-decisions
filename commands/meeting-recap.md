---
description: Turn meeting notes or a Teams transcript into decisions, action items and open questions
argument-hint: "[notes text, file path, or 'latest Teams meeting <name>']"
---

Label the meeting notes with `label_meeting_notes`: $ARGUMENTS

If a Teams or Microsoft 365 connector is available and a meeting is named, fetch its transcript first.

Then write the recap yourself:
1. Decisions made
2. Action items, with owners. Call out the lines in `action_items_without_owner`.
3. Open questions

Lines whose action is `human` or `escalate_llm`: read them yourself and place them correctly.
Before offering to post the recap anywhere, run `check_sensitive` with audience `internal`
(or `customer` if customers will see it) and share only the returned `redacted_text`.
Never post without the user's explicit yes.
