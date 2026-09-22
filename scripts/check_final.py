"""Check one final-assignment bundle: `submissions/<github>/final/`.

Called by `check_bundle.py` for a folder named `final`; everything else is
homework and never reaches this file.

WHY THIS CHECK CARRIES MORE WEIGHT THAN THE HOMEWORK ONE. A merged final is
posted by `finals.yml` to the course API with the server key, and the API signs
a receipt from what it is given. The pull-request check is the last thing that
reads the bundle before a job holding a secret does. So this refuses anything it
does not understand, rather than passing it along for the API to interpret.

NOTHING HERE TOUCHES THE NETWORK OR EXECUTES LEARNER CONTENT. It parses two JSON
files. `repo` and `commit` are shape-checked only: whether that commit exists is
not something a check without a token can honestly say.

THE CAPS MIRROR THE APP (`app/api/dev3pack/submit/route.ts`): 8000 characters
per answer and 40 citations. The app counts characters as JavaScript does, in
UTF-16 code units, so this does too; counting code points would pass an answer
full of emoji that the app then refuses, and one refused answer fails the
scoring step for everybody merged in the same push.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

FINAL = "final"

#: Exactly these two, and a notebook is refused rather than ignored: a final is
#: answers, and anything riding beside them is something nobody reviews.
FINAL_FILES = {"answers.json", "submission.json"}

#: 1 MiB, sized from the worst honest case rather than the typical one. The
#: private set is ~15 questions. Fifteen answers at the 8000-character cap, all
#: non-ASCII and written by `json.dumps` with its default escaping (six bytes a
#: character), is ~720 KB, plus up to 40 citations each. Ordinary prose is a
#: fifth of that. A tighter cap would refuse a learner who did nothing wrong; this
#: one still keeps a hostile file from bloating a public clone.
MAX_ANSWERS_BYTES = 1024 * 1024
#: The same ceiling as a homework claim: this file is a few hundred bytes.
MAX_SUBMISSION_BYTES = 64 * 1024

MAX_ANSWER_CHARS = 8000
MAX_CITATIONS = 40

#: This repository hands in ONE cohort. The API writes the cohort into the
#: credential it signs, so a free-form value would let a learner mint a
#: receipt under another cohort, or the public demo one.
COHORTS = {"2026-09"}

#: Practice ids are `fa-01`..`fa-10`, the smoke set uses `pf-NN`. The private
#: set's ids are not visible here, so this admits the family rather than a list:
#: lowercase letters, a dash, two or three digits. It rules out every key that
#: means something to a JavaScript object (`__proto__`, `constructor`) and
#: anything with a dot, slash or space.
TASK_ID = re.compile(r"^[a-z]{1,8}-[0-9]{2,3}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
QUESTION_SET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
#: A GitHub login, and a repository name as GitHub allows it. Anchored at both
#: ends, so a query string, a fragment, a trailing slash, a deeper path and any
#: other host or scheme are all refused by the same rule.
REPO_URL = re.compile(r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})$")
#: `path/to/module.py:ClassName`, relative, no `..` segment. Recorded, never run.
AGENT = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|:))[A-Za-z0-9_./-]{1,200}\.py:[A-Za-z_][A-Za-z0-9_]{0,99}$")
SUBMITTED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")

ANSWERS_KEYS = {"cohort", "course_release", "question_set_id", "answers"}
ANSWER_KEYS = {"answer", "citations", "confidence", "needs_human_review"}
SUBMISSION_KEYS = {"kind", "github", "repo", "commit", "agent", "submitted_at"}


class _Refused(ValueError):
    """JSON the checker will not read, for a reason `json` itself would allow."""


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    # `json` keeps the LAST of two equal keys silently. A reader that keeps the
    # first would then see a different answer than the one checked here.
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise _Refused(f"the key {key!r} appears twice")
        seen[key] = value
    return seen


def _no_constants(name: str) -> object:
    raise _Refused(f"{name} is not JSON")


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _load(path: Path, cap: int) -> tuple[object, str | None]:
    """The parsed file, or the one reason it cannot be read."""
    # Checked before reading, and a symlink is never followed: CI would
    # otherwise read whatever the link points at on the runner.
    if path.is_symlink() or not path.is_file():
        return None, f"{path}: must be a regular file"
    size = path.stat().st_size
    if size > cap:
        return None, f"{path}: {size} bytes, over the {cap}-byte cap"
    try:
        text = path.read_bytes().decode("utf-8")
        return (
            json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants),
            None,
        )
    except UnicodeDecodeError:
        return None, f"{path}: not UTF-8"
    except _Refused as error:
        return None, f"{path}: not valid JSON ({error})"
    except json.JSONDecodeError as error:
        return None, f"{path}: not valid JSON ({error})"
    except RecursionError:
        return None, f"{path}: nested too deeply to be an answers file"


def _keys(path: Path, where: str, value: dict, allowed: set[str]) -> list[str]:
    found = []
    unknown = sorted(set(value) - allowed)
    if unknown:
        found.append(f"{path}: unknown key(s) in {where}: {', '.join(unknown)}")
    missing = sorted(allowed - set(value))
    if missing:
        found.append(f"{path}: missing key(s) in {where}: {', '.join(missing)}")
    return found


def _answer_problems(path: Path, task_id: str, entry: object) -> list[str]:
    where = f"answers[{task_id!r}]"
    if not isinstance(entry, dict):
        return [f"{path}: {where} must be an object"]
    found = _keys(path, where, entry, ANSWER_KEYS)

    text = entry.get("answer")
    if "answer" in entry and not isinstance(text, str):
        found.append(f"{path}: {where}.answer must be a string")
    elif isinstance(text, str) and _utf16_len(text) > MAX_ANSWER_CHARS:
        found.append(
            f"{path}: {where}.answer is {_utf16_len(text)} characters, "
            f"over the {MAX_ANSWER_CHARS}-character cap"
        )

    citations = entry.get("citations")
    if "citations" in entry:
        if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
            found.append(f"{path}: {where}.citations must be a list of strings")
        elif len(citations) > MAX_CITATIONS:
            found.append(
                f"{path}: {where}.citations has {len(citations)}, over the cap of {MAX_CITATIONS}"
            )

    confidence = entry.get("confidence")
    if "confidence" in entry:
        # `bool` is an `int` in Python, and `true` is not a confidence.
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            found.append(f"{path}: {where}.confidence must be a number from 0 to 1")
        elif not (math.isfinite(confidence) and 0 <= confidence <= 1):
            found.append(f"{path}: {where}.confidence is {confidence}, outside 0 to 1")

    if "needs_human_review" in entry and not isinstance(entry["needs_human_review"], bool):
        found.append(f"{path}: {where}.needs_human_review must be true or false")
    return found


def _answers_problems(path: Path, document: object) -> list[str]:
    if not isinstance(document, dict):
        return [f"{path}: must be a JSON object"]
    found = _keys(path, "the top level", document, ANSWERS_KEYS)

    if "cohort" in document and document["cohort"] not in COHORTS:
        found.append(
            f"{path}: cohort {document['cohort']!r} is not one this repository hands in "
            f"({', '.join(sorted(COHORTS))})"
        )
    release = document.get("course_release")
    if "course_release" in document and not (isinstance(release, str) and HEX40.match(release)):
        found.append(f"{path}: course_release must be a full 40-character commit, not {release!r}")
    set_id = document.get("question_set_id")
    if "question_set_id" in document and not (
        isinstance(set_id, str) and QUESTION_SET_ID.match(set_id)
    ):
        found.append(f"{path}: question_set_id {set_id!r} is not an id")

    answers = document.get("answers")
    if "answers" in document:
        if not isinstance(answers, dict) or not answers:
            found.append(f"{path}: answers must be a non-empty object keyed by task id")
        else:
            for task_id, entry in answers.items():
                if not TASK_ID.match(task_id):
                    found.append(
                        f"{path}: {task_id!r} is not a task id (expected like 'fa-01')"
                    )
                    continue
                found += _answer_problems(path, task_id, entry)
    return found


def _submission_problems(path: Path, document: object, owner: str) -> list[str]:
    if not isinstance(document, dict):
        return [f"{path}: must be a JSON object"]
    found = _keys(path, "the top level", document, SUBMISSION_KEYS)

    if "kind" in document and document["kind"] != FINAL:
        found.append(f"{path}: kind is {document['kind']!r}, expected 'final'")
    # The same comparison homework makes: the claim against the folder. That
    # the folder is the pull request author's is checked by `verify.yml` and
    # again by `collect.py`, so a claim that matches its folder matches its author.
    if "github" in document and document["github"] != owner:
        found.append(f"{path}: claims {document['github']!r} but sits in {owner!r}")

    repo = document.get("repo")
    if "repo" in document:
        match = REPO_URL.match(repo) if isinstance(repo, str) else None
        if not match or match.group(2) in {".", ".."}:
            found.append(
                f"{path}: repo must be https://github.com/<owner>/<name> with nothing after it, "
                f"not {repo!r}"
            )
    commit = document.get("commit")
    if "commit" in document and not (isinstance(commit, str) and HEX40.match(commit)):
        found.append(f"{path}: commit must be a full 40-character commit, not {commit!r}")
    agent = document.get("agent")
    if "agent" in document and not (isinstance(agent, str) and AGENT.match(agent)):
        found.append(f"{path}: agent must look like 'agent.py:YourAgent', not {agent!r}")

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


def final_problems(directory: Path) -> list[str]:
    """Everything wrong with a final bundle. Empty means it is well-formed."""
    found: list[str] = []
    for name in sorted(FINAL_FILES):
        if not (directory / name).exists() and not (directory / name).is_symlink():
            found.append(f"{directory}: no {name} in the final bundle")
    for entry in sorted(directory.iterdir()):
        if entry.name == "notebook.ipynb":
            found.append(f"{directory}: a final bundle carries no notebook, only answers")
        elif entry.name not in FINAL_FILES:
            found.append(f"{directory}: unexpected file in the bundle: {entry.name}")

    owner = directory.parent.name
    answers_path = directory / "answers.json"
    submission_path = directory / "submission.json"
    if answers_path.exists() or answers_path.is_symlink():
        document, problem = _load(answers_path, MAX_ANSWERS_BYTES)
        found += [problem] if problem else _answers_problems(answers_path, document)
    if submission_path.exists() or submission_path.is_symlink():
        document, problem = _load(submission_path, MAX_SUBMISSION_BYTES)
        found += [problem] if problem else _submission_problems(submission_path, document, owner)
    return found
