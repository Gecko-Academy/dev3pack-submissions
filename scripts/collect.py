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
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
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


def latest_runs(checks) -> list[dict]:
    """Only the most recent run of a check counts.

    A pull request that is re-checked -- after the check itself was fixed, or
    after a maintainer re-runs it -- keeps EVERY run in its status rollup, on
    the same commit. Reading all of them meant one stale failure vetoed a fresh
    success forever: pull request 84 passed on the fixed checker and was still
    left as "the check is red", because two runs from before the fix were in the
    list beside it.

    Trusting the latest is safe here: a learner cannot re-run a check, and a new
    push changes the head commit, which `merge` already pins.
    """
    checks = list(checks)
    if not checks:
        return []
    return [max(checks, key=lambda check: check.get("startedAt") or check.get("completedAt") or "")]


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
    named = latest_runs(check for check in checks if check.get("name") == REQUIRED_CHECK)
    if not named:
        return False, f"the {REQUIRED_CHECK!r} check has not reported yet"
    if any(check.get("status") not in {"COMPLETED", None} for check in named):
        return False, "checks still running"
    if any(check.get("conclusion") not in {"SUCCESS", "SKIPPED", "NEUTRAL"} for check in named):
        return False, f"the {REQUIRED_CHECK!r} check is red"
    return True, "green"


def one_per_item(pulls: list[dict], green: set[int] | None = None) -> dict[int, str]:
    """Refuse a second submission of the same item by the same learner.

    Both would write the same path, so merging both means the later silently
    overwrites the earlier and the day reads as two submissions when it was one.

    THE EARLIEST NUMBER USED TO WIN, AND IT BLOCKED PEOPLE. On 2026-09-17 a
    learner's first attempt was red — `submission.json` edited after `submit`
    wrote it — so they ran `submit` again and opened a second, green one. This
    rule then refused the GREEN one as a "duplicate of" the red one, and the red
    one could never merge. Neither moved until a maintainer closed the older by
    hand, and it happened twice the same day. A learner who fixes their mistake
    correctly must not be worse off than one who never made it.

    So the winner is the NEWEST GREEN attempt: the one the learner meant, and the
    only one that can merge at all. An attempt that is not green never wins and
    never refuses anything, so a red first try simply waits to be closed.

    A pull request carrying several items loses as a whole when any one of them
    is superseded, because both would write that item's path. It is not dropped:
    it is read again on the next run, once the winner has merged, and whatever it
    still adds is either merged or reported as a conflict with main.

    `green` is the set of pull request numbers whose checks passed. Without it
    every open pull request is treated as a candidate, which is the old
    behaviour and is only used by callers that have no verdicts yet.
    """
    refusals: dict[int, str] = {}
    candidates: dict[tuple[str, str], list[int]] = {}
    for pull in pulls:
        number = pull["number"]
        if green is not None and number not in green:
            continue
        author = pull["author"]["login"]
        for path in (entry["path"] for entry in pull.get("files", [])):
            parts = Path(path).parts
            if len(parts) < 3:
                continue
            numbers = candidates.setdefault((author, parts[2]), [])
            if number not in numbers:
                numbers.append(number)

    for (_author, item), numbers in candidates.items():
        if len(numbers) < 2:
            continue
        winner = max(numbers)
        for number in numbers:
            if number != winner:
                refusals[number] = (
                    f"superseded by #{winner}, a newer submission for {item} "
                    "— this one is read again once that merges"
                )
    return refusals


#: What a learner is told after a merge, and only when it is worth telling.
#: Silence on a clean pass is deliberate: at 250 hand-ins a day, a bot that
#: congratulates everybody is a bot everybody mutes, and then the one message
#: that mattered is muted with it.
EMPTY = """Merged — this is accepted and counted. One thing to flag, though.

**This hand-in has no results in it.** Its claim reads:

```json
"passed": [], "not_reached": {not_reached}
```

That happens when the notebook is submitted without being **run and saved**.
The outputs only land in the file when you save, and for `{item}` those saved
lines are the only record there is — nothing on our side re-runs this session.

To fix it, and it takes a few minutes:

1. Run every cell, top to bottom. Look for `✅ {first} passed`
2. **Save the notebook** — this is the step that is usually missing
3. `uv run bootcamp submit {item} --github {login}`
4. Commit and push to your fork, and open a pull request

Re-submitting always replaces the earlier attempt, so nothing is lost and
nothing is locked. Ask in the group if any of it is unclear — this is a
mechanics problem rather than a you problem."""

PARTIAL = """Merged — accepted and counted.

For your own tracking: **{done} of {total} checks reported a pass** here
({missing} still open). That is a perfectly normal hand-in and you do not have
to do anything.

If you want them, finish the cells, run them, save, and submit again — the
later submission replaces this one."""


def claim_of(pull: dict) -> dict | None:
    """The `submission.json` this pull request is handing in, or None.

    Read at the merged commit rather than from `main`, so a later merge in the
    same run cannot change what we quote back at somebody.
    """
    paths = [entry["path"] for entry in pull.get("files", [])]
    found = [path for path in paths if path.endswith("/submission.json")]
    if len(found) != 1:
        return None
    raw = gh(
        "api",
        f"repos/{REPO}/contents/{found[0]}?ref={pull['headRefOid']}",
        "--jq",
        ".content",
        check=False,
    ).strip()
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def note_for(pull: dict) -> str | None:
    """What to say about this submission, or None to say nothing."""
    claim = claim_of(pull)
    if not claim:
        return None
    result = claim.get("result") or {}
    passed = list(result.get("passed") or [])
    failed = list(result.get("failed") or [])
    missing = list(result.get("not_reached") or [])
    item = str(claim.get("chapter") or "this session")
    login = (claim.get("student") or {}).get("github", "YOUR-USERNAME")

    if not passed and not failed and missing:
        return EMPTY.format(
            not_reached=json.dumps(missing),
            item=item,
            first=missing[0],
            login=login,
        )
    if failed or missing:
        return PARTIAL.format(
            done=len(passed),
            total=len(passed) + len(failed) + len(missing),
            missing=", ".join(sorted(failed + missing)),
        )
    return None


def say(number: int, body: str) -> None:
    """Leave the note. A failure here must never fail the run: the merge is the
    product, and a comment that did not post is a smaller problem than a job
    that stopped halfway through a cohort's hand-ins."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(body)
        path = handle.name
    try:
        gh("pr", "comment", str(number), "--repo", REPO, "--body-file", path, check=False)
    finally:
        os.unlink(path)


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
    # The verdicts first, because the duplicate rule needs to know which of two
    # attempts can actually merge before it decides which one wins.
    verdicts = {pull["number"]: verdict(pull) for pull in pulls}
    duplicates = one_per_item(pulls, {number for number, (ok, _) in verdicts.items() if ok})
    mergeable: list[dict] = []
    print(f"{len(pulls)} open pull request(s)\n")
    for pull in sorted(pulls, key=lambda p: p["number"]):
        number = pull["number"]
        ok, reason = verdicts[number]
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
            note = note_for(pull)
            if note:
                say(pull["number"], note)
                print(f"  #{pull['number']} noted: its claim reports nothing passed")
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
