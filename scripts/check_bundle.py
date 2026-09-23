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

# Run as `python3 scripts/check_bundle.py`, so its own directory is on the path.
from check_final import FINAL, final_problems

#: Must match `bootcamp_agent.submission.SCHEMA` in the course repository.
SCHEMA = "dev3pack.submission.v2"

#: Must match FULL_MARKS / HINT_COST / REVEAL_COST in `bootcamp_agent.hints`.
#: Duplicated for the same reason as SCHEMA: this repository is public and must
#: never need the private course to check a submission.
FULL_MARKS = 100
HINT_COST = 30
REVEAL_COST = 70

ITEM = re.compile(r"^(?:ch|w|cap)\d{2}$")

#: A bundle is exactly these two files (plus CHALLENGE_NOTEBOOK, below, in two
#: items only), and this refuses anything else rather than
#: ignoring it: a check that silently skips what it does not understand is how a
#: payload rides along beside an honest claim.
ALLOWED_FILES = {"submission.json", "notebook.ipynb"}

#: The one optional third file: the weekly challenge's demo notebook (demo 08
#: for ch05, demo 10 for ch10), attached by `bootcamp submit` so the points the
#: learner earned outside the homework notebook are handed in with it. Only
#: those two items may carry it; anywhere else it is refused like any stranger.
CHALLENGE_NOTEBOOK = "challenge.ipynb"
CHALLENGE_ITEMS = frozenset({"ch05", "ch10"})

#: Ceilings, not targets. A claim is a few hundred bytes and a teaching notebook
#: is well under a megabyte. At 250 learners handing in 28 items each, an
#: unbounded notebook is also how a repository becomes unclonable.
MAX_CLAIM_BYTES = 64 * 1024
MAX_NOTEBOOK_BYTES = 8 * 1024 * 1024


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
    # A final is answers, not a notebook and a claim, so it has its own rules.
    # Routed on the FOLDER, which `verify.yml` has already tied to the pull
    # request's author; no homework item can be called `final` (see ITEM).
    if directory.name == FINAL:
        return final_problems(directory)
    found: list[str] = []
    claim_path = directory / "submission.json"
    notebook = directory / "notebook.ipynb"

    if not claim_path.is_file():
        return [f"{directory}: no submission.json"]
    if not notebook.is_file():
        found.append(f"{directory}: no notebook.ipynb beside the claim")

    challenge = directory / CHALLENGE_NOTEBOOK
    for entry in sorted(directory.iterdir()):
        if entry.name == CHALLENGE_NOTEBOOK and directory.name not in CHALLENGE_ITEMS:
            found.append(
                f"{directory}: {CHALLENGE_NOTEBOOK} is only accepted in "
                f"{' and '.join(sorted(CHALLENGE_ITEMS))} bundles, not {directory.name}"
            )
        elif entry.name not in ALLOWED_FILES and entry.name != CHALLENGE_NOTEBOOK:
            found.append(f"{directory}: unexpected file in the bundle: {entry.name}")
        elif entry.is_symlink() or not entry.is_file():
            found.append(f"{directory}: {entry.name} must be a regular file")
    for name, cap in (
        (claim_path.name, MAX_CLAIM_BYTES),
        (notebook.name, MAX_NOTEBOOK_BYTES),
        (challenge.name, MAX_NOTEBOOK_BYTES),
    ):
        path = directory / name
        if path.is_file() and path.stat().st_size > cap:
            size = path.stat().st_size
            found.append(f"{directory}: {name} is {size} bytes, over the {cap}-byte cap")

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
        if expected not in notebook_digests(notebook.read_bytes()):
            found.append(
                f"{claim_path}: the notebook is not the one this score was claimed for. "
                "Hand-editing submission.json is the usual cause; re-run `bootcamp submit`"
            )

    found += challenge_problems(directory, claim_path, claim)
    return found


def challenge_problems(directory: Path, claim_path: Path, claim: dict) -> list[str]:
    """The optional challenge notebook, bound to the claim the way `notebook.ipynb` is.

    Absent is valid. Present, it must be a notebook (JSON with a `cells` list;
    nothing is executed) and match `evidence.challenge_sha256`, so the printed
    challenge line the leaderboard reads cannot be edited in after `bootcamp
    submit`. A claim that records a digest with no file beside it is refused
    too: the claim would describe a bundle that is not the one handed in.

    Placement, symlinks and size are reported by `problems_with`; this only
    reads a file those rules have already let through.
    """
    path = directory / CHALLENGE_NOTEBOOK
    evidence = claim.get("evidence")
    expected = evidence.get("challenge_sha256") if isinstance(evidence, dict) else None
    if not (path.exists() or path.is_symlink()):
        if expected is not None:
            return [
                f"{claim_path}: the claim records a {CHALLENGE_NOTEBOOK} but the bundle has none. "
                "Re-run `bootcamp submit`"
            ]
        return []
    if (
        directory.name not in CHALLENGE_ITEMS
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_NOTEBOOK_BYTES
    ):
        return []
    raw = path.read_bytes()
    found: list[str] = []
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        document = None
    if not (isinstance(document, dict) and isinstance(document.get("cells"), list)):
        found.append(f"{path}: not a notebook (JSON with a `cells` list)")
    if expected not in notebook_digests(raw):
        found.append(
            f"{claim_path}: {CHALLENGE_NOTEBOOK} is not the one this bundle was submitted with. "
            "Editing either file by hand is the usual cause; re-run `bootcamp submit`"
        )
    return found


def notebook_digests(raw: bytes) -> set[str]:
    """Every digest this notebook may honestly have been claimed under.

    LINE ENDINGS ARE NOT CONTENT. On Windows the notebook on disk has CRLF, so
    `bootcamp submit` hashes CRLF bytes -- and git, with `core.autocrlf`, commits
    LF. The file CI reads is then byte-different from the one that was hashed,
    though not one character of the notebook changed. Pull request 84 was refused
    for exactly that, and told its author they had re-run the notebook after
    submitting, which they had not.

    So the claim is compared against the file in both conventions. That
    tolerates a line-ending conversion and nothing else: any edit to the content
    still changes both digests, which the tests hold.
    """
    lf = raw.replace(b"\r\n", b"\n")
    return {
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(lf).hexdigest(),
        hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest(),
    }


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
