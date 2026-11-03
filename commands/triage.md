---
description: Triage customer feedback or tickets with Jev and route each one
argument-hint: "[feedback text, file path, or 'from Teams channel <name>']"
---

Triage the following feedback with the `triage_feedback` tool: $ARGUMENTS

- If the argument is a file path, read it; treat each blank-line-separated entry or each row as one item.
- If it names a Teams channel or other source and a matching connector is available, fetch the recent messages first.
- More than about ten items: delegate to the `pm-decider` subagent.

Report a table of category, product area, sentiment, churn risk, urgency, route and action.
Then list the items whose action is `human` or that show churn risk, and offer to draft replies for them.
