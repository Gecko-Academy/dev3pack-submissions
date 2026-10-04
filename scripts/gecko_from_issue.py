"""Turn a "Hand in my Gecko capstone" issue into `submissions/<author>/gecko/submission.json`.

    AUTHOR=octocat BODY="..." GH_TOKEN=... python3 scripts/gecko_from_issue.py reply.md

Run by `gecko-link.yml`. The student pasted one link into an issue form; this
does everything the hand-typed route asked of them:

- WHO: the issue's author, as GitHub reports it. Not a field anybody types, so it
  cannot claim somebody else.
- WHICH REPOSITORY: the first GitHub link in the body, cut back to
  `https://github.com/<owner>/<name>`. A trailing `.git`, `/`, or `/tree/main` is
  forgiven; the owner must be the author.
- WHICH COMMIT: the newest on the repository's default branch, read from the
  public API at hand-in time, so nobody copies a 40-character id.

THE BODY IS UNTRUSTED. It arrives through an environment variable, never through
the workflow's shell, and only a regex-matched URL is ever used from it.

Exit 0 and a reply when the file was written; exit 1 and a reply saying what to
fix when it was not. Either way the reply goes back on the issue.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from check_gecko import GECKO, gecko_problems

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})")
LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")
COURSE_ORG = "gecko-academy"

Fetch = Callable[[str], dict | None]


class Refused(Exception):
    """A reason the student can act on. Shown on the issue as is."""


def github_api(path: str) -> dict | None:
    """GET https://api.github.com/<path>, or None for a 404."""
    request = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "User-Agent": "dev3pack-gecko-link",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return json.loads(reply.read())
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def repo_from(body: str) -> tuple[str, str]:
    found = LINK.search(body or "")
    if not found:
        raise Refused(
            "I could not find a GitHub link in the issue. Edit it and paste one like "
            "`https://github.com/<you>/my-gecko-buyer`."
        )
    owner, name = found.group(1), found.group(2)
    name = name.removesuffix(".git")
    if name in {"", ".", ".."}:
        raise Refused(f"`{found.group(0)}` is not a repository link.")
    return owner, name


def resolve(author: str, body: str, fetch: Fetch) -> dict[str, str]:
    """The submission, or Refused with what to fix."""
    owner, name = repo_from(body)
    if owner.lower() == COURSE_ORG:
        raise Refused(
            "That link is the course template, not your copy of it. Make it yours "
            "(`git remote rename origin upstream`, then "
            "`gh repo create my-gecko-buyer --public --source . --remote origin --push`), "
            "and edit this issue with the new link."
        )
    if owner.lower() != author.lower():
        raise Refused(
            f"`{owner}/{name}` belongs to `{owner}`, and this issue was opened by "
            f"`{author}`. Hand in the repository under your own account."
        )
    repo = fetch(f"repos/{owner}/{name}")
    if repo is None:
        raise Refused(
            f"`https://github.com/{owner}/{name}` does not exist, or it is private. "
            "Make it public (Settings, General, Danger Zone, Change visibility), then "
            "edit this issue."
        )
    if repo.get("private"):
        raise Refused(f"`{repo['full_name']}` is private. Make it public, then edit this issue.")
    branch = repo.get("default_branch") or "main"
    head = fetch(f"repos/{repo['full_name']}/commits/{branch}")
    if not head or not head.get("sha"):
        raise Refused(f"`{repo['full_name']}` has no commits yet. Push your work, then edit this issue.")
    return {
        "kind": GECKO,
        "github": author,
        "repo": f"https://github.com/{repo['full_name']}",
        "commit": head["sha"],
        "submitted_at": datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def evidence_notes(full_name: str, commit: str, fetch: Fetch) -> list[str]:
    """What the judges will not find at that commit. Warnings, never a refusal."""
    tree = fetch(f"repos/{full_name}/git/trees/{commit}?recursive=1") or {}
    paths = {entry["path"] for entry in tree.get("tree", []) if entry.get("type") == "blob"}
    notes = []
    if not any(p.startswith("receipts/") and p.endswith(".md") for p in paths):
        notes.append("no `receipts/*.md`: no landed purchase is committed yet")
    if not any(p.startswith("refusals/") for p in paths):
        notes.append("no `refusals/`: run `make smoke` and commit what it writes")
    if "smoke-report.json" not in paths:
        notes.append("no `smoke-report.json`: run `make smoke` (or `make smoke-recorded`)")
    return notes


def hand_in(author: str, body: str, fetch: Fetch, root: Path = ROOT) -> tuple[bool, str]:
    """(written, the reply for the issue)."""
    if not LOGIN.match(author):
        return False, f"`{author}` is not a GitHub login."
    try:
        submission = resolve(author, body, fetch)
    except Refused as reason:
        return False, f"Not recorded yet. {reason}"
    where = root / "submissions" / author / GECKO
    where.mkdir(parents=True, exist_ok=True)
    for stray in where.iterdir():
        stray.unlink()
    (where / "submission.json").write_text(json.dumps(submission, indent=2) + "\n", encoding="utf-8")
    problems = gecko_problems(where)
    if problems:
        return False, "Not recorded: " + "; ".join(problems)

    full_name = submission["repo"].removeprefix("https://github.com/")
    commit = submission["commit"]
    lines = [
        f"Recorded: [{full_name} at `{commit[:7]}`]({submission['repo']}/tree/{commit}).",
        "",
        "It appears under **Finish line** in [TRACK.md](https://github.com/Gecko-Academy/dev3pack-submissions/blob/main/TRACK.md#finish-line) within a few minutes.",
        "Pushed more later? Edit this issue (any change) and the newest commit is recorded.",
    ]
    notes = evidence_notes(full_name, commit, fetch)
    if notes:
        lines += ["", "Recorded anyway, but the judges will look for these and not find them:"]
        lines += [f"- {note}" for note in notes]
    return True, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    reply_path = Path(args[0]) if args else Path("reply.md")
    written, reply = hand_in(os.environ.get("AUTHOR", ""), os.environ.get("BODY", ""), github_api)
    reply_path.write_text(reply + "\n", encoding="utf-8")
    print(reply)
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
