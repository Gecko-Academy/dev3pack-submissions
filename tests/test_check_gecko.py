"""The Gecko capstone hand-in: one `submission.json` linking the student's own repo.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_bundle  # noqa: E402

OWNER = "octocat"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def good_link() -> dict:
    return {
        "kind": "gecko",
        "github": OWNER,
        "repo": "https://github.com/octocat/my-gecko-buyer",
        "commit": COMMIT,
    }


class GeckoHandIn(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self._tmp.name) / "submissions" / OWNER / "gecko"
        self.bundle.mkdir(parents=True)
        self.write(good_link())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, document: object, name: str = "submission.json") -> None:
        (self.bundle / name).write_text(json.dumps(document), encoding="utf-8")

    def assertRefused(self, *fragments: str) -> None:
        found = check_bundle.problems_with(self.bundle)
        joined = "\n".join(found)
        self.assertTrue(found, "the hand-in was accepted")
        for fragment in fragments:
            self.assertIn(fragment, joined)

    def test_a_hand_typed_link_is_accepted(self) -> None:
        self.assertEqual(check_bundle.problems_with(self.bundle), [])

    def test_the_cli_timestamp_is_accepted(self) -> None:
        self.write({**good_link(), "submitted_at": "2026-10-04T12:00:00Z"})
        self.assertEqual(check_bundle.problems_with(self.bundle), [])

    def test_owner_case_does_not_matter(self) -> None:
        self.write({**good_link(), "repo": "https://github.com/OctoCat/my-gecko-buyer"})
        self.assertEqual(check_bundle.problems_with(self.bundle), [])

    def test_the_course_template_is_refused(self) -> None:
        self.write(
            {
                **good_link(),
                "repo": "https://github.com/Gecko-Academy/Dev3Pack-Gecko-Capstone-Project",
            }
        )
        self.assertRefused("belongs to 'Gecko-Academy'")

    def test_someone_elses_repo_is_refused(self) -> None:
        self.write({**good_link(), "repo": "https://github.com/hubot/my-gecko-buyer"})
        self.assertRefused("belongs to 'hubot'")

    def test_a_short_commit_is_refused(self) -> None:
        self.write({**good_link(), "commit": "0123456"})
        self.assertRefused("full 40-character commit")

    def test_a_link_with_a_path_is_refused(self) -> None:
        self.write({**good_link(), "repo": "https://github.com/octocat/my-gecko-buyer/tree/main"})
        self.assertRefused("repo must be")

    def test_the_wrong_kind_is_refused(self) -> None:
        self.write({**good_link(), "kind": "final"})
        self.assertRefused("expected 'gecko'")

    def test_a_claim_for_another_student_is_refused(self) -> None:
        self.write({**good_link(), "github": "hubot"})
        self.assertRefused("claims 'hubot'")

    def test_a_missing_key_is_refused(self) -> None:
        link = good_link()
        del link["commit"]
        self.write(link)
        self.assertRefused("missing key(s) in the top level: commit")

    def test_an_unknown_key_is_refused(self) -> None:
        self.write({**good_link(), "receipt": "x"})
        self.assertRefused("unknown key(s)")

    def test_code_beside_the_link_is_refused(self) -> None:
        (self.bundle / "notebook.ipynb").write_text("{}")
        self.assertRefused("unexpected file in the bundle: notebook.ipynb")

    def test_an_empty_folder_is_refused(self) -> None:
        (self.bundle / "submission.json").unlink()
        self.assertRefused("no submission.json")


if __name__ == "__main__":
    unittest.main()
