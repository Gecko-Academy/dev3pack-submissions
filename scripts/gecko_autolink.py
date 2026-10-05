"""Record the Gecko capstone link for students who built one but never sent it.

    GH_TOKEN=$(gh auth token) python3 scripts/gecko_autolink.py            # dry run
    GH_TOKEN=$(gh auth token) python3 scripts/gecko_autolink.py --write    # write the files

Instructor-run. Every capstone lives at `github.com/<student>/my-gecko-buyer`, so a
student who did the work but missed the form can be found. For each folder under
`submissions/` with no `gecko/submission.json`, this looks for that repository and
records it exactly as the form's bot would (`gecko_from_issue.resolve`): public,
the student's own, at its newest commit.

AN UNTOUCHED TEMPLATE IS NEVER RECORDED. A repository whose newest commit is also a
commit of the course template holds none of the student's own work (three did on
5 Oct). It is reported, not handed in on anybody's behalf.

`--write` only writes files; commit and open the pull request yourself, so a person
reads the list before it lands.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error

from gecko_from_issue import ROOT, Refused, evidence_notes, github_api, hand_in, resolve

TEMPLATE = "Gecko-Academy/Dev3Pack-Gecko-Capstone-Project"


def is_template_commit(commit: str) -> bool:
    """True when the template has this commit. GitHub answers 422, not 404, for a
    commit the repository does not have."""
    try:
        return github_api(f"repos/{TEMPLATE}/commits/{commit}") is not None
    except urllib.error.HTTPError as error:
        if error.code == 422:
            return False
        raise


def candidates() -> list[str]:
    root = ROOT / "submissions"
    return sorted(
        (
            d.name
            for d in root.iterdir()
            if d.is_dir() and not (d / "gecko" / "submission.json").exists()
        ),
        key=str.lower,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the link files")
    args = parser.parse_args(argv)
    link = "https://github.com/{}/my-gecko-buyer"
    for student in candidates():
        if github_api(f"repos/{student}/my-gecko-buyer") is None:
            continue
        try:
            found = resolve(student, link.format(student), github_api)
        except Refused as reason:
            print(f"{student:<20} skipped: {reason}")
            continue
        commit = found["commit"]
        if is_template_commit(commit):
            print(f"{student:<20} skipped: untouched template (newest commit {commit[:7]} is the course's)")
            continue
        full_name = found["repo"].removeprefix("https://github.com/")
        missing = evidence_notes(full_name, commit, github_api)
        note = f"  (missing: {'; '.join(missing)})" if missing else ""
        if args.write:
            written, reply = hand_in(student, link.format(student), github_api)
            print(f"{student:<20} {'RECORDED' if written else 'NOT recorded: ' + reply} at {commit[:7]}{note}")
        else:
            print(f"{student:<20} would record {found['repo']} at {commit[:7]}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
