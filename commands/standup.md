---
description: Read standup or status updates and surface what is at risk or blocked
argument-hint: "[updates text, file path, or 'from Teams channel <name>']"
---

Read these status updates with `read_status`, one call per person or per workstream: $ARGUMENTS

If a Teams or chat source is named and a connector is available, fetch the latest posts first.

Return: blocked items first, then at-risk items, then a one-line count of on-track items.
For each blocked or at-risk item, include who asked for help, any date slip, and any scope change.
Then offer to draft a short status summary for stakeholders, and run `check_sensitive`
with the right audience on that draft before showing it as ready to share.
