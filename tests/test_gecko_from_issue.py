"""The issue-form route: one pasted link becomes a checked gecko hand-in.

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

import gecko_from_issue  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
FORM = "### Your my-gecko-buyer link\n\n{link}\n"


def fake_github(*, exists: bool = True, private: bool = False, evidence: bool = True):
    def fetch(path: str) -> dict | None:
        if path == "repos/octocat/my-gecko-buyer" or path == "repos/OctoCat/my-gecko-buyer":
            if not exists:
                return None
            return {"full_name": "octocat/my-gecko-buyer", "private": private, "default_branch": "main"}
        if path == "repos/octocat/my-gecko-buyer/commits/main":
            return {"sha": SHA}
        if path.startswith("repos/octocat/my-gecko-buyer/git/trees/"):
            files = ["receipts/4yc7jA8L.md", "refusals/5-beans.json", "smoke-report.json"]
            return {"tree": [{"path": p, "type": "blob"} for p in (files if evidence else ["README.md"])]}
        return None

    return fetch


class IssueToHandIn(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_form(self, link: str, author: str = "octocat", **github: bool) -> tuple[bool, str]:
        return gecko_from_issue.hand_in(
            author, FORM.format(link=link), fake_github(**github), root=self.root
        )

    def written(self) -> dict:
        return json.loads(
            (self.root / "submissions" / "octocat" / "gecko" / "submission.json").read_text()
        )

    def test_a_pasted_link_is_recorded_with_the_newest_commit(self) -> None:
        ok, reply = self.run_form("https://github.com/octocat/my-gecko-buyer")
        self.assertTrue(ok, reply)
        claim = self.written()
        self.assertEqual(claim["repo"], "https://github.com/octocat/my-gecko-buyer")
        self.assertEqual(claim["commit"], SHA)
        self.assertEqual(claim["github"], "octocat")
        self.assertIn("Recorded", reply)
        self.assertNotIn("judges will look for", reply)

    def test_sloppy_links_are_forgiven(self) -> None:
        for link in (
            "https://github.com/octocat/my-gecko-buyer.git",
            "https://github.com/octocat/my-gecko-buyer/",
            "https://github.com/octocat/my-gecko-buyer/tree/main",
            "github link: https://www.github.com/OctoCat/my-gecko-buyer thanks!",
        ):
            ok, reply = self.run_form(link)
            self.assertTrue(ok, f"{link}: {reply}")
            self.assertEqual(self.written()["repo"], "https://github.com/octocat/my-gecko-buyer")

    def test_missing_evidence_is_recorded_with_a_warning(self) -> None:
        ok, reply = self.run_form("https://github.com/octocat/my-gecko-buyer", evidence=False)
        self.assertTrue(ok)
        self.assertIn("receipts/*.md", reply)
        self.assertIn("smoke-report.json", reply)

    def test_someone_elses_repository_is_refused(self) -> None:
        ok, reply = self.run_form("https://github.com/hubot/my-gecko-buyer")
        self.assertFalse(ok)
        self.assertIn("belongs to `hubot`", reply)
        self.assertFalse((self.root / "submissions").exists())

    def test_the_template_is_refused_with_how_to_make_it_yours(self) -> None:
        ok, reply = self.run_form(
            "https://github.com/Gecko-Academy/Dev3Pack-Gecko-Capstone-Project"
        )
        self.assertFalse(ok)
        self.assertIn("course template", reply)

    def test_a_private_or_missing_repository_is_refused(self) -> None:
        ok, reply = self.run_form("https://github.com/octocat/my-gecko-buyer", exists=False)
        self.assertFalse(ok)
        self.assertIn("private", reply)
        ok, reply = self.run_form("https://github.com/octocat/my-gecko-buyer", private=True)
        self.assertFalse(ok)
        self.assertIn("Make it public", reply)

    def test_no_link_at_all_is_refused(self) -> None:
        ok, reply = self.run_form("my repo is my-gecko-buyer")
        self.assertFalse(ok)
        self.assertIn("could not find a GitHub link", reply)

    def test_a_second_hand_in_replaces_the_first(self) -> None:
        self.run_form("https://github.com/octocat/my-gecko-buyer")
        folder = self.root / "submissions" / "octocat" / "gecko"
        (folder / "stray.txt").write_text("x")
        ok, _ = self.run_form("https://github.com/octocat/my-gecko-buyer")
        self.assertTrue(ok)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["submission.json"])


if __name__ == "__main__":
    unittest.main()
