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

IT NEVER RUNS A NOTEBOOK. It parses JSON and nothing else, which is why it can
run in a job holding a write token while the pull-request check, which sees
unmerged content from a fork, holds nothing at all. Since 2026-09-22 it does
READ the notebooks of two items per learner (ch05, ch10) as JSON, for one
printed line: the weekly challenge's points. See `session_challenge`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

# Run as `python3 scripts/render_track.py`, so its own directory is on the path.
from check_bundle import CHALLENGE_NOTEBOOK, MAX_NOTEBOOK_BYTES

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


#: Full marks per passing exercise, and what help costs. The same arithmetic
#: `check_bundle.py` validates a claim against, so the two cannot disagree.
FULL_MARKS = 100
HINT_COST = 30
REVEAL_COST = 70


def score_of(item: dict, result: dict, help_block: object, challenge: int = 0) -> int | None:
    """What the row is worth, COMPUTED -- never read out of the claim.

    Reading `result["score"]` meant a bundle carried its own mark, and a bundle
    built before an item became scored carries `null` forever. Every ch01 handed
    in on day one did exactly that: the leaderboard showed a name and no number,
    and re-running the collector changed nothing, because the null was in the
    submission rather than in the arithmetic.

    Computing it from `passed` fixes those rows without asking anybody to submit
    again, and makes the score stop being something the submitter can state.

    `challenge` is the weekly challenge's points for ch05/ch10, from
    `challenge_points`, added AFTER the floor: help spent on the session cannot
    eat into a challenge, and a challenge is not help's refund.
    """
    if not item["scored"]:
        return None
    passed = result.get("passed") or []
    block = help_block if isinstance(help_block, dict) else {}
    hinted = block.get("hinted", 0)
    revealed = block.get("revealed", 0)
    if not all(isinstance(value, int) and value >= 0 for value in (hinted, revealed)):
        hinted = revealed = 0
    earned = len(passed) * FULL_MARKS - revealed * REVEAL_COST - hinted * HINT_COST
    return max(earned, 0) + challenge


#: The session each weekly challenge ADDS TO (founder ruling 2026-09-22): same
#: item, same row, same webhook shape; only the number grows. The course raises
#: these items' `max_score` in `items.json` by CHALLENGE_MAX to match.
CHALLENGE_WEEK = {"ch05": "1", "ch10": "2"}
CHALLENGE_MAX = 500
#: ASCII so `\d` cannot match a non-ASCII digit that `int()` would still accept.
#: Three digits on purpose: `9999/500` is not a challenge line at all.
CHALLENGE_LINE = re.compile(r"^\s*week (1|2) challenge: (\d{1,3})/500\s*$", re.ASCII)


def challenge_points(item_id: str, notebook: Path) -> int:
    """The weekly challenge's points, as the notebook printed them. 0 if none.

    FROM THE NOTEBOOK, NEVER THE CLAIM. `submission.json` is the learner's own
    statement; the printed line is at least bound to the notebook the claim's
    hash covers. Anything unreadable is worth 0 rather than a failed render:
    one malformed bundle must not blank the whole leaderboard.

    Read as JSON only, never executed; a symlink is refused, not followed.
    """
    week = CHALLENGE_WEEK.get(item_id)
    if week is None:
        return 0
    document = _read_notebook(notebook)
    if document is None:
        return 0
    found = 0
    for text in _stream_texts(document):
        for line in text.splitlines():
            match = CHALLENGE_LINE.match(line)
            # A week-2 line in ch05 is not ch05's challenge: skipped, so it can
            # neither add points nor mask the real line printed before it.
            if match and match.group(1) == week:
                found = min(int(match.group(2)), CHALLENGE_MAX)
    return found


def session_challenge(item_id: str, bundle: Path) -> int:
    """The challenge points a ch05/ch10 bundle carries, from either notebook.

    Most learners played week 1 in the course's demo notebook, not the homework
    one, so `bootcamp submit` hands that in beside it as `challenge.ipynb`. The
    points are the larger of the two last lines, each read by the same rule:
    carrying the demo can only reveal a score, never double one.
    """
    return max(
        challenge_points(item_id, bundle / "notebook.ipynb"),
        challenge_points(item_id, bundle / CHALLENGE_NOTEBOOK),
    )


def _read_notebook(notebook: Path) -> object | None:
    if notebook.parent.is_symlink():
        return None
    try:
        # O_NOFOLLOW refuses a symlinked file at open time, not at a check before it.
        fd = os.open(notebook, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        # Bounded read: never more than one byte past the cap, whatever the size says.
        raw = handle.read(MAX_NOTEBOOK_BYTES + 1)
    if len(raw) > MAX_NOTEBOOK_BYTES:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None


def _stream_texts(document: object) -> list[str]:
    """Every stream output's text, in document order. Tolerates any shape."""
    texts: list[str] = []
    cells = document.get("cells") if isinstance(document, dict) else None
    for cell in cells if isinstance(cells, list) else []:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        outputs = cell.get("outputs")
        for output in outputs if isinstance(outputs, list) else []:
            if not isinstance(output, dict) or output.get("output_type") != "stream":
                continue
            text = output.get("text")
            if isinstance(text, list) and all(isinstance(part, str) for part in text):
                texts.append("".join(text))
            elif isinstance(text, str):
                texts.append(text)
    return texts


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

        # A FINAL IS NOT A TRACK ROW. Its score comes from the course API and
        # is recorded in `finals/`, by `finals.yml`. Reading its
        # `submission.json` here would list every final handed in under
        # `problems` as "no course item called 'final'", and a non-empty
        # `problems` tells a consumer the whole document is partial.
        if item_id == "final":
            continue

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
                "score": score_of(
                    item,
                    result,
                    claim.get("help"),
                    session_challenge(item_id, claim_path.parent),
                ),
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
