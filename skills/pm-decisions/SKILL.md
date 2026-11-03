---
name: pm-decisions
description: Use for product-management work that involves many small, bounded judgments - triaging customer feedback or tickets, spotting duplicate requests, checking whether a story is ready, flagging billing/security/privacy risk, scoring priority, reading standups as on track/at risk/blocked, labelling meeting notes, deciding what goes in release notes, and checking text for sensitive content before it is shared in Teams, email or public channels.
---

# PM decisions with Jev

This plugin gives you a fast decision layer (the `pm-jev` MCP tools, backed by
TypeSafe's Jev model). The split is simple:

- **Jev decides.** Classify, score, flag, check. Milliseconds, fractions of a cent, typed answers with confidence.
- **You write.** PRDs, replies, summaries, release notes, stakeholder updates. Jev never produces prose.

## Start of a session

Call `backend_status` once. If `backend` is `mock`, tell the user that no TypeSafe key
is configured and that answers are keyword heuristics for demo purposes only. Do not
present mock results as real judgments.

## Pick the tool

| PM job | Tool |
|---|---|
| A ticket, review, survey answer or customer message came in | `triage_feedback` |
| Is this request already in the backlog? | `find_duplicates` (pass the backlog as `[{id, title, summary}]`) |
| Is this story ready for sprint planning? | `check_story_ready` |
| Does this change need billing / security / privacy / compliance review? | `flag_risks` |
| How should this item rank? | `score_priority` (pass known factors in `overrides`) |
| What does this standup or status update mean? | `read_status` |
| What came out of this meeting? | `label_meeting_notes` |
| Does this PR belong in release notes? | `classify_release_note` |
| Is this safe to post or send? | `check_sensitive` with `audience` = internal, customer or public |

## Respect the `action` field

Every result carries `action`:

- `auto` - confident; act on it.
- `escalate_llm` - plausible but not certain; read the source yourself and confirm or correct before using it.
- `human` - the PM decides. Present the evidence and the candidates, and ask.

Never quietly upgrade a `human` result to a decision. For `score_priority`, list the
factors in `pm_to_confirm` and ask the PM to confirm them before reporting a final rank.

## Work in bulk

For more than about ten items (a ticket export, a sprint's worth of stories, a long
transcript), hand the batch to the `pm-decider` subagent so raw results stay out of the
main conversation. Ask it for a compact table plus the items that need a human.

## Microsoft Teams and other sources

This plugin has no Teams login of its own. If a Teams, Microsoft 365, Slack, Jira or
similar connector is available in the session, use it to fetch the content
(channel messages, meeting transcripts, tickets), then run the decision tools on it.

- Meeting transcript -> `label_meeting_notes`, then draft the recap yourself from the decisions and action items.
- Channel or chat messages -> `triage_feedback` per customer message, `read_status` per standup post.
- Never post back to Teams, email or any channel without showing the user the exact text and getting a clear yes.

## Before anything is shared

Run `check_sensitive` on any text you are about to post or send, with the right audience:

- `block` - do not share. Tell the user which checks fired.
- `review` - show the user what was flagged and let them decide.
- `safe` - share the returned `redacted_text`, not the original.

Local redaction (emails, phone numbers, keys, card numbers) always runs before text
reaches Jev, but it is pattern-based. The semantic checks catch meaning, such as an
unannounced roadmap date. Neither replaces the user's judgment for external audiences.

## Writing well with these results

- Quote the decision and its confidence when it matters ("routed to data_connections, 0.91").
- Combine several narrow results rather than asking one tool to answer a broad question.
- Keep math, dates and counts in your own reasoning or code; Jev is weak at them.
