"""Check one Gecko capstone hand-in: `submissions/<github>/gecko/submission.json`.

Called by `check_bundle.py` for a folder named `gecko`. The Gecko capstone is the
student's own `my-gecko-buyer` repository, judged from that repository. What is
handed in here is only the LINK: which repository, at which commit. Nothing is
scored, and nothing here posts anywhere.

ONE FILE, FOUR KEYS, THE FIFTH OPTIONAL. Students without a terminal write this
file by hand in the GitHub web editor, so it asks for nothing they cannot copy
from their repository's page. `submitted_at` is accepted when the CLI writes it.

THE REPOSITORY MUST BE THE STUDENT'S OWN. Its owner is compared with the folder,
which `verify.yml` has tied to the pull request's author. That alone refuses the
course template (owned by Gecko-Academy) and somebody else's buyer. GitHub logins
are case-insensitive, so the comparison is too.

NOTHING HERE TOUCHES THE NETWORK. Whether the commit exists, and what is in it,
is read later by `finish_line.py`, which the instructor runs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from check_final import HEX40, REPO_URL, SUBMITTED_AT, _keys, _load

GECKO = "gecko"
GECKO_FILES = {"submission.json"}
REQUIRED_KEYS = {"kind", "github", "repo", "commit"}
OPTIONAL_KEYS = {"submitted_at"}
MAX_SUBMISSION_BYTES = 64 * 1024


def _submission_problems(path: Path, document: object, owner: str) -> list[str]:
    if not isinstance(document, dict):
        return [f"{path}: must be a JSON object"]
    found = [
        problem
        for problem in _keys(path, "the top level", document, REQUIRED_KEYS | OPTIONAL_KEYS)
        if not problem.startswith(f"{path}: missing")
    ]
    missing = sorted(REQUIRED_KEYS - set(document))
    if missing:
        found.append(f"{path}: missing key(s) in the top level: {', '.join(missing)}")

    if "kind" in document and document["kind"] != GECKO:
        found.append(f"{path}: kind is {document['kind']!r}, expected 'gecko'")
    if "github" in document and document["github"] != owner:
        found.append(f"{path}: claims {document['github']!r} but sits in {owner!r}")

    repo = document.get("repo")
    if "repo" in document:
        match = REPO_URL.match(repo) if isinstance(repo, str) else None
        if not match or match.group(2) in {".", ".."}:
            found.append(
                f"{path}: repo must be https://github.com/<you>/<name> with nothing after it, "
                f"not {repo!r}"
            )
        elif match.group(1).lower() != owner.lower():
            found.append(
                f"{path}: repo {repo!r} belongs to {match.group(1)!r}; hand in your own "
                f"my-gecko-buyer, under https://github.com/{owner}/"
            )
    commit = document.get("commit")
    if "commit" in document and not (isinstance(commit, str) and HEX40.match(commit)):
        found.append(
            f"{path}: commit must be the full 40-character commit id (lowercase), "
            f"not {commit!r}. On GitHub, open Commits and press the copy button."
        )

    stamp = document.get("submitted_at")
    if "submitted_at" in document:
        valid = isinstance(stamp, str) and bool(SUBMITTED_AT.match(stamp))
        if valid:
            try:
                datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                valid = False
        if not valid:
            found.append(f"{path}: submitted_at must be an ISO 8601 UTC time, not {stamp!r}")
    return found


def gecko_problems(directory: Path) -> list[str]:
    """Everything wrong with a Gecko capstone hand-in. Empty means it is well-formed."""
    found: list[str] = []
    for entry in sorted(directory.iterdir()):
        if entry.name not in GECKO_FILES:
            found.append(
                f"{directory}: unexpected file in the bundle: {entry.name} "
                "(the Gecko capstone hand-in is submission.json only; your code stays in "
                "your own repository)"
            )
    path = directory / "submission.json"
    if not path.exists() and not path.is_symlink():
        return found + [f"{directory}: no submission.json in the Gecko capstone hand-in"]
    document, problem = _load(path, MAX_SUBMISSION_BYTES)
    return found + ([problem] if problem else _submission_problems(path, document, directory.parent.name))
