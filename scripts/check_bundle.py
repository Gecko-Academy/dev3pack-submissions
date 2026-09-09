"""Check one submitted bundle, with no credentials and no course checkout.

    python3 scripts/check_bundle.py submissions/octocat/ch03

This is everything CI can honestly say about a submission without re-running the
notebook, which a fork pull request may never do, because that job holds no
secret and no write token by design.

WHY THIS IS A FILE AND NOT A HEREDOC IN THE WORKFLOW. It was a heredoc for about
an hour, and in that hour the schema moved from v1 to v2 and the copy inside the
YAML did not. Every submission failed on a string nobody could see in a diff. A
file can be read, tested, and grepped.

WHY THE SCHEMA IS DUPLICATED HERE AT ALL. This repository is public and must
never need the private course to check a submission. That duplication is
deliberate, and it is why SCHEMA sits alone at the top with a comment: when the
course bumps it, this is the one line to follow.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

#: Must match `bootcamp_agent.submission.SCHEMA` in the course repository.
SCHEMA = "dev3pack.submission.v2"

#: Must match FULL_MARKS / HINT_COST / REVEAL_COST in `bootcamp_agent.hints`.
#: Duplicated for the same reason as SCHEMA: this repository is public and must
#: never need the private course to check a submission.
FULL_MARKS = 100
HINT_COST = 30
REVEAL_COST = 70

ITEM = re.compile(r"^(?:ch|w|cap)\d{2}$")


def submission_id_for(claim: dict) -> str:
    """The id this claim should carry, recomputed from the claim itself.

    Must match `bootcamp_agent.submission.submission_id_for`. Sorted keys, no
    incidental whitespace, UTF-8, taken over everything except the id. Editing
    any field after `bootcamp submit` wrote the file changes this, which is the
    point: the id is the claim's own fingerprint.
    """
    body = {key: value for key, value in claim.items() if key != "submission_id"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sub_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def problems_with(directory: Path) -> list[str]:
    """Everything wrong with this bundle. Empty means it is well-formed."""
    found: list[str] = []
    claim_path = directory / "submission.json"
    notebook = directory / "notebook.ipynb"

    if not claim_path.is_file():
        return [f"{directory}: no submission.json"]
    if not notebook.is_file():
        found.append(f"{directory}: no notebook.ipynb beside the claim")

    try:
        claim = json.loads(claim_path.read_text())
    except json.JSONDecodeError as error:
        return [f"{claim_path}: not valid JSON ({error})"]
    if not isinstance(claim, dict):
        return [f"{claim_path}: the claim must be a JSON object"]

    # Additive since 2026-09-09. A bundle written before ids existed carries
    # none and is still valid; one that carries a WRONG id was edited.
    stated = claim.get("submission_id")
    if stated is not None:
        expected = submission_id_for(claim)
        if stated != expected:
            found.append(
                f"{claim_path}: submission_id is {stated}, but this claim hashes to "
                f"{expected}. Re-run `uv run bootcamp submit` rather than editing the file"
            )

    if claim.get("schema") != SCHEMA:
        found.append(
            f"{claim_path}: schema {claim.get('schema')!r}, expected {SCHEMA!r}. "
            "Re-run `uv run bootcamp submit` with an up-to-date course checkout"
        )

    owner = directory.parent.name
    if claim.get("student", {}).get("github") != owner:
        found.append(
            f"{claim_path}: claims {claim.get('student', {}).get('github')!r} but sits in {owner!r}"
        )

    item = str(claim.get("chapter", ""))
    if not ITEM.match(item):
        found.append(f"{claim_path}: {item!r} is not a chapter or unit id")
    elif directory.name != item:
        found.append(f"{claim_path}: claims {item} but sits in a folder called {directory.name}")

    # The score, checked against the claim's own numbers. This needs no course
    # checkout, and under the manual-merge route it is the ONLY automated check
    # on the one number a gradebook consumes: the notebook's hash covers the
    # notebook, not the claim, so an edited score leaves the hash intact.
    result = claim.get("result", {})
    if result.get("scored"):
        passed = result.get("passed") or []
        help_block = claim.get("help") or {}
        hinted = help_block.get("hinted", 0)
        revealed = help_block.get("revealed", 0)
        if not all(isinstance(v, int) and v >= 0 for v in (hinted, revealed)):
            found.append(f"{claim_path}: help must be counts, not {help_block!r}")
        else:
            expected_score = max(
                len(passed) * FULL_MARKS - revealed * REVEAL_COST - hinted * HINT_COST, 0
            )
            if result.get("score") != expected_score:
                found.append(
                    f"{claim_path}: score is {result.get('score')}, but "
                    f"{len(passed)} passed with {hinted} hint(s) and {revealed} reveal(s) "
                    f"makes {expected_score}. Re-run `uv run bootcamp submit`"
                )
    elif result.get("score") is not None:
        found.append(f"{claim_path}: {item} is not marked, so score must be null")

    if notebook.is_file():
        expected = claim.get("evidence", {}).get("notebook_sha256")
        actual = hashlib.sha256(notebook.read_bytes()).hexdigest()
        if expected != actual:
            found.append(
                f"{claim_path}: the notebook is not the one this score was claimed for. "
                "Hand-editing submission.json is the usual cause; re-run `bootcamp submit`"
            )

    return found


def main(argv: list[str] | None = None) -> int:
    directories = [Path(a) for a in (argv if argv is not None else sys.argv[1:])]
    if not directories:
        print("usage: check_bundle.py <submissions/user/chapter> ...")
        return 2

    failed = False
    for directory in directories:
        found = problems_with(directory)
        if found:
            failed = True
            for problem in found:
                print(f"::error::{problem}")
        else:
            print(f"ok: {directory}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
