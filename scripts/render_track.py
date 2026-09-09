"""Render TRACK.md and track.json from the submissions in this repository.

    python3 scripts/render_track.py
    python3 scripts/render_track.py --check     # CI: fail if either is stale

NO COURSE CHECKOUT. That is the whole point of `items.json`, which the course
generates and ships here: the ids, the titles, whether an item is scored and
what it is out of. No answers, no checkers, no exercise bodies. It is the
denominator, and without it a blank cell cannot be told from a zero.

A PURE FOLD. Everything here is computed from `items.json` and the merged
`submissions/` tree, so regenerating from a clean checkout reproduces both files
byte for byte. If they ever differ, the submissions are right and these were
stale. Never hand-edit them.

IT NEVER OPENS A NOTEBOOK. It parses JSON claims and nothing else, which is why
it can run in a job holding a write token while the pull-request check, which
sees unmerged content from a fork, holds nothing at all.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ITEMS = ROOT / "items.json"
SUBMISSIONS = ROOT / "submissions"
MARKDOWN = ROOT / "TRACK.md"
JSON_OUT = ROOT / "track.json"

SCHEMA = "dev3pack.track.v1"
#: Must match `bootcamp_agent.submission.SCHEMA`, like `check_bundle.py`.
SUBMISSION_SCHEMA = "dev3pack.submission.v2"
LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")

VERIFIED = "verified"
CLAIMED = "claimed"
UNVERIFIABLE = "unverifiable"
HANDED_IN = "handed in"


def load_items() -> list[dict]:
    if not ITEMS.is_file():
        raise SystemExit(f"{ITEMS.name} is missing; the course ships it here")
    payload = json.loads(ITEMS.read_text(encoding="utf-8"))
    if payload.get("schema") != "dev3pack.items.v1":
        raise SystemExit(f"unknown items schema {payload.get('schema')!r}")
    return list(payload["items"])


def tier_of(item: dict, claim: dict) -> str:
    """What may honestly be said about this row.

    Four states, and collapsing any two of them misreads somebody's work.
    Nothing re-runs a notebook yet, so nothing is `verified`; the field exists
    so that when the verifier lands, no consumer has to change.
    """
    if not item["scored"]:
        return HANDED_IN
    if not item["verifiable"]:
        return UNVERIFIABLE
    return VERIFIED if claim.get("verified") is True else CLAIMED


def read_tree() -> tuple[list[dict], list[str]]:
    """One entry per (student, item), plus everything that could not be read."""
    entries: list[dict] = []
    problems: list[str] = []
    if not SUBMISSIONS.is_dir():
        return entries, [f"no {SUBMISSIONS.name}/ directory"]

    known = {item["id"]: item for item in load_items()}
    for claim_path in sorted(SUBMISSIONS.glob("*/*/submission.json")):
        student = claim_path.parent.parent.name
        item_id = claim_path.parent.name
        where = claim_path.relative_to(ROOT).as_posix()

        if not LOGIN.match(student):
            problems.append(f"{where}: {student!r} is not a GitHub login")
            continue
        item = known.get(item_id)
        if item is None:
            problems.append(f"{where}: no course item called {item_id!r}")
            continue
        try:
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            problems.append(f"{where}: not valid JSON ({error})")
            continue
        if claim.get("schema") != SUBMISSION_SCHEMA:
            problems.append(f"{where}: schema {claim.get('schema')!r}")
            continue
        if claim.get("chapter") != item_id:
            problems.append(f"{where}: claims {claim.get('chapter')!r} in {item_id}/")
            continue
        if str(claim.get("student", {}).get("github", "")) != student:
            problems.append(f"{where}: filed under {student}, claims someone else")
            continue

        result = claim.get("result", {})
        entries.append(
            {
                "github": student,
                "item": item_id,
                "submitted_at": claim.get("submitted_at"),
                "ran": bool(result.get("ran")),
                "passed": list(result.get("passed", [])),
                "failed": list(result.get("failed", [])),
                "not_reached": list(result.get("not_reached", [])),
                "scored": bool(item["scored"]),
                "score": result.get("score") if item["scored"] else None,
                "max_score": item["max_score"],
                "tier": tier_of(item, claim),
            }
        )
    return entries, problems


def render_json(entries: list[dict], problems: list[str], items: list[dict]) -> str:
    payload = {
        "schema": SCHEMA,
        "items": [
            {
                "id": item["id"],
                "title": item["title"],
                "scored": item["scored"],
                "verifiable": item["verifiable"],
                "max_score": item["max_score"],
            }
            for item in items
        ],
        "entries": sorted(entries, key=lambda e: (e["github"], e["item"])),
        "problems": problems,
    }
    return json.dumps(payload, indent=2) + "\n"


def render_markdown(entries: list[dict], problems: list[str], items: list[dict]) -> str:
    students = sorted({entry["github"] for entry in entries})
    by_key = {(entry["github"], entry["item"]): entry for entry in entries}
    graded = [item for item in items if item["kind"] != "unit"]
    units = [item for item in items if item["kind"] == "unit"]

    lines = [
        "<!-- generated by scripts/render_track.py — edit the submissions, not this file -->",
        "",
        "# Track",
        "",
        f"{len(students)} student(s) have handed something in, across "
        f"{len([i for i in graded if i['scored']])} marked items.",
        "",
        "A cell shows what that item was worth. `handed in` means the work was",
        "submitted and is not marked: week 0 is self-paced, and the two",
        "assistant-driven sessions can never be re-run. An empty cell means",
        "nothing has been submitted yet, which is not the same as a zero.",
        "",
        "| Student | " + " | ".join(item["id"] for item in graded) + " | Total |",
        "|---" * (len(graded) + 2) + "|",
    ]
    for student in students:
        cells = []
        earned = available = 0
        for item in graded:
            entry = by_key.get((student, item["id"]))
            if entry is None:
                cells.append("")
            elif not entry["scored"]:
                cells.append("handed in")
            else:
                cells.append(f"{entry['score']}/{entry['max_score']}")
                earned += entry["score"] or 0
                available += entry["max_score"] or 0
        total = f"{earned}/{available}" if available else "—"
        lines.append(f"| {student} | " + " | ".join(cells) + f" | {total} |")

    lines += ["", "## Week 0", "", "Handed in as a record of the work, never marked.", ""]
    lines += ["| Student | Units handed in |", "|---|---|"]
    for student in students:
        done = [item["id"] for item in units if (student, item["id"]) in by_key]
        lines.append(f"| {student} | {len(done)}/{len(units)} |")

    if problems:
        lines += ["", "## Problems", ""]
        lines += [f"- {problem}" for problem in problems]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if either file is stale")
    args = parser.parse_args(argv)

    items = load_items()
    entries, problems = read_tree()
    files = {
        JSON_OUT: render_json(entries, problems, items),
        MARKDOWN: render_markdown(entries, problems, items),
    }
    if args.check:
        stale = [p.name for p, text in files.items() if not p.is_file() or p.read_text() != text]
        if stale:
            print(f"stale: {', '.join(stale)} — run scripts/render_track.py", file=sys.stderr)
            return 1
        students = len({entry["github"] for entry in entries})
        print(f"track current: {len(entries)} submission(s) from {students} student(s)")
        return 0
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    print(f"{len(entries)} submission(s) from {len({e['github'] for e in entries})} student(s)")
    for problem in problems:
        print(f"  problem: {problem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
