"""Command line access to the same decisions, for scripts, CI and quick tests.

Examples:
    pm-jev triage "The nightly refresh keeps failing"
    pm-jev ready --file story.md
    pm-jev sensitive --audience public --file update.txt
    pm-jev duplicates "Export to PDF" --backlog backlog.json
    pm-jev notes --file meeting.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import engine


def _text(args: argparse.Namespace) -> str:
    if getattr(args, "file", None):
        return Path(args.file).read_text(encoding="utf-8")
    if getattr(args, "text", None):
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("provide text, --file, or pipe input on stdin")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pm-jev", description="Typed PM decisions backed by Jev.")
    parser.add_argument("--full", action="store_true", help="include raw model answers in the output")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("text", nargs="?")
        p.add_argument("--file")
        return p

    add("triage", "classify customer feedback")
    add("ready", "check a story against a definition of ready")
    add("risks", "flag review areas a change touches")
    pr = add("priority", "score reach, impact, confidence, effort")
    pr.add_argument("--effort", type=int, choices=range(4), help="known effort level 0-3")
    add("status", "read a standup or status update")
    add("notes", "label meeting notes line by line")
    rn = add("release", "classify a change for release notes (text is the PR title)")
    rn.add_argument("--body", default="")
    rn.add_argument("--labels", default="", help="comma-separated labels")
    se = add("sensitive", "check text before sharing")
    se.add_argument("--audience", choices=engine.AUDIENCES, default="customer")
    du = add("duplicates", "check a request against a backlog")
    du.add_argument("--backlog", required=True, help="JSON file: list of {id, title, summary}")

    args = parser.parse_args(argv)
    text = _text(args)
    if args.cmd == "triage":
        out = engine.triage_feedback(text)
    elif args.cmd == "ready":
        out = engine.check_story_ready(text)
    elif args.cmd == "risks":
        out = engine.flag_risks(text)
    elif args.cmd == "priority":
        out = engine.score_priority(text, {"effort": args.effort} if args.effort is not None else None)
    elif args.cmd == "status":
        out = engine.read_status(text)
    elif args.cmd == "notes":
        out = engine.label_meeting_notes(text)
    elif args.cmd == "release":
        labels = [x.strip() for x in args.labels.split(",") if x.strip()]
        out = engine.classify_release_note(text, args.body, labels)
    elif args.cmd == "sensitive":
        out = engine.check_sensitive(text, args.audience)
    else:
        backlog = json.loads(Path(args.backlog).read_text(encoding="utf-8"))
        out = engine.find_duplicates(text, backlog)
    if not args.full:
        out.pop("answers", None)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
