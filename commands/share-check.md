---
description: Check text for sensitive content before posting it to Teams, email, or anywhere public
argument-hint: "[internal|customer|public] [text or file path]"
---

Run `check_sensitive` on the text below. The first word is the audience if it is
internal, customer or public; otherwise use customer: $ARGUMENTS

Report the verdict first (safe, review or block), then which checks fired and what
local redaction removed. If the verdict is safe, give the `redacted_text` as the
version to share. If it is review or block, explain the issue in one line per flag and
suggest a rewrite that removes it.
