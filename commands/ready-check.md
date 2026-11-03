---
description: Check stories against a definition of ready and flag risky ones before sprint planning
argument-hint: "[story text, file path, or ticket ids if a tracker connector is available]"
---

For each story in: $ARGUMENTS

1. Run `check_story_ready`.
2. Run `flag_risks` on the same text.

Report one row per story: ready yes/no, gaps, clarity, risk flags with reviewers, action.
For stories that are not ready, suggest the specific missing pieces (for example a
draft acceptance criterion), written by you, clearly marked as suggestions.
