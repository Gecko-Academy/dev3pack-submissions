"""The final-bundle rules in `scripts/check_bundle.py`, and that homework kept its own.

    python3 -m unittest discover -s tests

Stdlib only, like everything else in this repository: no install step, so the
tests run anywhere `python3` does.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_bundle  # noqa: E402
import check_final  # noqa: E402

OWNER = "octocat"


def good_answers() -> dict:
    return {
        "cohort": "2026-09",
        "course_release": "a" * 40,
        "question_set_id": "private-2026-09-a",
        "answers": {
            "fa-01": {
                "answer": "Because the index is rebuilt nightly.",
                "citations": ["doc-3", "doc-7"],
                "confidence": 0.8,
                "needs_human_review": False,
            },
            "fa-10": {
                "answer": "I do not know from the provided corpus.",
                "citations": [],
                "confidence": 0,
                "needs_human_review": True,
            },
        },
    }


def good_submission() -> dict:
    return {
        "kind": "final",
        "github": OWNER,
        "repo": "https://github.com/octocat/dev3pack-final",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "agent": "agent.py:YourAgent",
        "submitted_at": "2026-10-01T18:30:00Z",
    }


class FinalBundle(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self._tmp.name) / "submissions" / OWNER / "final"
        self.bundle.mkdir(parents=True)
        self.write("answers.json", good_answers())
        self.write("submission.json", good_submission())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, document: object) -> None:
        (self.bundle / name).write_text(json.dumps(document), encoding="utf-8")

    def problems(self) -> list[str]:
        return check_bundle.problems_with(self.bundle)

    def assertRefused(self, *fragments: str) -> None:
        found = self.problems()
        joined = "\n".join(found)
        self.assertTrue(found, "the bundle was accepted")
        for fragment in fragments:
            self.assertIn(fragment, joined)

    # -- the good case, and the command-line exit code -------------------------

    def test_a_good_final_passes(self) -> None:
        self.assertEqual(self.problems(), [])

    def test_the_command_line_says_ok_and_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_bundle.py"), str(self.bundle)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok:", result.stdout)

    def test_the_command_line_exits_one_on_a_bad_final(self) -> None:
        (self.bundle / "answers.json").unlink()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_bundle.py"), str(self.bundle)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::", result.stdout)

    # -- the files ------------------------------------------------------------

    def test_missing_answers(self) -> None:
        (self.bundle / "answers.json").unlink()
        self.assertRefused("no answers.json in the final bundle")

    def test_missing_submission(self) -> None:
        (self.bundle / "submission.json").unlink()
        self.assertRefused("no submission.json in the final bundle")

    def test_an_extra_file(self) -> None:
        (self.bundle / "agent.py").write_text("print('hi')\n")
        self.assertRefused("unexpected file in the bundle: agent.py")

    def test_an_extra_directory(self) -> None:
        (self.bundle / "extra").mkdir()
        self.assertRefused("unexpected file in the bundle: extra")

    def test_a_notebook(self) -> None:
        (self.bundle / "notebook.ipynb").write_text("{}")
        self.assertRefused("a final bundle carries no notebook")

    def test_a_symlink_is_not_followed(self) -> None:
        target = Path(self._tmp.name) / "elsewhere.json"
        target.write_text(json.dumps(good_answers()))
        (self.bundle / "answers.json").unlink()
        (self.bundle / "answers.json").symlink_to(target)
        self.assertRefused("answers.json: must be a regular file")

    def test_oversize_answers(self) -> None:
        document = good_answers()
        # Valid at every other rule, so only the byte cap can refuse it.
        document["answers"] = {
            f"fa-{n:03d}": {
                "answer": "x" * 7000,
                "citations": [],
                "confidence": 0.5,
                "needs_human_review": False,
            }
            for n in range(160)
        }
        self.write("answers.json", document)
        self.assertRefused("answers.json:", f"over the {check_final.MAX_ANSWERS_BYTES}-byte cap")

    def test_oversize_submission(self) -> None:
        (self.bundle / "submission.json").write_text(" " * (check_final.MAX_SUBMISSION_BYTES + 1))
        self.assertRefused("submission.json:", "-byte cap")

    # -- submission.json ------------------------------------------------------

    def test_wrong_github(self) -> None:
        self.write("submission.json", {**good_submission(), "github": "someone-else"})
        self.assertRefused("claims 'someone-else' but sits in 'octocat'")

    def test_wrong_kind(self) -> None:
        self.write("submission.json", {**good_submission(), "kind": "homework"})
        self.assertRefused("kind is 'homework'")

    def test_bad_repo_urls(self) -> None:
        for repo in (
            "http://github.com/octocat/x",
            "https://gitlab.com/octocat/x",
            "https://github.com/octocat/x?tab=readme",
            "https://github.com/octocat/x#readme",
            "https://github.com/octocat/x/",
            "https://github.com/octocat/x/tree/main",
            "https://github.com/octocat",
            "https://github.com/octocat/..",
            "https://github.com.evil.example/octocat/x",
            42,
        ):
            with self.subTest(repo=repo):
                self.write("submission.json", {**good_submission(), "repo": repo})
                self.assertRefused("repo must be https://github.com/<owner>/<name>")

    def test_short_commit(self) -> None:
        self.write("submission.json", {**good_submission(), "commit": "0123456"})
        self.assertRefused("commit must be a full 40-character commit")

    def test_uppercase_commit(self) -> None:
        self.write("submission.json", {**good_submission(), "commit": "A" * 40})
        self.assertRefused("commit must be a full 40-character commit")

    def test_unknown_key_in_submission(self) -> None:
        self.write("submission.json", {**good_submission(), "score": 100})
        self.assertRefused("submission.json: unknown key(s) in the top level: score")

    def test_missing_key_in_submission(self) -> None:
        document = good_submission()
        del document["commit"]
        self.write("submission.json", document)
        self.assertRefused("submission.json: missing key(s) in the top level: commit")

    def test_bad_agent(self) -> None:
        for agent in ("../agent.py:X", "/etc/agent.py:X", "agent.py", "agent.py:1X", "a/../b.py:X"):
            with self.subTest(agent=agent):
                self.write("submission.json", {**good_submission(), "agent": agent})
                self.assertRefused("agent must look like 'agent.py:YourAgent'")

    def test_bad_submitted_at(self) -> None:
        for stamp in ("2026-10-01", "2026-10-01T18:30:00+02:00", "2026-13-01T18:30:00Z", None):
            with self.subTest(stamp=stamp):
                self.write("submission.json", {**good_submission(), "submitted_at": stamp})
                self.assertRefused("submitted_at must be an ISO 8601 UTC time")

    # -- answers.json ---------------------------------------------------------

    def test_unknown_top_level_key_in_answers(self) -> None:
        self.write("answers.json", {**good_answers(), "github": "someone-else"})
        self.assertRefused("answers.json: unknown key(s) in the top level: github")

    def test_unknown_key_in_one_answer(self) -> None:
        document = good_answers()
        document["answers"]["fa-01"]["score"] = 1
        self.write("answers.json", document)
        self.assertRefused("unknown key(s) in answers['fa-01']: score")

    def test_non_numeric_confidence(self) -> None:
        for confidence in ("0.9", True, None, 1.5, -0.1):
            with self.subTest(confidence=confidence):
                document = good_answers()
                document["answers"]["fa-01"]["confidence"] = confidence
                self.write("answers.json", document)
                self.assertRefused("answers['fa-01'].confidence")

    def test_nan_confidence_is_not_json(self) -> None:
        text = json.dumps(good_answers()).replace('"confidence": 0.8', '"confidence": NaN')
        (self.bundle / "answers.json").write_text(text)
        self.assertRefused("answers.json: not valid JSON", "NaN is not JSON")

    def test_forty_citations_pass_and_forty_one_do_not(self) -> None:
        document = good_answers()
        document["answers"]["fa-01"]["citations"] = [f"doc-{n}" for n in range(40)]
        self.write("answers.json", document)
        self.assertEqual(self.problems(), [])
        document["answers"]["fa-01"]["citations"].append("doc-40")
        self.write("answers.json", document)
        self.assertRefused("answers['fa-01'].citations has 41, over the cap of 40")

    def test_8000_characters_pass_and_8001_do_not(self) -> None:
        document = good_answers()
        document["answers"]["fa-01"]["answer"] = "x" * 8000
        self.write("answers.json", document)
        self.assertEqual(self.problems(), [])
        document["answers"]["fa-01"]["answer"] = "x" * 8001
        self.write("answers.json", document)
        self.assertRefused("answers['fa-01'].answer is 8001 characters, over the 8000-character cap")

    def test_characters_are_counted_as_the_app_counts_them(self) -> None:
        # 4000 emoji are 4000 code points and 8000 UTF-16 units; one more is over
        # the cap in JavaScript even though Python's len() says 4001.
        document = good_answers()
        document["answers"]["fa-01"]["answer"] = "\U0001f600" * 4001
        self.write("answers.json", document)
        self.assertRefused("answer is 8002 characters")

    def test_bad_task_ids(self) -> None:
        for task_id in ("__proto__", "constructor", "FA-01", "fa-1", "fa-0001", "fa 01", "../fa-01"):
            with self.subTest(task_id=task_id):
                document = good_answers()
                document["answers"] = {task_id: document["answers"]["fa-01"]}
                self.write("answers.json", document)
                self.assertRefused("is not a task id")

    def test_citations_must_be_strings(self) -> None:
        document = good_answers()
        document["answers"]["fa-01"]["citations"] = ["doc-1", 2]
        self.write("answers.json", document)
        self.assertRefused("citations must be a list of strings")

    def test_answer_must_be_a_string(self) -> None:
        document = good_answers()
        document["answers"]["fa-01"]["answer"] = ["not", "text"]
        self.write("answers.json", document)
        self.assertRefused("answers['fa-01'].answer must be a string")

    def test_needs_human_review_must_be_boolean(self) -> None:
        document = good_answers()
        document["answers"]["fa-01"]["needs_human_review"] = "yes"
        self.write("answers.json", document)
        self.assertRefused("needs_human_review must be true or false")

    def test_other_cohorts_are_refused(self) -> None:
        self.write("answers.json", {**good_answers(), "cohort": "2099-01"})
        self.assertRefused("cohort '2099-01' is not one this repository hands in")

    def test_short_course_release(self) -> None:
        self.write("answers.json", {**good_answers(), "course_release": "2026.09.1"})
        self.assertRefused("course_release must be a full 40-character commit")

    def test_empty_answers(self) -> None:
        self.write("answers.json", {**good_answers(), "answers": {}})
        self.assertRefused("answers must be a non-empty object")

    def test_duplicate_keys_are_refused(self) -> None:
        # The checker and the reader after it must never see different answers.
        (self.bundle / "answers.json").write_text(
            '{"cohort": "2026-09", "cohort": "2099-01", "course_release": "'
            + "a" * 40
            + '", "question_set_id": "q", "answers": {}}'
        )
        self.assertRefused("the key 'cohort' appears twice")

    def test_not_json(self) -> None:
        (self.bundle / "answers.json").write_text("{not json")
        self.assertRefused("answers.json: not valid JSON")

    def test_not_utf8(self) -> None:
        (self.bundle / "answers.json").write_bytes(b'{"cohort": "\xff"}')
        self.assertRefused("answers.json: not UTF-8")

    def test_deep_nesting_is_refused_not_crashed(self) -> None:
        (self.bundle / "answers.json").write_text("[" * 200_000 + "]" * 200_000)
        self.assertRefused("answers.json:")


class HomeworkUnchanged(unittest.TestCase):
    """The folder decides the rules; homework must still meet its own."""

    def test_every_merged_homework_bundle_still_passes(self) -> None:
        bundles = sorted(p.parent for p in (ROOT / "submissions").glob("*/*/submission.json"))
        bundles = [b for b in bundles if b.name != check_final.FINAL]
        self.assertTrue(bundles, "no homework in this checkout to compare against")
        for bundle in bundles:
            with self.subTest(bundle=bundle.relative_to(ROOT).as_posix()):
                self.assertEqual(check_bundle.problems_with(bundle), [])

    def test_a_final_shaped_bundle_in_a_chapter_folder_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "submissions" / OWNER / "ch03"
            bundle.mkdir(parents=True)
            (bundle / "answers.json").write_text(json.dumps(good_answers()))
            (bundle / "submission.json").write_text(json.dumps(good_submission()))
            found = "\n".join(check_bundle.problems_with(bundle))
            self.assertIn("no notebook.ipynb beside the claim", found)
            self.assertIn("unexpected file in the bundle: answers.json", found)

    def test_final_is_still_not_a_homework_item(self) -> None:
        self.assertIsNone(check_bundle.ITEM.match("final"))


if __name__ == "__main__":
    unittest.main()
