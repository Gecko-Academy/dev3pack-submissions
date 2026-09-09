"""Merge every submission that passed its checks, and rebuild the track.

    python3 scripts/collect.py                 # dry run: say what would merge
    python3 scripts/collect.py --merge         # do it

WHY THIS IS AUTOMATED. Two hundred and fifty learners handing in a session each
day is two hundred and fifty pull requests. A person cannot read that many, and
a person who rubber-stamps them is not a control, only a delay that feels like
one. So the check IS the gate, and this merges what the check passed.

WHAT MAKES THAT SAFE, and it is one property: **a pull request may only touch
its own author's folder.** Verified twice, once by the pull-request check and
again here before merging, because the check ran on the author's word about a
commit and this runs on the merge. The blast radius of a dishonest submission
is therefore that learner's own row and nothing else.

WHAT IT STILL CANNOT CATCH: a claim that is internally consistent and untrue —
a notebook that prints its own passes. Nothing here re-runs a notebook, so
nothing here can tell. That is what the verifier lane is for, and until it
exists every score reads `claimed`.

WHY IT MAY HOLD A WRITE TOKEN when the pull-request check may not: this runs on
a schedule, from `main`, on code that was reviewed. It never checks out a fork,
never executes a submitted notebook, and only ever parses JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: This repository, named once.
REPO = "Gecko-Academy/dev3pack-submissions"
#: The check that has to be green, by its exact name, at the exact commit merged.
REQUIRED_CHECK = "submissions"
#: A ceiling per run. A cohort day is ~250; anything far above that is a sign
#: something is wrong, and stopping is better than merging through it.
MAX_PER_RUN = 400

LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")


class CollectError(Exception):
    """The run was refused. Nothing was merged."""


def gh(*args: str, check: bool = True) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=180)
    if result.returncode != 0 and check:
        raise CollectError(f"gh {' '.join(args)}\n{result.stderr.strip()}")
    return result.stdout


def open_pulls() -> list[dict]:
    """Every open pull request, with what the checks say and what it touches."""
    raw = gh(
        "pr",
        "list",
        "--repo",
        REPO,
        "--state",
        "open",
        "--limit",
        str(MAX_PER_RUN),
        "--json",
        "number,author,title,headRefOid,isDraft,mergeable,baseRefName,statusCheckRollup,files",
    )
    return json.loads(raw)


def verdict(pull: dict) -> tuple[bool, str]:
    """Whether this may be merged, and the reason when it may not.

    Ordered so the answer a learner needs comes first: what is wrong with their
    submission, before anything about the state of the branch.
    """
    author = str(pull.get("author", {}).get("login", ""))
    if not LOGIN.match(author):
        return False, f"author {author!r} is not a GitHub login"

    files = [entry["path"] for entry in pull.get("files", [])]
    if not files:
        return False, "changes nothing"
    stray = [path for path in files if not path.startswith(f"submissions/{author}/")]
    if stray:
        return False, f"touches {stray[0]} — a submission may only touch submissions/{author}/"

    if pull.get("isDraft"):
        return False, "draft"
    if pull.get("baseRefName") != "main":
        return False, f"based on {pull.get('baseRefName')}, not main"
    if pull.get("mergeable") == "CONFLICTING":
        return False, "conflicts with main"

    checks = pull.get("statusCheckRollup") or []
    named = [check for check in checks if check.get("name") == REQUIRED_CHECK]
    if not named:
        return False, f"the {REQUIRED_CHECK!r} check has not reported yet"
    if any(check.get("status") not in {"COMPLETED", None} for check in named):
        return False, "checks still running"
    if any(check.get("conclusion") not in {"SUCCESS", "SKIPPED", "NEUTRAL"} for check in named):
        return False, f"the {REQUIRED_CHECK!r} check is red"
    return True, "green"


def one_per_item(pulls: list[dict]) -> dict[int, str]:
    """Refuse a second green submission of the same item by the same learner.

    Both would write the same path, so merging both means the later silently
    overwrites the earlier and the day reads as two submissions when it was one.
    The earlier number wins; the learner closes or updates the other.
    """
    refusals: dict[int, str] = {}
    seen: dict[tuple[str, str], int] = {}
    for pull in sorted(pulls, key=lambda p: p["number"]):
        author = pull["author"]["login"]
        for path in (entry["path"] for entry in pull.get("files", [])):
            parts = Path(path).parts
            if len(parts) < 3:
                continue
            key = (author, parts[2])
            if key in seen and seen[key] != pull["number"]:
                refusals[pull["number"]] = f"duplicate of #{seen[key]} for {parts[2]}"
            else:
                seen.setdefault(key, pull["number"])
    return refusals


def merge(number: int, head: str) -> None:
    """Squash-merge, refusing if the branch moved after its checks were read."""
    gh(
        "pr",
        "merge",
        str(number),
        "--repo",
        REPO,
        "--squash",
        "--delete-branch",
        "--match-head-commit",
        head,
    )


def refresh() -> None:
    """Bring the checkout up to the merges that just happened.

    The merges go through the API, so `main` moves while this checkout stays
    where it started. Rendering the track without this reads the tree from
    BEFORE the merges and writes a track that is missing exactly the
    submissions this run just accepted — which looked, in testing, like the
    merge had silently failed.
    """
    subprocess.run(["git", "fetch", "origin", "main"], cwd=ROOT, check=True, timeout=180)
    subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=ROOT, check=True, timeout=180)


def render_track() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render_track.py")],
        capture_output=True,
        text=True,
        timeout=300,
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        raise CollectError(f"the track could not be rebuilt:\n{result.stderr}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merge", action="store_true", help="merge; otherwise dry run")
    args = parser.parse_args(argv)

    pulls = open_pulls()
    duplicates = one_per_item(pulls)
    mergeable: list[dict] = []
    print(f"{len(pulls)} open pull request(s)\n")
    for pull in sorted(pulls, key=lambda p: p["number"]):
        number = pull["number"]
        ok, reason = verdict(pull)
        if ok and number in duplicates:
            ok, reason = False, duplicates[number]
        mark = "merge" if ok else "leave"
        print(f"  {mark:<6} #{number:<5} {pull['author']['login']:<22} {reason}")
        if ok:
            mergeable.append(pull)

    if not args.merge:
        print(f"\nDry run. {len(mergeable)} would merge. Re-run with --merge.")
        return 0
    if len(pulls) >= MAX_PER_RUN:
        raise CollectError(
            f"{len(pulls)} open pull requests is at the {MAX_PER_RUN} ceiling; "
            "something is wrong, and stopping beats merging through it"
        )

    merged = 0
    for pull in mergeable:
        try:
            merge(pull["number"], pull["headRefOid"])
            merged += 1
        except CollectError as error:
            # One bad merge must not strand the rest: a branch that moved
            # between the listing and now is exactly what the guard is for.
            print(f"  #{pull['number']} did not merge: {str(error)[:120]}", file=sys.stderr)
    print(f"\nmerged {merged} of {len(mergeable)}")

    if merged:
        refresh()
    render_track()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CollectError as error:
        print(f"\n{error}", file=sys.stderr)
        raise SystemExit(1) from error
